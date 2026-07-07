"""Deterministic risk-alert engine.

Fixed rules evaluate the load series (CTL/ATL/TSB) and recent wellness and produce
alerts. The fixed rules alone decide whether an alert exists.

Conservative floors keep the ratio rules quiet during the return phase (very low
base), where a single run would otherwise look like a huge spike. All thresholds
are starting points and tunable.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

from coach.db import get_connection
from coach.rules import (  # single source of truth for thresholds
    ACWR_HIGH,
    ACWR_MIN_CTL,
    BODY_BATTERY_LOW,
    RHR_ELEVATED_DELTA,
    SLEEP_SHORT_S,
    TSB_DEEP,
    WEEK_JUMP_MIN_LOAD,
    WEEK_JUMP_RATIO,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _week_load(conn: sqlite3.Connection, start_iso: str, end_iso: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(daily_load), 0) AS s FROM daily_load"
        " WHERE date >= ? AND date <= ?",
        (start_iso, end_iso),
    ).fetchone()
    return float(row["s"] or 0.0)


def _latest_load(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT date, ctl, atl, tsb FROM daily_load ORDER BY date DESC LIMIT 1"
    ).fetchone()


def recovery_flags(conn: sqlite3.Connection) -> list[str]:
    """Recent poor-recovery signals (last 3 days) vs the 30-day baseline."""
    flags: list[str] = []
    recent = conn.execute(
        "SELECT resting_hr, body_battery_high, sleep_seconds FROM daily_wellness"
        " ORDER BY date DESC LIMIT 3"
    ).fetchall()
    base = conn.execute(
        "SELECT AVG(resting_hr) AS rhr FROM daily_wellness"
        " WHERE resting_hr IS NOT NULL AND date >= date('now','-30 days')"
    ).fetchone()
    base_rhr = base["rhr"] if base and base["rhr"] else None
    for r in recent:
        if base_rhr and r["resting_hr"] and r["resting_hr"] >= base_rhr + RHR_ELEVATED_DELTA:
            flags.append("resting HR elevated")
        if r["body_battery_high"] is not None and r["body_battery_high"] <= BODY_BATTERY_LOW:
            flags.append("Body Battery low")
        if r["sleep_seconds"] is not None and r["sleep_seconds"] < SLEEP_SHORT_S:
            flags.append("short sleep")
    return sorted(set(flags))


def evaluate(conn: sqlite3.Connection, today: date | None = None) -> list[dict[str, Any]]:
    """Return a list of alerts (possibly empty). Deterministic and rule-based."""
    today = today or date.today()
    alerts: list[dict[str, Any]] = []
    latest = _latest_load(conn)
    if latest is None:
        return alerts

    this_week = _week_load(conn, (today - timedelta(days=6)).isoformat(), today.isoformat())
    prev_week = _week_load(conn, (today - timedelta(days=13)).isoformat(),
                           (today - timedelta(days=7)).isoformat())
    ctl, atl, tsb = latest["ctl"], latest["atl"], latest["tsb"]
    recovery = recovery_flags(conn)
    suffix = f" Recovery signals: {', '.join(recovery)}." if recovery else ""
    sev = "high" if recovery else "warning"

    # Rule 1: acute:chronic ratio (only above the base floor).
    if ctl >= ACWR_MIN_CTL and ctl > 0 and atl / ctl >= ACWR_HIGH:
        ratio = atl / ctl
        alerts.append({
            "rule": "acwr_high", "severity": sev,
            "title": "Acute load well above your base",
            "message": (f"Fatigue (ATL {atl:.0f}) is {ratio:.2f}x your fitness "
                        f"(CTL {ctl:.0f}) — above the {ACWR_HIGH:.1f} injury-risk "
                        f"threshold.{suffix}"),
        })

    # Rule 2: weekly load jump (works at low base thanks to the minimum floor).
    if (prev_week >= WEEK_JUMP_MIN_LOAD and this_week >= WEEK_JUMP_MIN_LOAD
            and this_week >= prev_week * WEEK_JUMP_RATIO):
        jump = (this_week / prev_week - 1) * 100
        alerts.append({
            "rule": "week_jump", "severity": sev,
            "title": "Big jump in weekly load",
            "message": (f"This week's load ({this_week:.0f}) is {jump:.0f}% over last "
                        f"week ({prev_week:.0f}).{suffix}"),
        })

    # Rule 3: deep fatigue / overreaching (form very negative).
    if tsb <= TSB_DEEP:
        alerts.append({
            "rule": "tsb_deep", "severity": sev,
            "title": "Accumulated fatigue",
            "message": (f"Form (TSB {tsb:.0f}) is deep in the hole — sustained fatigue "
                        f"beyond recovery.{suffix}"),
        })

    return alerts


def persist_alerts(conn: sqlite3.Connection, alerts: list[dict[str, Any]]) -> int:
    """Write new alerts to the inbox, skipping an already-unread one of the same title."""
    created = 0
    for a in alerts:
        exists = conn.execute(
            "SELECT 1 FROM inbox_message WHERE type='risk_alert' AND status='unread'"
            " AND title = ? LIMIT 1",
            (a["title"],),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO inbox_message (type, title, body, status, created_at)"
            " VALUES ('risk_alert', ?, ?, 'unread', ?)",
            (a["title"], a["message"], _utcnow()),
        )
        created += 1
    conn.commit()
    return created


def main() -> None:
    conn = get_connection()
    alerts = evaluate(conn)
    latest = _latest_load(conn)
    if latest:
        print(f"Current: CTL {latest['ctl']:.1f}, ATL {latest['atl']:.1f}, "
              f"TSB {latest['tsb']:.1f}. Recovery flags: {recovery_flags(conn) or 'none'}")
    if not alerts:
        print("No alerts — nothing above threshold (correct during a low-base rebuild).")
    else:
        created = persist_alerts(conn, alerts)
        print(f"{len(alerts)} alert(s), {created} new in the inbox:")
        for a in alerts:
            print(f"  [{a['severity']}] {a['title']}: {a['message']}")
    conn.close()


if __name__ == "__main__":
    main()
