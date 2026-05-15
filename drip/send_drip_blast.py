"""
TasksAI Drip Email Blast — multi-vertical, vertical-aware
Sends the specified email number to ALL real users, grouped by their actual product_id.
Each user gets the correct vertical's template, from-address, and content.

Usage:
  python3 send_drip_blast.py --email 2            # send Email 2 to all verticals
  python3 send_drip_blast.py --email 1 --dry-run  # preview only
  python3 send_drip_blast.py --email 3 --product law  # one vertical only

Skips: test/internal accounts, unsubscribed users, users who already received this email number.
"""

import json, pathlib, sys, time, urllib.request, urllib.error, urllib.parse

# ── Config ──────────────────────────────────────────────────────────────
ADMIN_SECRET    = "2e3b1d4149297c9fe9bb0a4ea5be5a57b6dc28ed7f38cd3a5bf0092c44398643"
API_BASE        = "https://api.lawtasksai.com"
ZOHO_ACCOUNT_ID = "6556209000000008002"
TOKEN_FILE      = pathlib.Path.home() / ".config/zoho-mail-tokens.json"
DRIP_DIR        = pathlib.Path(__file__).parent

SKIP_EMAILS = {
    "clio-test-hr@internal.test",
    "clio_test_probe_1777461218@example.com",
    "test-signup-realtor@mailinator.com",
    "test-signup-1778257956@example.com",
}

# ── Product metadata ─────────────────────────────────────────────────────
PRODUCT_META = {
    "law": {
        "product_name": "LawTasksAI", "domain": "lawtasksai.com",
        "skill_count": "206", "accent_color": "#2563eb",
        "first_prompt": "I need help analyzing the statute of limitations for a personal injury case in Colorado",
        "product_why": "LawTasksAI gives you expert-level legal frameworks — delivered as structured, professional output. Not generic AI. Actual legal analysis.",
        "task_cards": """
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>⚖️ Statute of Limitations Analyzer</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Get the exact limitations period, tolling rules, and key cases for any jurisdiction.</div></div>
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>📄 Demand Letter Drafter</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Professional demand letters ready to send — structured, jurisdiction-aware, persuasive.</div></div>
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>🔍 Contract Clause Analyzer</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Identify risk, ambiguity, and missing protections in any contract clause.</div></div>""",
    },
    "farmer": {
        "product_name": "FarmerTasksAI", "domain": "farmertasksai.com",
        "skill_count": "193", "accent_color": "#16a34a",
        "first_prompt": "I need help writing a crop rotation plan for my operation",
        "product_why": "FarmerTasksAI gives you 193 expert farming workflows — from crop planning and soil analysis to grant writing and equipment checklists. Real agricultural expertise, delivered as structured output.",
        "task_cards": """
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>🌱 Crop Rotation Planner</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Build a season-by-season rotation plan tailored to your soil type, crops, and operation size.</div></div>
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>🧪 Soil Amendment Calculator</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Get amendment recommendations based on soil test results and target crop requirements.</div></div>
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>📋 USDA Grant Writer</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Draft competitive USDA grant applications with the right structure and language.</div></div>""",
    },
    "realtor": {
        "product_name": "RealtorTasksAI", "domain": "realtortasksai.com",
        "skill_count": "169", "accent_color": "#0EA5E9",
        "first_prompt": "I need help writing a comparative market analysis for a 3-bed home",
        "product_why": "RealtorTasksAI gives you 169 expert real estate workflows — from CMA reports and listing descriptions to buyer presentations and contract reviews.",
        "task_cards": """
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>📊 CMA Report Generator</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Generate a structured comparative market analysis ready to present to clients.</div></div>
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>🏡 Listing Description Writer</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Write compelling MLS listing descriptions that highlight the right features for your market.</div></div>
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>🤝 Buyer Presentation Builder</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Build a professional buyer consultation presentation tailored to first-time or experienced buyers.</div></div>""",
    },
    "teacher": {
        "product_name": "TeacherTasksAI", "domain": "teachertasksai.com",
        "skill_count": "167", "accent_color": "#7C3AED",
        "first_prompt": "I need help writing parent progress updates for my class",
        "product_why": "TeacherTasksAI gives you 167 expert education workflows — from lesson planning and IEP summaries to report card comments and parent communications.",
        "task_cards": """
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>📝 Report Card Comment Generator</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Generate professional, personalized report card comments for every student in minutes.</div></div>
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>🎯 IEP Goal Summary</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Summarize IEP goals clearly for parent meetings and documentation requirements.</div></div>
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>📬 Parent Progress Update</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Write warm, professional parent update letters that communicate progress and next steps.</div></div>""",
    },
    "therapist": {
        "product_name": "TherapistTasksAI", "domain": "therapisttasksai.com",
        "skill_count": "155", "accent_color": "#0891B2",
        "first_prompt": "I need help writing a treatment plan summary for a new patient",
        "product_why": "TherapistTasksAI gives you 155 expert clinical workflows — from treatment plans and progress notes to intake summaries and session documentation.",
        "task_cards": """
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>🧠 Treatment Plan Builder</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Generate structured treatment plans with goals, interventions, and measurable outcomes.</div></div>
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>📋 Progress Note Writer</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Write compliant SOAP or DAP progress notes quickly from session highlights.</div></div>
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>📄 Intake Summary Generator</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Summarize new client intake information into a clean, structured clinical document.</div></div>""",
    },
    "dentist": {
        "product_name": "DentistTasksAI", "domain": "dentisttasksai.com",
        "skill_count": "170", "accent_color": "#0284C7",
        "first_prompt": "I need help writing a patient treatment summary for a crown procedure",
        "product_why": "DentistTasksAI gives you 170 expert dental workflows — from treatment summaries and insurance narratives to patient communications and clinical documentation.",
        "task_cards": """
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>🦷 Treatment Summary Writer</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Generate clear patient treatment summaries for any procedure.</div></div>
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>📝 Insurance Narrative Builder</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Write compelling insurance pre-authorization narratives that get approved.</div></div>
          <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>📬 Patient Communication Drafter</div><div style='font-size:0.85rem;color:#6b7280;margin-top:4px;'>Draft professional patient letters for recalls, treatment plans, and follow-ups.</div></div>""",
    },
}

# ── Args ─────────────────────────────────────────────────────────────────
DRY_RUN      = "--dry-run" in sys.argv
EMAIL_NUM    = int(next((sys.argv[i+1] for i, a in enumerate(sys.argv) if a == "--email"), 1))
PRODUCT_FILTER = next((sys.argv[i+1] for i, a in enumerate(sys.argv) if a == "--product"), None)

print("=" * 60)
print(f"TasksAI Drip Blast — Email {EMAIL_NUM}")
print(f"  Verticals: {PRODUCT_FILTER or 'ALL'}")
print(f"  Dry run:   {DRY_RUN}")
print("=" * 60)

# ── Helpers ───────────────────────────────────────────────────────────────
def get_token():
    t = json.loads(TOKEN_FILE.read_text())
    data = urllib.parse.urlencode({
        "refresh_token": t["refresh_token"], "client_id": t["client_id"],
        "client_secret": t["client_secret"], "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://accounts.zoho.com/oauth/v2/token", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["access_token"]

def fetch_all_users():
    req = urllib.request.Request(f"{API_BASE}/admin/users?limit=200",
                                 headers={"X-Admin-Secret": ADMIN_SECRET})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("users", [])

def fetch_drip_status():
    """Returns set of (email, email_number) already sent."""
    req = urllib.request.Request(f"{API_BASE}/admin/drip-status",
                                 headers={"X-Admin-Secret": ADMIN_SECRET})
    with urllib.request.urlopen(req, timeout=15) as r:
        rows = json.loads(r.read()).get("drip_emails", [])
    return {(r["email"], int(r["email_number"])) for r in rows}

def fetch_unsubscribed():
    """Returns set of emails that have unsubscribed."""
    req = urllib.request.Request(f"{API_BASE}/admin/unsubscribed-emails",
                                 headers={"X-Admin-Secret": ADMIN_SECRET})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return set(data.get("unsubscribed", []))
    except Exception as e:
        print(f"  (Warning: could not fetch unsubscribed list: {e})")
        return set()

def install_block(domain):
    gs = f"https://{domain}/getting-started"
    btns = "".join(
        f"<a href='{gs}#{slug}' style='display:inline-block;background:#e0e7ff;color:#3730a3;padding:6px 14px;border-radius:20px;font-size:0.82rem;font-weight:600;text-decoration:none;margin:3px;'>{label}</a>"
        for slug, label in [("claude-desktop","Claude Desktop"),("claude-code","Claude Code"),
                             ("cursor","Cursor"),("windsurf","Windsurf"),("cline","Cline"),("openclaw","OpenClaw")]
    )
    return (f"<div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px 24px;margin:16px 0;'>"
            f"<h3 style='margin:0 0 12px;font-size:1rem;color:#1a1a2e;'>Get set up in 5 minutes</h3>"
            f"<p style='font-size:0.9rem;color:#4b5563;margin:0 0 14px;'>Choose your AI client for step-by-step instructions:</p>"
            f"<div style='margin-bottom:16px;'>{btns}</div>"
            f"<p style='margin:0;font-size:0.82rem;background:#ecfdf5;border-radius:6px;padding:10px 12px;color:#065f46;'>✅ The installer handles everything automatically — no manual config needed.</p></div>")

def build_html(tmpl, p, pid, email, greeting):
    enc = urllib.parse.quote(email)
    html = tmpl
    for k, v in {
        "{{PRODUCT_NAME}}":        p["product_name"],
        "{{PRODUCT_ID}}":          pid,
        "{{PLATFORM_LABEL}}":      "your AI client",
        "{{INSTALL_BLOCK}}":       install_block(p["domain"]),
        "{{TASK_LIBRARY_URL}}":    f"https://{p['domain']}/task-library",
        "{{GETTING_STARTED_URL}}": f"https://{p['domain']}/getting-started",
        "{{SUPPORT_EMAIL}}":       f"hello@{p['domain']}",
        "{{FEEDBACK_BASE}}":       f"{API_BASE}/v1/feedback",
        "{{USER_EMAIL}}":          enc,
        "{{GREETING}}":            greeting,
        "{{FIRST_PROMPT}}":        p["first_prompt"],
        "{{SKILL_COUNT}}":         p["skill_count"],
        "{{ACCENT_COLOR}}":        p["accent_color"],
        "{{DOMAIN}}":              p["domain"],
        "{{PRODUCT_WHY}}":         p["product_why"],
        "{{TASK_CARDS}}":          p["task_cards"],
        "{{UNSUBSCRIBE_URL}}":     f"https://{p['domain']}/unsubscribe?email={enc}",
    }.items():
        html = html.replace(k, v)
    return html

def send_email(to, from_addr, subject, html, token):
    payload = json.dumps({"fromAddress": from_addr, "toAddress": to,
                          "subject": subject, "content": html, "mailFormat": "html"}).encode()
    req = urllib.request.Request(
        f"https://mail.zoho.com/api/accounts/{ZOHO_ACCOUNT_ID}/messages",
        data=payload,
        headers={"Authorization": f"Zoho-oauthtoken {token}", "Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return str(json.loads(r.read()).get("status", {}).get("code", "")) == "200"

def record_send(email, pid, email_number, subject):
    try:
        payload = json.dumps({"email": email, "product_id": pid,
                              "email_number": email_number, "subject": subject}).encode()
        urllib.request.urlopen(urllib.request.Request(
            f"{API_BASE}/admin/drip-record", data=payload,
            headers={"X-Admin-Secret": ADMIN_SECRET, "Content-Type": "application/json"},
            method="POST"), timeout=10)
    except Exception as e:
        print(f"    (record failed: {e})")

# ── Main ──────────────────────────────────────────────────────────────────
print("\nFetching users and drip status...")
all_users    = fetch_all_users()
already_sent = fetch_drip_status()
unsubscribed = fetch_unsubscribed()
print(f"  Total users in DB: {len(all_users)}")
print(f"  Already sent email {EMAIL_NUM}: {sum(1 for e,n in already_sent if n==EMAIL_NUM)}")
print(f"  Unsubscribed: {len(unsubscribed)}")

# Filter and group by product_id
from collections import defaultdict
by_product = defaultdict(list)
for u in all_users:
    email = u.get("email", "")
    pid   = u.get("product_id", "law")
    if (email in SKIP_EMAILS
            or "@example.com" in email
            or "@mailinator.com" in email
            or "@internal.test" in email):
        continue
    if PRODUCT_FILTER and pid != PRODUCT_FILTER:
        continue
    if pid not in PRODUCT_META:
        continue  # unknown vertical, skip
    by_product[pid].append(u)

# Build send list with skip reasons
print(f"\nSend plan for Email {EMAIL_NUM}:")
send_list = []
for pid in sorted(by_product):
    p = PRODUCT_META[pid]
    users = by_product[pid]
    print(f"\n  {p['product_name']} ({len(users)} users):")
    for u in users:
        email = u["email"]
        name  = (u.get("name") or "").strip()
        first = name.split()[0].capitalize() if name else ""
        greeting = f"Hi {first}," if first else "Hi there,"
        skip_reason = None
        if email in unsubscribed:
            skip_reason = "unsubscribed"
        elif (email, EMAIL_NUM) in already_sent:
            skip_reason = f"already sent email {EMAIL_NUM}"
        if skip_reason:
            print(f"    ⏭️  {email} — skip ({skip_reason})")
        else:
            print(f"    ✉️  {email}")
            send_list.append((pid, p, u, greeting))

print(f"\nTotal to send: {len(send_list)}")

if not send_list:
    print("Nothing to send.")
    sys.exit(0)

if DRY_RUN:
    print("\nDRY RUN — no emails sent.")
    sys.exit(0)

confirm = input(f"\nSend Email {EMAIL_NUM} to {len(send_list)} users across all verticals? (yes/no): ").strip().lower()
if confirm != "yes":
    print("Aborted.")
    sys.exit(0)

print("\nGetting Zoho token...")
token = get_token()
print("✓ Token obtained\n")

tmpl = (DRIP_DIR / f"email{EMAIL_NUM}_template.html").read_text()

sent = 0; failed = 0
for pid, p, u, greeting in send_list:
    email      = u["email"]
    from_addr  = f"hello@{p['domain']}"
    subject    = f"Have you run your first {p['product_name']} task yet?" if EMAIL_NUM == 2 else \
                 f"What would make {p['product_name']} useful for you?" if EMAIL_NUM == 3 else \
                 f"Your {p['product_name']} credits are ready — here's how to use them"
    html = build_html(tmpl, p, pid, email, greeting)
    print(f"  [{p['product_name']}] {email}...", end=" ", flush=True)
    if send_email(email, from_addr, subject, html, token):
        print("✅"); sent += 1; record_send(email, pid, EMAIL_NUM, subject)
    else:
        print("❌"); failed += 1
    time.sleep(0.5)

print(f"\n{'='*60}")
print(f"Done: {sent} sent, {failed} failed")
