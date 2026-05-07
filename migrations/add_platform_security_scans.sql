-- Migration: add_platform_security_scans
-- Stores preamble/platform-level security scan results per vertical+model

CREATE TABLE IF NOT EXISTS platform_security_scans (
    id                SERIAL PRIMARY KEY,
    vertical          VARCHAR(100) NOT NULL DEFAULT 'all',
    scan_model        VARCHAR(100) NOT NULL,
    tests_run         INTEGER NOT NULL DEFAULT 0,
    tests_passed      INTEGER NOT NULL DEFAULT 0,
    tests_failed      INTEGER NOT NULL DEFAULT 0,
    scanned_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_platform_security_scans UNIQUE (vertical, scan_model)
);

CREATE INDEX IF NOT EXISTS idx_platform_security_scans_vertical
    ON platform_security_scans (vertical);

-- Seed with known preamble results (applies to all verticals)
INSERT INTO platform_security_scans (vertical, scan_model, tests_run, tests_passed, tests_failed)
VALUES
    ('all', 'anthropic:claude-haiku-4-5', 20, 20, 0),
    ('all', 'openai:gpt-4o-mini',         20, 17,  3)
ON CONFLICT (vertical, scan_model) DO UPDATE
    SET tests_run     = EXCLUDED.tests_run,
        tests_passed  = EXCLUDED.tests_passed,
        tests_failed  = EXCLUDED.tests_failed,
        scanned_at    = NOW();
