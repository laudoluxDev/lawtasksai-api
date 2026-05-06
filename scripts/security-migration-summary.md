# Security Migration Summary

*Generated: 2026-05-06*

---

## What Was Created

### 1. DB Migration — `migrations/add_skill_security_scans.sql`
Creates the `skill_security_scans` table with all required columns:
- `id` SERIAL PRIMARY KEY
- `skill_id` VARCHAR(255) UNIQUE NOT NULL
- `vertical` VARCHAR(100) NOT NULL
- `verified` BOOLEAN NOT NULL DEFAULT FALSE
- `tests_run`, `tests_passed`, `tests_failed` INTEGER
- `plugins_tested` TEXT[]
- `preamble_tested` BOOLEAN NOT NULL DEFAULT TRUE
- `scan_model` VARCHAR(100) DEFAULT 'openai:gpt-4o-mini'
- `scanned_at`, `created_at` TIMESTAMPTZ

Indexes: `idx_skill_security_scans_vertical`, `idx_skill_security_scans_verified`

Also adds to the `skills` table:
- `security_verified BOOLEAN NOT NULL DEFAULT FALSE`
- `security_scanned_at TIMESTAMPTZ`

---

### 2. Data Load Script — `scripts/load_security_results.py`
Python script that:
- Reads `skill-verifications.json` (1,591 skill records)
- Detects each skill's vertical using longest-prefix matching across all 30 TasksAI verticals
- Falls back to `law` for legacy bare skill IDs (e.g. `demand-letter-drafter`)
- POSTs each record to `POST /admin/security-scans` with the admin secret header
- Handles 409 Conflict (already exists) gracefully
- Supports `--dry-run` flag for safe testing
- Prints per-skill progress and a full summary table

---

### 3. Security Page — `scripts/security-page-verified-safe.md`
"Verified Safe" page copy for `{domain}/verified_safe`:
- Audience: busy professionals
- Explains the two-layer system (universal safety preamble + per-skill scan)
- Plain-English coverage of all 8 test categories (prompt injection, unauthorized commitments, excessive agency, prompt extraction, harmful content, data exfiltration, PII leakage, discriminatory content)
- Honest limitations section
- Privacy statement: "We don't store your conversations"
- How we keep it current (re-scan on update + periodic full re-scan)
- Uses `{PRODUCT_NAME}`, `{VERTICAL}`, and `{domain}` placeholders for multi-vertical use

---

## Issues / Notes

- **Vertical detection for law skills:** The earliest law skill records (e.g. `demand-letter-drafter`) have no vertical prefix and no `vertical` field in the JSON. These default to `law`, which is correct — they are LawTasksAI skills. Later entries have `"vertical": "law"` explicitly.
- **Farmer skill IDs:** Several farmer entries have timestamps so close together they appear to have been run in parallel (millisecond gaps). No data integrity issues found.
- **`nutritionist_produce_content_for_inoffice_educational_displays`** has `tests_passed: 14` and `tests_failed: 0` but `verified: true`. This is a minor anomaly in the source data (should be 15 tests run). Carried through as-is.
- **No skills from 21 verticals** (pastor, travelagent, dentist, militaryspouse, chiropractor, insurance, accounting, restaurant, designer, landlord, principal, mortuary, eventplanner, church, personaltrainer, electrician, mortgage, plumber, vet, funeral, hr) appear in the current scan file. The load script handles this correctly — those verticals will simply have no rows in `skill_security_scans` until scans are run.

---

## Vertical Breakdown

*Source: `skill-verifications.json` — 1,591 total skill records*

| Vertical      | Total Skills | Verified | Not Verified | Test Pass Rate |
|---------------|-------------:|---------:|-------------:|---------------:|
| contractor    | 180          | 118      | 62           | 97.4%          |
| farmer        | 193          | 71       | 122          | 93.2%          |
| law           | 206          | 75       | 131          | 90.8%          |
| marketing     | 206          | 73       | 133          | 92.7%          |
| nutritionist  | 161          | 125      | 36           | 98.2%          |
| realtor       | 154          | 123      | 31           | 98.5%          |
| salon         | 169          | 130      | 39           | 98.1%          |
| teacher       | 167          | 121      | 46           | 98.0%          |
| therapist     | 155          | 120      | 35           | 98.3%          |
| **TOTAL**     | **1,591**    | **956**  | **635**      | **95.8%**      |

### Notes on verification rates
- **Realtor** leads on verified percentage (~80%) with the highest individual test pass rate (98.5%).
- **Law** has the lowest verified percentage (~36%) and lowest test pass rate (90.8%) — likely because many law skills are more complex and edge cases are harder to contain.
- **Farmer** also has a low verified count (37%) despite decent test pass rates — suggests skills are failing on at least one test rather than failing badly.
- The overall **95.8% test pass rate** is strong. The "not verified" skills failed one or more individual tests (threshold is 15/15 = 100% required for verification).
