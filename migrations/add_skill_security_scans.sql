-- Migration: add_skill_security_scans
-- Adds security scan tracking table and columns to skills table
-- Run: psql $DATABASE_URL -f migrations/add_skill_security_scans.sql

-- ============================================================
-- Table: skill_security_scans
-- ============================================================
CREATE TABLE IF NOT EXISTS skill_security_scans (
    id                SERIAL PRIMARY KEY,
    skill_id          VARCHAR(255) NOT NULL,
    vertical          VARCHAR(100) NOT NULL,
    verified          BOOLEAN NOT NULL DEFAULT FALSE,
    tests_run         INTEGER NOT NULL DEFAULT 0,
    tests_passed      INTEGER NOT NULL DEFAULT 0,
    tests_failed      INTEGER NOT NULL DEFAULT 0,
    plugins_tested    TEXT[],
    preamble_tested   BOOLEAN NOT NULL DEFAULT TRUE,
    scan_model        VARCHAR(100) DEFAULT 'openai:gpt-4o-mini',
    scanned_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_skill_security_scans_skill_id UNIQUE (skill_id)
);

-- Index on vertical for filtering by product area
CREATE INDEX IF NOT EXISTS idx_skill_security_scans_vertical
    ON skill_security_scans (vertical);

-- Index on verified for badge queries
CREATE INDEX IF NOT EXISTS idx_skill_security_scans_verified
    ON skill_security_scans (verified);

-- ============================================================
-- Alter skills table: add security columns
-- ============================================================
ALTER TABLE skills
    ADD COLUMN IF NOT EXISTS security_verified  BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE skills
    ADD COLUMN IF NOT EXISTS security_scanned_at TIMESTAMPTZ;
