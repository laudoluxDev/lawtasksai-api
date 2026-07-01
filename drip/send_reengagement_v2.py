#!/usr/bin/env python3
"""
Re-engagement v2 — magic-link campaign sender.

Uses POST /admin/campaign/send to generate per-user magic links and send
personalised emails via the API. Each click is tracked in campaign_clicks.

Usage:
  python3 send_reengagement_v2.py [--dry-run] [--product law,farmer,...]

Options:
  --dry-run       Generate tokens + preview first user email, do NOT send.
  --product X,Y   Only send to users of these verticals (comma-separated).
                  Omit to send to all active verticals.
"""
import os

import json
import sys
import urllib.request
import urllib.parse
from pathlib import Path

API_BASE   = "https://api.lawtasksai.com"
ADMIN_KEY  = os.getenv("LAWTASKSAI_ADMIN_SECRET", "")
TEMPLATE   = Path(__file__).parent / "reengagement_v2_template.html"

# Per-vertical accent colors (for template substitution before sending to API)
ACCENT_COLORS = {
    "law": "#2563eb", "realtor": "#dc2626", "farmer": "#16a34a",
    "teacher": "#7c3aed", "therapist": "#0891b2", "marketing": "#ea580c",
    "contractor": "#f97316", "accounting": "#1d4ed8", "chiropractor": "#0d9488",
    "dentist": "#38bdf8", "designer": "#ec4899", "electrician": "#eab308",
    "eventplanner": "#a855f7", "funeral": "#374151", "hr": "#0ea5e9",
    "insurance": "#0369a1", "landlord": "#92400e", "militaryspouse": "#1e40af",
    "mortgage": "#7e22ce", "mortuary": "#374151", "nutritionist": "#15803d",
    "pastor": "#9f1239", "personaltrainer": "#b45309", "plumber": "#075985",
    "principal": "#1e3a5f", "restaurant": "#b91c1c", "salon": "#be185d",
    "travelagent": "#0e7490", "vet": "#166534", "church": "#7c3aed",
}


def parse_args():
    dry_run = "--dry-run" in sys.argv
    product_ids = None
    for arg in sys.argv[1:]:
        if arg.startswith("--product="):
            product_ids = [p.strip() for p in arg.split("=", 1)[1].split(",")]
        elif arg == "--product" and sys.argv.index(arg) + 1 < len(sys.argv):
            product_ids = [p.strip() for p in sys.argv[sys.argv.index(arg) + 1].split(",")]
    return dry_run, product_ids


def api_post(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Admin-Secret": ADMIN_KEY,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def main():
    dry_run, product_ids = parse_args()

    print(f"{'[DRY RUN] ' if dry_run else ''}Re-engagement v2 Campaign")
    print(f"  API: {API_BASE}")
    if product_ids:
        print(f"  Verticals: {', '.join(product_ids)}")
    else:
        print("  Verticals: all active")
    print()

    # Load and pre-process template (inject accent_color placeholder support)
    template_html = TEMPLATE.read_text()

    # The template uses {{ACCENT_COLOR}} but the API endpoint only substitutes
    # {{FIRST_NAME}}, {{PRODUCT_NAME}}, {{DOMAIN}}, {{LICENSE_KEY}}, {{MAGIC_LINK}}.
    # We need to handle {{ACCENT_COLOR}} here per-vertical — but since we're sending
    # one template to the API and it renders per-user, we'll use a neutral default
    # and let the API handle the rest.  For multi-vertical sends, we call the endpoint
    # once per vertical so we can substitute the right accent color.

    verticals_to_send = product_ids or list(ACCENT_COLORS.keys())

    total_queued = 0
    total_skipped = 0
    all_errors = []

    for pid in verticals_to_send:
        accent = ACCENT_COLORS.get(pid, "#2563eb")
        html = template_html.replace("{{ACCENT_COLOR}}", accent)

        domain = "lawtasksai.com" if pid == "law" else f"{pid}tasksai.com"
        html = html.replace("{{EMAIL_ENCODED}}", "{{EMAIL_ENCODED}}")  # left for API

        product_name_map = {
            "law": "LawTasksAI", "realtor": "RealtorTasksAI", "farmer": "FarmerTasksAI",
            "teacher": "TeacherTasksAI", "therapist": "TherapistTasksAI",
            "marketing": "MarketingTasksAI", "contractor": "ContractorTasksAI",
        }
        product_display = product_name_map.get(pid, f"{pid.title()}TasksAI")

        subject = f"Your {product_display} credits are still waiting — new installer inside"

        print(f"  Sending to {pid} ({product_display})...")

        payload = {
            "campaign": "reengagement-v2",
            "subject": subject,
            "template_html": html,
            "product_ids": [pid],
            "dry_run": dry_run,
            "token_ttl_hours": 72,
        }

        try:
            result = api_post("/admin/campaign/send", payload)
            q = result.get("queued", 0)
            s = result.get("skipped", 0)
            errs = result.get("errors", [])
            total_queued += q
            total_skipped += s
            all_errors.extend(errs)

            status = "[DRY RUN]" if dry_run else "✅"
            print(f"    {status} queued={q} skipped={s}")
            if errs:
                for e in errs:
                    print(f"      ⚠️  {e}")

            if dry_run and result.get("preview"):
                prev = result["preview"]
                print(f"\n    Preview:")
                print(f"      To:      {prev.get('to')}")
                print(f"      From:    {prev.get('from')}")
                print(f"      Subject: {prev.get('subject')}")
                print(f"      Link:    {prev.get('magic_link')}")
                print(f"      HTML:    {prev.get('html_preview', '')[:200]}...")

        except Exception as e:
            print(f"    ❌ ERROR for {pid}: {e}")
            all_errors.append(f"{pid}: {e}")
            total_skipped += 1

    print()
    print("=" * 60)
    mode = "DRY RUN complete" if dry_run else "Send complete"
    print(f"{mode}: queued={total_queued}, skipped={total_skipped}, errors={len(all_errors)}")
    if all_errors:
        print("\nErrors:")
        for e in all_errors:
            print(f"  {e}")

    if dry_run:
        print("\nRun without --dry-run to send for real.")


if __name__ == "__main__":
    main()
