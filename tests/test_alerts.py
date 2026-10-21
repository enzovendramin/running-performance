"""The alert engine (checks reality)."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from coach import alerts


def _overload(db: sqlite3.Connection, now: str, today: date) -> None:
    """Previous week ~8/day (=56), this week ~16/day (=112); latest day ratio 1.67, TSB -28."""
    for i in range(14):
        d = (today - timedelta(days=i)).isoformat()
        ctl, atl, tsb = (30.0, 50.0, -28.0) if i == 0 else (30.0, 40.0, -10.0)
        db.execute("INSERT INTO daily_load VALUES (?, ?, ?, ?, ?, ?)",
                   (d, 16.0 if i <= 6 else 8.0, ctl, atl, tsb, now))


def test_synthetic_overload_fires_all_three_load_rules(db: sqlite3.Connection, now: str) -> None:
    today = date.today()
    _overload(db, now, today)
    fired = {a["rule"] for a in alerts.evaluate(db, today=today)}
    assert fired == {"acwr_high", "week_jump", "tsb_deep"}


def test_poor_recovery_raises_severity(db: sqlite3.Connection, now: str) -> None:
    today = date.today()
    _overload(db, now, today)
    for i in range(3):   # last 3 nights: high resting HR, low battery, 5 h sleep
        db.execute("INSERT INTO daily_wellness (date, resting_hr, body_battery_high,"
                   " sleep_seconds, synced_at) VALUES (?, 58, 20, 18000, ?)",
                   ((today - timedelta(days=i)).isoformat(), now))
    for i in range(3, 20):
        db.execute("INSERT INTO daily_wellness (date, resting_hr, synced_at) VALUES (?, 46, ?)",
                   ((today - timedelta(days=i)).isoformat(), now))
    assert alerts.recovery_flags(db, today) == [
        "Body Battery low", "resting HR elevated", "short sleep"]
    fired = alerts.evaluate(db, today=today)
    assert {a["severity"] for a in fired} == {"high"}
    # the text is user-facing, so it is in Portuguese (the dashboard's language)
    assert all("Sinais de recuperação: Body Battery baixa" in a["message"] for a in fired)


def test_an_old_night_never_counts_as_recent(db: sqlite3.Connection, now: str) -> None:
    # regression: "last 3 days" used to mean "last 3 rows", whatever their date
    old = (date.today() - timedelta(days=40)).isoformat()
    db.execute("INSERT INTO daily_wellness (date, sleep_seconds, synced_at) VALUES (?, 14400, ?)",
               (old, now))
    assert alerts.recovery_flags(db) == []


def test_no_load_data_means_no_alerts(db: sqlite3.Connection) -> None:
    assert alerts.evaluate(db) == []
