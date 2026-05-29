import urllib.request, urllib.parse, json, urllib.error

# Credentials loaded from environment — never hardcode these
# Set them in your shell before running:
#   export ZOHO_CLIENT_ID=...
#   export ZOHO_CLIENT_SECRET=...
#   export ZOHO_REFRESH_TOKEN=...
#   export ZOHO_ACCOUNT_ID=...
#   export ADMIN_SECRET=...
import os
ZOHO_CLIENT_ID     = os.environ["ZOHO_CLIENT_ID"]
ZOHO_CLIENT_SECRET = os.environ["ZOHO_CLIENT_SECRET"]
ZOHO_REFRESH_TOKEN = os.environ["ZOHO_REFRESH_TOKEN"]
ZOHO_ACCOUNT_ID    = os.environ.get("ZOHO_ACCOUNT_ID", "6556209000000008002")

USERS = [
    ("kentmercier@gmail.com",          "Kent"),
    ("Cinavina92308@gmail.com",        "Cindy"),
    ("Spokanedavid@live.com",          "David"),
    ("valleanette@gmail.com",          "Anette"),
    ("bojackhorseman226@yahoo.com",    "Joseph"),
    ("ntsoanekeletso232@gmail.com",    "Keletso"),
    ("matsu.matsushima69@gmail.com",   "Matsu"),
    ("994559732@qq.com",               "Shuyi"),
]

def get_token():
    data = urllib.parse.urlencode({
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token"
    }).encode()
    req = urllib.request.Request("https://accounts.zoho.com/oauth/v2/token", data=data, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["access_token"]

def send_email(token, to_addr, first_name, html_body):
    greeting = f"Hi {first_name}," if first_name else "Hi there,"
    body = html_body.replace("{{GREETING}}", greeting)
    payload = json.dumps({
        "fromAddress": "hello@lawtasksai.com",
        "toAddress": to_addr,
        "subject": "Two things that make LawTasksAI work every time",
        "content": body,
        "mailFormat": "html",
    }).encode()
    req = urllib.request.Request(
        f"https://mail.zoho.com/api/accounts/{ZOHO_ACCOUNT_ID}/messages",
        data=payload,
        headers={"Authorization": f"Zoho-oauthtoken {token}", "Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "body": e.read().decode()}

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
  .cta-primary{display:block;text-align:center;padding:13px 24px;border-radius:8px;font-weight:700;font-size:0.95rem;text-decoration:none !important;margin:12px 0;background:#2563eb;color:#ffffff !important;}
  .cta-secondary{display:block;text-align:center;padding:12px 24px;border-radius:8px;font-weight:600;font-size:0.9rem;text-decoration:none;margin:12px 0;background:white;color:#2563eb;border:2px solid #2563eb;}
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

EMAIL = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{STYLE}
</head><body>
<div class="wrap">

  <div class="hdr" style="background:#2563eb;">
    <h1>Two things that make LawTasksAI work every time</h1>
    <p>Do Thing 1 first &mdash; then Thing 2 works every time</p>
  </div>

  <div class="body">
    <p>{{{{GREETING}}}}</p>
    <p>You signed up for LawTasksAI and your credits are still waiting. There are just two things to do &mdash; and once you do, it works reliably every time.</p>

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
      <p style="font-size:0.8rem;color:#075985;margin:-8px 0 14px;">&#9432; <em>Your connector will be listed as <code style="background:#e0f2fe;padding:1px 5px;border-radius:3px;">lawtasksai</code> in the Desktop section.</em></p>
      <ol class="perm-steps" start="3">
        <li>Under <strong>Desktop</strong>, click <strong>lawtasksai</strong></li>
        <li>Next to <strong>Other tools</strong>, click the dropdown and select <strong>Always allow</strong></li>
      </ol>
      <img src="https://lawtasksai.com/images/setup/step2-always-allow.png" alt="Claude Desktop - LawTasksAI tool permissions set to Always Allow" class="perm-img">
      <p class="perm-caption">Set all four tools to &ldquo;Always allow&rdquo;</p>
      <p style="font-size:0.8rem;color:#075985;margin:-8px 0 14px;">&#9432; <em>You&rsquo;ll see four tools listed: <code style="background:#e0f2fe;padding:1px 5px;border-radius:3px;">lawtasksai_search</code>, <code style="background:#e0f2fe;padding:1px 5px;border-radius:3px;">lawtasksai_execute</code>, <code style="background:#e0f2fe;padding:1px 5px;border-radius:3px;">lawtasksai_balance</code>, <code style="background:#e0f2fe;padding:1px 5px;border-radius:3px;">lawtasksai_categories</code> &mdash; set them all to &ldquo;Always allow.&rdquo;</em></p>
      <p style="margin:0 0 6px;font-size:0.85rem;color:#075985;">Done. Now your skills work!</p>
      <ol class="perm-steps" start="5">
        <li>Start a <strong>new conversation</strong> in Claude Desktop &mdash; tools load at conversation start, not mid-chat.</li>
      </ol>
    </div>

    <hr class="div">

    <!-- THING 2 -->
    <div class="tbox">
      <h2>&#127919; Thing 2: Use the prefix</h2>
      <p>Start every prompt with <strong>&ldquo;Use LawTasksAI to&hellip;&rdquo;</strong> and Claude will search your skill library and automatically select the right expert framework &mdash; you don&rsquo;t need to know which of the 206 tasks to use.</p>
      <div class="code">Use LawTasksAI to analyze the statute of limitations for a PI case in Texas</div>
      <p class="note">Without that prefix, Claude answers from its own training instead of running your expert framework. With it &mdash; it works every time.</p>
    </div>

    <div class="exs">
      <h3>More examples to copy and paste:</h3>
      <div class="ex">Use LawTasksAI to draft a demand letter for a breach of contract case</div>
      <div class="ex">Use LawTasksAI to generate deposition questions for an eyewitness</div>
      <div class="ex">Use LawTasksAI to write a motion to compel discovery responses</div>
    </div>
    <p style="font-size:0.85rem;color:#6b7280;">You never need to know the task name. Just describe what you need &mdash; LawTasksAI finds the right framework for you.</p>
    <p style="font-size:0.85rem;color:#6b7280;">Each task uses 1 credit. Check your balance anytime: <em style="font-family:monospace;">Use LawTasksAI to check my balance</em></p>

    <hr class="div">

    <a href="https://lawtasksai.com/task-library" class="cta-primary">Browse all 206 frameworks &rarr;</a>
    <a href="https://lawtasksai.com/getting-started" class="cta-secondary">Re-download the installer &rarr;</a>

    <p style="font-size:0.85rem;color:#6b7280;margin-top:16px;">Your 5 credits never expire &mdash; they&rsquo;re here when you&rsquo;re ready.</p>
    <p style="font-size:0.9rem;">&mdash; The LawTasksAI Team<br>
    <span style="font-size:0.82rem;color:#9ca3af;">P.S. Reply anytime &mdash; I read every one.</span></p>
  </div>

  <div class="foot">
    You received this because you signed up for LawTasksAI. &nbsp;&middot;&nbsp;
    <a href="#">Unsubscribe</a>
  </div>

</div>
</body></html>"""

print("Getting Zoho token...")
token = get_token()
print(f"Token OK: {token[:20]}...")
print()

sent = 0
failed = 0
for email, first_name in USERS:
    result = send_email(token, email, first_name, EMAIL)
    mid = result.get("data", {}).get("messageId") if isinstance(result.get("data"), dict) else None
    if mid:
        print(f"  SENT -> {email}")
        sent += 1
    else:
        print(f"  FAIL -> {email}: {result}")
        failed += 1

print(f"\nDone: {sent} sent, {failed} failed")

# ── Record sends in drip_emails table (email_number=99 = ad-hoc blast) ────────
print("\nRecording sends in DB...")
ADMIN_SECRET = os.environ["ADMIN_SECRET"]
recorded = 0
for email, _ in USERS:
    payload = json.dumps({
        "email": email,
        "product_id": "law",
        "email_number": 99,
        "subject": "Two things that make LawTasksAI work every time"
    }).encode()
    req = urllib.request.Request(
        "https://api.lawtasksai.com/admin/drip-record",
        data=payload,
        headers={"X-Admin-Secret": ADMIN_SECRET, "Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as r:
            recorded += 1
    except Exception as e:
        print(f"  DB record failed for {email}: {e}")
print(f"Recorded {recorded}/{len(USERS)} sends in drip_emails (email_number=99)")
