"""Ingestion: pull data from Garmin, normalize, and store — with a sync health log.

Monitoring (§2A): each cycle writes to sync_log ('ok'/'failed'/'suspicious').
After N consecutive failures, it creates an inbox warning (escalation).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from coach import garmin
from coach.db import get_connection

ESCALATION_THRESHOLD = 3  # consecutive failures before warning the user


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_utc_iso(gmt_str: str | None) -> str | None:
    """Garmin startTimeGMT comes as 'YYYY-MM-DD HH:MM:SS' (GMT) -> ISO UTC."""
    if not gmt_str:
        return None
    try:
        dt = datetime.strptime(gmt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return gmt_str  # unexpected format: keep as received


# Thresholds to reclassify a clearly slow, low-HR "running" activity as a walk
# (Garmin sometimes labels walks/hikes as running).
_WALK_MAX_SPEED = 1.67  # m/s (~10:00/km)
_WALK_MAX_HR = 115


def _effective_type(
    activity_type: str | None,
    avg_speed: float | None,
    distance_m: float | None,
    duration_s: float | None,
    avg_hr: int | None,
) -> str | None:
    """Reclassify a clearly slow, low-HR 'running' activity as 'walking'."""
    if activity_type != "running":
        return activity_type
    speed = avg_speed
    if not speed and distance_m and duration_s:
        speed = distance_m / duration_s
    if speed is not None and speed < _WALK_MAX_SPEED and (avg_hr is None or avg_hr < _WALK_MAX_HR):
        return "walking"
    return activity_type


def _normalize_activity(a: dict[str, Any]) -> dict[str, Any]:
    """Map the raw Garmin summary to the activity table columns."""
    activity_type = _effective_type(
        (a.get("activityType") or {}).get("typeKey"),
        a.get("averageSpeed"),
        a.get("distance"),
        a.get("duration"),
        a.get("averageHR"),
    )
    return {
        "garmin_activity_id": a.get("activityId"),
        "start_time_utc": _to_utc_iso(a.get("startTimeGMT")),
        "start_time_local": a.get("startTimeLocal"),
        "type": activity_type,
        "distance_m": a.get("distance"),
        "duration_s": a.get("duration"),
        "moving_duration_s": a.get("movingDuration"),
        "avg_speed": a.get("averageSpeed"),
        "max_speed": a.get("maxSpeed"),
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "elevation_gain_m": a.get("elevationGain"),
        "elevation_loss_m": a.get("elevationLoss"),
        "aerobic_training_effect": a.get("aerobicTrainingEffect"),
        "anaerobic_training_effect": a.get("anaerobicTrainingEffect"),
        "training_load": a.get("activityTrainingLoad"),
        "raw_json": json.dumps(a, ensure_ascii=False),
    }


# Upsert by garmin_activity_id: updates the Garmin fields, but does NOT touch
# planned_workout_id (that's the matcher's job) or created_at.
_UPSERT_ACTIVITY = """
INSERT INTO activity (
    garmin_activity_id, start_time_utc, start_time_local, type, distance_m,
    duration_s, moving_duration_s, avg_speed, max_speed, avg_hr, max_hr,
    elevation_gain_m, elevation_loss_m, aerobic_training_effect,
    anaerobic_training_effect, training_load, raw_json, synced_at, created_at
) VALUES (
    :garmin_activity_id, :start_time_utc, :start_time_local, :type, :distance_m,
    :duration_s, :moving_duration_s, :avg_speed, :max_speed, :avg_hr, :max_hr,
    :elevation_gain_m, :elevation_loss_m, :aerobic_training_effect,
    :anaerobic_training_effect, :training_load, :raw_json, :now, :now
)
ON CONFLICT(garmin_activity_id) DO UPDATE SET
    start_time_utc            = excluded.start_time_utc,
    start_time_local          = excluded.start_time_local,
    type                      = excluded.type,
    distance_m                = excluded.distance_m,
    duration_s                = excluded.duration_s,
    moving_duration_s         = excluded.moving_duration_s,
    avg_speed                 = excluded.avg_speed,
    max_speed                 = excluded.max_speed,
    avg_hr                    = excluded.avg_hr,
    max_hr                    = excluded.max_hr,
    elevation_gain_m          = excluded.elevation_gain_m,
    elevation_loss_m          = excluded.elevation_loss_m,
    aerobic_training_effect   = excluded.aerobic_training_effect,
    anaerobic_training_effect = excluded.anaerobic_training_effect,
    training_load             = excluded.training_load,
    raw_json                  = excluded.raw_json,
    synced_at                 = excluded.synced_at;
"""


def _sleep_seconds(sleep: dict[str, Any]) -> int | None:
    """Total sleep duration in seconds, or None (e.g. slept without the watch).

    The FR245 doesn't expose a numeric sleep score via this API, so we use
    duration as the sleep signal.
    """
    dto = sleep.get("dailySleepDTO") or {}
    secs = dto.get("sleepTimeSeconds")
    return int(secs) if isinstance(secs, (int, float)) and secs > 0 else None


def _extract_wellness(
    day: str, summary: dict[str, Any], sleep: dict[str, Any]
) -> dict[str, Any]:
    """Map the Garmin daily user summary + sleep to daily_wellness columns."""
    return {
        "date": day,
        "resting_hr": summary.get("restingHeartRate"),
        "sleep_score": None,  # not available on FR245 via this API — we use sleep_seconds
        "sleep_seconds": _sleep_seconds(sleep),
        "avg_stress": summary.get("averageStressLevel"),
        "max_stress": summary.get("maxStressLevel"),
        "body_battery_high": summary.get("bodyBatteryHighestValue"),
        "body_battery_low": summary.get("bodyBatteryLowestValue"),
        "vo2max": None,  # VO2max lives in user_profile (current fitness), not per-day
        "raw_json": json.dumps(summary, ensure_ascii=False),
    }


_UPSERT_WELLNESS = """
INSERT INTO daily_wellness (
    date, resting_hr, sleep_score, sleep_seconds, avg_stress, max_stress,
    body_battery_high, body_battery_low, vo2max, raw_json, synced_at
) VALUES (
    :date, :resting_hr, :sleep_score, :sleep_seconds, :avg_stress, :max_stress,
    :body_battery_high, :body_battery_low, :vo2max, :raw_json, :now
)
ON CONFLICT(date) DO UPDATE SET
    resting_hr        = excluded.resting_hr,
    sleep_score       = excluded.sleep_score,
    sleep_seconds     = excluded.sleep_seconds,
    avg_stress        = excluded.avg_stress,
    max_stress        = excluded.max_stress,
    body_battery_high = excluded.body_battery_high,
    body_battery_low  = excluded.body_battery_low,
    vo2max            = excluded.vo2max,
    raw_json          = excluded.raw_json,
    synced_at         = excluded.synced_at;
"""

# VO2max is current fitness: ensure the profile row exists and refresh it.
_UPSERT_PROFILE_VO2 = """
INSERT INTO user_profile (id, vo2max, fitness_age, created_at, updated_at)
VALUES (1, :vo2max, :fitness_age, :now, :now)
ON CONFLICT(id) DO UPDATE SET
    vo2max      = excluded.vo2max,
    fitness_age = excluded.fitness_age,
    updated_at  = excluded.updated_at;
"""


def _log_sync(
    conn: sqlite3.Connection, status: str, detail: str, count: int | None
) -> None:
    conn.execute(
        "INSERT INTO sync_log (ran_at, status, source, detail, activities_fetched)"
        " VALUES (?, ?, 'garmin_sync', ?, ?)",
        (_utcnow(), status, detail, count),
    )
    conn.commit()


def _maybe_escalate(conn: sqlite3.Connection, threshold: int = ESCALATION_THRESHOLD) -> None:
    """If the last N attempts failed, create a warning (without duplicating)."""
    rows = conn.execute(
        "SELECT status FROM sync_log ORDER BY id DESC LIMIT ?", (threshold,)
    ).fetchall()
    if len(rows) < threshold or any(r["status"] == "ok" for r in rows):
        return
    already = conn.execute(
        "SELECT 1 FROM inbox_message WHERE type='sync_warning' AND status='unread' LIMIT 1"
    ).fetchone()
    if already:
        return
    conn.execute(
        "INSERT INTO inbox_message (type, title, body, status, created_at)"
        " VALUES ('sync_warning', ?, ?, 'unread', ?)",
        (
            "Can't reach Garmin",
            f"The sync failed {threshold} times in a row. Can you check the login/connection?",
            _utcnow(),
        ),
    )
    conn.commit()


def sync(days: int = 120, wellness_days: int = 60) -> dict[str, Any]:
    """Run one sync cycle (activities + daily wellness). Return a result summary."""
    conn = get_connection()
    try:
        try:
            client = garmin.connect()
            now = _utcnow()

            activities = garmin.fetch_activities(client, days=days)
            processed = 0
            for raw in activities:
                if not raw.get("activityId"):
                    continue
                row = _normalize_activity(raw)
                row["now"] = now
                conn.execute(_UPSERT_ACTIVITY, row)
                processed += 1

            wellness = garmin.fetch_wellness(client, days=wellness_days)
            w_processed = 0
            for day, summary, sleep in wellness:
                if not summary and not sleep:
                    continue
                w = _extract_wellness(day, summary, sleep)
                w["now"] = now
                conn.execute(_UPSERT_WELLNESS, w)
                w_processed += 1

            vo2max, fitness_age = garmin.fetch_current_vo2max(client)
            if vo2max is not None:
                conn.execute(
                    _UPSERT_PROFILE_VO2,
                    {"vo2max": vo2max, "fitness_age": fitness_age, "now": now},
                )
            conn.commit()

            if not activities:
                # connected but nothing returned: suspicious (not an error, but not normal)
                _log_sync(conn, "suspicious", "login ok, 0 activities returned", w_processed)
                result = {"status": "suspicious", "activities": 0, "wellness_days": w_processed}
            else:
                detail = f"{processed} activities, {w_processed} wellness days"
                _log_sync(conn, "ok", detail, len(activities))
                result = {"status": "ok", "activities": len(activities),
                          "wellness_days": w_processed}
        except Exception as exc:
            conn.rollback()
            _log_sync(conn, "failed", f"{type(exc).__name__}: {exc}", None)
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

        _maybe_escalate(conn)
        return result
    finally:
        conn.close()
