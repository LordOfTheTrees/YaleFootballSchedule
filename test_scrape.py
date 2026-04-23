"""
Local scrape test runner.

Examples:
  python test_scrape.py
  python test_scrape.py --season 2026
  python test_scrape.py --source yale
  python test_scrape.py --source espn --season 2025
  python test_scrape.py --write-calendar
"""
from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Yale football schedule scraping locally")
    parser.add_argument(
        "--season",
        type=int,
        default=None,
        help="Season year (default: same as Script.get_current_season())",
    )
    parser.add_argument(
        "--source",
        choices=["all", "yale", "espn"],
        default="all",
        help="all = Yale then ESPN with validation (same as scrape_schedule); yale/espn = single source",
    )
    parser.add_argument(
        "--write-calendar",
        action="store_true",
        help="If scraping succeeds, write yale_football.ics",
    )
    args = parser.parse_args()

    from Script import (
        create_calendar,
        get_current_season,
        scrape_espn_schedule,
        scrape_schedule,
        scrape_yale_schedule,
        validate_schedule,
    )

    season = args.season if args.season is not None else get_current_season()
    print(f"Season: {season}  source: {args.source}\n")

    if args.source == "all":
        games = scrape_schedule(season)
    elif args.source == "yale":
        games = scrape_yale_schedule(season)
    else:
        games = scrape_espn_schedule(season)

    if games is None:
        print("Result: no data available (treated as schedule not published)")
        sys.exit(0)
    if not games:
        print("Result: no games returned (failure or blocked)")
        sys.exit(1)

    print(f"Games returned: {len(games)}")
    ok = validate_schedule(games, season)
    print(f"validate_schedule: {'ok' if ok else 'failed'}\n")

    for g in sorted(games, key=lambda x: x["start"]):
        print(f"  {g['start'].strftime('%Y-%m-%d %H:%M')}  {g['title']}")

    if args.write_calendar:
        create_calendar(games)
        print("\nWrote yale_football.ics")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
