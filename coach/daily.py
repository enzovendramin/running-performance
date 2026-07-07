"""Daily orchestration: the full update cycle in one command.

Runs: sync (Garmin) -> rebuild load (CTL/ATL/TSB) -> match activities to plans ->
evaluate + persist risk alerts. Idempotent; safe to run on a schedule.

Schedule it with the OS (keep it thin and OS-swappable):
  Windows Task Scheduler -> daily -> action (Program/script + arguments):
    <project>\\venv\\Scripts\\python.exe   -m coach.daily
  Raspberry Pi / Linux (later) -> cron:
    0 6 * * *  cd <project> && venv/bin/python -m coach.daily

Usage (from the project root):
    python -m coach.daily
"""

from __future__ import annotations

from typing import Any

from coach import alerts, matching
from coach.db import get_connection, run_migrations
from coach.ingest import sync
from coach.load import rebuild_load


def run_daily() -> dict[str, Any]:
    """Run one full update cycle and return a summary of each stage."""
    conn = get_connection()
    run_migrations(conn)
    conn.close()

    sync_result = sync()  # opens/closes its own connection

    conn = get_connection()
    try:
        load_summary = rebuild_load(conn)
        match_summary = matching.apply_matching(conn)
        fired = alerts.evaluate(conn)
        alerts_new = alerts.persist_alerts(conn, fired)
    finally:
        conn.close()

    return {
        "sync": sync_result,
        "load": load_summary,
        "matching": match_summary,
        "alerts_fired": len(fired),
        "alerts_new": alerts_new,
    }


def main() -> None:
    print("Daily update complete:")
    for key, value in run_daily().items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
