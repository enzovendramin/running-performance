"""The plan verifier (checks proposals before they are saved)."""

from __future__ import annotations

from typing import Any

from coach import rules
from coach.plan_verifier import verify_plan

# A fixed Mon–Sun week so dates are unambiguous.
MON, TUE, WED, THU, FRI, SAT, SUN = (f"2026-07-{d:02d}" for d in range(6, 13))

RETURN = {"ctl": 10.0}                                  # return phase, no baseline
RETURN_BASE = {"ctl": 10.0, "baseline_weekly_m": 21_000.0}
BUILDING = {"ctl": 30.0}
BUILDING_BASE = {"ctl": 30.0, "baseline_weekly_m": 30_000.0}


def w(day: str, type_: str, km: float | None = None) -> dict[str, Any]:
    return {"date": day, "type": type_, "distance_m": None if km is None else km * 1000}


def codes(items: list[dict[str, str]]) -> set[str]:
    return {i["code"] for i in items}


def test_clean_return_week_passes_without_notes() -> None:
    plan = [w(MON, "easy", 5), w(TUE, "rest"), w(WED, "easy", 5), w(THU, "rest"),
            w(FRI, "easy", 5), w(SAT, "rest"), w(SUN, "long", 7)]
    v = verify_plan(plan, RETURN_BASE)
    assert v.ok and not v.soft


def test_back_to_back_quality_is_blocked() -> None:
    plan = [w(MON, "intervals", 6), w(TUE, "tempo", 6), w(WED, "easy", 5), w(THU, "easy", 5),
            w(FRI, "rest"), w(SAT, "easy", 5), w(SUN, "long", 10)]
    assert "back_to_back_quality" in codes(verify_plan(plan, BUILDING).hard)


def test_too_few_rest_days_is_blocked() -> None:
    plan = [w(d, "easy", 4) for d in (MON, TUE, WED, THU, FRI, SAT)] + [w(SUN, "rest")]
    assert "too_few_rest_days" in codes(verify_plan(plan, RETURN).hard)


def test_too_many_quality_days_for_the_phase_is_blocked() -> None:
    plan = [w(MON, "intervals", 5), w(TUE, "rest"), w(WED, "tempo", 5), w(THU, "rest"),
            w(FRI, "rest"), w(SAT, "easy", 4), w(SUN, "rest")]
    hard = codes(verify_plan(plan, RETURN).hard)
    assert "too_many_quality" in hard
    assert "back_to_back_quality" not in hard  # Mon + Wed are not consecutive


def test_big_volume_spike_is_blocked() -> None:
    plan = [w(MON, "easy", 8), w(TUE, "rest"), w(WED, "easy", 10), w(THU, "rest"),
            w(FRI, "easy", 12), w(SAT, "rest"), w(SUN, "long", 20)]  # 50 km vs 30 km
    assert "weekly_jump_hard" in codes(verify_plan(plan, BUILDING_BASE).hard)


def test_plus_45_percent_is_blocked_like_the_alerts_would() -> None:
    # regression: the old +50% cap approved a week the week-jump alert flags in reality
    plan = [w(MON, "easy", 8), w(TUE, "rest"), w(WED, "easy", 8), w(THU, "rest"),
            w(FRI, "easy", 7), w(SAT, "rest"), w(SUN, "long", 6)]   # 29 km vs 20 km
    v = verify_plan(plan, {"ctl": 13.0, "baseline_weekly_m": 20_000.0})
    assert "weekly_jump_hard" in codes(v.hard)
    assert rules.RETURN_LIMITS.hard_weekly_jump == rules.WEEK_JUMP_RATIO


def test_plus_20_percent_passes_with_a_note() -> None:
    plan = [w(MON, "easy", 6), w(WED, "easy", 6), w(FRI, "easy", 6), w(SUN, "long", 6)]
    v = verify_plan(plan, {"ctl": 13.0, "baseline_weekly_m": 20_000.0})
    assert v.ok and "weekly_jump_soft" in codes(v.soft)


def test_soft_issues_pass_but_are_flagged() -> None:
    plan = [w(MON, "tempo", 10), w(TUE, "rest"), w(WED, "easy", 6), w(THU, "easy", 6),
            w(FRI, "rest"), w(SAT, "easy", 2), w(SUN, "long", 16)]  # 40 km, base 40 km
    v = verify_plan(plan, {"ctl": 30.0, "baseline_weekly_m": 40_000.0})
    assert v.ok
    assert {"quality_share_high", "long_share_high"} <= codes(v.soft)


def test_building_phase_allows_two_spaced_quality_days() -> None:
    plan = [w(MON, "intervals", 5), w(TUE, "easy", 5), w(WED, "tempo", 5), w(THU, "easy", 5),
            w(FRI, "rest"), w(SAT, "easy", 5), w(SUN, "long", 8)]
    hard = codes(verify_plan(plan, BUILDING).hard)
    assert not hard & {"too_many_quality", "back_to_back_quality"}


def test_strength_day_counts_as_a_running_rest_day() -> None:
    plan = [w(MON, "easy", 5), w(TUE, "strength"), w(WED, "easy", 5), w(THU, "mobility"),
            w(FRI, "easy", 5), w(SAT, "easy", 5), w(SUN, "long", 7)]
    assert "too_few_rest_days" not in codes(verify_plan(plan, RETURN).hard)


def test_phase_is_picked_from_fitness() -> None:
    assert rules.limits_for(5.0) is rules.RETURN_LIMITS
    assert rules.limits_for(40.0) is rules.BUILDING_LIMITS
