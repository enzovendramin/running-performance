"""CLI to run one Garmin sync cycle and show what was ingested.

Usage (from the project root):
    python -m coach.sync
"""

from __future__ import annotations

from coach.db import get_connection, run_migrations
from coach.ingest import sync


def main() -> None:
    # make sure the schema is applied (idempotent)
    conn = get_connection()
    run_migrations(conn)
    conn.close()

    print("Sync result:", sync())

    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) AS n FROM activity").fetchone()["n"]
    recent = conn.execute(
        "SELECT start_time_local, type, distance_m, duration_s, avg_hr,"
        " aerobic_training_effect, training_load"
        " FROM activity ORDER BY start_time_utc DESC LIMIT 5"
    ).fetchall()
    wellness_total = conn.execute("SELECT COUNT(*) AS n FROM daily_wellness").fetchone()["n"]
    wellness_recent = conn.execute(
        "SELECT date, resting_hr, sleep_seconds, avg_stress, max_stress,"
        " body_battery_high, body_battery_low"
        " FROM daily_wellness ORDER BY date DESC LIMIT 3"
    ).fetchall()
    profile = conn.execute(
        "SELECT vo2max, fitness_age FROM user_profile WHERE id = 1"
    ).fetchone()
    conn.close()

    print(f"Total activities in the database: {total}")
    print("5 most recent:")
    for row in recent:
        print("  ", dict(row))
    print(f"\nTotal wellness days in the database: {wellness_total}")
    print("3 most recent:")
    for row in wellness_recent:
        print("  ", dict(row))
    print(f"\nProfile (current fitness): {dict(profile) if profile else None}")


if __name__ == "__main__":
    main()
