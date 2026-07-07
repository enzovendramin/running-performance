"""CLI to apply migrations to the real database.

Usage (from the project root):
    python -m coach.migrate
"""

from __future__ import annotations

from coach.db import get_connection, get_db_path, run_migrations, table_names


def main() -> None:
    db_path = get_db_path()
    conn = get_connection(db_path)
    try:
        applied = run_migrations(conn)
        tables = table_names(conn)
    finally:
        conn.close()

    if applied:
        print(f"Migrations applied: {applied}")
    else:
        print("Database already up to date; nothing to apply.")
    print(f"Database: {db_path}")
    print(f"Tables ({len(tables)}): {tables}")


if __name__ == "__main__":
    main()
