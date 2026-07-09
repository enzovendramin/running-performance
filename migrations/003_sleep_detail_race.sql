-- 003_sleep_detail_race.sql — sleep stages + overnight HR on wellness; race predictions.
-- Runs once via the migration runner (ALTER ... ADD COLUMN is not idempotent on its own).

-- Sleep stages + overnight HR. These already arrive in the get_sleep_data payload and
-- were previously discarded; NULL on days without watch data.
ALTER TABLE daily_wellness ADD COLUMN deep_sleep_seconds  INTEGER;
ALTER TABLE daily_wellness ADD COLUMN light_sleep_seconds INTEGER;
ALTER TABLE daily_wellness ADD COLUMN rem_sleep_seconds   INTEGER;
ALTER TABLE daily_wellness ADD COLUMN awake_sleep_seconds INTEGER;
ALTER TABLE daily_wellness ADD COLUMN sleep_hr_avg        INTEGER;  -- avg overnight HR
ALTER TABLE daily_wellness ADD COLUMN sleep_hr_min        INTEGER;  -- min overnight HR

-- Garmin's projected race times, one snapshot per day so the trend is trackable.
-- The API only returns the current value, so history accrues from the first sync onward.
CREATE TABLE IF NOT EXISTS race_prediction (
    date            TEXT PRIMARY KEY,
    time_5k_s       INTEGER,
    time_10k_s      INTEGER,
    time_half_s     INTEGER,
    time_marathon_s INTEGER,
    synced_at       TEXT NOT NULL
);
