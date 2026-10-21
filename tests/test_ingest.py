"""Cleaning Garmin data on the way in."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from coach import ingest


def test_slow_low_hr_run_is_reclassified_as_a_walk() -> None:
    # real case: 19:40/km at HR 83, recorded as "running"
    assert ingest._effective_type("running", 0.85, 8674, 10200, 83) == "walking"


def test_easy_run_with_low_hr_stays_a_run() -> None:
    # real case (2026-09-01): 7:02/km at HR 109 — low HR alone is not enough
    assert ingest._effective_type("running", 2.367, 4003, 1691, 109) == "running"


def test_zero_sleep_means_no_watch_not_zero_hours() -> None:
    assert ingest._sleep_seconds({"dailySleepDTO": {"sleepTimeSeconds": 0}}) is None
    assert ingest._sleep_seconds({}) is None
    assert ingest._sleep_seconds({"dailySleepDTO": {"sleepTimeSeconds": 28800}}) == 28800


def test_gmt_start_time_becomes_iso_utc() -> None:
    assert ingest._to_utc_iso("2026-09-01 19:02:03") == "2026-09-01T19:02:03+00:00"


def test_resyncing_an_activity_updates_it_instead_of_duplicating(
        db: sqlite3.Connection, now: str) -> None:
    raw = {"activityId": 1001, "startTimeGMT": "2026-09-01 19:02:03",
           "startTimeLocal": "2026-09-01 21:02:03", "activityType": {"typeKey": "running"},
           "distance": 4003.3, "duration": 1691.3, "averageSpeed": 2.367, "averageHR": 109}
    for distance in (4003.3, 4100.0):  # the second sync brings an edited distance
        db.execute(ingest._UPSERT_ACTIVITY,
                   {**ingest._normalize_activity({**raw, "distance": distance}), "now": now})
    rows = db.execute("SELECT distance_m FROM activity").fetchall()
    assert [r["distance_m"] for r in rows] == [4100.0]


def test_wellness_sync_only_refetches_recent_days(db: sqlite3.Connection, now: str) -> None:
    assert ingest._wellness_span(db, max_days=60) == 60  # empty DB: full window
    last = (date.today() - timedelta(days=5)).isoformat()
    db.execute("INSERT INTO daily_wellness (date, synced_at) VALUES (?, ?)", (last, now))
    assert ingest._wellness_span(db, max_days=60) == 8   # 5-day gap + 2 overlap + today
