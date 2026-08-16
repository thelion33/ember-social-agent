"""Regenerate the dated posting calendar from a cadence rule.

Extending the calendar is one command, not a hundred hand-edited lines:

    python tools/build_posting_plan.py --weeks 26
    python tools/build_posting_plan.py --weeks 52 --start 2027-01-01

The recurring floor in posting_plan.py is never touched by this script.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ember_social.posting_plan import RECURRING  # noqa: E402

PLAN_PATH = Path(__file__).resolve().parent.parent / "ember_social" / "posting_plan.py"
BEGIN = "# BEGIN GENERATED PLAN"
END = "# END GENERATED PLAN"

WEEKDAYS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def build(start: date, weeks: int, cadence: List[Dict[str, str]]) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    end = start + timedelta(weeks=weeks)
    for rule in cadence:
        weekday = WEEKDAYS[rule["weekday"].lower()]
        # First occurrence of this weekday on or after the start date.
        current = start + timedelta(days=(weekday - start.weekday()) % 7)
        while current < end:
            entries.append(
                {
                    "date": current.isoformat(),
                    "time": rule["time"],
                    "type": rule["type"],
                }
            )
            current += timedelta(days=7)
    entries.sort(key=lambda item: (item["date"], item["time"]))
    return entries


def render(entries: List[Dict[str, str]]) -> str:
    if not entries:
        return "PLAN: List[Dict[str, str]] = []"
    lines = ["PLAN: List[Dict[str, str]] = ["]
    for entry in entries:
        lines.append(
            '    {{"date": "{date}", "time": "{time}", "type": "{type}"}},'.format(
                **entry
            )
        )
    lines.append("]")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weeks", type=int, default=26, help="how far ahead to plan")
    parser.add_argument(
        "--start", default=None, help="ISO start date (default: today)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan without writing it"
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start) if args.start else date.today()
    entries = build(start, args.weeks, RECURRING)
    block = render(entries)

    if args.dry_run:
        print(block)
        return 0

    source = PLAN_PATH.read_text()
    if BEGIN not in source or END not in source:
        print("markers missing from {}".format(PLAN_PATH), file=sys.stderr)
        return 1

    head, rest = source.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    PLAN_PATH.write_text(
        "{head}{begin}\n{block}\n{end}{tail}".format(
            head=head, begin=BEGIN, block=block, end=END, tail=tail
        )
    )
    print(
        "wrote {} entries covering {} to {}".format(
            len(entries), entries[0]["date"], entries[-1]["date"]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
