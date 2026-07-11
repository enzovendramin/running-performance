"""Import a chat-designed weekly plan into coach.db — the return half of the bridge.

Contract: a JSON block (see the coach's model). It is ALWAYS run through the
deterministic plan_verifier first; a plan with HARD violations is never persisted.
On pass it is stored as a 'proposed' weekly_plan (the confirmation gate); the user
approves it to 'active'.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from statistics import mean
from typing import Any

from coach import plan_verifier, rules

BASE_TYPES = frozenset({"easy", "long", "recovery"})
ALLOWED_TYPES = rules.REST_TYPES | rules.QUALITY_TYPES | rules.SUPPORT_TYPES | BASE_TYPES


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_json(text: str) -> str:
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j == -1 or j < i:
        raise ValueError("Não encontrei um bloco JSON no texto colado.")
    return text[i:j + 1]


def _pace_s(v: Any) -> float | None:
    """'m:ss' per km -> seconds; also accepts a plain number of seconds."""
    if v in (None, ""):
        return None
    try:
        parts = str(v).split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return float(v)
    except (ValueError, TypeError):
        return None


def parse_plan(text: str) -> dict[str, Any]:
    """Parse + validate the plan JSON. Raises ValueError with a clear message."""
    try:
        plan = json.loads(_extract_json(text))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido: {exc.msg} (linha {exc.lineno}).") from exc
    if not isinstance(plan, dict):
        raise ValueError("O bloco não é um objeto JSON.")
    ws = plan.get("week_start")
    if not ws:
        raise ValueError("Falta 'week_start' (a segunda-feira da semana).")
    try:
        date.fromisoformat(ws)
    except ValueError as exc:
        raise ValueError(f"'week_start' não é uma data válida: {ws}.") from exc
    workouts = plan.get("workouts")
    if not isinstance(workouts, list) or not workouts:
        raise ValueError("'workouts' precisa ser uma lista não vazia.")
    for i, w in enumerate(workouts, 1):
        if not isinstance(w, dict):
            raise ValueError(f"Treino #{i} não é um objeto.")
        if not w.get("date"):
            raise ValueError(f"Treino #{i} sem 'date'.")
        try:
            date.fromisoformat(w["date"])
        except ValueError as exc:
            raise ValueError(f"Treino #{i}: data inválida {w['date']}.") from exc
        typ = (w.get("type") or "").lower()
        if typ not in ALLOWED_TYPES:
            raise ValueError(
                f"Treino #{i}: tipo '{w.get('type')}' inválido. "
                f"Use um de: {', '.join(sorted(ALLOWED_TYPES))}.")
        w["type"] = typ
    return plan


def _state(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT ctl FROM daily_load ORDER BY date DESC LIMIT 1").fetchone()
    ctl = row["ctl"] if row else None
    weekly: dict[int, float] = {}
    for r in conn.execute(
        "SELECT start_time_local, distance_m FROM activity WHERE type='running'"):
        wk = date.fromisoformat(r["start_time_local"][:10]).isocalendar()[1]
        weekly[wk] = weekly.get(wk, 0.0) + (r["distance_m"] or 0)
    active = [m for m in weekly.values() if m > 0]
    return {"ctl": ctl, "baseline_weekly_m": mean(active[-4:]) if active else None}


def verify(conn: sqlite3.Connection, plan: dict[str, Any]) -> plan_verifier.Verdict:
    workouts = [{"date": w["date"], "type": w["type"],
                 "distance_m": (w.get("km") or 0) * 1000 if w.get("km") else None}
                for w in plan["workouts"]]
    return plan_verifier.verify_plan(workouts, _state(conn))


def import_plan(conn: sqlite3.Connection, text: str) -> dict[str, Any]:
    """Parse, verify, and (only if it passes) persist as a 'proposed' weekly plan."""
    plan = parse_plan(text)
    verdict = verify(conn, plan)
    result: dict[str, Any] = {
        "ok": verdict.ok, "badge": verdict.badge(),
        "hard": verdict.hard, "soft": verdict.soft, "plan": plan, "plan_id": None,
    }
    if not verdict.ok:
        return result  # blocked: never persist an unverified plan
    now, ws = _utcnow(), plan["week_start"]
    conn.execute("UPDATE weekly_plan SET status='superseded', updated_at=?"
                 " WHERE week_start_date=? AND status='proposed'", (now, ws))
    pid = conn.execute(
        "INSERT INTO weekly_plan (goal_id, week_start_date, status, rationale,"
        " created_at, updated_at) VALUES (NULL, ?, 'proposed', ?, ?, ?)",
        (ws, plan.get("rationale"), now, now)).lastrowid
    for w in plan["workouts"]:
        conn.execute(
            "INSERT INTO planned_workout (weekly_plan_id, date, type, description,"
            " target_distance_m, target_pace_low_s_km, target_pace_high_s_km,"
            " target_intensity, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, w["date"], w["type"], w.get("note"),
             (w.get("km") or 0) * 1000 if w.get("km") else None,
             _pace_s(w.get("pace_min_per_km")), _pace_s(w.get("pace_max_per_km")),
             w.get("hr_target"), now, now))
    conn.commit()
    result["plan_id"] = pid
    return result


def approve_plan(conn: sqlite3.Connection, plan_id: int) -> bool:
    """Flip a 'proposed' plan to 'active', superseding others for the same week."""
    row = conn.execute("SELECT week_start_date FROM weekly_plan WHERE id=?",
                       (plan_id,)).fetchone()
    if not row:
        return False
    now, ws = _utcnow(), row["week_start_date"]
    conn.execute("UPDATE weekly_plan SET status='superseded', updated_at=?"
                 " WHERE week_start_date=? AND status IN ('active','proposed') AND id!=?",
                 (now, ws, plan_id))
    conn.execute("UPDATE weekly_plan SET status='active', approved_at=?, updated_at=?"
                 " WHERE id=?", (now, now, plan_id))
    conn.commit()
    return True
