"""Database access layer (SQLite) and a lightweight migration runner."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
DEFAULT_DB_PATH = PROJECT_ROOT / "coach.db"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db_path() -> Path:
    """DB path: env COACH_DB_PATH, or coach.db at the project root."""
    override = os.getenv("COACH_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Connection with foreign keys on, WAL (for file DBs), and rows keyed by name."""
    path = get_db_path() if db_path is None else Path(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  version INTEGER PRIMARY KEY,"
        "  applied_at TEXT NOT NULL"
        ")"
    )
    conn.commit()


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Migration versions already applied to this database."""
    _ensure_migrations_table(conn)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(row["version"]) for row in rows}


def _discover_migrations(migrations_dir: Path) -> list[tuple[int, Path]]:
    items: list[tuple[int, Path]] = []
    for path in migrations_dir.glob("*.sql"):
        head = path.name.split("_", 1)[0]
        try:
            items.append((int(head), path))
        except ValueError:
            continue  # ignore files not matching the NNN_name.sql pattern
    return sorted(items, key=lambda item: item[0])


def run_migrations(
    conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR
) -> list[int]:
    """Apply pending migrations in order. Return the versions applied now."""
    already = applied_versions(conn)
    newly: list[int] = []
    for version, path in _discover_migrations(migrations_dir):
        if version in already:
            continue
        conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, _utcnow()),
        )
        conn.commit()
        newly.append(version)
    return newly


def table_names(conn: sqlite3.Connection) -> list[str]:
    """User table names (excluding SQLite internal tables)."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]
