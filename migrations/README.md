# Database Migrations

This directory contains standalone migration scripts for the LawTasksAI API.

---

## Migration 001 — Multi-Tenant Expansion

**File:** `001_add_multitenant.py`  
**Status:** Production-ready, idempotent  
**Risk:** Additive-only — no tables dropped, no existing data modified

---

### What This Migration Does

| Step | Action | Safe to run twice? |
|------|--------|--------------------|
| 1 | Creates `products` table | ✓ `IF NOT EXISTS` |
| 2 | Seeds 27 products into `products` | ✓ `ON CONFLICT DO NOTHING` |
| 3 | Adds `product_id VARCHAR(50) DEFAULT 'law'` to 6 tables | ✓ checks column existence first |
| 4 | Creates `product_credit_packs` table | ✓ `IF NOT EXISTS` |
| 5 | Seeds 162 credit pack rows (6 tiers × 27 products) | ✓ `ON CONFLICT DO NOTHING` |
| 6 | Creates 7 indexes on `product_id` columns | ✓ `IF NOT EXISTS` |

**The `DEFAULT 'law'` guarantee:** All existing users, skills, licenses, usage logs, and credit transactions automatically get `product_id = 'law'`. LawTasksAI continues to work with zero changes.

---

### How to Run

#### Prerequisites

- Python 3.8+
- `asyncpg` installed (`pip install asyncpg`)
- `DATABASE_URL` set in your environment

#### Format

The `DATABASE_URL` should be a standard PostgreSQL URL. Both formats work:

```
postgresql://user:password@host:5432/dbname
postgresql+asyncpg://user:password@host:5432/dbname   ← SQLAlchemy format, auto-converted
```

#### Run the migration

```bash
# Set your database URL
export DATABASE_URL="postgresql://user:password@host:5432/lawtasksai"

# Run from the repo root
python migrations/001_add_multitenant.py
```

Or inline:

```bash
DATABASE_URL="postgresql://user:password@host:5432/lawtasksai" \
  python migrations/001_add_multitenant.py
```

#### Expected output (first run)

```
Connecting to database...

=== LawTasksAI Multi-Tenant Migration 001 ===

Step 1/6 — Creating `products` table (IF NOT EXISTS)...
  ✓ products table ready

Step 2/6 — Seeding 27 products...
  + Seeded: law (LawTasksAI)
  + Seeded: contractor (ContractorTasksAI)
  ... (27 total)
  ✓ 27 new products seeded (0 already existed)

Step 3/6 — Adding product_id column to tables...
  + Adding users.product_id ...
    → FK constraint added: fk_users_product_id
  ✓ users.product_id added
  ... (6 tables total)

Step 4/6 — Creating `product_credit_packs` table (IF NOT EXISTS)...
  ✓ product_credit_packs table ready

Step 5/6 — Seeding credit pack tiers (6 × 27 products)...
  ✓ 162 packs seeded, 0 already existed

Step 6/6 — Creating indexes...
  + Created index: idx_users_product_id
  + Created index: idx_skills_product_id
  ... (7 indexes total)

✅ Migration 001 complete. All steps ran successfully.

Summary:
  · products table:             27 products
  · product_credit_packs table: 162 rows (6 tiers × 27 products)
  · product_id column added to: users, skills, categories, usage_logs, licenses, credit_transactions
  · Indexes created on:         all product_id columns
```

#### Expected output (second run — fully idempotent)

```
Step 1/6 — Creating `products` table (IF NOT EXISTS)...
  ✓ products table ready
Step 2/6 — Seeding 27 products...
  · Already exists: law
  · Already exists: contractor
  ... (all 27 skipped)
  ✓ 0 new products seeded (27 already existed)
Step 3/6 — Adding product_id column to tables...
  · users.product_id already exists — skipping
  ... (all 6 skipped)
...
✅ Migration 001 complete. All steps ran successfully.
```

---

### How to Rollback

The rollback removes everything this migration added. It does **not** touch any user data, skill data, or other existing tables.

```bash
DATABASE_URL="postgresql://user:password@host:5432/lawtasksai" \
  python migrations/001_add_multitenant.py rollback
```

**What rollback removes:**

1. All 7 indexes on `product_id` columns  
2. `product_id` column (and its FK constraint) from: `users`, `skills`, `categories`, `usage_logs`, `licenses`, `credit_transactions`  
3. `product_credit_packs` table (entire table)  
4. `products` table (entire table)

**What rollback preserves:**

- All user accounts, passwords, license keys
- All skills and skill versions
- All usage logs and credit transactions
- All other tables and columns

---

### Product IDs — All 27 Verticals

These are the canonical `product_id` values used throughout the system.
All existing data is tagged `law`. New verticals use their slug below.

| # | product_id | Product Name | Domain |
|---|-----------|--------------|--------|
| 1 | `law` | LawTasksAI | lawtasksai.com |
| 2 | `contractor` | ContractorTasksAI | contractortasksai.com |
| 3 | `realtor` | RealtorTasksAI | realtortasksai.com |
| 4 | `mortgage` | MortgageTasksAI | mortgagetasksai.com |
| 5 | `insurance` | InsuranceTasksAI | insurancetasksai.com |
| 6 | `hr` | HRTasksAI | hrtasksai.com |
| 7 | `accounting` | AccountingTasksAI | accountingtasksai.com |
| 8 | `chiropractor` | ChiropractorTasksAI | chiropractortasksai.com |
| 9 | `vet` | VetTasksAI | vettasksai.com |
| 10 | `dentist` | DentistTasksAI | dentisttasksai.com |
| 11 | `plumber` | PlumberTasksAI | plumbertasksai.com |
| 12 | `landlord` | LandlordTasksAI | landlordtasksai.com |
| 13 | `nutritionist` | NutritionistTasksAI | nutritionisttasksai.com |
| 14 | `personaltrainer` | PersonalTrainerTasksAI | personaltrainertasksai.com |
| 15 | `therapist` | TherapistTasksAI | therapisttasksai.com |
| 16 | `eventplanner` | EventPlannerTasksAI | eventplannertasksai.com |
| 17 | `travelagent` | TravelAgentTasksAI | travelagenttasksai.com |
| 18 | `funeral` | FuneralTasksAI | funeraltasksai.com |
| 19 | `pastor` | PastorTasksAI | pastortasksai.com |
| 20 | `principal` | PrincipalTasksAI | principaltasksai.com |
| 21 | `farmer` | FarmerTasksAI | farmertasksai.com |
| 22 | `restaurant` | RestaurantTasksAI | restauranttasksai.com |
| 23 | `salon` | SalonTasksAI | salontasksai.com |
| 24 | `morticiary` | MorticianTasksAI | morticiantasksai.com |
| 25 | `churchadmin` | ChurchAdminTasksAI | churchadmintasksai.com |
| 26 | `militaryspouse` | MilitarySpouseTasksAI | militaryspousetasksai.com |
| 27 | `electrician` | ElectricianTasksAI | electriciantasksai.com |

> **Note:** The `product_id` slug `morticiary` follows the spec as provided. The display name and domain use the corrected spelling `mortician`.

---

### Credit Pack Tiers (same for all 27 products)

| pack_key | Name | Credits | Price |
|----------|------|---------|-------|
| `starter` | Starter | 15 | $29 |
| `pro` | Pro | 60 | $99 |
| `business` | Business | 150 | $199 |
| `power` | Power | 350 | $349 |
| `unlimited` | Unlimited | 800 | $599 |
| `enterprise` | Enterprise | 2,000 | $999 |

Individual products can override pricing at any time by updating their rows in `product_credit_packs`.

---

### Tables Modified

| Table | New Column | FK | Index |
|-------|------------|----|-------|
| `users` | `product_id VARCHAR(50) DEFAULT 'law'` | → `products.id` | `idx_users_product_id` |
| `skills` | `product_id VARCHAR(50) DEFAULT 'law'` | → `products.id` | `idx_skills_product_id` |
| `categories` | `product_id VARCHAR(50) DEFAULT 'law'` | → `products.id` | `idx_categories_product_id` |
| `usage_logs` | `product_id VARCHAR(50) DEFAULT 'law'` | → `products.id` | `idx_usage_logs_product_id` + `idx_usage_logs_product_executed` |
| `licenses` | `product_id VARCHAR(50) DEFAULT 'law'` | → `products.id` | `idx_licenses_product_id` |
| `credit_transactions` | `product_id VARCHAR(50) DEFAULT 'law'` | → `products.id` | `idx_credit_transactions_product_id` |

### New Tables Created

| Table | Purpose |
|-------|---------|
| `products` | Registry of all 27 verticals with domain, URL, and Stripe config |
| `product_credit_packs` | Per-product credit tier definitions (replaces hardcoded `CREDIT_PACKS` dict) |

---

### Testing Against a Staging Database First

Strongly recommended before running on production:

```bash
# 1. Create a copy of your prod DB (or use staging)
# 2. Run migration against staging
DATABASE_URL="postgresql://user:pass@staging-host/lawtasksai_staging" \
  python migrations/001_add_multitenant.py

# 3. Verify with psql
psql $STAGING_DATABASE_URL -c "SELECT COUNT(*) FROM products;"
# → 27

psql $STAGING_DATABASE_URL -c "SELECT COUNT(*) FROM product_credit_packs;"
# → 162

psql $STAGING_DATABASE_URL -c "\d users" | grep product_id
# → product_id | character varying(50) | not null default 'law'

# 4. Verify existing data untouched
psql $STAGING_DATABASE_URL -c "SELECT COUNT(*) FROM users WHERE product_id = 'law';"
# → (your existing user count)

# 5. Run rollback to confirm clean reversal
DATABASE_URL="postgresql://user:pass@staging-host/lawtasksai_staging" \
  python migrations/001_add_multitenant.py rollback

# 6. Run migration again to confirm idempotency
DATABASE_URL="postgresql://user:pass@staging-host/lawtasksai_staging" \
  python migrations/001_add_multitenant.py
```

---

### After the Migration — Next Steps

These are **not** part of this script (they modify `main.py`):

1. Add the `Product` SQLAlchemy model to `main.py`
2. Add `product_id` fields to the `User`, `Skill`, `Category`, `License`, `UsageLog`, `CreditTransaction` models
3. Add `get_product_id()` request dependency (resolves from `X-Product-ID` header or host)
4. Update `list_skills`, `register`, `login`, `stripe_webhook` to filter by `product_id`
5. Replace hardcoded `CREDIT_PACKS` dict with a `get_credit_packs(product_id, db)` DB lookup

See `ARCHITECTURE.md` in the project vault for full implementation details.
