"""
Offline parser tests - no network required.

The fixture mirrors the classic SIDEARM markup that yalebulldogs.com serves,
where the kickoff is a second <span> inside
.sidearm-schedule-game-opponent-date and no element carries a "time" class.
A selector-only lookup finds nothing there, which is how every 2026 game ended
up stamped with the 12:00 PM default.

Run: python test_parsers.py
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile

from bs4 import BeautifulSoup

import Script
from Script import (
    _TIME_RE,
    create_calendar,
    detect_schedule_structure,
    extract_game_data,
    parse_date_time,
    validate_schedule,
)

logging.disable(logging.CRITICAL)

# (date text, time text, opponent, venue text) - the real 2026 slate, with the
# kickoffs Yale had published and TBA for the rest.
_FIXTURE_GAMES = [
    ("Sep 19 (Sat)", "2:00 PM", "Holy Cross", "Worcester, Mass. / Fitton Field"),
    ("Sep 26 (Sat)", "", "Cornell", "Ithaca, N.Y. / Schoellkopf Field"),
    ("Oct 3 (Sat)", "12:00 PM", "Merrimack", "New Haven, Conn. / Yale Bowl"),
    ("Oct 10 (Sat)", "", "Dartmouth", "New Haven, Conn. / Yale Bowl"),
    ("Oct 17 (Sat)", "12:00 PM", "Rhode Island", "New Haven, Conn. / Yale Bowl"),
    ("Oct 23 (Fri)", "7:00 PM", "Pennsylvania", "Philadelphia, Pa. / Franklin Field"),
    ("Oct 31 (Sat)", "", "Columbia", "New York, N.Y. / Robert K. Kraft Field"),
    ("Nov 7 (Sat)", "", "Brown", "New Haven, Conn. / Yale Bowl"),
    ("Nov 14 (Sat)", "", "Princeton", "New Haven, Conn. / Yale Bowl"),
    ("Nov 21 (Sat)", "", "Harvard", "Boston, Mass. / Fenway Park"),
]

_HOME_VENUE = "New Haven, Conn. / Yale Bowl"


def _build_fixture() -> str:
    rows = []
    for date_text, time_text, opponent, venue in _FIXTURE_GAMES:
        # SIDEARM prints "TBA" in the time slot rather than omitting it.
        time_span = f"<span>{time_text or 'TBA'}</span>"
        tv = "ESPNU" if opponent == "Pennsylvania" else "TBA"
        rows.append(f"""
        <li class="sidearm-schedule-game sidearm-schedule-game-football">
          <div class="sidearm-schedule-game-row flex flex-align-center">
            <div class="sidearm-schedule-game-opponent-date flex-item-1">
              <span>{date_text}</span>
              {time_span}
            </div>
            <div class="sidearm-schedule-game-opponent-details flex-item-1">
              <div class="sidearm-schedule-game-opponent-name"><a href="#">{opponent}</a></div>
              <div class="sidearm-schedule-game-opponent-location">{venue}</div>
            </div>
            <div class="sidearm-schedule-game-coverage-links">
              <span class="sidearm-schedule-game-tv-network">{tv}</span>
            </div>
          </div>
        </li>""")
    return (
        "<html><body><ul class='sidearm-schedule-games'>"
        + "".join(rows)
        + "</ul></body></html>"
    )


def _parse_fixture():
    soup = BeautifulSoup(_build_fixture(), "html.parser")
    container, selector = detect_schedule_structure(soup)
    elements = container.select(selector) if container else []
    return [g for g in (extract_game_data(e) for e in elements) if g]


def _as_games(parsed, season=2026):
    """Turn extracted rows into the dicts the rest of the pipeline consumes."""
    import datetime

    games = []
    for row in parsed:
        start = parse_date_time(row["date_str"], row["time_str"], season)
        if not start:
            continue
        is_home = row["is_home"]
        games.append({
            "title": f"{row['opponent']} at Yale" if is_home else f"Yale at {row['opponent']}",
            "start": start,
            "end": start + datetime.timedelta(hours=3, minutes=30),
            "location": row["location"],
            "broadcast": row["broadcast"],
            "is_home": is_home,
            "opponent": row["opponent"],
            "date_str": row["date_str"],
            "time_str": row["time_str"],
            "time_known": row["time_known"],
        })
    return games


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    return bool(condition)


def test_time_regex() -> bool:
    print("Time regex accepts the formats schedule pages actually use:")
    ok = True
    for text, expected in [
        ("2:00 PM", "2:00 PM"),
        ("Sep 19 (Sat)2:00 PM", "2:00 PM"),
        ("12:00 p.m.", "12:00 p.m."),
        ("7:00PM", "7:00PM"),
    ]:
        match = _TIME_RE.search(text)
        ok &= check(f"{text!r}", bool(match) and match.group(1) == expected,
                    f"got {match.group(1) if match else None!r}")
    return ok


def test_kickoff_shares_the_date_node() -> bool:
    """The regression: kickoff and date live in one element, so no selector hits."""
    print("Kickoff parsed when it shares a node with the date (the 2026 bug):")
    card = """
    <li class="sidearm-schedule-game">
      <div class="sidearm-schedule-game-opponent-date">
        <span>Sep 19 (Sat)</span><span>2:00 PM</span>
      </div>
      <div class="sidearm-schedule-game-opponent-name">Holy Cross</div>
      <div class="sidearm-schedule-game-opponent-location">Worcester, Mass. / Fitton Field</div>
    </li>"""
    elem = BeautifulSoup(card, "html.parser").select_one(".sidearm-schedule-game")
    data = extract_game_data(elem)
    ok = check("time extracted", data is not None and data["time_str"] == "2:00 PM",
               repr(data["time_str"]) if data else "no data")
    ok &= check("time_known is True", bool(data and data["time_known"]))
    start = parse_date_time(data["date_str"], data["time_str"], 2026) if data else None
    ok &= check("kickoff is 2pm ET, not the noon default",
                start is not None and (start.hour, start.minute) == (14, 0), str(start))
    ok &= check("road game detected from the venue", data is not None and data["is_home"] is False,
                str(data["is_home"]) if data else "")
    return ok


def test_empty_element_does_not_clobber() -> bool:
    print("An empty time element does not discard a time found elsewhere:")
    card = """
    <li class="sidearm-schedule-game">
      <div class="sidearm-schedule-game-opponent-date"><span>Oct 23 (Fri)</span><span>7:00 PM</span></div>
      <div class="game-time"></div>
      <div class="sidearm-schedule-game-opponent-name">Pennsylvania</div>
      <div class="sidearm-schedule-game-opponent-location">Philadelphia, Pa. / Franklin Field</div>
    </li>"""
    elem = BeautifulSoup(card, "html.parser").select_one(".sidearm-schedule-game")
    data = extract_game_data(elem)
    start = parse_date_time(data["date_str"], data["time_str"], 2026) if data else None
    return check("7pm survives an empty .game-time",
                 start is not None and start.hour == 19, str(start))


def test_tba_is_flagged_not_invented() -> bool:
    print("A TBA kickoff is flagged rather than passed off as noon:")
    card = """
    <li class="sidearm-schedule-game">
      <div class="sidearm-schedule-game-opponent-date"><span>Nov 7 (Sat)</span><span>TBA</span></div>
      <div class="sidearm-schedule-game-opponent-name">Brown</div>
      <div class="sidearm-schedule-game-opponent-location">New Haven, Conn. / Yale Bowl</div>
    </li>"""
    elem = BeautifulSoup(card, "html.parser").select_one(".sidearm-schedule-game")
    data = extract_game_data(elem)
    ok = check("time_known is False", data is not None and data["time_known"] is False)
    ok &= check("time_str is empty", data is not None and data["time_str"] == "",
                repr(data["time_str"]) if data else "")
    start = parse_date_time(data["date_str"], data["time_str"], 2026) if data else None
    ok &= check("still placed at the noon placeholder",
                start is not None and start.hour == 12, str(start))
    return ok


def test_sidearm_fixture() -> bool:
    print("Full SIDEARM fixture (the real 2026 slate):")
    parsed = _parse_fixture()
    ok = check(f"extracted {len(parsed)} games", len(parsed) == 10)
    if not parsed:
        return False

    games = _as_games(parsed)
    ok &= check(f"{len(games)} games survive date parsing", len(games) == 10)

    by_opponent = {g["opponent"]: g for g in games}

    ok &= check("Holy Cross opener is 2:00 PM",
                by_opponent["Holy Cross"]["start"].hour == 14,
                str(by_opponent["Holy Cross"]["start"]))
    ok &= check("Holy Cross opener is a road game",
                by_opponent["Holy Cross"]["title"] == "Yale at Holy Cross",
                by_opponent["Holy Cross"]["title"])
    ok &= check("Penn Friday nighter is 7:00 PM",
                by_opponent["Pennsylvania"]["start"].hour == 19,
                str(by_opponent["Pennsylvania"]["start"]))
    ok &= check("Penn broadcast kept", by_opponent["Pennsylvania"]["broadcast"] == "ESPNU",
                by_opponent["Pennsylvania"]["broadcast"])
    ok &= check("TBA is not stored as a broadcaster",
                all(g["broadcast"].upper() != "TBA" for g in games))

    home = sorted(g["opponent"] for g in games if g["is_home"])
    away = sorted(g["opponent"] for g in games if not g["is_home"])
    ok &= check("5 home games", home == ["Brown", "Dartmouth", "Merrimack", "Princeton", "Rhode Island"],
                str(home))
    ok &= check("5 road games", away == ["Columbia", "Cornell", "Harvard", "Holy Cross", "Pennsylvania"],
                str(away))
    ok &= check("home games carry the Yale Bowl venue",
                all("Yale Bowl" in g["location"] for g in games if g["is_home"]))
    ok &= check("Harvard game is at Fenway",
                "Fenway" in by_opponent["Harvard"]["location"],
                by_opponent["Harvard"]["location"])

    timed = [g for g in games if g["time_known"]]
    ok &= check("4 published kickoffs, 6 TBA", len(timed) == 4, f"{len(timed)} timed")
    ok &= check("DST handled across the season",
                by_opponent["Holy Cross"]["start"].utcoffset().total_seconds() == -4 * 3600
                and by_opponent["Harvard"]["start"].utcoffset().total_seconds() == -5 * 3600)

    ok &= check("validate_schedule accepts the fixture", validate_schedule(games, 2026))
    return ok


def test_validation_catches_a_broken_time_parser() -> bool:
    print("Validation fails loudly when no kickoff could be read:")
    games = _as_games(_parse_fixture())
    for game in games:
        game["time_known"] = False
    ok = check("all-defaulted times rejected", validate_schedule(games, 2026) is False)

    games = _as_games(_parse_fixture())
    for game in games:
        game["is_home"] = True
        game["title"] = f"{game['opponent']} at Yale"
    ok &= check("all-home slate rejected", validate_schedule(games, 2026) is False)
    return ok


def test_uids_are_stable() -> bool:
    print("Event UIDs survive a re-scrape:")
    games = _as_games(_parse_fixture())
    original = Script.CALENDAR_FILE
    handle, path = tempfile.mkstemp(suffix=".ics")
    os.close(handle)
    try:
        Script.CALENDAR_FILE = path
        first = {e.name: e.uid for e in create_calendar(games).events}
        second = {e.name: e.uid for e in create_calendar(_as_games(_parse_fixture())).events}
    finally:
        Script.CALENDAR_FILE = original
        os.unlink(path)

    ok = check("10 UIDs minted", len(first) == 10, str(len(first)))
    ok &= check("identical across runs", first == second)
    ok &= check("UIDs are unique per game", len(set(first.values())) == 10)
    return ok


def main() -> None:
    results = [
        test_time_regex(),
        test_kickoff_shares_the_date_node(),
        test_empty_element_does_not_clobber(),
        test_tba_is_flagged_not_invented(),
        test_sidearm_fixture(),
        test_validation_catches_a_broken_time_parser(),
        test_uids_are_stable(),
    ]
    print()
    if all(results):
        print("All parser tests passed.")
        sys.exit(0)
    print("Parser tests FAILED.")
    sys.exit(1)


if __name__ == "__main__":
    main()
