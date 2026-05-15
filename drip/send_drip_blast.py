"""
TasksAI Drip Email Blast — Email 1
Sends Email 1 to all real users of a given vertical.
Usage: python3 send_drip_blast.py [--dry-run] [--product law]

Skips: known test/internal accounts
Includes: all real users including the owner
"""

import json, pathlib, sys, time, urllib.request, urllib.error, urllib.parse

# ── Config ──────────────────────────────────────────────────────────────
ADMIN_SECRET   = "2e3b1d4149297c9fe9bb0a4ea5be5a57b6dc28ed7f38cd3a5bf0092c44398643"
API_BASE       = "https://api.lawtasksai.com"
FROM_ADDRESS   = "hello@lawtasksai.com"
ZOHO_ACCOUNT_ID = "6556209000000008002"
TOKEN_FILE     = pathlib.Path.home() / ".config/zoho-mail-tokens.json"
DRIP_DIR       = pathlib.Path(__file__).parent

# Accounts that are clearly test/internal — never email these
SKIP_EMAILS = {
    "clio-test-hr@internal.test",
    "clio_test_probe_1777461218@example.com",
    "test-signup-realtor@mailinator.com",
    "test-signup-1778257956@example.com",
}

PRODUCT_META = {
    "law": {
        "product_name":  "LawTasksAI",
        "domain":        "lawtasksai.com",
        "skill_count":   "206",
        "occupation":    "attorneys",
        "accent_color":  "#2563eb",
        "first_prompt":  "I need help analyzing the statute of limitations for a personal injury case in Colorado",
        "product_why":   "LawTasksAI gives you expert-level legal frameworks — delivered as structured, professional output. Not generic AI. Actual legal analysis.",
        "task_cards":    """
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'>
            <div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>⚖️ Statute of Limitations Analyzer</div>
            <div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Get the exact limitations period, tolling rules, and key cases for any jurisdiction.</div>
          </div>
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'>
            <div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>📄 Demand Letter Drafter</div>
            <div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Professional demand letters ready to send — structured, jurisdiction-aware, persuasive.</div>
          </div>
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'>
            <div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>🔍 Contract Clause Analyzer</div>
            <div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Identify risk, ambiguity, and missing protections in any contract clause.</div>
          </div>""",
    },
}

# ── Args ─────────────────────────────────────────────────────────────────
DRY_RUN = "--dry-run" in sys.argv
PRODUCT = next((sys.argv[sys.argv.index("--product") + 1] for i, a in enumerate(sys.argv) if a == "--product"), "law")

print("=" * 60)
print(f"TasksAI Drip Blast — Email 1")
print(f"  Product:  {PRODUCT}")
print(f"  Dry run:  {DRY_RUN}")
print("=" * 60)

# ── Zoho token ───────────────────────────────────────────────────────────
def get_token():
    t = json.loads(TOKEN_FILE.read_text())
    data = urllib.parse.urlencode({
        "refresh_token": t["refresh_token"],
        "client_id":     t["client_id"],
        "client_secret": t["client_secret"],
        "grant_type":    "refresh_token",
    }).encode()
    req = urllib.request.Request(
        "https://accounts.zoho.com/oauth/v2/token", data=data, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["access_token"]

# ── Fetch users ───────────────────────────────────────────────────────────
def fetch_users(product_id):
    req = urllib.request.Request(
        f"{API_BASE}/admin/users?limit=200",
        headers={"X-Admin-Secret": ADMIN_SECRET}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    all_users = data.get("users", [])
    return [
        u for u in all_users
        if u.get("product_id") == product_id
        and u.get("email") not in SKIP_EMAILS
        and "@example.com" not in u.get("email", "")
        and "@mailinator.com" not in u.get("email", "")
        and "@internal.test" not in u.get("email", "")
    ]

# ── Install block (all platforms — shown when platforms unknown) ──────────
def all_platforms_block(p):
    domain = p["domain"]
    gs_url = f"https://{domain}/getting-started"
    return f"""
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px 24px;margin:16px 0;">
  <h3 style="margin:0 0 12px;font-size:1rem;color:#1a1a2e;">Get set up in 5 minutes</h3>
  <p style="font-size:0.9rem;color:#4b5563;margin:0 0 14px;">
    Choose your AI client below for step-by-step instructions:
  </p>
  <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;">
    <a href="{gs_url}#claude-desktop" style="display:inline-block;background:#e0e7ff;color:#3730a3;padding:6px 14px;border-radius:20px;font-size:0.82rem;font-weight:600;text-decoration:none;">Claude Desktop</a>
    <a href="{gs_url}#claude-code" style="display:inline-block;background:#e0e7ff;color:#3730a3;padding:6px 14px;border-radius:20px;font-size:0.82rem;font-weight:600;text-decoration:none;">Claude Code</a>
    <a href="{gs_url}#cursor" style="display:inline-block;background:#e0e7ff;color:#3730a3;padding:6px 14px;border-radius:20px;font-size:0.82rem;font-weight:600;text-decoration:none;">Cursor</a>
    <a href="{gs_url}#windsurf" style="display:inline-block;background:#e0e7ff;color:#3730a3;padding:6px 14px;border-radius:20px;font-size:0.82rem;font-weight:600;text-decoration:none;">Windsurf</a>
    <a href="{gs_url}#cline" style="display:inline-block;background:#e0e7ff;color:#3730a3;padding:6px 14px;border-radius:20px;font-size:0.82rem;font-weight:600;text-decoration:none;">Cline</a>
    <a href="{gs_url}#openclaw" style="display:inline-block;background:#e0e7ff;color:#3730a3;padding:6px 14px;border-radius:20px;font-size:0.82rem;font-weight:600;text-decoration:none;">OpenClaw</a>
  </div>
  <p style="margin:0;font-size:0.82rem;background:#ecfdf5;border-radius:6px;padding:10px 12px;color:#065f46;">
    ✅ The installer handles everything automatically — no manual config file editing needed.
  </p>
</div>"""

# ── Record send in DB ───────────────────────────────────────────────────────
def record_send(to_address, product_id, email_number, subject):
    """POST to admin API to record the drip send. Non-fatal if it fails."""
    try:
        payload = json.dumps({
            "email": to_address,
            "product_id": product_id,
            "email_number": email_number,
            "subject": subject,
        }).encode()
        req = urllib.request.Request(
            f"{API_BASE}/admin/drip-record",
            data=payload,
            headers={
                "X-Admin-Secret": ADMIN_SECRET,
                "Content-Type": "application/json",
            },
            method="POST"
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"    (drip record failed: {e})")


# ── Send one email ────────────────────────────────────────────────────────
def send_email(to_address, subject, html, token):
    payload = json.dumps({
        "fromAddress": FROM_ADDRESS,
        "toAddress":   to_address,
        "subject":     subject,
        "content":     html,
        "mailFormat":  "html",
    }).encode()
    req = urllib.request.Request(
        f"https://mail.zoho.com/api/accounts/{ZOHO_ACCOUNT_ID}/messages",
        data=payload,
        headers={
            "Authorization": f"Zoho-oauthtoken {token}",
            "Content-Type":  "application/json",
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        code = json.loads(r.read()).get("status", {}).get("code")
        return str(code) == "200"

# ── Main ──────────────────────────────────────────────────────────────────
p = PRODUCT_META[PRODUCT]
users = fetch_users(PRODUCT)

print(f"\nUsers to email: {len(users)}")
for u in users:
    print(f"  {u['email']:<42} credits={u.get('credits_balance',0)}  type={u.get('license_type')}")

print()
if DRY_RUN:
    print("DRY RUN — no emails sent.")
    sys.exit(0)

confirm = input(f"\nSend Email 1 to all {len(users)} users? (yes/no): ").strip().lower()
if confirm != "yes":
    print("Aborted.")
    sys.exit(0)

print("\nGetting Zoho token...")
token = get_token()
print("✓ Token obtained\n")

tmpl = (DRIP_DIR / "email1_template.html").read_text()

sent = 0; failed = 0
for u in users:
    email = u["email"]
    encoded_email = urllib.parse.quote(email)
    name = u.get('name', '') or ''
    first_name = name.split()[0] if name.strip() else ''
    greeting = f"Hi {first_name}," if first_name else "Hi there,"

    html = tmpl
    replacements = {
        "{{PRODUCT_NAME}}":        p["product_name"],
        "{{PRODUCT_ID}}":          PRODUCT,
        "{{PLATFORM_LABEL}}":      "your AI client",
        "{{INSTALL_BLOCK}}":       all_platforms_block(p),
        "{{TASK_LIBRARY_URL}}":    f"https://{p['domain']}/task-library",
        "{{GETTING_STARTED_URL}}": f"https://{p['domain']}/getting-started",
        "{{SUPPORT_EMAIL}}":       f"hello@{p['domain']}",
        "{{FEEDBACK_BASE}}":       f"{API_BASE}/v1/feedback",
        "{{USER_EMAIL}}":          encoded_email,
        "{{GREETING}}":            greeting,
        "{{FIRST_PROMPT}}":        p['first_prompt'],
        "{{SKILL_COUNT}}":         p['skill_count'],
        "{{ACCENT_COLOR}}":        p['accent_color'],
        "{{DOMAIN}}":              p['domain'],
        "{{PRODUCT_WHY}}":         p['product_why'],
        "{{TASK_CARDS}}":          p['task_cards'],
        "{{UNSUBSCRIBE_URL}}":     f"https://{p['domain']}/unsubscribe?email={encoded_email}",
    }
    for k, v in replacements.items():
        html = html.replace(k, v)

    subject = f"Your {p['product_name']} credits are ready — here's how to use them"

    print(f"  Sending to {email}...", end=" ", flush=True)
    if send_email(email, subject, html, token):
        print("✅")
        sent += 1
        record_send(email, PRODUCT, 1, subject)
    else:
        print("❌")
        failed += 1

    time.sleep(0.5)  # be gentle with Zoho rate limits

print(f"\n{'='*60}")
print(f"Done: {sent} sent, {failed} failed")
