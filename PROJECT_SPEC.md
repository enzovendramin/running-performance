# Running Performance — functional specification (MVP)

## Overview
Personal, single-user system that reads Garmin Connect data, keeps a structured history
in SQLite, and presents it as a local dashboard. Running only; triathlon is out of scope.

## Architecture
- **The app measures.** A deterministic local dashboard: it syncs Garmin data, computes
  training-load metrics, tracks planned vs. actual, flags risk, and stores everything. It
  makes no training decisions.
- **Planning is external and verified.** Training plans are set by the athlete and
  imported into the database; an imported plan must pass the deterministic verifier first.
- **Safety is always deterministic, rule-based code** (the alert engine and the plan
  verifier). An unsafe-but-plausible plan must never be saved.

## Device and available data
Watch: Garmin Forerunner 245 Music.

Available: activities (type, duration, distance, pace, HR), Body Battery, daily stress,
Training Status / Load / Effect (aerobic + anaerobic), VO2max, sleep duration.

**Not** available (do not assume): continuous/overnight HRV Status (needs a newer watch);
cycling power; pool-swimming data. A numeric sleep *score* is not exposed via this API —
sleep **duration** is used instead (and is absent on no-watch nights).

## Metrics
- **Per-run load: Banister TRIMP** (HR-based, exponential intensity weighting) — Garmin
  does not expose per-activity training load, so it is derived.
- **CTL** (fitness, 42-day EWMA), **ATL** (fatigue, 7-day EWMA), **TSB** (form = CTL − ATL).
- **Intensity zones** by average HR: easy / moderate / hard.
- **Wellness**: resting HR, sleep duration, Body Battery, stress; VO2max on the profile.
- Running only feeds the load model; slow low-HR runs are reclassified as walking.

## Safety (deterministic)
- **Alert engine** checks *reality*: weekly-load jump, acute:chronic ratio, deep fatigue,
  corroborated by recovery signals. Conservative low-base floor so a returning runner
  isn't false-alarmed.
- **Plan verifier** checks *proposals* before they are saved: HARD rules block, SOFT rules
  pass but are flagged. Thresholds are shared with the alert engine, so a saved plan won't
  trip the alerts reality would.

## Dashboard (local, FastAPI)
Five screens (Portuguese UI, white + purple, interactive, fully offline):
1. **Visão geral** — key metrics as gauges with a scale + label, the active week, alerts.
2. **Condição & carga** — CTL / ATL / TSB over time.
3. **Treinos** — volume by week/month with intensity folded in, plus the full run log.
4. **Recuperação** — resting HR, Body Battery, stress.
5. **Relatórios** — an archive of weekly snapshots.

Product decision kept from the start: **no daily "readiness card"/verdict** — it becomes
an excuse not to train before even trying. Prioritise the plan and the load trend.

## Data model (SQLite; schema in `migrations/`)
Three layers:
1. **Profile & goal** — `user_profile`, `goal` (no active goal = maintenance, a valid
   state), `restriction` (injuries/limits the watch can't capture).
2. **Plan** — `weekly_plan` (with a `rationale`), `planned_workout`, `activity` (real
   Garmin data, linked to a planned workout when it matches).
3. **Events** — `feedback_event`, `adjustment` (every plan change records its `reasoning`).

Plus the numeric foundation: `daily_wellness`, `daily_load`, and delivery/ops tables
`sync_log`, `inbox_message`.

## Constraints
- Single-user, **no authentication**, no scaling complexity.
- **No hardcoded credentials**; `.env`, tokens, and `*.db` are never committed.
- **OS-portable** — avoid environment-specific paths (a Raspberry Pi move is possible).
- **English** in code, docs, and commits. The dashboard UI is Portuguese (the athlete's
  language).

## Out of scope (MVP)
- Triathlon (cycling, swimming).
- Telegram bot — a possible V2; the dashboard reads a plain SQLite DB, so another
  interface can be added later without touching the core.
- Cloud hosting / remote access.

## Next
- **`coach.import`**: write a designed plan into the database, passing the plan verifier
  first — the path by which plans get into the app.
