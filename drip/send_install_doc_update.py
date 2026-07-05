#!/usr/bin/env python3
"""
One-off customer update: installer prompt + local document output.

Dry-run is the default. Use --send only after reviewing the preview and
recipient summary.

Examples:
  DATABASE_URL=postgresql+asyncpg://... python3 drip/send_install_doc_update.py
  DATABASE_URL=postgresql+asyncpg://... python3 drip/send_install_doc_update.py --product farmer
  DATABASE_URL=postgresql+asyncpg://... python3 drip/send_install_doc_update.py --send --test-email you@example.com --product farmer
  DATABASE_URL=postgresql+asyncpg://... python3 drip/send_install_doc_update.py --send
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import html
import json
import os
import sys
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


CAMPAIGN = "install-doc-output-update-2026-07"
CAMPAIGN_EMAIL_NUMBER = 105
SUBJECT = "New: easier install prompts and local saved documents"
BATCH_SIZE = 5
BATCH_PAUSE_SECONDS = 90
EMAIL_PAUSE_SECONDS = 8

REPO_ROOT = Path(__file__).resolve().parents[1]
VAULT_ROOT = REPO_ROOT.parents[1]
VERTICALS_FILE = VAULT_ROOT / "tasksai-repos" / "tasksai-landing-template" / "verticals.json"
ZOHO_TOKEN_FILE = Path.home() / ".config" / "zoho-mail-tokens.json"
DEFAULT_PREVIEW_FILE = Path("/tmp/tasksai-install-doc-update-preview.html")
DEFAULT_SUMMARY_FILE = Path("/tmp/tasksai-install-doc-update-summary.json")

INTERNAL_EMAIL_MARKERS = ("test", "internal", "mailinator", "example.com", "probe")


@dataclass
class Vertical:
    product_id: str
    name: str
    domain: str
    support_email: str
    accent_color: str
    skill_count: int


@dataclass
class Recipient:
    user_id: str | None
    email: str
    name: str
    product_id: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="Send email. Without this, preview only.")
    parser.add_argument("--product", action="append", dest="products", help="Limit to one product_id. Can be repeated.")
    parser.add_argument("--test-email", help="Send only a test/proof email to this address.")
    parser.add_argument("--limit", type=int, help="Limit eligible customer recipients.")
    parser.add_argument("--include-internal", action="store_true", help="Include obvious test/internal emails.")
    parser.add_argument("--preview-file", default=str(DEFAULT_PREVIEW_FILE), help="Where to write the first HTML preview.")
    parser.add_argument("--summary-file", default=str(DEFAULT_SUMMARY_FILE), help="Where to write the JSON summary.")
    return parser.parse_args()


def load_verticals() -> dict[str, Vertical]:
    raw = json.loads(VERTICALS_FILE.read_text())
    verticals = {
        item["product_id"]: Vertical(
            product_id=item["product_id"],
            name=item["name"],
            domain=item["domain"],
            support_email=item.get("support_email") or f"hello@{item['domain']}",
            accent_color=item.get("accent_color") or "#2563eb",
            skill_count=int(item.get("skill_count") or 0),
        )
        for item in raw
    }
    verticals["law"] = Vertical(
        product_id="law",
        name="LawTasksAI",
        domain="lawtasksai.com",
        support_email="hello@lawtasksai.com",
        accent_color="#2563eb",
        skill_count=206,
    )
    return verticals


def first_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        return "there"
    return cleaned.split()[0].title()


def is_internal_email(email: str) -> bool:
    lowered = (email or "").lower()
    return any(marker in lowered for marker in INTERNAL_EMAIL_MARKERS)


def encoded_sender_name(product_name: str) -> str:
    return f"=?UTF-8?B?{base64.b64encode(product_name.encode()).decode()}?="


def from_address(vertical: Vertical) -> str:
    return f"{encoded_sender_name(vertical.name)} <{vertical.support_email}>"


def install_url(vertical: Vertical) -> str:
    return (
        f"https://{vertical.domain}/install.html"
        f"?utm_source=email&utm_medium=broadcast&utm_campaign={CAMPAIGN}"
    )


def unsubscribe_url(vertical: Vertical, email: str) -> str:
    return f"https://{vertical.domain}/unsubscribe.html?email={urllib.parse.quote(email)}"


def build_html_email(vertical: Vertical, recipient: Recipient) -> str:
    safe_name = html.escape(first_name(recipient.name))
    product = html.escape(vertical.name)
    domain = html.escape(vertical.domain)
    support = html.escape(vertical.support_email)
    color = html.escape(vertical.accent_color)
    install = html.escape(install_url(vertical))
    unsub = html.escape(unsubscribe_url(vertical, recipient.email))

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(SUBJECT)}</title>
</head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:Arial,Helvetica,sans-serif;color:#1f2937;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f5f7;padding:32px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px;background:#ffffff;border-radius:8px;overflow:hidden;">
          <tr>
            <td style="background:{color};padding:28px 36px;text-align:center;">
              <div style="font-size:21px;line-height:1.3;font-weight:700;color:#ffffff;">{product}</div>
              <div style="font-size:13px;line-height:1.5;color:#ffffff;opacity:.9;margin-top:6px;">Easier setup and local saved documents</div>
            </td>
          </tr>
          <tr>
            <td style="padding:34px 36px 30px;">
              <p style="margin:0 0 16px;font-size:16px;line-height:1.65;color:#374151;">Hi {safe_name},</p>
              <p style="margin:0 0 16px;font-size:16px;line-height:1.65;color:#374151;">We updated {product} so setup is simpler, and your task results are easier to keep.</p>

              <h2 style="font-size:17px;line-height:1.35;margin:26px 0 10px;color:#111827;">1. The install prompt is now the main setup path</h2>
              <p style="margin:0 0 16px;font-size:15px;line-height:1.65;color:#4b5563;">Go to the install page, choose your AI app, copy the official GitHub install prompt, and paste it into your AI assistant. The assistant can connect your account through the browser and configure the supported client automatically.</p>

              <h2 style="font-size:17px;line-height:1.35;margin:26px 0 10px;color:#111827;">2. Skill runs can now save a local file</h2>
              <p style="margin:0 0 16px;font-size:15px;line-height:1.65;color:#4b5563;">When your AI app runs a {product} task, it can now save the result as a Word document or Markdown file in your local workspace. Headings, bullets, quotes, and tables are handled as document formatting instead of plain pasted text.</p>

              <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:16px 18px;margin:24px 0;">
                <p style="margin:0;font-size:14px;line-height:1.6;color:#374151;"><strong>Your data stays with your AI app.</strong> These files are created on your computer by your AI framework. They are not created on, uploaded to, or stored by {domain}.</p>
              </div>

              <table role="presentation" cellspacing="0" cellpadding="0" style="margin:28px 0 18px;">
                <tr>
                  <td style="border-radius:7px;background:{color};">
                    <a href="{install}" style="display:inline-block;padding:13px 22px;color:#ffffff;text-decoration:none;font-weight:700;font-size:15px;">Open the {product} install page</a>
                  </td>
                </tr>
              </table>

              <p style="margin:0 0 16px;font-size:15px;line-height:1.65;color:#4b5563;">After updating, you can check the connection by asking your AI assistant: <strong>Check my {product} balance.</strong></p>
              <p style="margin:0;font-size:15px;line-height:1.65;color:#4b5563;">Questions? Reply to this email or write to <a href="mailto:{support}" style="color:{color};">{support}</a>.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:22px 36px 28px;background:#f9fafb;border-top:1px solid #e5e7eb;">
              <p style="margin:0 0 10px;font-size:12px;line-height:1.55;color:#6b7280;"><strong>Why am I receiving this?</strong> You're receiving this because you created a {product} account or have an active {product} license.</p>
              <p style="margin:0;font-size:12px;line-height:1.55;color:#6b7280;"><a href="{unsub}" style="color:#6b7280;text-decoration:underline;">Unsubscribe from {product} emails</a>. Unsubscribing only stops marketing and update emails; it does not affect your account, credits, or license.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def build_text_email(vertical: Vertical, recipient: Recipient) -> str:
    install = install_url(vertical)
    unsub = unsubscribe_url(vertical, recipient.email)
    name = first_name(recipient.name)
    return f"""Hi {name},

We updated {vertical.name} so setup is simpler, and your task results are easier to keep.

1. The install prompt is now the main setup path
Go to the install page, choose your AI app, copy the official GitHub install prompt, and paste it into your AI assistant. The assistant can connect your account through the browser and configure the supported client automatically.

2. Skill runs can now save a local file
When your AI app runs a {vertical.name} task, it can now save the result as a Word document or Markdown file in your local workspace. Headings, bullets, quotes, and tables are handled as document formatting instead of plain pasted text.

Your data stays with your AI app. These files are created on your computer by your AI framework. They are not created on, uploaded to, or stored by {vertical.domain}.

Open the install page:
{install}

After updating, you can check the connection by asking your AI assistant:
Check my {vertical.name} balance.

Questions? Reply to this email or write to {vertical.support_email}.

Why am I receiving this?
You're receiving this because you created a {vertical.name} account or have an active {vertical.name} license.

Unsubscribe from {vertical.name} emails:
{unsub}

Unsubscribing only stops marketing and update emails; it does not affect your account, credits, or license.
"""


async def fetch_recipients(
    db: AsyncSession,
    products: set[str] | None,
    include_internal: bool,
    limit: int | None,
) -> tuple[list[Recipient], dict[str, int]]:
    product_filter = ""
    params: dict[str, object] = {"campaign_email_number": CAMPAIGN_EMAIL_NUMBER}
    if products:
        product_filter = "AND COALESCE(l.product_id, u.product_id, 'law') = ANY(:products)"
        params["products"] = sorted(products)
    limit_sql = ""
    if limit:
        limit_sql = "LIMIT :limit"
        params["limit"] = limit

    result = await db.execute(text(f"""
        WITH active_recipients AS (
            SELECT DISTINCT ON (LOWER(u.email), COALESCE(l.product_id, u.product_id, 'law'))
                u.id AS user_id,
                LOWER(u.email) AS email,
                COALESCE(u.name, '') AS name,
                COALESCE(l.product_id, u.product_id, 'law') AS product_id,
                COALESCE(es.subscribed, TRUE) AS subscribed,
                de.id IS NOT NULL AS already_sent,
                u.created_at AS created_at
            FROM users u
            JOIN licenses l
              ON l.user_id = u.id
             AND l.status = 'active'
            LEFT JOIN email_subscriptions es
              ON es.user_id = u.id
             AND es.product_id = COALESCE(l.product_id, u.product_id, 'law')
            LEFT JOIN drip_emails de
              ON LOWER(de.email) = LOWER(u.email)
             AND de.product_id = COALESCE(l.product_id, u.product_id, 'law')
             AND de.email_number = :campaign_email_number
            WHERE u.is_active = TRUE
              AND u.email IS NOT NULL
              {product_filter}
            ORDER BY LOWER(u.email), COALESCE(l.product_id, u.product_id, 'law'), l.created_at DESC
        )
        SELECT user_id, email, name, product_id, subscribed, already_sent
        FROM active_recipients
        ORDER BY product_id, created_at ASC
        {limit_sql}
    """), params)

    rows = result.fetchall()
    stats = {
        "active_license_rows": len(rows),
        "unsubscribed_skipped": 0,
        "already_sent_skipped": 0,
        "internal_skipped": 0,
        "eligible": 0,
    }

    recipients: list[Recipient] = []
    for row in rows:
        email = row.email
        if not row.subscribed:
            stats["unsubscribed_skipped"] += 1
            continue
        if row.already_sent:
            stats["already_sent_skipped"] += 1
            continue
        if not include_internal and is_internal_email(email):
            stats["internal_skipped"] += 1
            continue
        recipients.append(
            Recipient(
                user_id=str(row.user_id) if row.user_id else None,
                email=email,
                name=row.name or "",
                product_id=row.product_id or "law",
            )
        )
    stats["eligible"] = len(recipients)
    return recipients, stats


def proof_recipient(email: str, product_id: str) -> Recipient:
    return Recipient(user_id=None, email=email.strip().lower(), name="Kent", product_id=product_id)


def load_zoho_mail_credentials() -> dict:
    if not ZOHO_TOKEN_FILE.exists():
        raise FileNotFoundError(f"Zoho token file not found: {ZOHO_TOKEN_FILE}")
    creds = json.loads(ZOHO_TOKEN_FILE.read_text())
    required = ("refresh_token", "client_id", "client_secret")
    missing = [key for key in required if not creds.get(key)]
    if missing:
        raise RuntimeError(f"Zoho token file is missing: {', '.join(missing)}")
    return creds


async def get_zoho_access_token(client: httpx.AsyncClient, creds: dict) -> str:
    response = await client.post(
        "https://accounts.zoho.com/oauth/v2/token",
        data={
            "refresh_token": creds["refresh_token"],
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "grant_type": "refresh_token",
        },
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Zoho did not return an access token")
    return token


async def send_email(
    client: httpx.AsyncClient,
    access_token: str,
    account_id: str,
    vertical: Vertical,
    recipient: Recipient,
) -> dict:
    payload = {
        "fromAddress": from_address(vertical),
        "toAddress": recipient.email,
        "subject": SUBJECT,
        "mailFormat": "html",
        "content": build_html_email(vertical, recipient),
    }
    response = await client.post(
        f"https://mail.zoho.com/api/accounts/{account_id}/messages",
        json=payload,
        headers={"Authorization": f"Zoho-oauthtoken {access_token}"},
    )
    return {
        "ok": response.status_code in (200, 201),
        "status_code": response.status_code,
        "body": response.text[:500],
    }


async def record_send(db: AsyncSession, recipient: Recipient, subject: str) -> None:
    if not recipient.user_id:
        return
    await db.execute(text("""
        INSERT INTO drip_emails (id, user_id, email, product_id, email_number, subject)
        VALUES (:id, :user_id, :email, :product_id, :email_number, :subject)
        ON CONFLICT (email, product_id, email_number) DO NOTHING
    """), {
        "id": str(uuid.uuid4()),
        "user_id": recipient.user_id,
        "email": recipient.email,
        "product_id": recipient.product_id,
        "email_number": CAMPAIGN_EMAIL_NUMBER,
        "subject": subject,
    })


def summarize_by_product(recipients: list[Recipient]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for recipient in recipients:
        counts[recipient.product_id] = counts.get(recipient.product_id, 0) + 1
    return dict(sorted(counts.items()))


async def main() -> int:
    args = parse_args()
    verticals = load_verticals()
    requested_products = set(args.products or [])
    unknown_products = requested_products - set(verticals)
    if unknown_products:
        print(f"ERROR: unknown product_id(s): {', '.join(sorted(unknown_products))}")
        return 1

    if args.test_email:
        product_id = sorted(requested_products)[0] if requested_products else "law"
        recipients = [proof_recipient(args.test_email, product_id)]
        stats = {
            "active_license_rows": 0,
            "unsubscribed_skipped": 0,
            "already_sent_skipped": 0,
            "internal_skipped": 0,
            "eligible": 1,
            "mode": "test-email",
        }
    else:
        database_url = os.getenv("DATABASE_URL", "")
        if not database_url:
            print("ERROR: DATABASE_URL is not set")
            return 1
        engine = create_async_engine(database_url, echo=False)
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with Session() as db:
            recipients, stats = await fetch_recipients(db, requested_products or None, args.include_internal, args.limit)
        await engine.dispose()

    if not recipients:
        print("No eligible recipients.")
        print(json.dumps(stats, indent=2))
        return 0

    first = recipients[0]
    first_vertical = verticals.get(first.product_id, verticals["law"])
    preview_file = Path(args.preview_file)
    preview_file.write_text(build_html_email(first_vertical, first))

    summary = {
        "campaign": CAMPAIGN,
        "subject": SUBJECT,
        "dry_run": not args.send,
        "test_email": bool(args.test_email),
        "requested_products": sorted(requested_products) if requested_products else "all",
        "recipient_stats": stats,
        "recipient_counts_by_product": summarize_by_product(recipients),
        "senders_by_product": {
            pid: from_address(verticals[pid])
            for pid in sorted(summarize_by_product(recipients))
            if pid in verticals
        },
        "preview_file": str(preview_file),
    }
    Path(args.summary_file).write_text(json.dumps(summary, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"\nPreview HTML written to {preview_file}")
    print(f"Summary JSON written to {args.summary_file}")

    if not args.send:
        print("\nDry run only. Re-run with --send after approval.")
        return 0

    creds = load_zoho_mail_credentials()
    account_id = str(creds.get("account_id") or os.getenv("ZOHO_ACCOUNT_ID") or "6556209000000008002")
    sent = 0
    failed: list[dict[str, str]] = []

    database_url = os.getenv("DATABASE_URL", "")
    engine = create_async_engine(database_url, echo=False) if database_url and not args.test_email else None
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False) if engine else None

    async with httpx.AsyncClient(timeout=30.0) as client:
        access_token = await get_zoho_access_token(client, creds)
        db_context = Session() if Session else None
        db = await db_context.__aenter__() if db_context else None
        try:
            for index, recipient in enumerate(recipients, start=1):
                vertical = verticals.get(recipient.product_id, verticals["law"])
                print(f"[{index}/{len(recipients)}] {vertical.name} -> {recipient.email} from {vertical.support_email}")
                result = await send_email(client, access_token, account_id, vertical, recipient)
                if result["ok"]:
                    sent += 1
                    if db:
                        await record_send(db, recipient, SUBJECT)
                        await db.commit()
                    print("  sent")
                else:
                    failed.append({
                        "email": recipient.email,
                        "product_id": recipient.product_id,
                        "status_code": str(result["status_code"]),
                        "body": result["body"],
                    })
                    print(f"  failed: {result['status_code']}")

                if index < len(recipients):
                    time.sleep(EMAIL_PAUSE_SECONDS)
                if index % BATCH_SIZE == 0 and index < len(recipients):
                    print(f"Batch pause: {BATCH_PAUSE_SECONDS}s")
                    time.sleep(BATCH_PAUSE_SECONDS)
        finally:
            if db_context:
                await db_context.__aexit__(None, None, None)
            if engine:
                await engine.dispose()

    result_summary = {
        **summary,
        "dry_run": False,
        "sent": sent,
        "failed_count": len(failed),
        "failed": failed,
    }
    Path(args.summary_file).write_text(json.dumps(result_summary, indent=2))
    print(json.dumps({"sent": sent, "failed_count": len(failed)}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
