"""Shared fixtures: every test gets its own throwaway database — coach.db is never touched."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from coach.db import get_connection, run_migrations, utcnow


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    """A fresh, fully migrated in-memory database."""
    conn = get_connection(":memory:")
    run_migrations(conn)
    yield conn
    conn.close()


@pytest.fixture
def now() -> str:
    return utcnow()
