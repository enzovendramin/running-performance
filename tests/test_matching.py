"""Linking real runs to planned workouts."""

from __future__ import annotations

import itertools
import sqlite3
from datetime import date
from typing import Any

from coach import matching


def run(id_: int, day: str, dist: float | None = 5000.0) -> dict[str, Any]:
    return {"id": id_, "date": day, "type": "running", "distance_m": dist}


def plan(id_: int, day: str) -> dict[str, Any]:
    return {"id": id_, "date": day, "type": "running"}


def test_same_day_match() -> None:
    assert matching.compute_matches([plan(1, "2026-07-06")], [run(10, "2026-07-06")]) == {10: 1}


def test_next_day_matches_when_that_day_has_no_plan() -> None:
    assert matching.compute_matches([plan(1, "2026-07-06")], [run(10, "2026-07-07")]) == {10: 1}


def test_same_day_wins_over_a_neighbour() -> None:
    plans = [plan(1, "2026-07-06"), plan(2, "2026-07-07")]
    assert matching.compute_matches(plans, [run(10, "2026-07-07")]) == {10: 2}


def test_one_plan_takes_only_one_run() -> None:
    m = matching.compute_matches([plan(1, "2026-07-06")],
                                 [run(10, "2026-07-06"), run(11, "2026-07-06")])
    assert list(m.values()) == [1]


def test_run_without_a_plan_is_extra() -> None:
    assert matching.compute_matches([], [run(10, "2026-07-06")]) == {}


def test_a_walk_never_matches_a_run() -> None:
    walk = {"id": 10, "date": "2026-07-06", "type": "walking", "distance_m": 5000.0}
    assert matching.compute_matches([plan(1, "2026-07-06")], [walk]) == {}


def test_two_days_away_is_out_of_tolerance() -> None:
    assert matching.compute_matches([plan(1, "2026-07-06")], [run(10, "2026-07-08")]) == {}


class Week:
    """Builds plans, workouts and runs in the test database."""

    def __init__(self, db: sqlite3.Connection, now: str) -> None:
        self.db, self.now, self.ids = db, now, itertools.count(1)

    def plan(self, status: str, week: str) -> int:
        return self.db.execute(
            "INSERT INTO weekly_plan (week_start_date, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)", (week, status, self.now, self.now)).lastrowid

    def workout(self, plan_id: int, day: str, typ: str, km: float | None = None) -> int:
        return self.db.execute(
            "INSERT INTO planned_workout (weekly_plan_id, date, type, target_distance_m,"
            " status, created_at, updated_at) VALUES (?, ?, ?, ?, 'planned', ?, ?)",
            (plan_id, day, typ, km and km * 1000, self.now, self.now)).lastrowid

    def run(self, day: str, meters: float) -> int:
        return self.db.execute(
            "INSERT INTO activity (garmin_activity_id, start_time_utc, start_time_local, type,"
            " distance_m, synced_at, created_at) VALUES (?, ?, ?, 'running', ?, ?, ?)",
            (next(self.ids), self.now, f"{day} 08:00:00", meters, self.now, self.now)).lastrowid

    def statuses(self) -> dict[int, str]:
        return {r["id"]: r["status"]
                for r in self.db.execute("SELECT id, status FROM planned_workout")}


def test_statuses_completed_modified_skipped(db: sqlite3.Connection, now: str) -> None:
    wk = Week(db, now)
    p = wk.plan("active", "2026-06-29")
    done, modified, skipped = (wk.workout(p, d, "easy", 5)
                               for d in ("2026-06-30", "2026-07-01", "2026-07-02"))
    wk.run("2026-06-30", 5100)   # on target
    wk.run("2026-07-01", 9000)   # way over target
    wk.run("2026-07-05", 6000)   # no plan that day or the day before
    summary = matching.apply_matching(db, today=date(2026, 7, 7))
    st = wk.statuses()
    assert (st[done], st[modified], st[skipped]) == ("completed", "modified", "skipped")
    assert summary == {"linked": 2, "skipped": 1, "extra_runs": 1}


def test_superseded_plan_cannot_steal_runs(db: sqlite3.Connection, now: str) -> None:
    """Regression (real week of 2026-08-24): a superseded plan for the same week stole
    the runs — one even "completed" a rest day — leaving the active plan all skipped."""
    wk = Week(db, now)
    old, new = wk.plan("superseded", "2026-08-24"), wk.plan("active", "2026-08-24")
    wk.workout(old, "2026-08-25", "rest")
    easy = wk.workout(new, "2026-08-25", "easy", 5)
    rest = wk.workout(new, "2026-08-26", "rest")
    longrun = wk.workout(new, "2026-08-29", "long", 7)
    run_id = wk.run("2026-08-25", 5012)
    for _ in range(2):  # running it twice gives the same result
        matching.apply_matching(db, today=date(2026, 9, 1))
    st = wk.statuses()
    link = db.execute("SELECT planned_workout_id FROM activity WHERE id=?", (run_id,)).fetchone()[0]
    assert link == easy
    assert st[easy] == "completed" and st[longrun] == "skipped"
    assert st[rest] == "planned"  # rest days are not tracked
