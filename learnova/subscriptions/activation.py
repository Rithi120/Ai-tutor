"""Issuing, redeeming and revoking Premium activation codes.

The cash workflow: the administrator confirms a payment, issues one code on the admin
page (or with `flask premium issue`), reads it out or hands it over, and the student
types it into Settings -> Subscription. The plaintext is returned exactly once, from
`issue`; nothing stores, logs or re-displays it.

Redemption rules:
- one generic failure for unknown, malformed, expired, revoked and already-used codes,
  so the answer never tells a guesser which codes exist;
- failed attempts are counted in the database (per account and per hashed source
  address), so the limit survives restarts and applies across workers;
- the claim is a single conditional UPDATE, so two simultaneous redemptions of one
  code cannot both succeed, and the redemption row is unique per code;
- an active subscription is extended from its current expiry, a lapsed one starts now,
  both by calendar months (see periods.add_months).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from . import codes
from .periods import add_months, aware
from .plans import SubscriptionSettings

ACTIVATED, ALREADY_YOURS, INVALID, RATE_LIMITED = "activated", "already_yours", "invalid", "rate_limited"


@dataclass(frozen=True)
class RedeemResult:
    outcome: str
    expires_at: datetime | None = None
    retry_after_minutes: int = 0

    @property
    def ok(self) -> bool:
        return self.outcome in (ACTIVATED, ALREADY_YOURS)


@dataclass(frozen=True)
class IssuedCode:
    code: str            # plaintext - show once, never store
    key_id: int
    months: int
    expires_at: datetime


def _naive(moment: datetime) -> datetime:
    return aware(moment).astimezone(timezone.utc).replace(tzinfo=None)


class ActivationService:
    def __init__(self, *, db: Any, user_model: Any, key_model: Any, entitlement_model: Any,
                 redemption_model: Any, audit_model: Any,
                 settings: Callable[[], SubscriptionSettings], secret: Callable[[], str],
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.db, self.User, self.Key = db, user_model, key_model
        self.Entitlement, self.Redemption, self.Audit = entitlement_model, redemption_model, audit_model
        self.settings, self._secret, self.now = settings, secret, now

    # ---- audit -------------------------------------------------------------------

    def source_reference(self, address: str | None) -> str:
        """A keyed hash of the client address: countable, never reversible to an IP."""

        if not address:
            return ""
        return hmac.new(self._secret().encode("utf-8"), address.encode("utf-8"), hashlib.sha256).hexdigest()[:32]

    def audit(self, action: str, *, actor_id: int | None = None, subject_id: int | None = None,
              key_id: int | None = None, source: str = "", **detail: Any) -> None:
        """Add an audit row to the session. Never pass a code - only ids and outcomes."""

        self.db.session.add(self.Audit(
            created_at=_naive(self.now()), action=action[:40], actor_user_id=actor_id,
            subject_user_id=subject_id, premium_key_id=key_id, source_reference=source[:64],
            detail_json=json.dumps(detail, ensure_ascii=False, default=str)[:2000]))

    # ---- redemption --------------------------------------------------------------

    def _failures_since(self, since: datetime, *, user_id: int | None = None, source: str = ""):
        Audit = self.Audit
        query = self.db.select(Audit.created_at).where(
            Audit.action == "code_redeem_failed", Audit.created_at >= _naive(since))
        if user_id is not None:
            query = query.where(Audit.actor_user_id == user_id)
        else:
            query = query.where(Audit.source_reference == source)
        return [aware(moment) for moment in self.db.session.scalars(query.order_by(Audit.created_at)).all()]

    def _rate_limited(self, user_id: int, source: str, now: datetime) -> int:
        """Minutes until another attempt is allowed, or 0."""

        settings = self.settings()
        window = timedelta(minutes=settings.redeem_window_minutes)
        checks = [(self._failures_since(now - window, user_id=user_id), settings.redeem_attempts_per_account)]
        if source:
            checks.append((self._failures_since(now - window, source=source), settings.redeem_attempts_per_source))
        wait = 0
        for failures, limit in checks:
            if len(failures) >= limit:
                # Room appears when the oldest failure that still counts leaves the window.
                opens = failures[len(failures) - limit] + window
                wait = max(wait, int((opens - now).total_seconds() // 60) + 1)
        return wait

    def _expiry_after(self, base: datetime, key: Any) -> datetime:
        months = int(key.duration_months or 0)
        if months > 0:
            return add_months(base, months, self.settings().zone)
        # Keys issued before calendar months were introduced keep what was sold: days.
        return aware(base) + timedelta(days=int(key.duration_days or 30))

    def redeem(self, user_id: int, raw: object, *, source_address: str | None = None) -> RedeemResult:
        db, Key, User = self.db, self.Key, self.User
        now = self.now()
        source = self.source_reference(source_address)
        # Serialise this account's attempts (as the allowance lock does) so parallel
        # guesses cannot all slip under the attempt limit.
        db.session.execute(db.update(User).where(User.id == user_id).values(
            usage_lock_version=User.usage_lock_version + 1))
        wait = self._rate_limited(user_id, source, now)
        if wait:
            db.session.rollback()
            return RedeemResult(RATE_LIMITED, retry_after_minutes=wait)

        hashes = codes.candidate_hashes(raw)
        key = None
        if hashes:
            key = db.session.scalar(db.select(Key).where(Key.key_hash.in_(hashes)).with_for_update())
        if key is not None and not any(hmac.compare_digest(key.key_hash, item) for item in hashes):
            key = None
        if key is not None and key.redeemed_at is not None and key.redeemed_by_id == user_id:
            # A double submit or a retry after a lost response: say it worked, extend nothing.
            record = db.session.scalar(db.select(self.Entitlement).where(self.Entitlement.user_id == user_id))
            db.session.rollback()
            return RedeemResult(ALREADY_YOURS, expires_at=aware(record.expires_at) if record else None)

        reason = ""
        if not hashes:
            reason = "malformed"
        elif key is None:
            reason = "unknown"
        elif key.revoked_at is not None:
            reason = "revoked"
        elif key.redeemed_at is not None:
            reason = "already_redeemed"
        elif aware(key.expires_at) <= now:
            reason = "expired"
        if not reason and key is not None:
            # The claim is decided by the database in one statement. synchronize_session
            # is off: SQLite returns naive datetimes and Python-side re-evaluation of the
            # WHERE against the loaded key would compare naive with aware.
            claimed = db.session.execute(db.update(Key).where(
                Key.id == key.id, Key.redeemed_at.is_(None), Key.revoked_at.is_(None),
                Key.expires_at > now,
            ).values(redeemed_at=now, redeemed_by_id=user_id).execution_options(synchronize_session=False))
            if claimed.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
                reason = "lost_race"
        if reason:
            db.session.rollback()
            # Recorded in its own transaction: the failure must count even though the
            # claim above was rolled back.
            db.session.execute(db.update(User).where(User.id == user_id).values(
                usage_lock_version=User.usage_lock_version + 1))
            self.audit("code_redeem_failed", actor_id=user_id, subject_id=user_id,
                       key_id=key.id if key is not None and reason != "unknown" else None,
                       source=source, reason=reason)
            db.session.commit()
            return RedeemResult(INVALID)

        assert key is not None
        record = db.session.scalar(db.select(self.Entitlement).where(
            self.Entitlement.user_id == user_id).with_for_update())
        active = bool(record and record.status == "active" and aware(record.expires_at) > now)
        previous = aware(record.expires_at) if record else None
        new_expiry = self._expiry_after(previous if active and previous else now, key)
        if record is None:
            record = self.Entitlement(user_id=user_id, starts_at=now, expires_at=new_expiry,
                                      status="active", granted_by="activation_code", updated_at=now)
            db.session.add(record)
        else:
            if not active:
                record.starts_at = now
            record.expires_at, record.status, record.updated_at = new_expiry, "active", now
            record.granted_by = "activation_code"
        db.session.add(self.Redemption(premium_key_id=key.id, user_id=user_id, redeemed_at=now,
                                       previous_expires_at=previous, new_expires_at=new_expiry))
        self.audit("code_redeemed", actor_id=user_id, subject_id=user_id, key_id=key.id, source=source,
                   extended=active, months=int(key.duration_months or 0),
                   days=0 if key.duration_months else int(key.duration_days or 30),
                   expires_at=new_expiry.isoformat())
        db.session.commit()
        return RedeemResult(ACTIVATED, expires_at=new_expiry)

    # ---- administration ----------------------------------------------------------

    def issue(self, admin_id: int, *, months: int, payment_amount: Decimal, payment_date: date,
              payment_reference: str) -> IssuedCode:
        settings = self.settings()
        if not 1 <= months <= settings.code_max_duration_months:
            raise ValueError("months out of range")
        now = self.now()
        code = codes.generate_code()
        normalized = codes.normalize_code(code)
        assert normalized is not None
        key = self.Key(
            key_hash=codes.code_hash(normalized), code_hint=codes.code_hint(normalized),
            duration_months=months, duration_days=0, created_at=now,
            expires_at=now + timedelta(days=settings.code_validity_days), created_by_id=admin_id,
            payment_amount=f"{payment_amount:.2f}", payment_date=payment_date,
            payment_reference=payment_reference[:100])
        self.db.session.add(key)
        self.db.session.flush()
        self.audit("code_issued", actor_id=admin_id, key_id=key.id, months=months,
                   payment_amount=f"{payment_amount:.2f}", payment_date=payment_date.isoformat())
        self.db.session.commit()
        return IssuedCode(code=code, key_id=key.id, months=months, expires_at=aware(key.expires_at))

    def revoke(self, admin_id: int, key_id: int) -> bool:
        """Revoke an unused code. Redeemed codes stay redeemed: the student has paid."""

        Key, now = self.Key, self.now()
        changed = self.db.session.execute(self.db.update(Key).where(
            Key.id == key_id, Key.redeemed_at.is_(None), Key.revoked_at.is_(None),
        ).values(revoked_at=now, revoked_by_id=admin_id).execution_options(synchronize_session=False))
        if changed.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
            self.db.session.rollback()
            return False
        self.audit("code_revoked", actor_id=admin_id, key_id=key_id)
        self.db.session.commit()
        return True

    def expire_due(self, user_id: int | None = None) -> int:
        """Mark lapsed subscriptions expired and audit each once. Access never depends on
        this - an expired date is Free on every request - it keeps the record honest."""

        Entitlement, now = self.Entitlement, self.now()
        query = self.db.select(Entitlement).where(
            Entitlement.status == "active", Entitlement.expires_at <= now)
        if user_id is not None:
            query = query.where(Entitlement.user_id == user_id)
        due = self.db.session.scalars(query).all()
        for record in due:
            record.status, record.updated_at = "expired", now
            self.audit("subscription_expired", subject_id=record.user_id,
                       expired_at=aware(record.expires_at).isoformat())
        if due:
            self.db.session.commit()
        return len(due)

    @staticmethod
    def key_status(key: Any, now: datetime) -> str:
        if key.revoked_at:
            return "revoked"
        if key.redeemed_at:
            return "redeemed"
        if aware(key.expires_at) <= now:
            return "expired"
        return "unused"
