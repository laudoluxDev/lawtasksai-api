-- Pricing test for first paid-search relaunch cohort.
-- Keeps the existing credit-pack structure and public pack keys intact.
-- Run against production/staging after deploying matching landing pages.

BEGIN;

INSERT INTO product_credit_packs (product_id, pack_key, name, credits, price_cents)
VALUES
  ('teacher', 'starter', 'Starter', 20, 500),
  ('teacher', 'professional', 'Professional', 60, 1200),
  ('teacher', 'office', 'School Year', 200, 2900),

  ('farmer', 'starter', 'Starter', 25, 900),
  ('farmer', 'professional', 'Professional', 75, 1900),
  ('farmer', 'office', 'Office', 250, 4900),

  ('realtor', 'starter', 'Starter', 20, 900),
  ('realtor', 'professional', 'Professional', 80, 2900),
  ('realtor', 'office', 'Office', 300, 7900),

  ('therapist', 'starter', 'Starter', 20, 900),
  ('therapist', 'professional', 'Professional', 80, 2900),
  ('therapist', 'office', 'Office', 300, 7900),

  ('law', 'starter', 'Starter', 25, 1900),
  ('law', 'professional', 'Professional', 100, 4900),
  ('law', 'office', 'Office', 400, 14900)
ON CONFLICT (product_id, pack_key)
DO UPDATE SET
  name = EXCLUDED.name,
  credits = EXCLUDED.credits,
  price_cents = EXCLUDED.price_cents;

COMMIT;
