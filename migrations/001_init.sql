-- 001_init.sql — initial schema (Phase 1). See docs/SCHEMA_PROPOSAL.md.
-- Idempotent (IF NOT EXISTS) so it is safe to re-run.

PRAGMA foreign_keys = ON;

-- ---------- Layer 1: profile and goal ----------
CREATE TABLE IF NOT EXISTS user_profile (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    name           TEXT,
    garmin_user_id INTEGER,
    timezone       TEXT NOT NULL DEFAULT 'Europe/Paris',
    unit_distance  TEXT NOT NULL DEFAULT 'km',
    unit_pace      TEXT NOT NULL DEFAULT 'min_per_km',
    week_start     INTEGER NOT NULL DEFAULT 1,   -- 1 = Monday
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

-- No row with status='active' = maintenance mode (a valid state).
CREATE TABLE IF NOT EXISTS goal (
    id            INTEGER PRIMARY KEY,
    status        TEXT NOT NULL CHECK (status IN ('active','completed','abandoned')),
    type          TEXT NOT NULL CHECK (type IN ('race','general_fitness','return_from_injury')),
    target_date   TEXT,
    target_metric TEXT,
    description   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS restriction (
    id          INTEGER PRIMARY KEY,
    type        TEXT NOT NULL CHECK (type IN ('injury','schedule','health','other')),
    description TEXT NOT NULL,
    body_part   TEXT,
    active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    reported_at TEXT NOT NULL,
    resolved_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- ---------- Layer 2: active plan ----------
-- status='proposed' is the confirmation gate (§2E).
CREATE TABLE IF NOT EXISTS weekly_plan (
    id              INTEGER PRIMARY KEY,
    goal_id         INTEGER REFERENCES goal(id),   -- NULL in maintenance
    week_start_date TEXT NOT NULL,                 -- the Monday of the week
    status          TEXT NOT NULL CHECK (status IN ('proposed','active','completed','superseded')),
    rationale       TEXT,
    created_at      TEXT NOT NULL,
    approved_at     TEXT,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_weekly_plan_week ON weekly_plan (week_start_date);

CREATE TABLE IF NOT EXISTS planned_workout (
    id                    INTEGER PRIMARY KEY,
    weekly_plan_id        INTEGER NOT NULL REFERENCES weekly_plan(id),
    date                  TEXT NOT NULL,
    type                  TEXT NOT NULL,
    description           TEXT,
    target_distance_m     REAL,
    target_duration_s     REAL,
    target_pace_low_s_km  REAL,   -- pace target (range), Runna-style
    target_pace_high_s_km REAL,
    target_intensity      TEXT,
    status                TEXT NOT NULL DEFAULT 'planned'
                          CHECK (status IN ('planned','completed','skipped','modified')),
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_planned_workout_plan ON planned_workout (weekly_plan_id);
CREATE INDEX IF NOT EXISTS idx_planned_workout_date ON planned_workout (date);

-- Real Garmin data (raw). planned_workout_id is NULL if it did not match (§2F).
CREATE TABLE IF NOT EXISTS activity (
    id                        INTEGER PRIMARY KEY,
    garmin_activity_id        INTEGER NOT NULL UNIQUE,   -- dedup on sync
    planned_workout_id        INTEGER REFERENCES planned_workout(id),
    start_time_utc            TEXT NOT NULL,
    start_time_local          TEXT NOT NULL,
    type                      TEXT,
    distance_m                REAL,
    duration_s                REAL,
    moving_duration_s         REAL,
    avg_speed                 REAL,   -- m/s (pace derived)
    max_speed                 REAL,
    avg_hr                    INTEGER,
    max_hr                    INTEGER,
    elevation_gain_m          REAL,
    elevation_loss_m          REAL,
    aerobic_training_effect   REAL,
    anaerobic_training_effect REAL,
    training_load             REAL,   -- session load (basis for CTL/ATL/TSB)
    raw_json                  TEXT,   -- raw payload (future-proofing)
    synced_at                 TEXT NOT NULL,
    created_at                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_start ON activity (start_time_utc);

-- ---------- Layer 3: events ----------
CREATE TABLE IF NOT EXISTS feedback_event (
    id               INTEGER PRIMARY KEY,
    activity_id      INTEGER REFERENCES activity(id),
    type             TEXT NOT NULL CHECK (type IN
                       ('post_workout','pain','fatigue','difficulty','anxiety','general')),
    description      TEXT NOT NULL,
    perceived_effort INTEGER CHECK (perceived_effort BETWEEN 1 AND 10),
    reported_at      TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

-- reasoning = long-term memory; magnitude/status = autonomy (§2E).
CREATE TABLE IF NOT EXISTS adjustment (
    id                 INTEGER PRIMARY KEY,
    weekly_plan_id     INTEGER REFERENCES weekly_plan(id),
    planned_workout_id INTEGER REFERENCES planned_workout(id),
    trigger_type       TEXT NOT NULL CHECK (trigger_type IN
                         ('feedback','garmin_signal','weekly_replan','user_request')),
    trigger_ref_id     INTEGER,
    change_summary     TEXT NOT NULL,
    reasoning          TEXT NOT NULL,
    magnitude          TEXT NOT NULL CHECK (magnitude IN ('small','large')),
    status             TEXT NOT NULL CHECK (status IN ('proposed','applied','auto_applied','rejected')),
    created_at         TEXT NOT NULL,
    applied_at         TEXT
);

-- ---------- Numeric foundation (Phase 0: all available) ----------
CREATE TABLE IF NOT EXISTS daily_wellness (
    date              TEXT PRIMARY KEY,
    resting_hr        INTEGER,
    sleep_score       INTEGER,
    avg_stress        INTEGER,
    max_stress        INTEGER,
    body_battery_high INTEGER,
    body_battery_low  INTEGER,
    vo2max            REAL,          -- via get_training_status
    raw_json          TEXT,
    synced_at         TEXT NOT NULL
);

-- DERIVED series (precomputed in code; regenerable from activity).
CREATE TABLE IF NOT EXISTS daily_load (
    date        TEXT PRIMARY KEY,
    daily_load  REAL NOT NULL,   -- sum of the day's load
    ctl         REAL NOT NULL,   -- Fitness (42d EWMA)
    atl         REAL NOT NULL,   -- Fatigue (7d EWMA)
    tsb         REAL NOT NULL,   -- Form = yesterday's ctl - atl
    computed_at TEXT NOT NULL
);

-- ---------- Monitored ingestion + delivery ----------
CREATE TABLE IF NOT EXISTS sync_log (
    id                 INTEGER PRIMARY KEY,
    ran_at             TEXT NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('ok','failed','suspicious')),
    source             TEXT NOT NULL CHECK (source IN ('garmin_sync','manual_import')),
    detail             TEXT,
    activities_fetched INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sync_log_ran ON sync_log (ran_at);

-- Inbox (§2G): used in Phases 3-4, defined now for coherence.
CREATE TABLE IF NOT EXISTS inbox_message (
    id         INTEGER PRIMARY KEY,
    type       TEXT NOT NULL CHECK (type IN ('weekly_report','risk_alert','sync_warning','proposal')),
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    ref_type   TEXT,
    ref_id     INTEGER,
    status     TEXT NOT NULL DEFAULT 'unread' CHECK (status IN ('unread','read','actioned')),
    created_at TEXT NOT NULL,
    read_at    TEXT
);
