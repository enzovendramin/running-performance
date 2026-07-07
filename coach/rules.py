"""Shared, deterministic training rules.

Single source of truth for the thresholds used by BOTH the alert engine (checks
reality — coach/alerts.py) and the plan verifier (checks proposals —
coach/plan_verifier.py). So a plan the coach proposes won't trip the same alerts
reality would.

Rules split into HARD (safety, must pass) and SOFT (advisory, allowed but flagged),
and are phase-aware: a returning runner gets more conservative caps than a trained
one. Numbers are starting points, tunable — the HARD safety rules encode plain
training sense; the SOFT numbers get refined during the methodology study.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Load / alert thresholds (used by coach.alerts) ---
ACWR_HIGH = 1.5            # acute:chronic (ATL/CTL) ratio that flags overload
ACWR_MIN_CTL = 20.0        # floor: skip ratio rules below this fitness (return phase)
WEEK_JUMP_RATIO = 1.30     # >30% week-over-week load jump (alerts, on reality)
WEEK_JUMP_MIN_LOAD = 30.0  # floor: both weeks must exceed this load
TSB_DEEP = -25.0           # deep fatigue / overreaching (form)

# --- Recovery corroboration (used by coach.alerts) ---
RHR_ELEVATED_DELTA = 5     # recent resting HR this far above the 30-day baseline
BODY_BATTERY_LOW = 25      # Body Battery peak never rose above this
SLEEP_SHORT_S = 6 * 3600   # under 6 h

# --- Workout classification (used by the plan verifier) ---
QUALITY_TYPES = frozenset({"tempo", "intervals", "threshold", "repetitions",
                           "fartlek", "hard", "race", "hills"})
REST_TYPES = frozenset({"rest", "off"})


def is_quality(workout_type: str | None) -> bool:
    return (workout_type or "").lower() in QUALITY_TYPES


def is_rest(workout_type: str | None) -> bool:
    return (workout_type or "").lower() in REST_TYPES


# --- Athlete phase (drives how conservative the plan rules are) ---
RETURN_CTL_MAX = 20.0        # below this fitness => "return" phase (conservative)
MIN_BASELINE_M = 15_000.0    # below this weekly baseline, skip volume-jump rules (return phase)


def phase_for(ctl: float | None) -> str:
    return "return" if (ctl is None or ctl < RETURN_CTL_MAX) else "building"


@dataclass(frozen=True)
class PlanLimits:
    """Per-phase limits the plan verifier enforces."""
    min_rest_days: int                # HARD: at least this many rest days/week
    max_quality_days: int             # HARD: at most this many quality days/week
    allow_back_to_back_quality: bool  # HARD: quality on consecutive days allowed?
    hard_weekly_jump: float           # HARD: weekly volume jump above this is blocked
    soft_weekly_jump: float           # SOFT: gentle progression target
    soft_max_quality_share: float     # SOFT: fraction of weekly distance at quality
    soft_max_long_share: float        # SOFT: long run as fraction of weekly distance


RETURN_LIMITS = PlanLimits(
    min_rest_days=2, max_quality_days=1, allow_back_to_back_quality=False,
    hard_weekly_jump=1.5, soft_weekly_jump=1.10,
    soft_max_quality_share=0.10, soft_max_long_share=0.35,
)
BUILDING_LIMITS = PlanLimits(
    min_rest_days=1, max_quality_days=2, allow_back_to_back_quality=False,
    hard_weekly_jump=1.5, soft_weekly_jump=1.15,
    soft_max_quality_share=0.20, soft_max_long_share=0.35,
)


def limits_for(ctl: float | None) -> PlanLimits:
    return RETURN_LIMITS if phase_for(ctl) == "return" else BUILDING_LIMITS
