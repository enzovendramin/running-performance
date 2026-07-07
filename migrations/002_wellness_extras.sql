-- 002_wellness_extras.sql — VO2max/fitness age on the profile; sleep duration on wellness.
-- Runs once via the migration runner (ALTER ... ADD COLUMN is not idempotent on its own).

-- Current fitness (slowly-changing) lives on the profile, not per day.
ALTER TABLE user_profile ADD COLUMN vo2max REAL;
ALTER TABLE user_profile ADD COLUMN fitness_age INTEGER;

-- The FR245 doesn't expose a numeric sleep score via this API, so we store sleep
-- DURATION as the sleep signal. NULL when there's no data (e.g. slept without the watch).
ALTER TABLE daily_wellness ADD COLUMN sleep_seconds INTEGER;
