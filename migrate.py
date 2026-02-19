"""
Migration script: Copy all data from Supabase PostgreSQL to Cloud SQL PostgreSQL.
"""
import asyncio
import asyncpg
import os

SOURCE_URL = os.getenv("SOURCE_DATABASE_URL")
TARGET_URL = os.getenv("DATABASE_URL")

# Tables in dependency order
TABLES = [
    "categories",
    "users", 
    "skills",
    "skill_versions",
    "licenses",
    "usage_logs",
    "credit_transactions",
]

async def migrate():
    print("Connecting to source (Supabase)...")
    src = await asyncpg.connect(SOURCE_URL, timeout=15)
    print("Connected to Supabase!")
    
    print("Connecting to target (Cloud SQL)...")
    tgt = await asyncpg.connect(TARGET_URL, timeout=15)
    print("Connected to Cloud SQL!")
    
    # Add missing columns
    print("Adding missing columns...")
    for stmt in [
        "ALTER TABLE skill_versions ADD COLUMN IF NOT EXISTS deprecated_at TIMESTAMP",
        "ALTER TABLE usage_logs ADD COLUMN IF NOT EXISTS request_hash TEXT",
    ]:
        try:
            await tgt.execute(stmt)
            print(f"  OK: {stmt[:60]}")
        except Exception as e:
            print(f"  Skip: {e}")
    
    # Truncate all tables in reverse order (to handle FK constraints)
    print("\nClearing target tables...")
    for table in reversed(TABLES):
        try:
            await tgt.execute(f'TRUNCATE "{table}" CASCADE')
            print(f"  Truncated {table}")
        except Exception as e:
            print(f"  {table}: {e}")
    
    # Migrate each table
    print("\nMigrating data...")
    for table in TABLES:
        try:
            exists = await src.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=$1)",
                table
            )
            if not exists:
                print(f"  {table}: skipped (not in source)")
                continue
            
            count = await src.fetchval(f'SELECT count(*) FROM "{table}"')
            print(f"  {table}: {count} rows")
            
            if count == 0:
                continue
            
            rows = await src.fetch(f'SELECT * FROM "{table}"')
            
            # Get target columns
            tgt_cols = await tgt.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name=$1 AND table_schema='public'",
                table
            )
            tgt_col_names = {r['column_name'] for r in tgt_cols}
            
            # Only use columns that exist in both source and target
            src_cols = list(rows[0].keys())
            cols = [c for c in src_cols if c in tgt_col_names]
            
            col_names = ", ".join(f'"{c}"' for c in cols)
            placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
            
            inserted = 0
            errors = set()
            for row in rows:
                values = [row[c] for c in cols]
                try:
                    await tgt.execute(
                        f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders})',
                        *values
                    )
                    inserted += 1
                except Exception as e:
                    err = str(e)[:80]
                    if err not in errors:
                        errors.add(err)
                        print(f"    Error: {err}")
            
            print(f"    Inserted {inserted}/{count}")
            
        except Exception as e:
            print(f"  {table}: ERROR - {e}")
    
    # Reset sequences
    print("\nResetting sequences...")
    for table in TABLES:
        try:
            seq_query = await tgt.fetch("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name=$1 AND column_default LIKE 'nextval%'
            """, table)
            for seq_row in seq_query:
                col = seq_row['column_name']
                await tgt.execute(f"""
                    SELECT setval(pg_get_serial_sequence('"{table}"', '{col}'), 
                           COALESCE((SELECT MAX("{col}") FROM "{table}"), 1))
                """)
                print(f"  Reset {table}.{col}")
        except:
            pass
    
    await src.close()
    await tgt.close()
    print("\nMigration complete!")

if __name__ == "__main__":
    asyncio.run(migrate())
