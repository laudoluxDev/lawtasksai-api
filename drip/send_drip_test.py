#!/usr/bin/env python3
"""
TasksAI Drip Email Test Sender
Sends all 3 drip emails to a test address so Kent can review them.
Usage: python3 send_drip_test.py [email] [platform]
  email:    recipient (default: kentmercier@gmail.com)
  platform: claude_desktop | openclaw | cursor | windsurf | cline | other (default: claude_desktop)
"""

import json, os, sys, urllib.request, urllib.error, urllib.parse
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).parent
TEST_EMAIL   = sys.argv[1] if len(sys.argv) > 1 else "kentmercier@gmail.com"
TEST_PLATFORM = sys.argv[2] if len(sys.argv) > 2 else "claude_desktop"

# LawTasksAI defaults (used for test sends)
PRODUCT = {
    "product_id":   "law",
    "product_name": "LawTasksAI",
    "domain":       "lawtasksai.com",
    "accent_color": "#2563eb",
    "skill_count":  206,
    "occupation":   "attorneys",
    "product_why":  "every attorney deserves expert legal research and drafting support — not just the ones at big firms",
    "sample_tasks": [
        ("Statute of Limitations Analyzer",
         "Enter a cause of action and jurisdiction — get the exact limitations period, tolling rules, and key cases.",
         "1 credit"),
        ("Demand Letter Drafter",
         "Generates a professionally worded demand letter with proper legal citations for your jurisdiction.",
         "1 credit"),
        ("Deposition Question Generator",
         "Produces a full deposition question set tailored to the witness type and case theory.",
         "1 credit"),
    ],
}

ZOHO_MAIL_TOKENS = Path.home() / ".config" / "zoho-mail-tokens.json"
ZOHO_ACCOUNT_ID  = "6556209000000008002"
FROM_ADDRESS     = "hello@lawtasksai.com"

# ── Zoho Mail token ──────────────────────────────────────────────────────
def get_zoho_mail_token() -> str:
    with open(ZOHO_MAIL_TOKENS) as f:
        creds = json.load(f)
    data = (
        f"refresh_token={creds['refresh_token']}"
        f"&client_id={creds['client_id']}"
        f"&client_secret={creds['client_secret']}"
        f"&grant_type=refresh_token"
    ).encode()
    req = urllib.request.Request(
        "https://accounts.zoho.com/oauth/v2/token",
        data=data, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["access_token"]

# ── Send via Zoho Mail ───────────────────────────────────────────────────
def send_email(subject: str, html_body: str, token: str) -> bool:
    payload = json.dumps({
        "fromAddress": FROM_ADDRESS,
        "toAddress":   TEST_EMAIL,
        "subject":     subject,
        "content":     html_body,
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
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            code = result.get("status", {}).get("code")
            print(f"  → Zoho status code: {code}")
            return str(code) == "200"
    except urllib.error.HTTPError as e:
        body = e.read()
        print(f"  → HTTP {e.code}: {body[:300]}")
        return False

# ── Platform install blocks ──────────────────────────────────────────────
def platform_install_block(platform: str, p: dict) -> str:
    domain = p["domain"]
    pid    = p["product_id"]
    pname  = p["product_name"]
    gs_url = f"https://{domain}/getting-started"

    blocks = {
        "claude_desktop": f"""
<div style="background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;padding:12px 16px;margin-bottom:14px;font-size:0.85rem;color:#92400e;">
  ⚠️ <strong>Important:</strong> These instructions are for the <strong>Claude Desktop app</strong>,
  not claude.ai (the website). They're different products.
  <a href="https://claude.ai/download" style="color:#92400e;font-weight:700;">Download Claude Desktop free →</a>
</div>
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px 24px;margin:16px 0;">
  <h3 style="margin:0 0 12px;font-size:1rem;color:#1a1a2e;">Install on Claude Desktop</h3>
  <ol style="margin:0;padding-left:20px;color:#4b5563;font-size:0.9rem;line-height:1.9;">
    <li>Download your skill package (link in your confirmation email)</li>
    <li>Extract the zip — open the downloaded <code style="background:#e5e7eb;padding:1px 5px;border-radius:3px;font-size:0.85em;">mcp</code> folder</li>
    <li>Run <code style="background:#e5e7eb;padding:1px 5px;border-radius:3px;font-size:0.85em;">install.py</code> — it handles everything automatically</li>
    <li>Restart Claude Desktop</li>
    <li>Ask Claude: <em style="color:#1a1a2e;">“What legal tasks can you help me with?”</em></li>
  </ol>
  <p style="margin:14px 0 0;font-size:0.82rem;background:#ecfdf5;border-radius:6px;padding:10px 12px;color:#065f46;">
    ✅ The installer automatically finds and updates your Claude Desktop config — no manual file editing needed.<br/>
    🖥️ <strong>Also have Claude Code?</strong> The installer detects both and configures them in the same run.
  </p>
</div>""",

        "openclaw": f"""
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px 24px;margin:16px 0;">
  <h3 style="margin:0 0 12px;font-size:1rem;color:#1a1a2e;">Install on OpenClaw</h3>
  <p style="margin:0 0 10px;color:#4b5563;font-size:0.9rem;">Open OpenClaw and type:</p>
  <code style="background:#1a1a2e;color:#7dd3fc;padding:10px 16px;border-radius:6px;display:block;font-size:0.88rem;margin-bottom:10px;">
    openclaw skills install {pid}
  </code>
  <p style="margin:0;color:#6b7280;font-size:0.85rem;">That's it. OpenClaw downloads and configures everything automatically.</p>
</div>""",

        "cursor": f"""
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px 24px;margin:16px 0;">
  <h3 style="margin:0 0 12px;font-size:1rem;color:#1a1a2e;">Install on Cursor</h3>
  <ol style="margin:0;padding-left:20px;color:#4b5563;font-size:0.9rem;line-height:1.9;">
    <li>Download your skill package (link in your confirmation email)</li>
    <li>Extract the zip, open the <code style="background:#e5e7eb;padding:1px 5px;border-radius:3px;font-size:0.85em;">mcp/</code> folder and run:<br>
      <code style="background:#1a1a2e;color:#7dd3fc;padding:8px 12px;border-radius:6px;display:inline-block;margin-top:6px;font-size:0.85rem;">
        pip3 install -r requirements.txt &amp;&amp; python3 install.py
      </code>
    </li>
    <li>In Cursor: <strong>Settings → MCP</strong> — confirm {pname} appears</li>
    <li>Restart Cursor</li>
  </ol>
</div>""",

        "windsurf": f"""
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px 24px;margin:16px 0;">
  <h3 style="margin:0 0 12px;font-size:1rem;color:#1a1a2e;">Install on Windsurf</h3>
  <ol style="margin:0;padding-left:20px;color:#4b5563;font-size:0.9rem;line-height:1.9;">
    <li>Download your skill package (link in your confirmation email)</li>
    <li>Extract the zip, open the <code style="background:#e5e7eb;padding:1px 5px;border-radius:3px;font-size:0.85em;">mcp/</code> folder and run:<br>
      <code style="background:#1a1a2e;color:#7dd3fc;padding:8px 12px;border-radius:6px;display:inline-block;margin-top:6px;font-size:0.85rem;">
        pip3 install -r requirements.txt &amp;&amp; python3 install.py
      </code>
    </li>
    <li>In Windsurf: click the <strong>MCPs icon</strong> (top-right of Cascade panel) — confirm {pname} appears</li>
    <li>Restart Windsurf. Config file: <code style="background:#e5e7eb;padding:1px 4px;border-radius:3px;font-size:0.82em;">~/.codeium/windsurf/mcp_config.json</code></li>
  </ol>
</div>""",

        "cline": f"""
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px 24px;margin:16px 0;">
  <h3 style="margin:0 0 12px;font-size:1rem;color:#1a1a2e;">Install on Cline (VS Code)</h3>
  <ol style="margin:0;padding-left:20px;color:#4b5563;font-size:0.9rem;line-height:1.9;">
    <li>Download your skill package (link in your confirmation email)</li>
    <li>Extract the zip, open the <code style="background:#e5e7eb;padding:1px 5px;border-radius:3px;font-size:0.85em;">mcp/</code> folder and run:<br>
      <code style="background:#1a1a2e;color:#7dd3fc;padding:8px 12px;border-radius:6px;display:inline-block;margin-top:6px;font-size:0.85rem;">
        pip3 install -r requirements.txt &amp;&amp; python3 install.py
      </code>
    </li>
    <li>In VS Code: <strong>Cline sidebar → MCP Servers → Configure MCP Servers</strong></li>
    <li>Reload VS Code</li>
  </ol>
</div>""",

        "claude_code": f"""
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px 24px;margin:16px 0;">
  <h3 style="margin:0 0 12px;font-size:1rem;color:#1a1a2e;">Install on Claude Code</h3>
  <ol style="margin:0;padding-left:20px;color:#4b5563;font-size:0.9rem;line-height:1.9;">
    <li>Download your skill package (link in your confirmation email)</li>
    <li>Extract the zip — open the downloaded <code style="background:#e5e7eb;padding:1px 5px;border-radius:3px;font-size:0.85em;">mcp</code> folder</li>
    <li>Run <code style="background:#e5e7eb;padding:1px 5px;border-radius:3px;font-size:0.85em;">install.py</code> — it handles everything automatically</li>
    <li>Start a new Claude Code session</li>
    <li>Ask: <em style="color:#1a1a2e;">“What legal tasks can you help me with?”</em></li>
  </ol>
  <p style="margin:14px 0 0;font-size:0.82rem;background:#ecfdf5;border-radius:6px;padding:10px 12px;color:#065f46;">
    ✅ The installer writes to <code>~/.claude.json</code> automatically.<br/>
    🖥️ <strong>Also have Claude Desktop?</strong> The installer detects both and configures them in the same run.
  </p>
</div>""",

        "other": f"""
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px 24px;margin:16px 0;">
  <h3 style="margin:0 0 12px;font-size:1rem;color:#1a1a2e;">Get installed</h3>
  <p style="margin:0 0 10px;color:#4b5563;font-size:0.9rem;">Your download link is in your confirmation email. The zip includes instructions for every supported platform:</p>
  <ul style="margin:0;padding-left:20px;color:#4b5563;font-size:0.9rem;line-height:1.9;">
    <li>✅ Claude Desktop (Mac &amp; Windows)</li>
    <li>✅ OpenClaw (Mac, Windows, Linux)</li>
    <li>✅ Cursor, Windsurf, Cline (VS Code)</li>
  </ul>
  <p style="margin:12px 0 0;font-size:0.85rem;">
    <a href="{gs_url}" style="color:#2563eb;font-weight:700;">→ Full setup guide at {domain}/getting-started</a>
  </p>
</div>""",
    }

    block = blocks.get(platform, blocks["other"])
    # Always append the "changed platforms" link
    block += f"""
<p style="font-size:0.8rem;color:#9ca3af;text-align:center;margin-top:8px;">
  Using a different AI tool? <a href="{gs_url}" style="color:#6b7280;">Get updated install instructions →</a>
</p>"""
    return block


# ── Task cards ───────────────────────────────────────────────────────────
def task_cards_html(tasks: list) -> str:
    cards = ""
    for name, desc, credit in tasks:
        cards += f"""
<div style="border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;">
  <div style="font-weight:700;color:#1a1a2e;font-size:0.95rem;margin-bottom:4px;">{name}</div>
  <div style="font-size:0.85rem;color:#6b7280;line-height:1.5;">{desc}</div>
  <div style="font-size:0.78rem;color:#9ca3af;margin-top:6px;">Uses {credit}</div>
</div>"""
    return cards


# ── Build email HTML ─────────────────────────────────────────────────────
def build_email(template_path: Path, p: dict, platform: str, email_num: int) -> str:
    html = template_path.read_text()

    domain   = p["domain"]
    pname    = p["product_name"]
    pid      = p["product_id"]
    greeting = "Hi there,"  # test — real sends use first name

    replacements = {
        "{{PRODUCT_NAME}}":       pname,
        "{{PRODUCT_ID}}":         pid,
        "{{DOMAIN}}":             domain,
        "{{ACCENT_COLOR}}":       p["accent_color"],
        "{{GREETING}}":           greeting,
        "{{SKILL_COUNT}}":        str(p["skill_count"]),
        "{{TASK_LIBRARY_URL}}":   f"https://{domain}/task-library",
        "{{GETTING_STARTED_URL}}":f"https://{domain}/getting-started",
        "{{UNSUBSCRIBE_URL}}":    f"https://{domain}/unsubscribe",
        "{{FEEDBACK_BASE}}": "https://api.lawtasksai.com/v1/feedback",
        "{{USER_EMAIL}}": urllib.parse.quote(TEST_EMAIL),
        "{{PRODUCT_ID}}": pid,
        "{{PRODUCT_WHY}}":        p.get("product_why", "professionals deserve better tools"),
        "{{INSTALL_BLOCK}}":      platform_install_block(platform, p),
        "{{TASK_CARDS}}":         task_cards_html(p["sample_tasks"]),
        "{{FIRST_PROMPT}}":       f'"What {p["occupation"].rstrip("s")} tasks can you help me with?"',
    }

    for k, v in replacements.items():
        html = html.replace(k, v)

    return html


# ── Main ─────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*55}")
    print(f"TasksAI Drip Email Test Sender")
    print(f"  To:       {TEST_EMAIL}")
    print(f"  Platform: {TEST_PLATFORM}")
    print(f"  Product:  {PRODUCT['product_name']}")
    print(f"{'='*55}\n")

    print("Getting Zoho Mail token...")
    token = get_zoho_mail_token()
    print("✓ Token obtained\n")

    emails = [
        (
            1,
            SCRIPT_DIR / "email1_template.html",
            f"[TEST] Email 1 — Your {PRODUCT['product_name']} credits are ready ({TEST_PLATFORM})",
        ),
        (
            2,
            SCRIPT_DIR / "email2_template.html",
            f"[TEST] Email 2 — Have you run your first {PRODUCT['product_name']} task yet?",
        ),
        (
            3,
            SCRIPT_DIR / "email3_template.html",
            f"[TEST] Email 3 — Quick question about {PRODUCT['product_name']}",
        ),
    ]

    for num, template_path, subject in emails:
        print(f"Sending Email {num}: {subject[:60]}...")
        html = build_email(template_path, PRODUCT, TEST_PLATFORM, num)
        ok = send_email(subject, html, token)
        print(f"  {'✅ Sent' if ok else '❌ Failed'}\n")

    print("Done. Check kentmercier@gmail.com for all 3 emails.")
    print("To test a different platform: python3 send_drip_test.py <email> <platform>")
    print("Platforms: claude_desktop | openclaw | cursor | windsurf | cline | other\n")


if __name__ == "__main__":
    main()
