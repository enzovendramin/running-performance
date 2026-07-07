"""Verify the deterministic plan verifier (Step 2 gate).

Pure, in-memory scenarios (no database, rule-based): a good plan passes; each known-bad
plan is blocked by the RIGHT hard rule; soft-only issues pass but are flagged. Mirrors
the verify_* style used elsewhere (cf. coach/verify_alerts.py, coach/verify_matching.py).

Usage (from the project root):
    python -m coach.verify_plan_verifier
"""

from __future__ import annotations

from coach import rules
from coach.plan_verifier import verify_plan

# A fixed Mon–Sun week so dates are unambiguous.
MON, TUE, WED, THU, FRI, SAT, SUN = (f"2026-07-{d:02d}" for d in range(6, 13))


def w(date: str, type_: str, km: float | None = None) -> dict:
    return {"date": date, "type": type_, "distance_m": None if km is None else km * 1000}


def _codes(items: list[dict[str, str]]) -> set[str]:
    return {i["code"] for i in items}


# state helpers
RETURN = {"ctl": 10.0}                              # return phase, no baseline (skip jump)
RETURN_BASE = {"ctl": 10.0, "baseline_weekly_m": 21_000.0}
BUILDING = {"ctl": 30.0}
BUILDING_BASE = {"ctl": 30.0, "baseline_weekly_m": 30_000.0}


def main() -> None:
    passed = 0
    failed = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal passed, failed
        mark = "OK " if cond else "XX "
        print(f"  {mark}{name}" + (f" — {detail}" if detail and not cond else ""))
        if cond:
            passed += 1
        else:
            failed += 1

    # 1. Clean return-phase week: 3 easy + 1 long, 3 rest days, no quality, gentle volume.
    good = [
        w(MON, "easy", 5), w(TUE, "rest"), w(WED, "easy", 5), w(THU, "rest"),
        w(FRI, "easy", 5), w(SAT, "rest"), w(SUN, "long", 7),
    ]
    v = verify_plan(good, RETURN_BASE)
    check("good return plan passes clean", v.ok and not v.soft, v.badge())

    # 2. Back-to-back quality days -> HARD.
    b2b = [
        w(MON, "intervals", 6), w(TUE, "tempo", 6), w(WED, "easy", 5),
        w(THU, "easy", 5), w(FRI, "rest"), w(SAT, "easy", 5), w(SUN, "long", 10),
    ]
    v = verify_plan(b2b, BUILDING)
    check("back-to-back quality blocked", "back_to_back_quality" in _codes(v.hard), v.badge())

    # 3. Too few rest days (return needs >= 2) -> HARD.
    no_rest = [w(d, "easy", 4) for d in (MON, TUE, WED, THU, FRI, SAT)] + [w(SUN, "rest")]
    v = verify_plan(no_rest, RETURN)
    check("too few rest days blocked", "too_few_rest_days" in _codes(v.hard), v.badge())

    # 4. Too many quality days for the phase (return cap = 1), non-consecutive -> HARD.
    two_quality = [
        w(MON, "intervals", 5), w(TUE, "rest"), w(WED, "tempo", 5), w(THU, "rest"),
        w(FRI, "rest"), w(SAT, "easy", 4), w(SUN, "rest"),
    ]
    v = verify_plan(two_quality, RETURN)
    check("too many quality days blocked", "too_many_quality" in _codes(v.hard), v.badge())
    check("  (and not misread as back-to-back)",
          "back_to_back_quality" not in _codes(v.hard))

    # 5. Volume jump beyond the hard cap (+>50% over baseline) -> HARD.
    spike = [
        w(MON, "easy", 8), w(TUE, "rest"), w(WED, "easy", 10), w(THU, "rest"),
        w(FRI, "easy", 12), w(SAT, "rest"), w(SUN, "long", 20),  # 50 km vs 30 km base
    ]
    v = verify_plan(spike, BUILDING_BASE)
    check("weekly volume spike blocked", "weekly_jump_hard" in _codes(v.hard), v.badge())

    # 6. Soft-only issues: pass, but flag high quality share and long-run share.
    soft = [
        w(MON, "tempo", 10), w(TUE, "rest"), w(WED, "easy", 6), w(THU, "easy", 6),
        w(FRI, "rest"), w(SAT, "easy", 2), w(SUN, "long", 16),  # 40 km, base 40 km
    ]
    v = verify_plan(soft, {"ctl": 30.0, "baseline_weekly_m": 40_000.0})
    check("soft-only plan passes", v.ok, v.badge())
    check("  flags quality share", "quality_share_high" in _codes(v.soft))
    check("  flags long-run share", "long_share_high" in _codes(v.soft))

    # 7. Building phase allows two (non-consecutive) quality days -> no quality-count block.
    two_ok = [
        w(MON, "intervals", 5), w(TUE, "easy", 5), w(WED, "tempo", 5), w(THU, "easy", 5),
        w(FRI, "rest"), w(SAT, "easy", 5), w(SUN, "long", 8),
    ]
    v = verify_plan(two_ok, BUILDING)
    check("building allows 2 quality days",
          "too_many_quality" not in _codes(v.hard) and "back_to_back_quality" not in _codes(v.hard),
          v.badge())

    # 8. Phase awareness sanity: same limits picked from CTL as from an explicit choice.
    check("limits_for(return CTL) is RETURN_LIMITS", rules.limits_for(5.0) is rules.RETURN_LIMITS)
    check("limits_for(building CTL) is BUILDING_LIMITS",
          rules.limits_for(40.0) is rules.BUILDING_LIMITS)

    print(f"\n{passed} passed, {failed} failed.")
    if failed:
        raise SystemExit(1)
    print("OK: verifier accepts safe plans and blocks unsafe ones (in-memory).")


if __name__ == "__main__":
    main()
