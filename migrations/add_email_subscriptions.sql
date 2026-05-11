-- Migration: add_email_subscriptions
-- Created: 2026-05-11
-- Purpose: Per-vertical email subscription tracking for broadcast system

CREATE TABLE IF NOT EXISTS email_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    product_id VARCHAR(50) NOT NULL,
    subscribed BOOLEAN DEFAULT TRUE,
    subscribed_at TIMESTAMP DEFAULT NOW(),
    unsubscribed_at TIMESTAMP,
    CONSTRAINT uq_email_sub_user_product UNIQUE (user_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_email_subscriptions_user_id ON email_subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_email_subscriptions_product_id ON email_subscriptions(product_id);
CREATE INDEX IF NOT EXISTS idx_email_subscriptions_subscribed ON email_subscriptions(subscribed);
