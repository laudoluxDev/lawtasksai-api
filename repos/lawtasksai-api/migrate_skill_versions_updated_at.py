"""
Migration: Add updated_at column to skill_versions table.

Run once against production DB:
    DATABASE_URL=<your-url> python3 migrate_skill_versions_updated_at.py
"""
import asyncio
import asyncpg
import os

DATABASE_URL = os.environ["DATABASE_URL"]

async def migrate():
    print("Connecting to database...")
    conn = await asyncpg.connect(DATABASE_URL, timeout=15)
    print("Connected.")

    print("Adding updated_at column to skill_versions...")
    try:
        await conn.execute("""
            ALTER TABLE skill_versions
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;
        """)
        print("  OK: updated_at column added (or already existed).")
    except Exception as e:
        print(f"  ERROR: {e}")
        await conn.close()
        return

    # Backfill: set updated_at = published_at for all existing rows
    print("Backfilling updated_at from published_at for existing rows...")
    try:
        result = await conn.execute("""
            UPDATE skill_versions
            SET updated_at = published_at
            WHERE updated_at IS NULL;
        """)
        print(f"  OK: {result}")
    except Exception as e:
        print(f"  ERROR during backfill: {e}")

    await conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    asyncio.run(migrate())
