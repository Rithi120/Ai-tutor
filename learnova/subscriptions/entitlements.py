"""The one place that decides what an account may do: plan, Premium expiry, allowances.

Free accounts have three allowances: projects (rolling window), short lessons (calendar
day) and the chat allowance (credits per calendar period). Premium has none of them.
Abuse protection - request rates, request sizes, site and provider budgets - lives in
the AI gateway and applies to everyone; it is deliberately not part of this module.

Usage is a ledger of rows in `usage_quota_event`:

    reserved  -> taken before the expensive work starts, so a parallel request sees it
    committed -> the work succeeded; this row is what the allowance counts
    released  -> the work failed; the row stays for the record but counts for nothing

A reservation older than the TTL counts for nothing either: a worker that died mid-call
must not hold a student's allowance forever.

Concurrency. Checking and reserving are one transaction that first updates the
account's `usage_lock_version`. On PostgreSQL that takes the user row's lock, so a
second request for the same account waits until the first has committed its
reservation, then counts it. On SQLite the same UPDATE takes the database write lock.
Either way two requests cannot both see "one left" - and the lock is per account, so
students never wait on each other.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from learnova.ai_services.service import AIRequestLimitError

from .periods import aware, period_bounds
from .plans import FREE, PREMIUM, SubscriptionSettings

RESERVED, COMMITTED, RELEASED = "reserved", "committed", "released"
# `AIRequestLimitError.scope` for each allowance; app.ai_failure_message words them.
QUOTA_SCOPES = {"project": "free_projects", "lesson": "free_lessons", "chat": "free_chat"}


class QuotaExceededError(AIRequestLimitError):
    """A free allowance is used up. Scopes: free_projects, free_lessons, free_chat.

    A subclass of the gateway's limit error on purpose: every route already turns that
    into a translated "you have reached your limit, back at <time>" answer, so a new
    allowance cannot reach a student as a crash or a generic failure.
    """

    def __init__(self, kind: str, *, resets_at: datetime | None):
        retry = None
        if resets_at is not None:
            retry = max(1, int((resets_at - datetime.now(timezone.utc)).total_seconds()) + 1)
        super().__init__(f"Free {kind} allowance used up.", scope=QUOTA_SCOPES[kind],
                         resets_at=resets_at, retry_after_seconds=retry)
        self.kind = kind


@dataclass(frozen=True)
class Allowance:
    kind: str
    limit: float | None          # None = no ordinary limit (Premium)
    used: float
    resets_at: datetime | None   # when room next appears; None when unknown or unlimited

    @property
    def unlimited(self) -> bool:
        return self.limit is None

    @property
    def remaining(self) -> float | None:
        return None if self.limit is None else max(0.0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        return self.limit is not None and self.used >= self.limit


@dataclass(frozen=True)
class Entitlements:
    plan: str
    premium_active: bool
    premium_starts_at: datetime | None
    premium_expires_at: datetime | None
    projects: Allowance
    lessons: Allowance
    chat: Allowance


def _naive(moment: datetime) -> datetime:
    return aware(moment).astimezone(timezone.utc).replace(tzinfo=None)


class Reservation:
    """A held piece of allowance. Premium and already-counted work get a no-op one."""

    def __init__(self, service: "EntitlementService | None", event_id: int | None, kind: str):
        self._service, self.event_id, self.kind = service, event_id, kind
        self.done = event_id is None

    @property
    def active(self) -> bool:
        return not self.done

    def commit(self, *, reference: str | None = None, amount: float | None = None) -> None:
        """Count the work, in the request's own session (call after the work is saved)."""

        if self.done or self._service is None or self.event_id is None:
            return
        self.done = True
        self._service.finish(self.event_id, COMMITTED, reference=reference, amount=amount)

    def release(self) -> None:
        """Give the allowance back, after the request's own transaction has ended."""

        if self.done or self._service is None or self.event_id is None:
            return
        self.done = True
        self._service.defer(self.event_id, RELEASED)

    def settle_later(self, amount: float) -> None:
        """Commit with the measured amount after the request; 0 means it cost nothing."""

        if self.done or self._service is None or self.event_id is None:
            return
        self.done = True
        if amount <= 0:
            self._service.defer(self.event_id, RELEASED, amount=0.0)
        else:
            self._service.defer(self.event_id, COMMITTED, amount=amount)


class EntitlementService:
    """Bound to the application's database and models by app.py at import time."""

    def __init__(self, *, db: Any, user_model: Any, event_model: Any, entitlement_model: Any,
                 settings: Callable[[], SubscriptionSettings],
                 deferred: Callable[[], list[tuple[int, str, float | None]]],
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.db, self.User, self.Event, self.Entitlement = db, user_model, event_model, entitlement_model
        self.settings, self._deferred, self.now = settings, deferred, now

    # ---- plan --------------------------------------------------------------------

    def premium_record(self, user_id: int):
        return self.db.session.scalar(self.db.select(self.Entitlement).where(
            self.Entitlement.user_id == user_id))

    def premium_active(self, user_id: int | None, now: datetime | None = None) -> bool:
        if not user_id:
            return False
        record = self.premium_record(user_id)
        return bool(record and record.status == "active" and aware(record.expires_at) > (now or self.now()))

    # ---- allowances --------------------------------------------------------------

    def _window(self, kind: str, now: datetime) -> tuple[datetime, datetime | None, float]:
        """(counting starts, fixed reset or None for rolling, limit)."""

        settings = self.settings()
        if kind == "project":
            days = settings.free_project_window_days
            return now - timedelta(days=days), None, float(settings.free_projects_per_window)
        if kind == "lesson":
            start, end = period_bounds(now, "day", settings.zone)
            return start, end, float(settings.free_lessons_per_day)
        if kind == "chat":
            start, end = period_bounds(now, settings.free_chat_period, settings.zone)
            return start, end, float(settings.free_chat_credits_per_period)
        raise ValueError(f"unknown allowance {kind!r}")

    def _counted(self, user_id: int, kind: str, since: datetime, now: datetime):
        """Rows that count right now: committed in the window, or reserved and still live."""

        Event = self.Event
        stale_before = now - timedelta(minutes=self.settings().reservation_ttl_minutes)
        return self.db.session.scalars(self.db.select(Event).where(
            Event.user_id == user_id, Event.kind == kind, Event.occurred_at >= _naive(since),
            self.db.or_(Event.status == COMMITTED,
                        self.db.and_(Event.status == RESERVED, Event.occurred_at >= _naive(stale_before))),
        ).order_by(Event.occurred_at)).all()

    def allowance(self, user_id: int, kind: str, *, premium: bool | None = None,
                  now: datetime | None = None) -> Allowance:
        now = now or self.now()
        if premium is None:
            premium = self.premium_active(user_id, now)
        if premium:
            return Allowance(kind, None, 0.0, None)
        since, fixed_reset, limit = self._window(kind, now)
        rows = self._counted(user_id, kind, since, now)
        used = float(sum(row.amount for row in rows))
        resets_at = fixed_reset
        if fixed_reset is None:
            # Rolling window: room appears when the oldest counted project leaves it.
            days = self.settings().free_project_window_days
            resets_at = aware(rows[0].occurred_at) + timedelta(days=days) if rows else None
        return Allowance(kind, limit, used, resets_at)

    def entitlements(self, user_id: int, now: datetime | None = None) -> Entitlements:
        now = now or self.now()
        record = self.premium_record(user_id)
        active = bool(record and record.status == "active" and aware(record.expires_at) > now)
        return Entitlements(
            plan=PREMIUM if active else FREE, premium_active=active,
            premium_starts_at=aware(record.starts_at) if record and active else None,
            premium_expires_at=aware(record.expires_at) if record else None,
            projects=self.allowance(user_id, "project", premium=active, now=now),
            lessons=self.allowance(user_id, "lesson", premium=active, now=now),
            chat=self.allowance(user_id, "chat", premium=active, now=now),
        )

    # ---- reserve, commit, release ------------------------------------------------

    def already_counted(self, user_id: int, kind: str, reference: str) -> bool:
        Event = self.Event
        now = self.now()
        stale_before = now - timedelta(minutes=self.settings().reservation_ttl_minutes)
        return self.db.session.scalar(self.db.select(Event.id).where(
            Event.user_id == user_id, Event.kind == kind, Event.reference == reference,
            self.db.or_(Event.status == COMMITTED,
                        self.db.and_(Event.status == RESERVED, Event.occurred_at >= _naive(stale_before))),
        ).limit(1)) is not None

    def reserve(self, user_id: int, kind: str, *, amount: float = 1.0, reference: str = "") -> Reservation:
        """Hold `amount` of the allowance or raise QuotaExceededError.

        Commits the session: the reservation must be visible to other requests before
        the expensive work begins. With a `reference`, work already counted (or being
        counted by a parallel request) is not counted again.
        """

        db, Event, User = self.db, self.Event, self.User
        now = self.now()
        if self.premium_active(user_id, now):
            return Reservation(None, None, kind)
        # The account lock: see the module docstring.
        db.session.execute(db.update(User).where(User.id == user_id).values(
            usage_lock_version=User.usage_lock_version + 1))
        if reference and self.already_counted(user_id, kind, reference):
            db.session.commit()
            return Reservation(None, None, kind)
        allowance = self.allowance(user_id, kind, premium=False, now=now)
        if allowance.limit is not None and allowance.used + amount > allowance.limit:
            # Commit, not roll back: the lock must be released either way, and what the
            # request had already done (pages it read, the student's chat message) is
            # kept, as it would be for any other AI failure.
            db.session.commit()
            raise QuotaExceededError(kind, resets_at=allowance.resets_at)
        event = Event(user_id=user_id, kind=kind, status=RESERVED, amount=float(amount),
                      reference=reference[:80], occurred_at=_naive(now), created_at=_naive(now))
        db.session.add(event)
        db.session.commit()
        return Reservation(self, event.id, kind)

    def check(self, user_id: int, kind: str) -> Allowance:
        """Raise QuotaExceededError when nothing is left, without reserving anything."""

        allowance = self.allowance(user_id, kind)
        if allowance.exhausted:
            raise QuotaExceededError(kind, resets_at=allowance.resets_at)
        return allowance

    def finish(self, event_id: int, status: str, *, reference: str | None = None,
               amount: float | None = None) -> None:
        values: dict[str, Any] = {"status": status, "settled_at": _naive(self.now())}
        if status == COMMITTED:
            # A committed row counts from the moment the work succeeded.
            values["occurred_at"] = _naive(self.now())
        if reference is not None:
            values["reference"] = reference[:80]
        if amount is not None:
            values["amount"] = float(amount)
        self.db.session.execute(self.db.update(self.Event).where(
            self.Event.id == event_id, self.Event.status == RESERVED).values(**values))
        self.db.session.commit()

    def defer(self, event_id: int, status: str, *, amount: float | None = None) -> None:
        self._deferred().append((event_id, status, amount))

    def flush_deferred(self, connection_factory: Callable[[], Any]) -> None:
        """Apply deferred settlements on a fresh connection, after the request's session ended."""

        pending = list(self._deferred())
        if not pending:
            return
        self._deferred().clear()
        now = _naive(self.now())
        with connection_factory() as connection:
            for event_id, status, amount in pending:
                values: dict[str, Any] = {"status": status, "settled_at": now}
                if amount is not None:
                    values["amount"] = float(amount)
                if status == COMMITTED:
                    values["occurred_at"] = now
                connection.execute(self.db.update(self.Event).where(
                    self.Event.id == event_id, self.Event.status == RESERVED).values(**values))
