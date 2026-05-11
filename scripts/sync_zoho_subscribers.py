#!/usr/bin/env python3
"""
One-time script: sync existing users from DB → Zoho Campaigns list.
Adds each user as a contact with their product_id custom field.
Also backfills email_subscriptions table for users who don't have a row yet.

Usage:
    DATABASE_URL=postgresql+asyncpg://... python3 scripts/sync_zoho_subscribers.py [--dry-run]
"""

import asyncio
import json
import os
import sys
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text

DATABASE_URL = os.getenv("DATABASE_URL", "")
DRY_RUN = "--dry-run" in sys.argv

CAMPAIGNS_TOKENS = json.load(open(os.path.expanduser("~/.config/zoho-campaigns-tokens.json")))
LIST_KEY = CAMPAIGNS_TOKENS["list_key"]


async def get_campaigns_token() -> str:
    r = httpx.post(
        "https://accounts.zoho.com/oauth/v2/token",
        data={
            "refresh_token": CAMPAIGNS_TOKENS["refresh_token"],
            "client_id": CAMPAIGNS_TOKENS["client_id"],
            "client_secret": CAMPAIGNS_TOKENS["client_secret"],
            "grant_type": "refresh_token"
        }
    )
    return r.json().get("access_token", "")


async def add_contact(token: str, email: str, name: str, product_id: str) -> bool:
    contact_info = json.dumps({
        "Contact Email": email,
        "First Name": (name or "").split()[0] if name else "",
        "CONTACT_CF1": product_id,
    })
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://campaigns.zoho.com/api/v1.1/listsubscribe",
            params={"resfmt": "JSON", "listkey": LIST_KEY, "contactinfo": contact_info},
            headers={"Authorization": f"Zoho-oauthtoken {token}"}
        )
        data = resp.json()
        ok = data.get("code") == "0" or "already" in data.get("message", "").lower()
        return ok


async def main():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    engine = create_async_engine(DATABASE_URL, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        # Fetch all users not yet in email_subscriptions
        result = await db.execute(text("""
            SELECT u.id, u.email, u.name, u.product_id
            FROM users u
            LEFT JOIN email_subscriptions es
                ON es.user_id = u.id AND es.product_id = u.product_id
            WHERE es.id IS NULL
            ORDER BY u.created_at
        """))
        users = result.fetchall()

    print(f"Found {len(users)} users without email_subscriptions rows")
    if DRY_RUN:
        for u in users[:10]:
            print(f"  [dry] {u.email} / {u.product_id}")
        print(f"  ... (dry run, no changes)")
        return

    token = await get_campaigns_token()
    if not token:
        print("ERROR: could not get Campaigns token")
        sys.exit(1)

    ok_count = 0
    fail_count = 0

    async with Session() as db:
        for i, u in enumerate(users):
            email = u.email
            name = u.name or ""
            product_id = u.product_id or "law"

            # Add to Zoho
            success = await add_contact(token, email, name, product_id)
            if success:
                ok_count += 1
            else:
                fail_count += 1
                print(f"  [warn] Zoho add failed for {email}")

            # Backfill email_subscriptions
            try:
                await db.execute(text("""
                    INSERT INTO email_subscriptions (user_id, product_id)
                    VALUES (:uid, :pid)
                    ON CONFLICT (user_id, product_id) DO NOTHING
                """), {"uid": str(u.id), "pid": product_id})
                if (i + 1) % 50 == 0:
                    await db.commit()
                    # Refresh token every 50 users (token lasts 1hr but be safe)
                    if i > 0 and i % 200 == 0:
                        token = await get_campaigns_token()
                    print(f"  ... {i+1}/{len(users)} processed")
            except Exception as e:
                print(f"  [err] DB insert failed for {email}: {e}")

        await db.commit()

    print(f"\nDone: {ok_count} added to Zoho, {fail_count} failed, {len(users)} DB rows backfilled")


if __name__ == "__main__":
    asyncio.run(main())
