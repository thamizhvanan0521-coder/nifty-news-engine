from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_ist() -> datetime:
    return datetime.now(IST)


def to_ist(dt: datetime) -> datetime:
    return dt.astimezone(IST)


def fmt_ist(dt: datetime) -> str:
    return to_ist(dt).strftime("%d-%b %H:%M IST")


def parse_hhmm(s: str) -> time:
    h, m = s.strip().split(":")
    return time(int(h), int(m))


def in_window(t: time, start: time, end: time) -> bool:
    """True if t is inside [start, end). Handles windows that cross midnight."""
    if start <= end:
        return start <= t < end
    return t >= start or t < end


def is_trading_day(d: date, holidays: set[str]) -> bool:
    return d.weekday() < 5 and d.isoformat() not in holidays


def last_session_close(ref_ist: datetime, holidays: set[str]) -> datetime:
    """Most recent 15:30 IST close at or before ref_ist, on a trading day."""
    d = ref_ist.date()
    close_today = datetime.combine(d, MARKET_CLOSE, IST)
    if ref_ist >= close_today and is_trading_day(d, holidays):
        return close_today
    d -= timedelta(days=1)
    while not is_trading_day(d, holidays):
        d -= timedelta(days=1)
    return datetime.combine(d, MARKET_CLOSE, IST)
