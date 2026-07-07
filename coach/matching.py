"""Match real activities to planned workouts (§2F).

Rules (1-day tolerance, approved):
- Same-day match wins.
- A run may match a plan ±`tolerance_days` away ONLY if its own day has no plan.
- One-to-one: each plan matches at most one activity, and vice versa.
- Only a running activity matches a running plan.
- An unmatched run is "extra" (planned_workout_id stays NULL).
- A plan with no match, once its date + tolerance has passed, becomes `skipped`.
- A matched plan whose actual distance is far from target becomes `modified`, else
  `completed`.

Dormant until plans exist, but built and tested now.
The core (`compute_matches`) is a pure function for easy testing.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

MODIFIED_DISTANCE_RATIO = 0.4  # actual differs from target by more than this -> "modified"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_diff(a_iso: str, b_iso: str) -> int:
    return (date.fromisoformat(a_iso) - date.fromisoformat(b_iso)).days


def compute_matches(
    plans: list[dict[str, Any]],
    activities: list[dict[str, Any]],
    tolerance_days: int = 1,
) -> dict[int, int]:
    """Return {activity_id: plan_id} for matched running activities (pure)."""
    run_plans = [p for p in plans if p.get("type") == "running"]
    run_acts = sorted(
        (a for a in activities if a.get("type") == "running"), key=lambda a: a["date"]
    )
    plan_dates = {p["date"] for p in run_plans}
    matched_plan: dict[int, int] = {}   # plan_id -> activity_id
    matched_act: dict[int, int] = {}    # activity_id -> plan_id

    acts_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in run_acts:
        acts_by_date[a["date"]].append(a)
    plans_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in run_plans:
        plans_by_date[p["date"]].append(p)

    # Pass 1: same-day (highest priority).
    for d, dplans in plans_by_date.items():
        for p in dplans:
            avail = [a for a in acts_by_date.get(d, []) if a["id"] not in matched_act]
            if avail:
                matched_plan[p["id"]] = avail[0]["id"]
                matched_act[avail[0]["id"]] = p["id"]

    # Pass 2: ±tolerance, only for activities whose OWN day has no plan.
    for a in run_acts:
        if a["id"] in matched_act or a["date"] in plan_dates:
            continue
        best, best_dist = None, None
        for p in run_plans:
            if p["id"] in matched_plan:
                continue
            dist = abs(_date_diff(p["date"], a["date"]))
            if dist <= tolerance_days and (best_dist is None or dist < best_dist):
                best, best_dist = p, dist
        if best is not None:
            matched_plan[best["id"]] = a["id"]
            matched_act[a["id"]] = best["id"]

    return matched_act


def is_modified(target_distance_m: float | None, actual_distance_m: float | None) -> bool:
    """True if the actual distance is far enough from target to call it 'modified'."""
    if not target_distance_m or not actual_distance_m:
        return False
    return abs(actual_distance_m - target_distance_m) / target_distance_m > MODIFIED_DISTANCE_RATIO


def apply_matching(
    conn: sqlite3.Connection, tolerance_days: int = 1, today: date | None = None
) -> dict[str, int]:
    """Read plans + activities, link them, and update statuses. Returns a summary."""
    today = today or date.today()
    plans = [
        {"id": r["id"], "date": r["date"], "type": "running",
         "target_distance_m": r["target_distance_m"], "status": r["status"]}
        for r in conn.execute(
            "SELECT id, date, type, target_distance_m, status FROM planned_workout"
        ).fetchall()
    ]
    # planned_workout.type is a workout type ('easy'/'long'/...), all running; treat as running.
    acts = [
        {"id": r["id"], "date": (r["start_time_local"] or "")[:10], "type": r["type"],
         "distance_m": r["distance_m"]}
        for r in conn.execute(
            "SELECT id, start_time_local, type, distance_m FROM activity"
        ).fetchall()
    ]
    plan_by_id = {p["id"]: p for p in plans}
    act_by_id = {a["id"]: a for a in acts}

    matches = compute_matches(
        [{**p, "type": "running"} for p in plans], acts, tolerance_days
    )

    now = _utcnow()
    linked = 0
    for activity_id, plan_id in matches.items():
        conn.execute(
            "UPDATE activity SET planned_workout_id = ? WHERE id = ?", (plan_id, activity_id)
        )
        target = plan_by_id[plan_id]["target_distance_m"]
        actual = act_by_id[activity_id]["distance_m"]
        status = "modified" if is_modified(target, actual) else "completed"
        conn.execute(
            "UPDATE planned_workout SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, plan_id),
        )
        linked += 1

    matched_plan_ids = set(matches.values())
    skipped = 0
    for p in plans:
        if p["id"] in matched_plan_ids:
            continue
        if _date_diff(today.isoformat(), p["date"]) > tolerance_days:
            conn.execute(
                "UPDATE planned_workout SET status = 'skipped', updated_at = ? WHERE id = ?"
                " AND status = 'planned'",
                (now, p["id"]),
            )
            skipped += 1
    conn.commit()
    extra = sum(1 for a in acts if a["type"] == "running" and a["id"] not in matches)
    return {"linked": linked, "skipped": skipped, "extra_runs": extra}
