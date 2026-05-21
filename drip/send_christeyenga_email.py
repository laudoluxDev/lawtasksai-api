#!/usr/bin/env python3
"""
One-shot: send account mix-up explanation email to christeyenga8@gmail.com
"""
import json, urllib.request, urllib.parse, pathlib, os, sys

TOKEN_FILE      = pathlib.Path.home() / ".config/zoho-mail-tokens.json"
_tok_data       = json.loads(TOKEN_FILE.read_text())
ZOHO_ACCOUNT_ID = _tok_data["account_id"]

RECIPIENT   = "christeyenga8@gmail.com"
FROM_ADDR   = f"=?UTF-8?B?{__import__('base64').b64encode('TasksAI Team'.encode()).decode()}?= <kent@lawtasksai.com>"
SUBJECT     = "We fixed a mix-up on your account"
UNSUB_URL   = "https://api.lawtasksai.com/waitlist/unsubscribe?email=christeyenga8@gmail.com&product_id=chiropractor&token=b86f73409a161238e6b2885a16432a1f"


def get_zoho_token():
    t = json.loads(TOKEN_FILE.read_text())
    data = urllib.parse.urlencode({
        "refresh_token": t["refresh_token"],
        "client_id": t["client_id"],
        "client_secret": t["client_secret"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://accounts.zoho.com/oauth/v2/token", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["access_token"]


HTML = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f9fafb; margin: 0; padding: 0; }}
  .wrapper {{ max-width: 560px; margin: 40px auto; background: white; border-radius: 12px; border: 1px solid #e5e7eb; overflow: hidden; }}
  .header {{ background: #0ea5e9; padding: 28px 36px; }}
  .header h1 {{ color: white; font-size: 1.2rem; margin: 0; font-weight: 700; }}
  .body {{ padding: 32px 36px; color: #374151; line-height: 1.65; font-size: 0.95rem; }}
  .body p {{ margin: 0 0 16px; }}
  .cta {{ display: inline-block; margin: 8px 0 16px; padding: 12px 24px; background: #0ea5e9; color: white !important; border-radius: 8px; font-weight: 600; text-decoration: none; font-size: 0.95rem; }}
  .footer {{ padding: 20px 36px; border-top: 1px solid #e5e7eb; font-size: 0.78rem; color: #9ca3af; }}
  .footer a {{ color: #9ca3af; }}
</style>
</head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>ChiropractorTasksAI</h1>
  </div>
  <div class="body">
    <p>Hi Christian,</p>
    <p>We had a data issue on our end today that affected your account — your original ChiropractorTasksAI signup got caught in a cleanup process it shouldn't have been.</p>
    <p>We also noticed you were accidentally added to our companion site, LawTasksAI. We've removed you from there — and since you never downloaded or installed anything from that site, there's nothing to uninstall on your end.</p>
    <p>Here's where things stand: you're now on the ChiropractorTasksAI waitlist. When it launches, you'll be first to know and you'll get a free trial.</p>
    <p>If you happen to be an attorney and actually wanted a LawTasksAI account, you're welcome to sign up at <a href="https://lawtasksai.com/signup" style="color:#0ea5e9;">lawtasksai.com/signup</a> — you'll get 5 free credits just like everyone else.</p>
    <p>We're sorry for the confusion.</p>
    <p style="margin-top: 24px;">— The ChiropractorTasksAI Team</p>
  </div>
  <div class="footer">
    <p>You're receiving this because you signed up at chiropractortasksai.com.<br>
    <a href="{UNSUB_URL}">Unsubscribe from ChiropractorTasksAI waitlist</a></p>
  </div>
</div>
</body>
</html>"""


def send_email(token):
    payload = json.dumps({
        "fromAddress": FROM_ADDR,
        "toAddress": RECIPIENT,
        "subject": SUBJECT,
        "content": HTML,
        "mailFormat": "html"
    }).encode()
    req = urllib.request.Request(
        f"https://mail.zoho.com/api/accounts/{ZOHO_ACCOUNT_ID}/messages",
        data=payload,
        headers={
            "Authorization": f"Zoho-oauthtoken {token}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        result = json.loads(r.read())
        code = str(result.get("status", {}).get("code", ""))
        return code == "200", result


if __name__ == "__main__":
    print("Fetching Zoho token...")
    token = get_zoho_token()
    print(f"Token: {token[:20]}...")

    print(f"Sending to {RECIPIENT}...")
    ok, result = send_email(token)

    if ok:
        print(f"✅ Sent successfully")
    else:
        print(f"❌ Failed: {result}")
        sys.exit(1)
