"""Verify the alert engine fires on a fabricated overload (Phase 2 gate).

Builds an IN-MEMORY database with a synthetic overload (spiked week, high
acute:chronic ratio, deep fatigue) plus poor recovery signals, then runs the
deterministic engine and prints the alerts — without touching the real database.

Usage (from the project root):
    python -m coach.verify_alerts
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from coach import alerts
from coach.db import get_connection, run_migrations


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    conn = get_connection(":memory:")
    run_migrations(conn)
    now = _now()
    today = date.today()

    # Synthetic load: prev week ~8/day (=56), this week ~16/day (=112) → big jump.
    # Latest day set to a high acute:chronic ratio and deep negative form.
    for i in range(0, 14):
        d = (today - timedelta(days=i)).isoformat()
        daily_load = 16.0 if i <= 6 else 8.0
        if i == 0:
            ctl, atl, tsb = 30.0, 50.0, -28.0  # ratio 1.67, deep TSB
        else:
            ctl, atl, tsb = 30.0, 40.0, -10.0
        conn.execute(
            "INSERT INTO daily_load (date, daily_load, ctl, atl, tsb, computed_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (d, daily_load, ctl, atl, tsb, now),
        )

    # Poor recovery for the last 3 days; older baseline is lower so recent RHR reads elevated.
    for i in range(0, 3):
        conn.execute(
            "INSERT INTO daily_wellness (date, resting_hr, body_battery_high,"
            " sleep_seconds, synced_at) VALUES (?, 58, 20, 18000, ?)",
            ((today - timedelta(days=i)).isoformat(), now),
        )
    for i in range(3, 20):
        conn.execute(
            "INSERT INTO daily_wellness (date, resting_hr, synced_at) VALUES (?, 46, ?)",
            ((today - timedelta(days=i)).isoformat(), now),
        )
    conn.commit()

    print(f"Recovery flags: {alerts.recovery_flags(conn)}")
    fired = alerts.evaluate(conn, today=today)
    print(f"\n{len(fired)} alert(s) fired on the synthetic overload:")
    for a in fired:
        print(f"  [{a['severity']}] {a['title']}\n      {a['message']}")

    assert any(a["rule"] == "acwr_high" for a in fired), "expected acwr_high"
    assert any(a["rule"] == "week_jump" for a in fired), "expected week_jump"
    assert any(a["rule"] == "tsb_deep" for a in fired), "expected tsb_deep"
    print("\nOK: all three load rules fired (in-memory — nothing persisted).")

    conn.close()


if __name__ == "__main__":
    main()
