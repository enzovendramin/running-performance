"""Deterministic plan verifier (the safety layer).

Checks a proposed weekly plan against fixed training rules BEFORE it reaches the
user. Two tiers (decision from the design debate):
- HARD violations block the plan (it must be corrected and re-checked).
- SOFT violations pass but are flagged to the user.

Pure and deterministic (rule-based). Rules and thresholds come from coach/rules.py, so
they stay consistent with the alert engine. Each violation carries a machine-readable
`code` and a human-readable `message` (for the user).

A workout is a dict: {"date": "YYYY-MM-DD", "type": str, "distance_m": float|None}.
Athlete state: {"ctl": float|None, "baseline_weekly_m": float|None}.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from coach import rules


@dataclass
class Verdict:
    hard: list[dict[str, str]] = field(default_factory=list)
    soft: list[dict[str, str]] = field(default_factory=list)
    checks_run: int = 0

    @property
    def ok(self) -> bool:
        """True when no HARD rule is violated (the plan may still carry soft flags)."""
        return not self.hard

    def badge(self) -> str:
        """Short, user-facing verification summary (decision #3: visible verification)."""
        if self.hard:
            return f"BLOCKED: {len(self.hard)} safety violation(s)"
        base = f"Passed all {self.checks_run} safety checks"
        return base + (f" ({len(self.soft)} note(s))" if self.soft else "")


def _training_workouts(workouts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [w for w in workouts if not rules.is_rest(w.get("type"))]


def verify_plan(
    workouts: list[dict[str, Any]],
    state: dict[str, Any] | None = None,
    limits: rules.PlanLimits | None = None,
) -> Verdict:
    """Verify a weekly plan. Returns a Verdict with hard/soft violations."""
    state = state or {}
    ctl = state.get("ctl")
    baseline_m = state.get("baseline_weekly_m")
    limits = limits or rules.limits_for(ctl)
    v = Verdict()

    training = _training_workouts(workouts)
    quality = [w for w in workouts if rules.is_quality(w.get("type"))]
    training_days = {w["date"] for w in training}
    total_m = sum(w.get("distance_m") or 0 for w in training)

    # HARD 1: no back-to-back quality days.
    v.checks_run += 1
    if not limits.allow_back_to_back_quality:
        qdates = sorted(date.fromisoformat(w["date"]) for w in quality)
        if any((qdates[i] - qdates[i - 1]).days == 1 for i in range(1, len(qdates))):
            v.hard.append({
                "code": "back_to_back_quality",
                "message": "Two quality/hard days on consecutive days — put an easy or "
                           "rest day between them.",
            })

    # HARD 2: enough rest days.
    v.checks_run += 1
    rest_days = 7 - len(training_days)
    if rest_days < limits.min_rest_days:
        v.hard.append({
            "code": "too_few_rest_days",
            "message": f"Only {rest_days} rest day(s) this week; need at least "
                       f"{limits.min_rest_days}.",
        })

    # HARD 3: not too many quality days.
    v.checks_run += 1
    if len(quality) > limits.max_quality_days:
        v.hard.append({
            "code": "too_many_quality",
            "message": f"{len(quality)} quality days; the cap for this phase is "
                       f"{limits.max_quality_days}.",
        })

    # HARD 4 / SOFT (volume jump) — only with a meaningful baseline (skip in early return).
    if baseline_m and baseline_m >= rules.MIN_BASELINE_M:
        v.checks_run += 1
        jump_pct = (total_m / baseline_m - 1) * 100
        if total_m > baseline_m * limits.hard_weekly_jump:
            v.hard.append({
                "code": "weekly_jump_hard",
                "message": f"Weekly volume {total_m / 1000:.0f} km is +{jump_pct:.0f}% "
                           f"over baseline {baseline_m / 1000:.0f} km "
                           f"(hard cap +{(limits.hard_weekly_jump - 1) * 100:.0f}%).",
            })
        elif total_m > baseline_m * limits.soft_weekly_jump:
            v.soft.append({
                "code": "weekly_jump_soft",
                "message": f"Weekly volume is +{jump_pct:.0f}% over baseline — steeper "
                           f"than the ~{(limits.soft_weekly_jump - 1) * 100:.0f}% guideline.",
            })

    # SOFT 1: intensity distribution (quality share).
    if total_m > 0:
        v.checks_run += 1
        q_share = sum(w.get("distance_m") or 0 for w in quality) / total_m
        if q_share > limits.soft_max_quality_share:
            v.soft.append({
                "code": "quality_share_high",
                "message": f"{q_share * 100:.0f}% of volume is quality — above the "
                           f"{limits.soft_max_quality_share * 100:.0f}% guideline for this phase.",
            })

    # SOFT 2: long-run share.
    if total_m > 0:
        v.checks_run += 1
        longest = max((w.get("distance_m") or 0 for w in training), default=0)
        long_share = longest / total_m
        if long_share > limits.soft_max_long_share:
            v.soft.append({
                "code": "long_share_high",
                "message": f"The long run is {long_share * 100:.0f}% of weekly volume — "
                           f"above the {limits.soft_max_long_share * 100:.0f}% guideline.",
            })

    return v
