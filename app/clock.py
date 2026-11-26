"""Shanghai timestamps for a user who lives in Asia/Shanghai."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def now_sh() -> datetime:
    return datetime.now(SHANGHAI)


def today_sh() -> date:
    return now_sh().date()


def iso_sh(moment: datetime | None = None) -> str:
    current = moment or now_sh()
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI)
    return current.astimezone(SHANGHAI).isoformat(timespec="seconds")
