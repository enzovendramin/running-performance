"""Schema and migration runner."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from coach.db import MIGRATIONS_DIR, get_connection, run_migrations, table_names

EXPECTED_TABLES = {
    "activity", "adjustment", "daily_load", "daily_wellness", "feedback_event", "goal",
    "inbox_message", "planned_workout", "race_prediction", "restriction",
    "schema_migrations", "sync_log", "user_profile", "weekly_plan",
}


def test_fresh_database_gets_every_table(db: sqlite3.Connection) -> None:
    assert set(table_names(db)) == EXPECTED_TABLES


def test_migrations_are_applied_only_once(db: sqlite3.Connection) -> None:
    assert run_migrations(db) == []


def test_foreign_keys_are_enforced(db: sqlite3.Connection, now: str) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO planned_workout (weekly_plan_id, date, type, created_at, updated_at)"
            " VALUES (99999, '2026-07-08', 'easy', ?, ?)", (now, now))


def test_failed_migration_rolls_back_completely(tmp_path: Path) -> None:
    for f in MIGRATIONS_DIR.glob("*.sql"):
        shutil.copy(f, tmp_path)
    (tmp_path / "005_broken.sql").write_text(
        "ALTER TABLE activity ADD COLUMN half_done REAL;\n"
        "ALTER TABLE no_such_table ADD COLUMN x REAL;\n", encoding="utf-8")
    conn = get_connection(":memory:")
    with pytest.raises(sqlite3.OperationalError):
        run_migrations(conn, tmp_path)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(activity)")}
    versions = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    assert "half_done" not in columns  # the statement before the error was undone
    assert 5 not in versions           # and the migration is not recorded as applied
