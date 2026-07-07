"""Tests for the activity<->plan matcher (Step 1a).

Pure-function scenarios for compute_matches + an in-memory DB check for
apply_matching (statuses: completed / modified / skipped, and extra runs).

Usage (from the project root):
    python -m coach.verify_matching
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from coach import matching
from coach.db import get_connection, run_migrations


def _run(id_: int, day: str, dist: float | None = 5000.0) -> dict:
    return {"id": id_, "date": day, "type": "running", "distance_m": dist}


def _plan(id_: int, day: str) -> dict:
    return {"id": id_, "date": day, "type": "running"}


def test_compute_matches() -> None:
    # 1. same-day
    assert matching.compute_matches([_plan(1, "2026-07-06")], [_run(10, "2026-07-06")]) == {10: 1}
    # 2. +1 day when the activity's own day has no plan
    assert matching.compute_matches([_plan(1, "2026-07-06")], [_run(10, "2026-07-07")]) == {10: 1}
    # 3. same-day priority: activity on 07-07 takes plan 2, not neighbor plan 1
    assert matching.compute_matches(
        [_plan(1, "2026-07-06"), _plan(2, "2026-07-07")], [_run(10, "2026-07-07")]
    ) == {10: 2}
    # 4. one-to-one: two runs same day, one plan -> only one matches (other is extra)
    m = matching.compute_matches([_plan(1, "2026-07-06")],
                                 [_run(10, "2026-07-06"), _run(11, "2026-07-06")])
    assert list(m.values()) == [1] and len(m) == 1
    # 5. no plan -> extra (empty)
    assert matching.compute_matches([], [_run(10, "2026-07-06")]) == {}
    # 6. walking never matches a running plan
    assert matching.compute_matches(
        [_plan(1, "2026-07-06")],
        [{"id": 10, "date": "2026-07-06", "type": "walking", "distance_m": 5000.0}],
    ) == {}
    # 7. out of tolerance (2 days) -> no match
    assert matching.compute_matches([_plan(1, "2026-07-06")], [_run(10, "2026-07-08")]) == {}
    print("compute_matches: 7 scenarios OK")


def test_apply_matching() -> None:
    conn = get_connection(":memory:")
    run_migrations(conn)
    now = datetime.now(timezone.utc).isoformat()
    plan_id = conn.execute(
        "INSERT INTO weekly_plan (week_start_date, status, created_at, updated_at)"
        " VALUES ('2026-06-29', 'active', ?, ?)", (now, now)
    ).lastrowid

    def add_plan(day: str, target: float) -> int:
        return conn.execute(
            "INSERT INTO planned_workout (weekly_plan_id, date, type, target_distance_m,"
            " status, created_at, updated_at) VALUES (?, ?, 'easy', ?, 'planned', ?, ?)",
            (plan_id, day, target, now, now),
        ).lastrowid

    def add_activity(day: str, dist: float) -> int:
        return conn.execute(
            "INSERT INTO activity (garmin_activity_id, start_time_utc, start_time_local,"
            " type, distance_m, synced_at, created_at)"
            " VALUES (?, ?, ?, 'running', ?, ?, ?)",
            (hash(day + str(dist)) & 0x7fffffff, now, f"{day} 08:00:00", dist, now, now),
        ).lastrowid

    p_done = add_plan("2026-06-30", 5000.0)     # matched, similar distance -> completed
    p_mod = add_plan("2026-07-01", 5000.0)      # matched, far distance -> modified
    p_skip = add_plan("2026-07-02", 5000.0)     # no activity, past -> skipped
    add_activity("2026-06-30", 5100.0)          # ~ on target
    add_activity("2026-07-01", 9000.0)          # way over -> modified
    add_activity("2026-07-05", 6000.0)          # no plan -> extra
    conn.commit()

    summary = matching.apply_matching(conn, tolerance_days=1, today=date(2026, 7, 7))
    statuses = {
        r["id"]: r["status"]
        for r in conn.execute("SELECT id, status FROM planned_workout")
    }
    assert statuses[p_done] == "completed", statuses
    assert statuses[p_mod] == "modified", statuses
    assert statuses[p_skip] == "skipped", statuses
    assert summary["linked"] == 2 and summary["skipped"] == 1 and summary["extra_runs"] == 1, summary
    print(f"apply_matching: statuses + summary OK ({summary})")
    conn.close()


def main() -> None:
    test_compute_matches()
    test_apply_matching()
    print("\nAll matching tests passed.")


if __name__ == "__main__":
    main()
