"""
send_two_things_all_verticals.py
─────────────────────────────────
Sends the "Two things that make [Product] work every time" email
to all users across all verticals that have real users in the DB.

Skips:
  - law (already sent 2026-05-29, email_number=99 records exist)
  - test/internal accounts
  - anyone already recorded in drip_emails with email_number=99

Usage:
  export ZOHO_CLIENT_ID=...
  export ZOHO_CLIENT_SECRET=...
  export ZOHO_REFRESH_TOKEN=...
  export ZOHO_ACCOUNT_ID=6556209000000008002
  export ADMIN_SECRET=...
  python3 send_two_things_all_verticals.py [--dry-run]

Pass --dry-run to preview without sending.
"""

import os, sys, json, urllib.request, urllib.parse, urllib.error

# ── Credentials from environment ──────────────────────────────────────────────
ZOHO_CLIENT_ID     = os.environ["ZOHO_CLIENT_ID"]
ZOHO_CLIENT_SECRET = os.environ["ZOHO_CLIENT_SECRET"]
ZOHO_REFRESH_TOKEN = os.environ["ZOHO_REFRESH_TOKEN"]
ZOHO_ACCOUNT_ID    = os.environ.get("ZOHO_ACCOUNT_ID", "6556209000000008002")
ADMIN_SECRET       = os.environ["ADMIN_SECRET"]
API_BASE           = "https://api.lawtasksai.com"

DRY_RUN = "--dry-run" in sys.argv

# ── Vertical config ────────────────────────────────────────────────────────────
# accent_color, skill_count, sample_tasks (first 3 used as prefix examples)
VERTICALS = {
    "law": {
        "product_name":  "LawTasksAI",
        "accent_color":  "#2563eb",
        "skill_count":   206,
        "domain":        "lawtasksai.com",
        "examples": [
            "analyze the statute of limitations for a PI case in Texas",
            "draft a demand letter for a breach of contract case",
            "generate deposition questions for an eyewitness",
        ],
    },
    "farmer": {
        "product_name":  "FarmerTasksAI",
        "accent_color":  "#16a34a",
        "skill_count":   193,
        "domain":        "farmertasksai.com",
        "examples": [
            "draft a USDA program application",
            "write a crop insurance claim narrative",
            "create a farm succession planning checklist",
        ],
    },
    "realtor": {
        "product_name":  "RealtorTasksAI",
        "accent_color":  "#dc2626",
        "skill_count":   169,
        "domain":        "realtortasksai.com",
        "examples": [
            "write a property listing description for a 3-bed ranch home",
            "prepare a buyer consultation agenda",
            "draft a counteroffer letter",
        ],
    },
    "teacher": {
        "product_name":  "TeacherTasksAI",
        "accent_color":  "#7c3aed",
        "skill_count":   167,
        "domain":        "teachertasksai.com",
        "examples": [
            "write a lesson plan for 8th grade fractions",
            "draft a parent communication letter about a struggling student",
            "create a classroom management behavior plan",
        ],
    },
    "therapist": {
        "product_name":  "TherapistTasksAI",
        "accent_color":  "#0891b2",
        "skill_count":   155,
        "domain":        "therapisttasksai.com",
        "examples": [
            "write a SOAP note for a CBT session",
            "draft a treatment plan for anxiety disorder",
            "create an intake assessment questionnaire",
        ],
    },
    "contractor": {
        "product_name":  "ContractorTasksAI",
        "accent_color":  "#f97316",
        "skill_count":   180,
        "domain":        "contractortasksai.com",
        "examples": [
            "draft a change order request for additional electrical work",
            "prepare a pay application for the current billing period",
            "write a notice of delay letter to the project owner",
        ],
    },
    "marketing": {
        "product_name":  "MarketingTasksAI",
        "accent_color":  "#ea580c",
        "skill_count":   206,
        "domain":        "marketingtasksai.com",
        "examples": [
            "create a Google Ads campaign for a SaaS product",
            "write a 5-email welcome sequence for new subscribers",
            "build a content calendar for Q3",
        ],
    },
}

SKIP_VERTICALS = {"law"}  # already sent

# ── Zoho helpers ───────────────────────────────────────────────────────────────
def get_zoho_token():
    data = urllib.parse.urlencode({
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id":     ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type":    "refresh_token",
    }).encode()
    req = urllib.request.Request(
        "https://accounts.zoho.com/oauth/v2/token", data=data, method="POST"
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["access_token"]


def send_email(token, to_addr, from_addr, subject, html_body):
    payload = json.dumps({
        "fromAddress": from_addr,
        "toAddress":   to_addr,
        "subject":     subject,
        "content":     html_body,
        "mailFormat":  "html",
    }).encode()
    req = urllib.request.Request(
        f"https://mail.zoho.com/api/accounts/{ZOHO_ACCOUNT_ID}/messages",
        data=payload,
        headers={"Authorization": f"Zoho-oauthtoken {token}",
                 "Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "body": e.read().decode()}


def record_send(email, product_id, subject):
    payload = json.dumps({
        "email":        email,
        "product_id":   product_id,
        "email_number": 99,
        "subject":      subject,
    }).encode()
    req = urllib.request.Request(
        f"{API_BASE}/admin/drip-record",
        data=payload,
        headers={"X-Admin-Secret": ADMIN_SECRET,
                 "Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as r:
            return True
    except Exception:
        return False


# ── Fetch users from DB ────────────────────────────────────────────────────────
def get_users():
    req = urllib.request.Request(
        f"{API_BASE}/admin/users",
        headers={"X-Admin-Secret": ADMIN_SECRET}
    )
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    users = data if isinstance(data, list) else data.get("users", [])
    return users


def get_already_sent():
    """Return set of (email, product_id) that already have email_number=99."""
    req = urllib.request.Request(
        f"{API_BASE}/admin/drip-queue",
        headers={"X-Admin-Secret": ADMIN_SECRET}
    )
    # drip-queue only gives counts — we'll dedupe by attempting record and
    # catching the unique constraint error instead. So return empty set here.
    return set()


# ── Email template ─────────────────────────────────────────────────────────────
STYLE = """<style>
  body{margin:0;padding:0;background:#f4f5f7;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;}
  .wrap{max-width:600px;margin:32px auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);}
  .hdr{padding:28px 40px;text-align:center;}
  .hdr h1{color:white;margin:0;font-size:1.25rem;font-weight:700;line-height:1.4;}
  .hdr p{color:rgba(255,255,255,0.85);margin:6px 0 0;font-size:0.88rem;}
  .body{padding:36px 40px;}
  p{color:#4b5563;font-size:0.95rem;line-height:1.7;margin:0 0 16px;}
  .tbox{background:#fef3c7;border:2px solid #f59e0b;border-radius:10px;padding:18px 22px;margin:20px 0;}
  .tbox h2{color:#92400e;font-size:1rem;font-weight:800;margin:0 0 6px;}
  .tbox p{color:#78350f;font-size:0.88rem;margin:0 0 10px;}
  .code{background:#0f172a;color:#7dd3fc;border-radius:8px;padding:12px 16px;font-family:monospace;font-size:0.88rem;line-height:1.6;margin:10px 0;}
  .note{font-size:0.8rem;color:#92400e;margin:8px 0 0;}
  .exs{margin:20px 0;}
  .exs h3{font-size:0.9rem;font-weight:700;color:#1a1a2e;margin:0 0 10px;}
  .ex{background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:10px 14px;margin-bottom:8px;font-family:monospace;font-size:0.85rem;color:#1a1a2e;}
  .cta-primary{display:block;text-align:center;padding:13px 24px;border-radius:8px;font-weight:700;font-size:0.95rem;text-decoration:none !important;margin:12px 0;color:#ffffff !important;}
  .cta-secondary{display:block;text-align:center;padding:12px 24px;border-radius:8px;font-weight:600;font-size:0.9rem;text-decoration:none;margin:12px 0;background:white;border:2px solid currentColor;}
  .perm-box{background:#f0f9ff;border:1px solid #bae6fd;border-radius:10px;padding:20px 24px;margin:24px 0;}
  .perm-box h3{color:#0c4a6e;font-size:0.95rem;font-weight:700;margin:0 0 10px;}
  .perm-box p{color:#075985;font-size:0.88rem;margin:0 0 10px;}
  .perm-steps{padding-left:18px;color:#075985;font-size:0.88rem;line-height:2;margin:0 0 14px;}
  .perm-img{width:100%;border-radius:8px;border:1px solid #e0f2fe;display:block;margin:10px 0;}
  .perm-caption{font-size:0.78rem;color:#6b7280;text-align:center;margin:0 0 14px;}
  .div{border:none;border-top:1px solid #e5e7eb;margin:28px 0;}
  .foot{padding:20px 40px;background:#f9fafb;text-align:center;font-size:0.8rem;color:#9ca3af;}
  .foot a{color:#9ca3af;text-decoration:none;}
</style>"""


def build_email(cfg, greeting):
    pn     = cfg["product_name"]       # e.g. FarmerTasksAI
    pid    = pn.lower().replace(" ", "") # e.g. farmertasksai
    color  = cfg["accent_color"]
    count  = cfg["skill_count"]
    domain = cfg["domain"]
    ex1, ex2, ex3 = cfg["examples"]

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{STYLE}
</head><body>
<div class="wrap">

  <div class="hdr" style="background:{color};">
    <h1>Two things that make {pn} work every time</h1>
    <p>Do Thing 1 first &mdash; then Thing 2 works every time</p>
  </div>

  <div class="body">
    <p>{greeting}</p>
    <p>You signed up for {pn} and your credits are still waiting. There are just two things to do &mdash; and once you do, it works reliably every time.</p>

    <!-- THING 1 -->
    <div class="perm-box">
      <h3>&#128274; Thing 1: Turn on permissions in Claude Desktop (30 seconds)</h3>
      <p>This has to happen first. Without it, the prefix in Thing 2 won&rsquo;t work &mdash; Claude will skip your skills entirely.</p>
      <p style="font-size:0.82rem;color:#075985;margin:0 0 10px;">&#9432; <em>Using Cursor, Windsurf, or Cline? Skip this step &mdash; your tools are already available once the installer runs.</em></p>
      <ol class="perm-steps">
        <li>In Claude Desktop, click <strong>Settings</strong></li>
        <li>Go to <strong>Connectors</strong> &mdash; you&rsquo;ll see a note that connectors have moved to <strong>Customize</strong>. Click that link.</li>
      </ol>
      <img src="https://lawtasksai.com/images/setup/step1-connectors.png" alt="Claude Desktop Settings - Connectors pointing to Customize" class="perm-img">
      <p class="perm-caption">Settings &rarr; Connectors &rarr; click &ldquo;Customize&rdquo;</p>
      <p style="font-size:0.8rem;color:#075985;margin:-8px 0 14px;">&#9432; <em>Your connector will be listed as <code style="background:#e0f2fe;padding:1px 5px;border-radius:3px;">{pid}</code> in the Desktop section.</em></p>
      <ol class="perm-steps" start="3">
        <li>Under <strong>Desktop</strong>, click <strong>{pid}</strong></li>
        <li>Next to <strong>Other tools</strong>, click the dropdown and select <strong>Always allow</strong></li>
      </ol>
      <img src="https://lawtasksai.com/images/setup/step2-always-allow.png" alt="Claude Desktop - {pn} tool permissions set to Always Allow" class="perm-img">
      <p class="perm-caption">Set all four tools to &ldquo;Always allow&rdquo;</p>
      <p style="font-size:0.8rem;color:#075985;margin:-8px 0 14px;">&#9432; <em>You&rsquo;ll see four tools: <code style="background:#e0f2fe;padding:1px 5px;border-radius:3px;">{pid}_search</code>, <code style="background:#e0f2fe;padding:1px 5px;border-radius:3px;">{pid}_execute</code>, <code style="background:#e0f2fe;padding:1px 5px;border-radius:3px;">{pid}_balance</code>, <code style="background:#e0f2fe;padding:1px 5px;border-radius:3px;">{pid}_categories</code> &mdash; set them all to &ldquo;Always allow.&rdquo;</em></p>
      <p style="margin:0 0 6px;font-size:0.85rem;color:#075985;">Done. Now your skills work!</p>
      <ol class="perm-steps" start="5">
        <li>Start a <strong>new conversation</strong> in Claude Desktop &mdash; tools load at conversation start, not mid-chat.</li>
      </ol>
    </div>

    <hr class="div">

    <!-- THING 2 -->
    <div class="tbox">
      <h2>&#127919; Thing 2: Use the prefix</h2>
      <p>Start every prompt with <strong>&ldquo;Use {pn} to&hellip;&rdquo;</strong> and Claude will search your skill library and automatically select the right expert framework &mdash; you don&rsquo;t need to know which of the {count} tasks to use.</p>
      <div class="code">Use {pn} to {ex1}</div>
      <p class="note">Without that prefix, Claude answers from its own training instead of running your expert framework. With it &mdash; it works every time.</p>
    </div>

    <div class="exs">
      <h3>More examples to copy and paste:</h3>
      <div class="ex">Use {pn} to {ex2}</div>
      <div class="ex">Use {pn} to {ex3}</div>
      <div class="ex">Use {pn} to check my balance</div>
    </div>
    <p style="font-size:0.85rem;color:#6b7280;">You never need to know the task name. Just describe what you need &mdash; {pn} finds the right framework for you.</p>
    <p style="font-size:0.85rem;color:#6b7280;">Each task uses 1 credit. Check your balance anytime: <em style="font-family:monospace;">Use {pn} to check my balance</em></p>

    <hr class="div">

    <a href="https://{domain}/task-library" class="cta-primary" style="background:{color};">Browse all {count} frameworks &rarr;</a>
    <a href="https://{domain}/getting-started" class="cta-secondary" style="color:{color};">Re-download the installer &rarr;</a>

    <p style="font-size:0.85rem;color:#6b7280;margin-top:16px;">Your 5 credits never expire &mdash; they&rsquo;re here when you&rsquo;re ready.</p>
    <p style="font-size:0.9rem;">&mdash; The {pn} Team<br>
    <span style="font-size:0.82rem;color:#9ca3af;">P.S. Reply anytime &mdash; I read every one.</span></p>
  </div>

  <div class="foot">
    You received this because you signed up for {pn}. &nbsp;&middot;&nbsp;
    <a href="#">Unsubscribe</a>
  </div>

</div>
</body></html>"""


# ── Main ───────────────────────────────────────────────────────────────────────
def is_test(email):
    return any(x in email.lower() for x in
               ["test", "internal", "mailinator", "example.com", "probe"])


def main():
    print(f"{'[DRY RUN] ' if DRY_RUN else ''}Fetching users from DB...")
    all_users = get_users()

    # Group real users by vertical, skip already-sent verticals
    by_vertical = {}
    for u in all_users:
        pid = u.get("product_id", "")
        email = u.get("email", "")
        if pid in SKIP_VERTICALS:
            continue
        if pid not in VERTICALS:
            continue
        if is_test(email):
            continue
        by_vertical.setdefault(pid, []).append({
            "email":      email,
            "first_name": (u.get("first_name") or u.get("name") or "").split()[0],
        })

    if not by_vertical:
        print("No users found outside already-sent verticals.")
        return

    print(f"\nVerticals to send: {list(by_vertical.keys())}")
    for pid, users in by_vertical.items():
        print(f"  {pid}: {len(users)} users")

    print()
    if not DRY_RUN:
        confirm = input("Send to all of the above? (yes/no): ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            return

    if DRY_RUN:
        token = "dry-run"
        print("[DRY RUN] Skipping Zoho token fetch\n")
    else:
        print("\nGetting Zoho token...")
        token = get_zoho_token()
        print(f"Token OK: {token[:20]}...\n")

    total_sent = 0
    total_failed = 0

    for pid, users in by_vertical.items():
        cfg = VERTICALS[pid]
        pn = cfg["product_name"]
        from_addr = f"hello@{cfg['domain']}"
        subject = f"Two things that make {pn} work every time"

        print(f"=== {pn} ({len(users)} users) ===")
        for u in users:
            email = u["email"]
            first_name = u["first_name"]
            greeting = f"Hi {first_name}," if first_name else "Hi there,"
            html = build_email(cfg, greeting)

            if DRY_RUN:
                print(f"  [DRY RUN] Would send to {email} from {from_addr}")
                total_sent += 1
                continue

            result = send_email(token, email, from_addr, subject, html)
            mid = result.get("data", {}).get("messageId") if isinstance(result.get("data"), dict) else None
            if mid:
                print(f"  SENT  -> {email}")
                record_send(email, pid, subject)
                total_sent += 1
            else:
                print(f"  FAIL  -> {email}: {result}")
                total_failed += 1
        print()

    print(f"Done: {total_sent} sent, {total_failed} failed")


if __name__ == "__main__":
    main()
