"""Corrective Email 1 blast — sends correct vertical email to users who were mislabeled as law."""
import json, pathlib, urllib.request, urllib.parse, time

ACCOUNT_ID = "6556209000000008002"
ADMIN_SECRET = "2e3b1d4149297c9fe9bb0a4ea5be5a57b6dc28ed7f38cd3a5bf0092c44398643"
API_BASE = "https://api.lawtasksai.com"
TOKEN_FILE = pathlib.Path.home() / ".config/zoho-mail-tokens.json"

PRODUCT_META = {
    "farmer":   ("FarmerTasksAI","farmertasksai.com","193","#16a34a",
                 "I need help writing a crop rotation plan for my operation",
                 "FarmerTasksAI gives you 193 expert farming workflows — from crop planning and soil analysis to grant writing and equipment checklists.",
                 "🌱 Crop Rotation Planner|🧪 Soil Amendment Calculator|📋 USDA Grant Writer"),
    "realtor":  ("RealtorTasksAI","realtortasksai.com","169","#0EA5E9",
                 "I need help writing a comparative market analysis for a 3-bed home",
                 "RealtorTasksAI gives you 169 expert real estate workflows — from CMA reports and listing descriptions to buyer presentations and contract reviews.",
                 "📊 CMA Report Generator|🏡 Listing Description Writer|🤝 Buyer Presentation Builder"),
    "teacher":  ("TeacherTasksAI","teachertasksai.com","167","#7C3AED",
                 "I need help writing parent progress updates for my class",
                 "TeacherTasksAI gives you 167 expert education workflows — from lesson planning and IEP summaries to report card comments and parent communications.",
                 "📝 Report Card Comment Generator|🎯 IEP Goal Summary|📬 Parent Progress Update"),
    "therapist":("TherapistTasksAI","therapisttasksai.com","155","#0891B2",
                 "I need help writing a treatment plan summary for a new patient",
                 "TherapistTasksAI gives you 155 expert clinical workflows — from treatment plans and progress notes to intake summaries and session documentation.",
                 "🧠 Treatment Plan Builder|📋 Progress Note Writer|📄 Intake Summary Generator"),
    "dentist":  ("DentistTasksAI","dentisttasksai.com","170","#0284C7",
                 "I need help writing a patient treatment summary for a crown procedure",
                 "DentistTasksAI gives you 170 expert dental workflows — from treatment summaries and insurance narratives to patient communications.",
                 "🦷 Treatment Summary Writer|📝 Insurance Narrative Builder|📬 Patient Communication Drafter"),
}

USERS = {
    "farmer":    [("jameswigen24@gmail.com","James"),("miguel.d.mirano@student.wwcc.edu","Miguel"),
                  ("grayson.wallis@wwcc.edu","Grayson"),("brittalarsen4@gmail.com","Britta"),("pettitt@champlain.edu","")],
    "realtor":   [("warddsrbisson@gmail.com",""),("veronicajennings049@gmail.com","Veronica")],
    "teacher":   [("rspr41@gmail.com",""),("vismaya.v.m.nair@gmail.com","Vismaya")],
    "therapist": [("mikebenhaven@gmail.com","Mike"),("kaylabayla2032@gmail.com","Kayla")],
    "dentist":   [("kentmercier@gmail.com","Kent")],
}

# Get Zoho token
t = json.loads(TOKEN_FILE.read_text())
data = urllib.parse.urlencode({"refresh_token":t["refresh_token"],"client_id":t["client_id"],"client_secret":t["client_secret"],"grant_type":"refresh_token"}).encode()
with urllib.request.urlopen(urllib.request.Request("https://accounts.zoho.com/oauth/v2/token",data=data,method="POST"),timeout=10) as r:
    token = json.loads(r.read())["access_token"]
print("✓ Token obtained\n")

tmpl = (pathlib.Path(__file__).parent / "email1_template.html").read_text()

def task_cards_html(cards_str):
    html = ""
    for card in cards_str.split("|"):
        html += f"<div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:10px;'><div style='font-weight:700;font-size:0.9rem;color:#1a1a2e;'>{card.strip()}</div></div>"
    return html

def install_block(domain):
    gs = f"https://{domain}/getting-started"
    platforms = [
        ("claude-desktop", "Claude Desktop",  "Most popular",        "Anthropic's free app"),
        ("cursor",         "Cursor",           "AI code editor",      "Great for professionals"),
        ("windsurf",       "Windsurf",         "AI code editor",      "Clean, fast interface"),
        ("cline",          "Cline",            "VS Code extension",   "Inside VS Code"),
        ("claude-code",    "Claude Code",      "Anthropic CLI",       "Terminal-based"),
        ("openclaw",       "OpenClaw",         "Alternative",         "One command, no config"),
    ]
    selector_style = ("display:inline-block;text-decoration:none;border:2px solid #e5e7eb;"
                      "border-radius:8px;padding:10px 16px;margin:4px;background:#ffffff;"
                      "min-width:130px;text-align:center;vertical-align:top;")
    label_style = "display:block;font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.05em;color:#9ca3af;margin-bottom:2px;"
    name_style  = "display:block;font-size:0.92rem;font-weight:700;color:#1a1a2e;"
    note_style  = "display:block;font-size:0.75rem;color:#6b7280;margin-top:2px;"
    cards = "".join(
        f"<a href='{gs}?platform={slug}' style='{selector_style}'>"
        f"<span style='{label_style}'>{sub}</span>"
        f"<span style='{name_style}'>{label}</span>"
        f"<span style='{note_style}'>{note}</span>"
        f"</a>"
        for slug, label, sub, note in platforms
    )
    return (
        f"<div style='background:#f9fafb;border:2px solid #e5e7eb;border-radius:10px;padding:24px;margin:20px 0;'>"
        f"<div style='margin-bottom:6px;'>"
        f"<span style='font-size:1.1rem;font-weight:800;color:#1a1a2e;'>Experience our new installer!</span>&nbsp;"
        f"<span style='display:inline-block;background:#f59e0b;color:#ffffff;font-size:0.7rem;font-weight:800;"
        f"padding:2px 9px;border-radius:20px;letter-spacing:0.05em;text-transform:uppercase;'>✨ New</span>"
        f"</div>"
        f"<p style='font-size:0.88rem;color:#4b5563;margin:0 0 16px;line-height:1.5;'>"
        f"One-click setup — no terminal, no config files. Select your AI client to get started:"
        f"</p>"
        f"<div style='margin-bottom:16px;'>{cards}</div>"
        f"<p style='margin:0;font-size:0.82rem;background:#ecfdf5;border:1px solid #bbf7d0;border-radius:6px;"
        f"padding:10px 14px;color:#065f46;'>"
        f"✅ The installer handles everything automatically — enter your license key, click Install, restart your AI client."
        f"</p></div>"
    )

def send(to, from_addr, subject, html):
    payload = json.dumps({"fromAddress":from_addr,"toAddress":to,"subject":subject,"content":html,"mailFormat":"html"}).encode()
    req = urllib.request.Request(f"https://mail.zoho.com/api/accounts/{ACCOUNT_ID}/messages",data=payload,headers={"Authorization":f"Zoho-oauthtoken {token}","Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=15) as r:
        return str(json.loads(r.read()).get("status",{}).get("code",""))=="200"

def record(email, pid, subject):
    try:
        payload = json.dumps({"email":email,"product_id":pid,"email_number":1,"subject":subject}).encode()
        urllib.request.urlopen(urllib.request.Request(f"{API_BASE}/admin/drip-record",data=payload,headers={"X-Admin-Secret":ADMIN_SECRET,"Content-Type":"application/json"},method="POST"),timeout=10)
    except: pass

sent=0; failed=0
for pid, users in USERS.items():
    pname,domain,count,accent,prompt,why,cards = PRODUCT_META[pid]
    print(f"\n=== {pname} ===")
    for email, first_name in users:
        greeting = f"Hi {first_name}," if first_name else "Hi there,"
        enc = urllib.parse.quote(email)
        html = tmpl
        for k,v in {
            "{{PRODUCT_NAME}}":pname,"{{PRODUCT_ID}}":pid,"{{PLATFORM_LABEL}}":"your AI client",
            "{{INSTALL_BLOCK}}":install_block(domain),
            "{{TASK_LIBRARY_URL}}":f"https://{domain}/task-library",
            "{{GETTING_STARTED_URL}}":f"https://{domain}/getting-started",
            "{{SUPPORT_EMAIL}}":f"hello@{domain}",
            "{{FEEDBACK_BASE}}":"https://api.lawtasksai.com/v1/feedback",
            "{{USER_EMAIL}}":enc,"{{GREETING}}":greeting,"{{FIRST_PROMPT}}":prompt,
            "{{SKILL_COUNT}}":count,"{{ACCENT_COLOR}}":accent,"{{DOMAIN}}":domain,
            "{{PRODUCT_WHY}}":why,"{{TASK_CARDS}}":task_cards_html(cards),
            "{{UNSUBSCRIBE_URL}}":f"https://{domain}/unsubscribe?email={enc}",
        }.items():
            html = html.replace(k,v)
        subject = f"{pname}: New one-click installer is here"
        print(f"  {email}...", end=" ", flush=True)
        if send(email, f"{pname} Team <hello@{domain}>", subject, html):
            print("✅"); sent+=1; record(email, pid, subject)
        else:
            print("❌"); failed+=1
        time.sleep(0.5)

print(f"\n{'='*50}\nDone: {sent} sent, {failed} failed")
