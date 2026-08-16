"""Decides what is due right now.

Two failure modes shape this module.

Cron is late — GitHub's scheduler routinely fires many minutes after the hour
and sometimes skips an hour entirely — so nothing matches on the current hour.
Instead, anything scheduled within the last CATCHUP_WINDOW_HOURS that has not
already posted is still due.

And two runs can overlap, so every entry gets a stable key that does not depend
on when the run happened. Both runs compute the same key, and the second finds
it already in the execution log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

from .. import config as cfg
from .. import posting_plan
from ..state import ExecutionLog

WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass(frozen=True)
class DueEntry:
    key: str
    post_type: str
    scheduled_for: datetime
    source: str  # "plan" or "recurring"

    @property
    def minutes_late(self) -> float:
        return 0.0


def _zone(timezone_name: str):
    from zoneinfo import ZoneInfo

    return ZoneInfo(timezone_name)


def make_key(scheduled: datetime, post_type: str) -> str:
    """Stable across runs, machines, and retries.

    Deliberately excludes the run time: two concurrent runs must derive the
    same key from the same calendar entry or dedupe cannot work.
    """
    return "{}|{}|{}".format(
        scheduled.strftime("%Y-%m-%d"), scheduled.strftime("%H:%M"), post_type
    )


def _parse_entry(
    entry: Dict[str, str], on_date, tz
) -> Optional[datetime]:
    try:
        hour, minute = (int(part) for part in entry["time"].split(":"))
    except (KeyError, ValueError):
        return None
    return datetime(
        on_date.year, on_date.month, on_date.day, hour, minute, tzinfo=tz
    )


def candidates_for_date(on_date, tz, plan, recurring) -> List[DueEntry]:
    """Dated entries for this date, falling back to the recurring floor.

    The floor only applies when the dated calendar has nothing for the date,
    so a hand-planned day is never doubled up by the recurring rule.
    """
    iso = on_date.isoformat()
    dated = [entry for entry in plan if entry.get("date") == iso]

    found: List[DueEntry] = []
    if dated:
        for entry in dated:
            scheduled = _parse_entry(entry, on_date, tz)
            if scheduled is None:
                continue
            found.append(
                DueEntry(
                    key=make_key(scheduled, entry["type"]),
                    post_type=entry["type"],
                    scheduled_for=scheduled,
                    source="plan",
                )
            )
        return found

    weekday = WEEKDAY_NAMES[on_date.weekday()]
    for entry in recurring:
        if entry.get("weekday", "").lower() != weekday:
            continue
        scheduled = _parse_entry(entry, on_date, tz)
        if scheduled is None:
            continue
        found.append(
            DueEntry(
                key=make_key(scheduled, entry["type"]),
                post_type=entry["type"],
                scheduled_for=scheduled,
                source="recurring",
            )
        )
    return found


def due_now(
    now: Optional[datetime] = None,
    timezone_name: Optional[str] = None,
    window_hours: int = cfg.CATCHUP_WINDOW_HOURS,
    log: Optional[ExecutionLog] = None,
    plan: Optional[Sequence[Dict[str, str]]] = None,
    recurring: Optional[Sequence[Dict[str, str]]] = None,
) -> List[DueEntry]:
    timezone_name = timezone_name or cfg.get_config().timezone
    tz = _zone(timezone_name)
    now = now.astimezone(tz) if now else datetime.now(tz)

    plan = posting_plan.PLAN if plan is None else plan
    recurring = posting_plan.RECURRING if recurring is None else recurring
    already = log.keys() if log is not None else set()

    window_start = now - timedelta(hours=window_hours)

    # The window can reach back across midnight, so yesterday is in scope too.
    dates = {window_start.date(), now.date()}

    due: List[DueEntry] = []
    for on_date in sorted(dates):
        for candidate in candidates_for_date(on_date, tz, plan, recurring):
            if candidate.scheduled_for > now:
                continue
            if candidate.scheduled_for < window_start:
                continue
            if candidate.key in already:
                continue
            due.append(candidate)

    due.sort(key=lambda item: item.scheduled_for)
    return due
