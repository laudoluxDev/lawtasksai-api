"""
Migration 002: Add skill_abbreviations table
=============================================
Safe to run against a live production database. Fully idempotent.

What this does:
  1. Creates the `skill_abbreviations` table scoped per vertical
  2. Seeds all abbreviations from server.py _ABBREVS into the DB
  3. Creates indexes for fast per-vertical lookups

Rollback: call rollback() -- drops table and indexes.

Usage:
    DATABASE_URL="postgresql://..." python migrations/002_add_abbreviations.py
    DATABASE_URL="postgresql://..." python migrations/002_add_abbreviations.py rollback
"""

import asyncio
import os
import sys

import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL", "")


def _normalize_dsn(url: str) -> str:
    for prefix in ("postgresql+asyncpg://", "postgres+asyncpg://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix):]
    return url


# ---------------------------------------------------------------------------
# Seed data -- sourced from _ABBREVS in server.py
# ---------------------------------------------------------------------------

ABBREVIATIONS = {
    "law": {
        "mtc":   "motion to compel",
        "rogs":  "interrogatories",
        "rog":   "interrogatory",
        "rfa":   "request for admission",
        "rfas":  "requests for admission",
        "rfp":   "request for production",
        "rfps":  "requests for production",
        "tro":   "temporary restraining order",
        "pi":    "personal injury",
        "msj":   "motion for summary judgment",
        "msk":   "motion to strike",
        "sj":    "summary judgment",
        "jnov":  "judgment notwithstanding verdict",
        "mil":   "motion in limine",
        "sol":   "statute of limitations",
        "aff":   "affidavit",
        "decl":  "declaration",
        "depo":  "deposition",
        "deps":  "depositions",
        "frcp":  "federal rules civil procedure",
        "fre":   "federal rules evidence",
        "compl": "complaint",
        "ans":   "answer",
        "roe":   "rules of evidence",
        "atty":  "attorney",
    },
    "realtor": {
        "mls":   "multiple listing service",
        "cma":   "comparative market analysis",
        "dom":   "days on market",
        "arv":   "after repair value",
        "hoa":   "homeowners association",
        "coe":   "close of escrow",
        "emd":   "earnest money deposit",
        "piti":  "principal interest taxes insurance",
        "ltv":   "loan to value",
        "nar":   "national association of realtors",
        "bom":   "back on market",
        "uc":    "under contract",
        "fs":    "for sale",
        "fsbo":  "for sale by owner",
        "reo":   "real estate owned",
    },
    "contractor": {
        "rfi":   "request for information",
        "sow":   "scope of work",
        "co":    "change order",
        "gc":    "general contractor",
        "ntp":   "notice to proceed",
        "pco":   "potential change order",
        "aia":   "american institute of architects",
        "lien":  "mechanics lien",
        "sub":   "subcontractor",
        "por":   "purchase order request",
        "cos":   "certificate of substantial completion",
        "punch": "punch list",
        "g702":  "payment application",
        "g703":  "schedule of values",
    },
    "farmer": {
        "fsa":   "farm service agency",
        "nrcs":  "natural resources conservation service",
        "crp":   "conservation reserve program",
        "arc":   "agriculture risk coverage",
        "plc":   "price loss coverage",
        "usda":  "united states department of agriculture",
        "eqip":  "environmental quality incentives program",
        "csa":   "community supported agriculture",
        "gmp":   "good manufacturing practices",
        "gap":   "good agricultural practices",
    },
    "hr": {
        "pip":   "performance improvement plan",
        "pto":   "paid time off",
        "fmla":  "family medical leave act",
        "ada":   "americans with disabilities act",
        "eeoc":  "equal employment opportunity commission",
        "w2":    "wage and tax statement",
        "i9":    "employment eligibility verification",
        "cobra": "consolidated omnibus budget reconciliation act",
        "osha":  "occupational safety and health administration",
        "erp":   "employee relations policy",
    },
    "accounting": {
        "cogs":    "cost of goods sold",
        "ar":      "accounts receivable",
        "ap":      "accounts payable",
        "gaap":    "generally accepted accounting principles",
        "ytd":     "year to date",
        "mtd":     "month to date",
        "ebitda":  "earnings before interest taxes depreciation amortization",
        "cpa":     "certified public accountant",
        "sox":     "sarbanes oxley",
    },
    "mortgage": {
        "ltv":    "loan to value",
        "dti":    "debt to income",
        "arm":    "adjustable rate mortgage",
        "apr":    "annual percentage rate",
        "pmi":    "private mortgage insurance",
        "hud":    "housing and urban development",
        "fnma":   "fannie mae",
        "fhlmc":  "freddie mac",
        "heloc":  "home equity line of credit",
        "gfe":    "good faith estimate",
        "cd":     "closing disclosure",
        "le":     "loan estimate",
    },
    "insurance": {
        "doi":   "department of insurance",
        "gl":    "general liability",
        "wc":    "workers compensation",
        "coi":   "certificate of insurance",
        "dec":   "declarations page",
        "aob":   "assignment of benefits",
        "uwi":   "underwriting information",
        "clue":  "comprehensive loss underwriting exchange",
        "pip":   "personal injury protection",
    },
    "therapist": {
        "dap":   "data assessment plan",
        "soap":  "subjective objective assessment plan",
        "hipaa": "health insurance portability and accountability act",
        "phi":   "protected health information",
        "dx":    "diagnosis",
        "tx":    "treatment",
        "iop":   "intensive outpatient program",
        "php":   "partial hospitalization program",
        "cbt":   "cognitive behavioral therapy",
        "dbt":   "dialectical behavior therapy",
        "emdr":  "eye movement desensitization reprocessing",
    },
    "chiropractor": {
        "soap":  "subjective objective assessment plan",
        "rom":   "range of motion",
        "pi":    "personal injury",
        "hipaa": "health insurance portability and accountability act",
        "icd":   "international classification of diseases",
        "cpt":   "current procedural terminology",
        "eob":   "explanation of benefits",
    },
    "dentist": {
        "hipaa": "health insurance portability and accountability act",
        "cdt":   "current dental terminology",
        "perio": "periodontal",
        "ortho": "orthodontic",
        "endo":  "endodontic",
        "eob":   "explanation of benefits",
        "pano":  "panoramic radiograph",
    },
    "teacher": {
        "iep":   "individualized education program",
        "504":   "section 504 accommodation plan",
        "ell":   "english language learner",
        "sped":  "special education",
        "pbis":  "positive behavioral interventions and supports",
        "mtss":  "multi-tiered system of supports",
        "rti":   "response to intervention",
        "ferpa": "family educational rights and privacy act",
        "pd":    "professional development",
        "plc":   "professional learning community",
    },
    "vet": {
        "soap":  "subjective objective assessment plan",
        "avma":  "american veterinary medical association",
        "rx":    "prescription",
        "dx":    "diagnosis",
        "tx":    "treatment",
        "hx":    "history",
        "pe":    "physical examination",
    },
    "electrician": {
        "nec":   "national electrical code",
        "gfci":  "ground fault circuit interrupter",
        "afci":  "arc fault circuit interrupter",
        "rfi":   "request for information",
        "co":    "change order",
        "ntp":   "notice to proceed",
    },
    "plumber": {
        "ipc":   "international plumbing code",
        "upc":   "uniform plumbing code",
        "rfi":   "request for information",
        "co":    "change order",
        "ntp":   "notice to proceed",
        "pex":   "cross-linked polyethylene",
        "abs":   "acrylonitrile butadiene styrene",
    },
}


async def table_exists(conn, table: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=$1", table
    )
    return row is not None


async def index_exists(conn, index_name: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM pg_indexes WHERE schemaname='public' AND indexname=$1",
        index_name,
    )
    return row is not None


async def migrate(conn):
    print("\n=== TasksAI Migration 002: skill_abbreviations ===\n")

    print("Step 1/3 -- Creating skill_abbreviations table...")
    if await table_exists(conn, "skill_abbreviations"):
        print("  . Table already exists -- skipping creation")
    else:
        await conn.execute("""
            CREATE TABLE skill_abbreviations (
                id           SERIAL PRIMARY KEY,
                product_id   TEXT NOT NULL,
                abbreviation TEXT NOT NULL,
                expansion    TEXT NOT NULL,
                created_at   TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (product_id, abbreviation)
            )
        """)
        print("  + skill_abbreviations table created")

    print("\nStep 2/3 -- Creating indexes...")
    if not await index_exists(conn, "idx_abbrev_product"):
        await conn.execute(
            "CREATE INDEX idx_abbrev_product ON skill_abbreviations(product_id)"
        )
        print("  + idx_abbrev_product created")
    else:
        print("  . idx_abbrev_product already exists")

    print("\nStep 3/3 -- Seeding abbreviations...")
    total_inserted = 0
    total_skipped  = 0
    for product_id, abbrevs in ABBREVIATIONS.items():
        inserted = 0
        skipped  = 0
        for abbr, expansion in abbrevs.items():
            result = await conn.execute("""
                INSERT INTO skill_abbreviations (product_id, abbreviation, expansion)
                VALUES ($1, $2, $3)
                ON CONFLICT (product_id, abbreviation) DO NOTHING
            """, product_id, abbr.lower(), expansion.lower())
            n = int(result.split()[-1])
            inserted += n
            skipped  += (1 - n)
        print(f"  {product_id:20s} {inserted} inserted, {skipped} skipped")
        total_inserted += inserted
        total_skipped  += skipped

    total_rows = await conn.fetchval("SELECT COUNT(*) FROM skill_abbreviations")
    print(f"\nMigration 002 complete.")
    print(f"  Total: {total_inserted} inserted, {total_skipped} skipped")
    print(f"  Rows in skill_abbreviations: {total_rows}\n")


async def rollback(conn):
    print("\n=== Rollback: Migration 002 ===\n")
    await conn.execute("DROP INDEX IF EXISTS idx_abbrev_product")
    print("  . Dropped index idx_abbrev_product")
    await conn.execute("DROP TABLE IF EXISTS skill_abbreviations")
    print("  . Dropped table skill_abbreviations")
    print("\nRollback complete.\n")


async def main():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL environment variable is not set.")
        sys.exit(1)
    dsn  = _normalize_dsn(DATABASE_URL)
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "migrate"
    if mode not in ("migrate", "rollback"):
        print(f"ERROR: Unknown mode '{mode}'. Use 'migrate' or 'rollback'.")
        sys.exit(1)

    print("Connecting to database...")
    try:
        conn = await asyncpg.connect(dsn)
    except Exception as e:
        print(f"ERROR: Could not connect: {e}")
        sys.exit(1)

    try:
        async with conn.transaction():
            if mode == "rollback":
                await rollback(conn)
            else:
                await migrate(conn)
    except Exception as e:
        print(f"\nERROR during {mode}: {e}")
        print("Transaction rolled back. Database unchanged.")
        sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
