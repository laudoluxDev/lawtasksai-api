"""
Migration 004: Add platforms column to users table.
Stores the MCP client platforms a user has selected (e.g. ["claude_desktop", "openclaw"]).
"""

import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DATABASE_URL = os.getenv("DATABASE_URL", "").replace("postgresql://", "postgresql+asyncpg://")

async def run():
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        # Add platforms column if it doesn't exist
        await conn.execute(text("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS platforms JSONB DEFAULT '[]'::jsonb;
        """))
        print("✅ Migration 004: platforms column added to users table")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(run())
