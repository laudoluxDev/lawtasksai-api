#!/usr/bin/env python3
"""
Re-engagement email sender — new installer campaign
Sends vertical-branded HTML email to each user from hello@{vertical}tasksai.com

Usage:
  python3 send_reengagement.py [--dry-run]

Sends in batches of 5 with 90s gaps to reduce spam scoring risk.
All sends logged to /tmp/reengagement_send_log.json
"""

import json
import sys
import time
import uuid
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path

DRY_RUN = '--dry-run' in sys.argv
BATCH_SIZE = 5
BATCH_PAUSE_SECONDS = 90  # between batches
EMAIL_PAUSE_SECONDS = 8   # between individual sends

ZOHO_TOKEN_FILE = Path.home() / '.config' / 'zoho-mail-tokens.json'
TEMPLATE_FILE   = Path(__file__).parent / 'reengagement_template.html'
VERTICALS_FILE  = Path('/Users/clio/dev/tasksai-landing-template/verticals.json')
SEND_LIST_FILE  = Path('/tmp/send_list.json')
LOG_FILE        = Path('/tmp/reengagement_send_log.json')

# Law vertical not in verticals.json — define inline
LAW_VERTICAL = {
    'product_id': 'law', 'name': 'LawTasksAI', 'accent_color': '#2563eb',
    'domain': 'lawtasksai.com', 'skill_count': 206, 'occupation': 'legal',
}

def load_verticals():
    with open(VERTICALS_FILE) as f:
        data = json.load(f)
    v = {item['product_id']: item for item in data}
    v['law'] = LAW_VERTICAL
    return v

def get_access_token():
    with open(ZOHO_TOKEN_FILE) as f:
        creds = json.load(f)
    data = urllib.parse.urlencode({
        'refresh_token': creds['refresh_token'],
        'client_id':     creds['client_id'],
        'client_secret': creds['client_secret'],
        'grant_type':    'refresh_token',
    }).encode()
    req = urllib.request.Request(
        'https://accounts.zoho.com/oauth/v2/token', data=data, method='POST')
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())['access_token'], creds['account_id']

def build_email(template, vertical, user, message_id):
    first_name = (user.get('name') or 'there').split()[0].title()
    email = user['email']
    license_key = user.get('license_key', '')
    product_id = vertical['product_id']
    domain = vertical['domain']

    # Plain text version (fallback)
    plain = f"""Hi {first_name},

You signed up for {vertical['name']} a while back and grabbed 5 free credits — but you haven't run a task yet.

We think we know why: the installer had a bug.

We've migrated to a native Windows .exe installer (and a Mac equivalent) — a much smoother experience. It's a single double-click file: no terminal, no config files, no JSON editing.

Your credits are still there. They never expire.

Here's all you need to do:
1. Go to https://{domain}/getting-started.html?utm_source=email&utm_medium=reengagement&utm_campaign=installer-v2&utm_content=plain-text
2. Click Download Installer (Mac or Windows)
3. Double-click it — your license key is pre-filled automatically
4. Restart Claude Desktop (or Cursor / Windsurf / Cline)

Windows users: If you see "Windows protected your PC", click More info → Run anyway.

To confirm it's working, open your AI client and type:
  Check my {vertical['name']} balance

Your license key: {license_key}

Questions? Reply to this email — we respond to every message.

— The {vertical['name']} Team

Unsubscribe: https://{domain}/unsubscribe.html?email={urllib.parse.quote(email)}
"""

    # HTML version
    html = template
    html = html.replace('{{PRODUCT_NAME}}',   vertical['name'])
    html = html.replace('{{PRODUCT_ID}}',     product_id)
    html = html.replace('{{ACCENT_COLOR}}',   vertical['accent_color'])
    html = html.replace('{{DOMAIN}}',         domain)
    html = html.replace('{{SKILL_COUNT}}',    str(vertical['skill_count']))
    html = html.replace('{{OCCUPATION}}',     vertical.get('occupation', 'professional'))
    html = html.replace('{{FIRST_NAME}}',     first_name)
    html = html.replace('{{LICENSE_KEY}}',    license_key)
    html = html.replace('{{MESSAGE_ID}}',     message_id)
    html = html.replace('{{EMAIL_ENCODED}}',  urllib.parse.quote(email))

    return html, plain

def send_email(access_token, account_id, from_addr, to_addr, subject, html, plain):
    payload = json.dumps({
        'fromAddress': from_addr,
        'toAddress':   to_addr,
        'subject':     subject,
        'mailFormat':  'html',
        'content':     html,
        'textContent': plain,
    }).encode('utf-8')
    req = urllib.request.Request(
        f'https://mail.zoho.com/api/accounts/{account_id}/messages',
        data=payload,
        headers={
            'Authorization': f'Zoho-oauthtoken {access_token}',
            'Content-Type':  'application/json',
        },
        method='POST'
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def main():
    print(f"{'[DRY RUN] ' if DRY_RUN else ''}Re-engagement send — {datetime.now().strftime('%Y-%m-%d %H:%M MT')}")
    print(f"Batch size: {BATCH_SIZE} | Pause between batches: {BATCH_PAUSE_SECONDS}s\n")

    verticals = load_verticals()
    with open(TEMPLATE_FILE) as f:
        template = f.read()
    with open(SEND_LIST_FILE) as f:
        users = json.load(f)

    if not DRY_RUN:
        access_token, account_id = get_access_token()
    else:
        access_token, account_id = 'dry-run', 'dry-run'

    log = []
    errors = []

    for i, user in enumerate(users):
        product_id = user.get('product_id', 'law')
        vertical = verticals.get(product_id, LAW_VERTICAL)
        email = user['email']
        name = user.get('name') or 'there'
        from_addr = f"hello@{'lawtasksai' if product_id == 'law' else product_id + 'tasksai'}.com"
        subject = f"Your {vertical['name']} credits are waiting — new installer inside"
        message_id = f"reeng-{product_id}-{uuid.uuid4().hex[:10]}"

        html, plain = build_email(template, vertical, user, message_id)

        print(f"  [{i+1}/{len(users)}] {name} <{email}> — {vertical['name']} — from: {from_addr}")

        if DRY_RUN:
            log.append({'status': 'dry-run', 'email': email, 'product': product_id, 'message_id': message_id})
            print(f"         [DRY RUN — would send message_id: {message_id}]")
        else:
            try:
                result = send_email(access_token, account_id, from_addr, email, subject, html, plain)
                status = result.get('status', {}).get('description', 'unknown')
                log.append({'status': status, 'email': email, 'product': product_id,
                            'message_id': message_id, 'sent_at': datetime.utcnow().isoformat()})
                print(f"         ✅ {status} | mid: {message_id}")
            except Exception as e:
                errors.append({'email': email, 'error': str(e)})
                log.append({'status': 'error', 'email': email, 'product': product_id,
                            'error': str(e), 'sent_at': datetime.utcnow().isoformat()})
                print(f"         ❌ ERROR: {e}")

        # Pause between sends
        if not DRY_RUN and i < len(users) - 1:
            time.sleep(EMAIL_PAUSE_SECONDS)

        # Batch pause
        if not DRY_RUN and (i + 1) % BATCH_SIZE == 0 and i < len(users) - 1:
            print(f"\n  --- Batch of {BATCH_SIZE} complete. Pausing {BATCH_PAUSE_SECONDS}s before next batch ---\n")
            time.sleep(BATCH_PAUSE_SECONDS)

    # Write log
    with open(LOG_FILE, 'w') as f:
        json.dump(log, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Done. {len([l for l in log if l['status'] not in ('error','dry-run')])} sent, {len(errors)} errors")
    print(f"Log: {LOG_FILE}")
    if errors:
        print(f"\nErrors:")
        for e in errors:
            print(f"  {e['email']}: {e['error']}")

if __name__ == '__main__':
    main()
