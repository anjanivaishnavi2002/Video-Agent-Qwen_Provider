-- Run once against the `video_agent` database if you prefer manual migrations.
-- (The app also applies these automatically at startup via app.db.database.init_db.)

ALTER TABLE candidates ADD COLUMN IF NOT EXISTS resume_filename VARCHAR;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS resume_profile JSON;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS consent_version VARCHAR;
ALTER TABLE candidates ADD COLUMN IF NOT EXISTS consented_at TIMESTAMP;

ALTER TABLE interviews ADD COLUMN IF NOT EXISTS end_reason VARCHAR;
ALTER TABLE interviews ADD COLUMN IF NOT EXISTS access_token VARCHAR;
ALTER TABLE interviews ADD COLUMN IF NOT EXISTS settings_snapshot JSON;
ALTER TABLE interviews ADD COLUMN IF NOT EXISTS started_at TIMESTAMP;
ALTER TABLE interviews ADD COLUMN IF NOT EXISTS ended_at TIMESTAMP;
ALTER TABLE interviews ADD COLUMN IF NOT EXISTS video_size_bytes INTEGER;
ALTER TABLE interviews ADD COLUMN IF NOT EXISTS video_uploaded_at TIMESTAMP;

CREATE TABLE IF NOT EXISTS interview_events (
    id           SERIAL PRIMARY KEY,
    interview_id INTEGER NOT NULL REFERENCES interviews(id),
    event_type   VARCHAR NOT NULL,
    occurred_at  TIMESTAMP NOT NULL,
    offset_ms    INTEGER,
    details      JSON,
    created_at   TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_interview_events_interview_id ON interview_events(interview_id);

-- OPTIONAL cleanup of the old job-based architecture (only when you no longer need old data):
-- ALTER TABLE interviews DROP COLUMN IF EXISTS job_id;
-- DROP TABLE IF EXISTS jobs;
