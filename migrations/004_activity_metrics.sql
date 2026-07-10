-- 004_activity_metrics.sql — per-run cadence, aerobic decoupling, HR time-in-zone.
-- Cadence comes from the activity summary; decoupling + zones need a per-activity
-- fetch (splits + hr-in-timezones), computed once and stored (NULL until enriched).

ALTER TABLE activity ADD COLUMN avg_cadence    REAL;  -- avg running cadence (steps/min)
ALTER TABLE activity ADD COLUMN decoupling_pct REAL;  -- Pa:HR aerobic decoupling (%), from splits
ALTER TABLE activity ADD COLUMN hr_zones_json  TEXT;  -- [{"zone":n,"secs":s,"low":bpm}, ...]
