"""
Migration 001: Add multi-tenant product_id columns
====================================================
Safe to run against a live production database. Fully idempotent — running
twice does nothing harmful.

What this does:
  1. Creates the `products` table and seeds all 27 verticals (law + 26 new)
  2. Creates the `product_credit_packs` table and seeds 6 tiers × 27 products
  3. Adds `product_id VARCHAR(50) DEFAULT 'law'` to:
       users, skills, categories, usage_logs, licenses, credit_transactions
  4. Adds a FK constraint (products.id) on each new column
  5. Creates indexes on each new column for query performance

Rollback: call rollback() — drops new columns, new tables, new indexes.
          Existing data is untouched (columns default to 'law').

Usage:
    DATABASE_URL="postgresql://user:pass@host/db" python migrations/001_add_multitenant.py
    DATABASE_URL="postgresql://user:pass@host/db" python migrations/001_add_multitenant.py rollback
"""

import asyncio
import os
import sys

import asyncpg

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL", "")

# asyncpg uses postgresql:// or postgres:// (not postgresql+asyncpg://)
# Strip SQLAlchemy driver prefix if present.
def _normalize_dsn(url: str) -> str:
    for prefix in ("postgresql+asyncpg://", "postgres+asyncpg://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix):]
    return url


# ---------------------------------------------------------------------------
# Product registry — 27 verticals
# ---------------------------------------------------------------------------

# (product_id, name, display_name, domain, api_base_url, frontend_url)
PRODUCTS = [
    ("law",            "LawTasksAI",            "Law Tasks AI",             "lawtasksai.com",            "https://api.lawtasksai.com",            "https://lawtasksai.com"),
    ("contractor",     "ContractorTasksAI",      "Contractor Tasks AI",      "contractortasksai.com",     "https://api.contractortasksai.com",     "https://contractortasksai.com"),
    ("realtor",        "RealtorTasksAI",         "Realtor Tasks AI",         "realtortasksai.com",        "https://api.realtortasksai.com",        "https://realtortasksai.com"),
    ("mortgage",       "MortgageTasksAI",        "Mortgage Tasks AI",        "mortgagetasksai.com",       "https://api.mortgagetasksai.com",       "https://mortgagetasksai.com"),
    ("insurance",      "InsuranceTasksAI",       "Insurance Tasks AI",       "insurancetasksai.com",      "https://api.insurancetasksai.com",      "https://insurancetasksai.com"),
    ("hr",             "HRTasksAI",              "HR Tasks AI",              "hrtasksai.com",             "https://api.hrtasksai.com",             "https://hrtasksai.com"),
    ("accounting",     "AccountingTasksAI",      "Accounting Tasks AI",      "accountingtasksai.com",     "https://api.accountingtasksai.com",     "https://accountingtasksai.com"),
    ("chiropractor",   "ChiropractorTasksAI",    "Chiropractor Tasks AI",    "chiropractortasksai.com",   "https://api.chiropractortasksai.com",   "https://chiropractortasksai.com"),
    ("vet",            "VetTasksAI",             "Vet Tasks AI",             "vettasksai.com",            "https://api.vettasksai.com",            "https://vettasksai.com"),
    ("dentist",        "DentistTasksAI",         "Dentist Tasks AI",         "dentisttasksai.com",        "https://api.dentisttasksai.com",        "https://dentisttasksai.com"),
    ("plumber",        "PlumberTasksAI",         "Plumber Tasks AI",         "plumbertasksai.com",        "https://api.plumbertasksai.com",        "https://plumbertasksai.com"),
    ("landlord",       "LandlordTasksAI",        "Landlord Tasks AI",        "landlordtasksai.com",       "https://api.landlordtasksai.com",       "https://landlordtasksai.com"),
    ("nutritionist",   "NutritionistTasksAI",    "Nutritionist Tasks AI",    "nutritioniisttasksai.com",  "https://api.nutritionisttasksai.com",   "https://nutritionisttasksai.com"),
    ("personaltrainer","PersonalTrainerTasksAI", "Personal Trainer Tasks AI","personaltrainertasksai.com","https://api.personaltrainertasksai.com","https://personaltrainertasksai.com"),
    ("therapist",      "TherapistTasksAI",       "Therapist Tasks AI",       "therapisttasksai.com",      "https://api.therapisttasksai.com",      "https://therapisttasksai.com"),
    ("eventplanner",   "EventPlannerTasksAI",    "Event Planner Tasks AI",   "eventplannertasksai.com",   "https://api.eventplannertasksai.com",   "https://eventplannertasksai.com"),
    ("travelagent",    "TravelAgentTasksAI",     "Travel Agent Tasks AI",    "travelagenttasksai.com",    "https://api.travelagenttasksai.com",    "https://travelagenttasksai.com"),
    ("funeral",        "FuneralTasksAI",         "Funeral Tasks AI",         "funeraltasksai.com",        "https://api.funeraltasksai.com",        "https://funeraltasksai.com"),
    ("pastor",         "PastorTasksAI",          "Pastor Tasks AI",          "pastortasksai.com",         "https://api.pastortasksai.com",         "https://pastortasksai.com"),
    ("principal",      "PrincipalTasksAI",       "Principal Tasks AI",       "principaltasksai.com",      "https://api.principaltasksai.com",      "https://principaltasksai.com"),
    ("farmer",         "FarmerTasksAI",          "Farmer Tasks AI",          "farmertasksai.com",         "https://api.farmertasksai.com",         "https://farmertasksai.com"),
    ("restaurant",     "RestaurantTasksAI",      "Restaurant Tasks AI",      "restauranttasksai.com",     "https://api.restauranttasksai.com",     "https://restauranttasksai.com"),
    ("salon",          "SalonTasksAI",           "Salon Tasks AI",           "salontasksai.com",          "https://api.salontasksai.com",          "https://salontasksai.com"),
    ("mortician",     "MorticianTasksAI",       "Mortician Tasks AI",       "morticiantasksai.com",      "https://api.morticiantasksai.com",      "https://morticiantasksai.com"),
    ("churchadmin",    "ChurchAdminTasksAI",     "Church Admin Tasks AI",    "churchadmintasksai.com",    "https://api.churchadmintasksai.com",    "https://churchadmintasksai.com"),
    ("militaryspouse", "MilitarySpouseTasksAI",  "Military Spouse Tasks AI", "militaryspousetasksai.com", "https://api.militaryspousetasksai.com", "https://militaryspousetasksai.com"),
    ("electrician",    "ElectricianTasksAI",     "Electrician Tasks AI",     "electriciantasksai.com",    "https://api.electriciantasksai.com",    "https://electriciantasksai.com"),
]

# ---------------------------------------------------------------------------
# Credit pack tiers (6 tiers, same for all 27 products)
# (pack_key, name, credits, price_cents, display_order)
# ---------------------------------------------------------------------------

CREDIT_TIERS = [
    ("starter",    "Starter",    15,   2900, 1),
    ("pro",        "Pro",        60,   9900, 2),
    ("business",   "Business",  150,  19900, 3),
    ("power",      "Power",     350,  34900, 4),
    ("unlimited",  "Unlimited", 800,  59900, 5),
    ("enterprise", "Enterprise",2000, 99900, 6),
]

# ---------------------------------------------------------------------------
# Tables that get product_id added
# ---------------------------------------------------------------------------

TABLES_TO_ALTER = [
    "users",
    "skills",
    "categories",
    "usage_logs",
    "licenses",
    "credit_transactions",
]

# ---------------------------------------------------------------------------
# Helper: check if a column already exists
# ---------------------------------------------------------------------------

async def column_exists(conn, table: str, column: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = $1
          AND column_name  = $2
        """,
        table,
        column,
    )
    return row is not None


async def index_exists(conn, index_name: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM pg_indexes WHERE schemaname='public' AND indexname=$1",
        index_name,
    )
    return row is not None


async def constraint_exists(conn, table: str, constraint_name: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT 1
        FROM information_schema.table_constraints
        WHERE table_schema     = 'public'
          AND table_name       = $1
          AND constraint_name  = $2
        """,
        table,
        constraint_name,
    )
    return row is not None


# ---------------------------------------------------------------------------
# Forward migration
# ---------------------------------------------------------------------------

async def migrate(conn):
    print("\n=== LawTasksAI Multi-Tenant Migration 001 ===\n")

    # ------------------------------------------------------------------
    # STEP 1: Create `products` table
    # ------------------------------------------------------------------
    print("Step 1/6 — Creating `products` table (IF NOT EXISTS)...")
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id                       VARCHAR(50)  PRIMARY KEY,
            name                     VARCHAR(200) NOT NULL,
            display_name             VARCHAR(200) NOT NULL,
            domain                   VARCHAR(255),
            api_base_url             VARCHAR(255),
            frontend_url             VARCHAR(255),
            stripe_webhook_secret    VARCHAR(255),
            stripe_publishable_key   VARCHAR(255),
            anthropic_system_prompt  TEXT,
            is_active                BOOLEAN      DEFAULT TRUE,
            created_at               TIMESTAMP    DEFAULT NOW()
        )
        """
    )
    print("  ✓ products table ready")

    # ------------------------------------------------------------------
    # STEP 2: Seed all 27 products (ON CONFLICT DO NOTHING = idempotent)
    # ------------------------------------------------------------------
    print("\nStep 2/6 — Seeding 27 products...")
    seeded = 0
    for prod in PRODUCTS:
        product_id, name, display_name, domain, api_base_url, frontend_url = prod
        result = await conn.execute(
            """
            INSERT INTO products
                (id, name, display_name, domain, api_base_url, frontend_url, is_active)
            VALUES ($1, $2, $3, $4, $5, $6, TRUE)
            ON CONFLICT (id) DO NOTHING
            """,
            product_id, name, display_name, domain, api_base_url, frontend_url,
        )
        # asyncpg returns "INSERT 0 N" — parse N
        inserted = int(result.split()[-1])
        if inserted:
            seeded += 1
            print(f"  + Seeded: {product_id} ({name})")
        else:
            print(f"  · Already exists: {product_id}")
    print(f"  ✓ {seeded} new products seeded ({len(PRODUCTS) - seeded} already existed)")

    # ------------------------------------------------------------------
    # STEP 3: Add product_id column to all 6 tables
    # ------------------------------------------------------------------
    print("\nStep 3/6 — Adding product_id column to tables...")
    for table in TABLES_TO_ALTER:
        if await column_exists(conn, table, "product_id"):
            print(f"  · {table}.product_id already exists — skipping")
            continue

        print(f"  + Adding {table}.product_id ...")

        # Add column with DEFAULT 'law' (no FK yet — products table now exists)
        await conn.execute(
            f"""
            ALTER TABLE {table}
                ADD COLUMN product_id VARCHAR(50) NOT NULL DEFAULT 'law'
            """
        )

        # Add FK constraint separately (idempotent pattern: check first)
        fk_name = f"fk_{table}_product_id"
        if not await constraint_exists(conn, table, fk_name):
            await conn.execute(
                f"""
                ALTER TABLE {table}
                    ADD CONSTRAINT {fk_name}
                    FOREIGN KEY (product_id) REFERENCES products(id)
                """
            )
            print(f"    → FK constraint added: {fk_name}")
        else:
            print(f"    · FK constraint already exists: {fk_name}")

        print(f"  ✓ {table}.product_id added")

    # ------------------------------------------------------------------
    # STEP 4: Create `product_credit_packs` table
    # ------------------------------------------------------------------
    print("\nStep 4/6 — Creating `product_credit_packs` table (IF NOT EXISTS)...")
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS product_credit_packs (
            id              SERIAL       PRIMARY KEY,
            product_id      VARCHAR(50)  NOT NULL REFERENCES products(id),
            pack_key        VARCHAR(50)  NOT NULL,
            name            VARCHAR(100) NOT NULL,
            credits         INTEGER      NOT NULL,
            price_cents     INTEGER      NOT NULL,
            stripe_price_id VARCHAR(100),
            is_active       BOOLEAN      DEFAULT TRUE,
            display_order   INTEGER      DEFAULT 0,
            UNIQUE(product_id, pack_key)
        )
        """
    )
    print("  ✓ product_credit_packs table ready")

    # ------------------------------------------------------------------
    # STEP 5: Seed 6 tiers × 27 products = 162 rows
    # ------------------------------------------------------------------
    print("\nStep 5/6 — Seeding credit pack tiers (6 × 27 products)...")
    pack_seeded = 0
    pack_skipped = 0
    for product_id, _name, _display, _domain, _api, _fe in PRODUCTS:
        for pack_key, pack_name, credits, price_cents, display_order in CREDIT_TIERS:
            result = await conn.execute(
                """
                INSERT INTO product_credit_packs
                    (product_id, pack_key, name, credits, price_cents, display_order)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (product_id, pack_key) DO NOTHING
                """,
                product_id, pack_key, pack_name, credits, price_cents, display_order,
            )
            inserted = int(result.split()[-1])
            if inserted:
                pack_seeded += 1
            else:
                pack_skipped += 1
    print(f"  ✓ {pack_seeded} packs seeded, {pack_skipped} already existed")

    # ------------------------------------------------------------------
    # STEP 6: Create indexes on product_id columns
    # ------------------------------------------------------------------
    print("\nStep 6/6 — Creating indexes...")

    simple_indexes = [
        ("idx_users_product_id",              "users",              "product_id"),
        ("idx_skills_product_id",             "skills",             "product_id"),
        ("idx_categories_product_id",         "categories",         "product_id"),
        ("idx_usage_logs_product_id",         "usage_logs",         "product_id"),
        ("idx_licenses_product_id",           "licenses",           "product_id"),
        ("idx_credit_transactions_product_id","credit_transactions","product_id"),
    ]

    for idx_name, table, column in simple_indexes:
        if await index_exists(conn, idx_name):
            print(f"  · Index {idx_name} already exists")
        else:
            await conn.execute(
                f"CREATE INDEX {idx_name} ON {table}({column})"
            )
            print(f"  + Created index: {idx_name}")

    # Composite index for analytics queries on usage_logs
    composite_idx = "idx_usage_logs_product_executed"
    if await index_exists(conn, composite_idx):
        print(f"  · Index {composite_idx} already exists")
    else:
        await conn.execute(
            "CREATE INDEX idx_usage_logs_product_executed ON usage_logs(product_id, executed_at DESC)"
        )
        print(f"  + Created index: {composite_idx}")

    print("\n✅ Migration 001 complete. All steps ran successfully.\n")
    print("Summary:")
    print(f"  · products table:             27 products")
    print(f"  · product_credit_packs table: 162 rows (6 tiers × 27 products)")
    print(f"  · product_id column added to: {', '.join(TABLES_TO_ALTER)}")
    print(f"  · Indexes created on:         all product_id columns")
    print()


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

async def rollback(conn):
    print("\n=== Rollback: Migration 001 ===\n")
    print("WARNING: This will remove all product_id columns and product tables.")
    print("Existing row data in those columns will be lost.")
    print("No other data (users, skills, etc.) will be dropped.\n")

    # Drop indexes first
    print("Step 1/4 — Dropping indexes...")
    indexes = [
        "idx_users_product_id",
        "idx_skills_product_id",
        "idx_categories_product_id",
        "idx_usage_logs_product_id",
        "idx_usage_logs_product_executed",
        "idx_licenses_product_id",
        "idx_credit_transactions_product_id",
    ]
    for idx in indexes:
        await conn.execute(f"DROP INDEX IF EXISTS {idx}")
        print(f"  · Dropped index: {idx}")

    # Drop FK constraints then columns
    print("\nStep 2/4 — Dropping product_id columns (with FK constraints)...")
    for table in TABLES_TO_ALTER:
        fk_name = f"fk_{table}_product_id"
        # Drop FK constraint first
        if await constraint_exists(conn, table, fk_name):
            await conn.execute(
                f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {fk_name}"
            )
            print(f"  · Dropped FK: {fk_name}")
        # Drop column
        if await column_exists(conn, table, "product_id"):
            await conn.execute(
                f"ALTER TABLE {table} DROP COLUMN IF EXISTS product_id"
            )
            print(f"  · Dropped column: {table}.product_id")
        else:
            print(f"  · {table}.product_id didn't exist — skipping")

    # Drop product_credit_packs (no other tables reference it)
    print("\nStep 3/4 — Dropping product_credit_packs table...")
    await conn.execute("DROP TABLE IF EXISTS product_credit_packs")
    print("  · Dropped: product_credit_packs")

    # Drop products table
    print("\nStep 4/4 — Dropping products table...")
    await conn.execute("DROP TABLE IF EXISTS products")
    print("  · Dropped: products")

    print("\n✅ Rollback complete. Database is back to pre-migration state.\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    if not DATABASE_URL:
        print(
            "ERROR: DATABASE_URL environment variable is not set.\n"
            "Usage: DATABASE_URL='postgresql://user:pass@host/db' python migrations/001_add_multitenant.py [rollback]"
        )
        sys.exit(1)

    dsn = _normalize_dsn(DATABASE_URL)
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "migrate"

    if mode not in ("migrate", "rollback"):
        print(f"ERROR: Unknown mode '{mode}'. Use 'migrate' (default) or 'rollback'.")
        sys.exit(1)

    print(f"\nConnecting to database...")
    try:
        conn = await asyncpg.connect(dsn)
    except Exception as e:
        print(f"ERROR: Could not connect to database: {e}")
        sys.exit(1)

    try:
        async with conn.transaction():
            if mode == "rollback":
                await rollback(conn)
            else:
                await migrate(conn)
    except Exception as e:
        print(f"\nERROR during {mode}: {e}")
        print("Transaction rolled back. Database is unchanged.")
        sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
