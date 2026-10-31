"""Calendar arithmetic for subscriptions and allowances.

Everything takes and returns timezone-aware datetimes. "A day" and "a month" are
calendar ones in the application's timezone (APP_TIMEZONE, Europe/Berlin by default),
because that is what a student means by "today" - a quota that reset at 01:00 or 02:00
local time, as a UTC day does in Germany, would read as a bug.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Period = Literal["day", "week", "month"]
PERIODS: tuple[Period, ...] = ("day", "week", "month")


def resolve_timezone(name: str) -> tzinfo:
    """The named IANA zone; ValueError for an unknown name (checked once at startup)."""

    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"Unknown timezone {name!r}") from error


def aware(moment: datetime) -> datetime:
    """Naive datetimes from SQLite are UTC by construction in this application."""

    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def add_months(moment: datetime, months: int, zone: tzinfo) -> datetime:
    """The same local wall-clock time `months` calendar months later, as UTC.

    The day is clamped to the target month's length: 31 January plus one month is
    28 February (29 in a leap year), never 2 or 3 March. Local time is kept across a
    daylight-saving change, so a code redeemed at 10:00 expires at 10:00.
    """

    if months < 0:
        raise ValueError("months must not be negative")
    local = aware(moment).astimezone(zone)
    index = local.month - 1 + months
    year, month = local.year + index // 12, index % 12 + 1
    day = min(local.day, calendar.monthrange(year, month)[1])
    shifted = datetime.combine(date(year, month, day), local.timetz().replace(tzinfo=None), zone)
    return shifted.astimezone(timezone.utc)


def period_bounds(now: datetime, period: Period, zone: tzinfo) -> tuple[datetime, datetime]:
    """Start of the current calendar period and the moment it resets, both UTC.

    A week starts on Monday (ISO 8601, and the German school week).
    """

    today = aware(now).astimezone(zone).date()
    if period == "day":
        start, end = today, today + timedelta(days=1)
    elif period == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
    elif period == "month":
        start = today.replace(day=1)
        end = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
    else:
        raise ValueError(f"unknown period {period!r}")
    return (datetime.combine(start, time(0), zone).astimezone(timezone.utc),
            datetime.combine(end, time(0), zone).astimezone(timezone.utc))
