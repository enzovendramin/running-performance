"""Load engine: per-run effort (TRIMP) + the CTL/ATL/TSB series.

- Counts running only (walking/cycling are excluded from the training model).
- Banister TRIMP: weights intensity (HR) exponentially, so a hard run counts more
  than a long walk.
- CTL (fitness, 42-day average), ATL (fatigue, 7-day), TSB (form = CTL - ATL).
- The HR parameters are starting points, tunable (resting HR improves once
  wellness is synced).
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from coach.db import get_connection

DEFAULT_REST_HR = 48
DEFAULT_MAX_HR = 190
SEX = "M"  # Banister TRIMP coefficient

CTL_DAYS = 42
ATL_DAYS = 7


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def banister_trimp(
    duration_s: float | None, avg_hr: float | None, rest_hr: int, max_hr: int
) -> float:
    """Banister TRIMP: duration x HR reserve x exponential intensity weight."""
    if not duration_s or not avg_hr:
        return 0.0
    hrr = (avg_hr - rest_hr) / max(max_hr - rest_hr, 1)
    hrr = min(max(hrr, 0.0), 1.0)
    if hrr <= 0:
        return 0.0
    duration_min = duration_s / 60.0
    factor = 0.86 * math.exp(1.67 * hrr) if SEX == "F" else 0.64 * math.exp(1.92 * hrr)
    return duration_min * hrr * factor


def _local_date(start_time_local: str | None) -> str | None:
    return start_time_local[:10] if start_time_local else None


def rebuild_load(
    conn: sqlite3.Connection,
    default_rest_hr: int = DEFAULT_REST_HR,
    max_hr: int = DEFAULT_MAX_HR,
) -> dict[str, object]:
    """Recompute daily_load (daily TRIMP + CTL/ATL/TSB) from running activities.

    Uses each day's real resting HR from daily_wellness when available, falling
    back to default_rest_hr otherwise.
    """
    rest_by_date = {
        r["date"]: r["resting_hr"]
        for r in conn.execute(
            "SELECT date, resting_hr FROM daily_wellness WHERE resting_hr IS NOT NULL"
        )
    }
    rows = conn.execute(
        "SELECT start_time_local, duration_s, avg_hr, max_hr FROM activity"
        " WHERE type = 'running'"
    ).fetchall()
    if not rows:
        return {"days": 0, "note": "no runs"}

    # Raise max_hr to the highest observed value (a lower bound for the true max).
    observed_max = max((r["max_hr"] or 0) for r in rows)
    max_hr = max(max_hr, observed_max)

    # Sum TRIMP per local day, using that day's real resting HR when available.
    daily: dict[str, float] = defaultdict(float)
    for r in rows:
        day = _local_date(r["start_time_local"])
        if not day:
            continue
        rest_hr = rest_by_date.get(day, default_rest_hr)
        daily[day] += banister_trimp(r["duration_s"], r["avg_hr"], rest_hr, max_hr)

    # Iterate EVERY day in the range: rest days have load 0, but CTL/ATL keep
    # decaying (that's how fatigue drops and fitness bleeds off during rest).
    start = date.fromisoformat(min(daily))
    end = date.today()
    ctl = atl = 0.0
    now = _utcnow()
    out: list[tuple[str, float, float, float, float, str]] = []
    cur = start
    while cur <= end:
        load = daily.get(cur.isoformat(), 0.0)
        ctl += (load - ctl) / CTL_DAYS
        atl += (load - atl) / ATL_DAYS
        out.append(
            (cur.isoformat(), round(load, 1), round(ctl, 1), round(atl, 1),
             round(ctl - atl, 1), now)
        )
        cur += timedelta(days=1)

    conn.executemany(
        "INSERT INTO daily_load (date, daily_load, ctl, atl, tsb, computed_at)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(date) DO UPDATE SET daily_load=excluded.daily_load,"
        " ctl=excluded.ctl, atl=excluded.atl, tsb=excluded.tsb,"
        " computed_at=excluded.computed_at",
        out,
    )
    conn.commit()
    return {"days": len(out), "run_days": len(daily),
            "days_with_real_rest_hr": len(rest_by_date), "max_hr": max_hr}


def main() -> None:
    conn = get_connection()
    print("Load recomputed:", rebuild_load(conn))
    print("\nLast 10 days  (load | fitness CTL | fatigue ATL | form TSB):")
    for r in conn.execute(
        "SELECT * FROM daily_load ORDER BY date DESC LIMIT 10"
    ).fetchall():
        print(
            f"  {r['date']}  load={r['daily_load']:>5}  CTL={r['ctl']:>5}"
            f"  ATL={r['atl']:>5}  TSB={r['tsb']:>6}"
        )
    conn.close()


if __name__ == "__main__":
    main()
