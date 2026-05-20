-- Email tracking tables
-- Run once against production DB

-- Email opens (1x1 pixel tracking)
CREATE TABLE IF NOT EXISTS email_opens (
    id          SERIAL PRIMARY KEY,
    message_id  VARCHAR(255) NOT NULL DEFAULT '',
    product_id  VARCHAR(50)  NOT NULL DEFAULT '',
    email       VARCHAR(255) NOT NULL DEFAULT '',
    opened_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_email_opens_message_id ON email_opens(message_id);
CREATE INDEX IF NOT EXISTS idx_email_opens_email      ON email_opens(email);
CREATE INDEX IF NOT EXISTS idx_email_opens_opened_at  ON email_opens(opened_at);

-- last_active_at on users (activation tracking)
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_users_last_active_at ON users(last_active_at);
