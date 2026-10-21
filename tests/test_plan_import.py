"""Importing and approving a chat-designed plan."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from coach import plan_import

GOOD = ('Here is your plan: {"week_start": "2026-10-26", "workouts": ['
        '{"date": "2026-10-27", "type": "Easy", "km": 5, "pace_min_per_km": "6:15",'
        ' "pace_max_per_km": "7:00"}, {"date": "2026-10-29", "type": "rest"}]} Good luck!')


def test_plan_is_found_inside_surrounding_text_and_saved_as_proposed(
        db: sqlite3.Connection) -> None:
    result = plan_import.import_plan(db, GOOD)
    assert result["ok"] and result["plan_id"]
    status = db.execute("SELECT status FROM weekly_plan").fetchone()[0]
    pace = db.execute("SELECT target_pace_low_s_km, target_pace_high_s_km FROM planned_workout"
                      " WHERE type='easy'").fetchone()
    assert status == "proposed"
    assert tuple(pace) == (375, 420)   # "6:15" -> 375 s/km


def test_unknown_workout_type_is_rejected_with_a_clear_message() -> None:
    with pytest.raises(ValueError, match="tipo 'corrida' inválido"):
        plan_import.parse_plan(
            '{"week_start": "2026-10-26", "workouts": [{"date": "2026-10-27", "type": "corrida"}]}')


def test_blocked_plan_is_never_saved(db: sqlite3.Connection) -> None:
    days = [f"2026-10-{d}" for d in range(26, 32)] + ["2026-11-01"]
    workouts = ",".join(f'{{"date": "{d}", "type": "easy", "km": 4}}' for d in days)
    result = plan_import.import_plan(db, f'{{"week_start": "2026-10-26", "workouts": [{workouts}]}}')
    assert not result["ok"]  # 7 running days: no rest day at all
    assert db.execute("SELECT COUNT(*) FROM weekly_plan").fetchone()[0] == 0


def test_only_a_pending_proposal_can_be_approved(db: sqlite3.Connection) -> None:
    pid = plan_import.import_plan(db, GOOD)["plan_id"]
    assert plan_import.approve_plan(db, pid) is True
    assert plan_import.approve_plan(db, pid) is False      # already active
    second = plan_import.import_plan(db, GOOD)["plan_id"]
    plan_import.approve_plan(db, second)                    # supersedes the first one
    assert plan_import.approve_plan(db, pid) is False       # a superseded plan stays retired


def test_baseline_spans_the_year_boundary_and_skips_the_current_week(
        db: sqlite3.Connection, now: str) -> None:
    # regression: weeks were keyed by number only (no year) and the unfinished week counted
    mondays = [date(2026, 12, 14), date(2026, 12, 21), date(2026, 12, 28), date(2027, 1, 4)]
    runs = [(m + timedelta(days=1), 10_000) for m in mondays] + [(date(2027, 1, 12), 50_000)]
    for i, (day, meters) in enumerate(runs, 1):
        db.execute(
            "INSERT INTO activity (garmin_activity_id, start_time_utc, start_time_local, type,"
            " distance_m, synced_at, created_at) VALUES (?, ?, ?, 'running', ?, ?, ?)",
            (i, now, f"{day.isoformat()} 08:00:00", meters, now, now))
    state = plan_import._state(db, today=date(2027, 1, 13))
    assert state["baseline_weekly_m"] == 10_000
