"""TRIMP and the CTL / ATL / TSB series."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from coach.load import ATL_DAYS, CTL_DAYS, banister_trimp, rebuild_load


def test_trimp_matches_the_stored_value_for_the_sept_1_run() -> None:
    # 28.2 min at avg HR 109, resting 40, max 190 -> 20.1 (the value in coach.db)
    assert round(banister_trimp(1691.256, 109, 40, 190), 1) == 20.1


def test_harder_run_counts_more_than_proportionally() -> None:
    easy = banister_trimp(1691.256, 109, 40, 190)
    hard = banister_trimp(1691.256, 160, 40, 190)
    assert hard > 3 * easy  # same 28 min, intensity weighs exponentially


def test_avg_hr_below_resting_gives_zero_load() -> None:
    assert banister_trimp(1800, 35, 40, 190) == 0.0


def _add_run(db: sqlite3.Connection, now: str, day: date, garmin_id: int) -> None:
    db.execute(
        "INSERT INTO activity (garmin_activity_id, start_time_utc, start_time_local, type,"
        " duration_s, avg_hr, max_hr, synced_at, created_at)"
        " VALUES (?, ?, ?, 'running', 1800, 150, 180, ?, ?)",
        (garmin_id, now, f"{day.isoformat()} 08:00:00", now, now))


def test_one_run_then_rest_days_decay_fatigue_faster_than_fitness(
        db: sqlite3.Connection, now: str) -> None:
    run_day = date.today() - timedelta(days=3)
    _add_run(db, now, run_day, 1)
    rebuild_load(db)
    rows = db.execute("SELECT daily_load, ctl, atl FROM daily_load ORDER BY date").fetchall()
    first, last = rows[0], rows[-1]
    load = first["daily_load"]
    assert round(first["ctl"], 1) == round(load / CTL_DAYS, 1)
    assert round(first["atl"], 1) == round(load / ATL_DAYS, 1)
    assert len(rows) == 4                                    # run day + 3 rest days
    assert last["atl"] / first["atl"] < last["ctl"] / first["ctl"]  # fatigue drops faster


def test_rebuild_drops_stale_days(db: sqlite3.Connection, now: str) -> None:
    db.execute("INSERT INTO daily_load VALUES ('2020-01-01', 99, 99, 99, 0, 'old')")
    _add_run(db, now, date.today(), 1)
    rebuild_load(db)
    dates = [r["date"] for r in db.execute("SELECT date FROM daily_load")]
    assert "2020-01-01" not in dates
