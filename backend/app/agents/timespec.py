"""Symbolic time expressions -> absolute UTC windows.

The LLM emits a SYMBOL ("tomorrow_morning"), never a timestamp. Python resolves
it here. Same reasoning as places.py: a timestamp is a number, and a model that
invents one shifts the entire answer to a different part of the forecast with
no visible error.

Times are resolved in IST (Asia/Kolkata) because "tomorrow morning" means
tomorrow morning to a fisherman in Nagapattinam, then converted to UTC because
that is what the database stores. Getting this backwards shifts every window by
5h30m -- which for "tomorrow morning" lands it in tonight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

IST = timezone(timedelta(hours=5, minutes=30), "IST")

TimeSymbol = Literal[
    "now", "today", "tonight",
    "tomorrow", "tomorrow_morning", "tomorrow_afternoon", "tomorrow_night",
    "next_24h", "next_48h", "next_72h",
]

TIME_SYMBOLS: tuple[str, ...] = (
    "now", "today", "tonight", "tomorrow", "tomorrow_morning",
    "tomorrow_afternoon", "tomorrow_night", "next_24h", "next_48h", "next_72h",
)

# Local-clock definitions, stated rather than assumed. "Morning" for a fishing
# crew starts before dawn: boats leave in the dark.
_LOCAL_WINDOWS: dict[str, tuple[int, int, int]] = {
    # symbol: (day offset, start hour IST, end hour IST)
    "today": (0, 0, 24),
    "tonight": (0, 18, 24),
    "tomorrow": (1, 0, 24),
    "tomorrow_morning": (1, 4, 12),
    "tomorrow_afternoon": (1, 12, 18),
    "tomorrow_night": (1, 18, 24),
}
_ROLLING = {"next_24h": 24, "next_48h": 48, "next_72h": 72}


@dataclass(frozen=True, slots=True)
class TimeWindow:
    symbol: str
    start: datetime          # UTC, inclusive
    end: datetime            # UTC, exclusive
    label: str               # human phrasing, IST

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0


class UnknownTimeSymbol(ValueError):
    def __init__(self, symbol: str) -> None:
        super().__init__(
            f"Unknown time expression {symbol!r}. Known: {', '.join(TIME_SYMBOLS)}"
        )


def resolve(symbol: str, now: datetime | None = None) -> TimeWindow:
    """Symbol -> absolute UTC window. `now` is injectable so this is testable."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sym = (symbol or "now").strip().lower()

    if sym == "now":
        return TimeWindow("now", now, now + timedelta(hours=1),
                          f"now ({now.astimezone(IST):%H:%M} IST)")

    if sym in _ROLLING:
        h = _ROLLING[sym]
        return TimeWindow(sym, now, now + timedelta(hours=h), f"the next {h} hours")

    if sym in _LOCAL_WINDOWS:
        day, h0, h1 = _LOCAL_WINDOWS[sym]
        local = now.astimezone(IST)
        base = (local + timedelta(days=day)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start_local, end_local = base + timedelta(hours=h0), base + timedelta(hours=h1)
        return TimeWindow(
            sym,
            start_local.astimezone(timezone.utc),
            end_local.astimezone(timezone.utc),
            f"{sym.replace('_', ' ')} "
            f"({start_local:%a %d %b %H:%M}–{end_local:%H:%M} IST)",
        )

    raise UnknownTimeSymbol(sym)
