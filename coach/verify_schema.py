"""Phase 1 verification (the PLAN_REVIEW gate).

Migrates an IN-MEMORY database, inserts sample records across the three layers
(including a 'proposed' weekly_plan and a 'proposed'/pending adjustment), reads
them back, and proves FKs and the pending state work — without touching the real
database.

Usage (from the project root):
    python -m coach.verify_schema
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from coach.db import get_connection, run_migrations, table_names


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    conn = get_connection(":memory:")
    print(f"Migrations applied (in memory): {run_migrations(conn)}")
    print(f"Tables ({len(table_names(conn))}): {table_names(conn)}")
    now = _now()

    # --- Layer 1: profile (no active goal = maintenance; no restriction = no injury) ---
    conn.execute(
        "INSERT INTO user_profile (id, name, garmin_user_id, created_at, updated_at)"
        " VALUES (1, ?, ?, ?, ?)",
        ("Enzo Vendramin", 111273632, now, now),
    )

    # --- Layer 2: weekly_plan PROPOSED (the gate), no goal (maintenance) ---
    plan_id = conn.execute(
        "INSERT INTO weekly_plan (goal_id, week_start_date, status, rationale,"
        " created_at, updated_at) VALUES (NULL, ?, 'proposed', ?, ?, ?)",
        ("2026-07-06", "Base rebuild: 3 easy runs, conservative volume.",
         now, now),
    ).lastrowid
    workout_id = conn.execute(
        "INSERT INTO planned_workout (weekly_plan_id, date, type, description,"
        " target_distance_m, target_pace_low_s_km, target_pace_high_s_km,"
        " created_at, updated_at) VALUES (?, ?, 'easy', ?, ?, ?, ?, ?, ?)",
        (plan_id, "2026-07-07", "Easy run, conversational pace",
         5000.0, 360.0, 390.0, now, now),
    ).lastrowid
    conn.execute(
        "INSERT INTO activity (garmin_activity_id, planned_workout_id, start_time_utc,"
        " start_time_local, type, distance_m, duration_s, avg_speed,"
        " aerobic_training_effect, anaerobic_training_effect, training_load,"
        " synced_at, created_at)"
        " VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)",
        (23498122069, workout_id, now, now, 6165.12, 2167.78, 2.84, 3.0, 0.5, 55.0,
         now, now),
    )

    # --- Layer 3: feedback + adjustment PROPOSED (pending, magnitude large) ---
    fb_id = conn.execute(
        "INSERT INTO feedback_event (activity_id, type, description, perceived_effort,"
        " reported_at, created_at)"
        " VALUES ((SELECT id FROM activity LIMIT 1), 'fatigue', ?, 6, ?, ?)",
        ("Legs felt heavy toward the end.", now, now),
    ).lastrowid
    conn.execute(
        "INSERT INTO adjustment (weekly_plan_id, trigger_type, trigger_ref_id,"
        " change_summary, reasoning, magnitude, status, created_at)"
        " VALUES (?, 'feedback', ?, ?, ?, 'large', 'proposed', ?)",
        (plan_id, fb_id, "Cut the week's long run by 2 km.",
         "Reported fatigue + rising acute load; conservative pullback on the return.", now),
    )

    # --- Numeric foundation ---
    conn.execute(
        "INSERT INTO daily_wellness (date, resting_hr, sleep_score, avg_stress,"
        " body_battery_high, body_battery_low, vo2max, synced_at)"
        " VALUES (?, 48, 82, 33, 95, 12, 61.0, ?)",
        ("2026-07-05", now),
    )
    conn.execute(
        "INSERT INTO daily_load (date, daily_load, ctl, atl, tsb, computed_at)"
        " VALUES (?, 55.0, 42.0, 48.0, -6.0, ?)",
        ("2026-07-05", now),
    )
    conn.commit()

    print("\n-- Proposed plan (pending approval) --")
    for row in conn.execute("SELECT id, week_start_date, status, rationale FROM weekly_plan"):
        print(dict(row))

    print("\n-- Pending adjustment with reasoning (long-term memory) --")
    for row in conn.execute(
        "SELECT id, magnitude, status, change_summary, reasoning FROM adjustment"
    ):
        print(dict(row))

    print("\n-- Activity linked to the planned workout (join) --")
    for row in conn.execute(
        "SELECT a.garmin_activity_id, a.distance_m, pw.type AS planned_type,"
        " pw.target_distance_m FROM activity a"
        " JOIN planned_workout pw ON pw.id = a.planned_workout_id"
    ):
        print(dict(row))

    print("\n-- Load foundation (CTL/ATL/TSB) --")
    for row in conn.execute("SELECT * FROM daily_load"):
        print(dict(row))

    # FK is enabled: a workout with a nonexistent plan must fail.
    print("\n-- FK enforced (should fail) --")
    try:
        conn.execute(
            "INSERT INTO planned_workout (weekly_plan_id, date, type, created_at,"
            " updated_at) VALUES (99999, '2026-07-08', 'easy', ?, ?)",
            (now, now),
        )
        conn.commit()
        print("  ERROR: FK did NOT block (unexpected)")
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        print(f"  OK: FK blocked as expected ({type(exc).__name__}: {exc})")

    conn.close()
    print("\nVerification complete (in-memory DB — nothing persisted).")


if __name__ == "__main__":
    main()
