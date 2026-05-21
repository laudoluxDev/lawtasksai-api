"""
drip_utils.py — TasksAI drip email utilities.
Used by main.py to send transactional drip emails (Email 1/2/3)
with per-vertical content and per-user platform install blocks.
"""

import json, os, urllib.parse
from pathlib import Path

DRIP_DIR = Path(__file__).parent
ZOHO_ACCOUNT_ID = "6556209000000008002"

# ── Per-vertical config ──────────────────────────────────────────────────
# accent_color, occupation, product_why, and sample_tasks are unique per vertical.
# domain and skill_count are pulled from the DB at send time.

VERTICAL_CONFIG = {
    "law": {
        "accent_color": "#2563eb",
        "occupation": "attorneys",
        "product_why": "every attorney deserves expert legal research and drafting support — not just the ones at big firms",
        "sample_tasks": [
            ("Statute of Limitations Analyzer", "Enter a cause of action and jurisdiction — get the exact limitations period, tolling rules, and key cases.", "1 credit"),
            ("Demand Letter Drafter", "Generates a professionally worded demand letter with proper legal citations for your jurisdiction.", "1 credit"),
            ("Deposition Question Generator", "Produces a full deposition question set tailored to the witness type and case theory.", "1 credit"),
        ],
    },
    "realtor": {
        "accent_color": "#dc2626",
        "occupation": "realtors",
        "product_why": "every agent deserves professional-grade listing copy, contract analysis, and client communication tools",
        "sample_tasks": [
            ("Listing Description Writer", "Enter property details — get compelling MLS copy tailored to your market.", "1 credit"),
            ("Offer Comparison Analyzer", "Compare multiple offers side-by-side with a clear recommendation summary.", "1 credit"),
            ("Buyer Consultation Prep", "Generates a full agenda and talking points for your buyer consultation meeting.", "1 credit"),
        ],
    },
    "farmer": {
        "accent_color": "#16a34a",
        "occupation": "farmers",
        "product_why": "every farmer deserves access to expert agronomic advice, grant research, and business planning tools",
        "sample_tasks": [
            ("Crop Rotation Planner", "Input your fields and crops — get a multi-season rotation plan with yield projections.", "1 credit"),
            ("USDA Grant Finder", "Identifies applicable USDA programs for your operation type and location.", "1 credit"),
            ("Farm Lease Analyzer", "Reviews your farm lease terms and flags common problem clauses.", "1 credit"),
        ],
    },
    "teacher": {
        "accent_color": "#7c3aed",
        "occupation": "teachers",
        "product_why": "every educator deserves tools that reduce prep time and help them focus on what matters — their students",
        "sample_tasks": [
            ("Lesson Plan Generator", "Enter your grade, subject, and standard — get a complete lesson plan with activities.", "1 credit"),
            ("Rubric Builder", "Creates a detailed grading rubric for any assignment type and grade level.", "1 credit"),
            ("Parent Email Drafter", "Writes professional parent communication for any situation — behavior, progress, or concerns.", "1 credit"),
        ],
    },
    "therapist": {
        "accent_color": "#0891b2",
        "occupation": "therapists",
        "product_why": "every therapist deserves administrative support that lets them spend more time with clients",
        "sample_tasks": [
            ("Session Note Generator", "Enter session highlights — get a complete SOAP note ready for your EHR.", "1 credit"),
            ("Treatment Plan Drafter", "Creates a structured treatment plan based on diagnosis, goals, and interventions.", "1 credit"),
            ("Intake Form Analyzer", "Summarizes a new client intake form and flags key clinical considerations.", "1 credit"),
        ],
    },
    "marketing": {
        "accent_color": "#ea580c",
        "occupation": "marketers",
        "product_why": "every marketer deserves AI tools built for their workflow — not generic chatbots",
        "sample_tasks": [
            ("Campaign Brief Generator", "Enter your product and goals — get a complete campaign brief ready to share.", "1 credit"),
            ("Ad Copy Writer", "Generates platform-specific ad copy (Google, Meta, LinkedIn) for any offer.", "1 credit"),
            ("Competitor Analysis Summarizer", "Analyzes competitor positioning and surfaces key differentiation opportunities.", "1 credit"),
        ],
    },
    "contractor": {
        "accent_color": "#f97316",
        "occupation": "contractors",
        "product_why": "every contractor deserves professional proposals, contracts, and client communication tools",
        "sample_tasks": [
            ("Project Proposal Generator", "Enter project scope and pricing — get a professional proposal ready to send.", "1 credit"),
            ("Change Order Drafter", "Generates a formal change order with cost breakdown and approval signature block.", "1 credit"),
            ("Subcontractor Agreement", "Creates a basic subcontractor agreement tailored to your project type.", "1 credit"),
        ],
    },
    "accounting": {
        "accent_color": "#1d4ed8",
        "occupation": "accountants",
        "product_why": "every accountant deserves tools that handle the research and drafting so they can focus on clients",
        "sample_tasks": [
            ("Tax Research Summarizer", "Enter a tax question — get a plain-English answer with relevant code sections.", "1 credit"),
            ("Client Letter Drafter", "Generates professional client letters for any tax or advisory situation.", "1 credit"),
            ("Engagement Letter Creator", "Creates a complete engagement letter for any service type.", "1 credit"),
        ],
    },
    "chiropractor": {
        "accent_color": "#0d9488",
        "occupation": "chiropractors",
        "product_why": "every chiropractor deserves clinical documentation tools that keep up with their patient load",
        "sample_tasks": [
            ("SOAP Note Generator", "Enter visit details — get a complete chiropractic SOAP note.", "1 credit"),
            ("Treatment Plan Drafter", "Creates a structured care plan with visit frequency and goals.", "1 credit"),
            ("Insurance Appeal Letter", "Drafts a professional appeal for denied chiropractic claims.", "1 credit"),
        ],
    },
    "dentist": {
        "accent_color": "#38bdf8",
        "occupation": "dentists",
        "product_why": "every dental practice deserves tools that reduce documentation time and improve patient communication",
        "sample_tasks": [
            ("Treatment Note Generator", "Enter procedure details — get a complete clinical note.", "1 credit"),
            ("Patient Education Script", "Creates a clear, friendly explanation of any procedure for patients.", "1 credit"),
            ("Insurance Narrative Writer", "Drafts a supporting narrative for any dental insurance claim.", "1 credit"),
        ],
    },
    "designer": {
        "accent_color": "#ec4899",
        "occupation": "designers",
        "product_why": "every designer deserves tools that handle the business side so they can focus on the creative",
        "sample_tasks": [
            ("Project Proposal Generator", "Enter scope and budget — get a professional design proposal.", "1 credit"),
            ("Client Brief Analyzer", "Summarizes a client brief and identifies ambiguities to clarify upfront.", "1 credit"),
            ("Revision Policy Drafter", "Creates a clear revision policy for any project type.", "1 credit"),
        ],
    },
    "electrician": {
        "accent_color": "#eab308",
        "occupation": "electricians",
        "product_why": "every electrician deserves tools that handle estimates, code lookups, and client communication",
        "sample_tasks": [
            ("Job Estimate Generator", "Enter project details — get a professional estimate with line items.", "1 credit"),
            ("NEC Code Lookup", "Get a plain-English explanation of any NEC code section.", "1 credit"),
            ("Service Agreement Drafter", "Creates a basic service agreement for residential or commercial work.", "1 credit"),
        ],
    },
    "eventplanner": {
        "accent_color": "#a855f7",
        "occupation": "event planners",
        "product_why": "every event planner deserves tools that handle the logistics and documentation so they can focus on execution",
        "sample_tasks": [
            ("Event Proposal Generator", "Enter event details and budget — get a complete client proposal.", "1 credit"),
            ("Vendor Contract Reviewer", "Flags key clauses and risks in any vendor contract.", "1 credit"),
            ("Run-of-Show Builder", "Creates a detailed run-of-show timeline for any event type.", "1 credit"),
        ],
    },
    "funeral": {
        "accent_color": "#374151",
        "occupation": "funeral directors",
        "product_why": "every funeral home deserves tools that handle the documentation so staff can focus on families",
        "sample_tasks": [
            ("Obituary Writer", "Enter family details — get a professionally written obituary.", "1 credit"),
            ("Family Communication Drafter", "Creates compassionate correspondence for any family situation.", "1 credit"),
            ("Service Planning Guide", "Generates a complete service planning checklist for any arrangement type.", "1 credit"),
        ],
    },
    "hr": {
        "accent_color": "#0ea5e9",
        "occupation": "HR professionals",
        "product_why": "every HR team deserves tools that handle policy drafting, job descriptions, and employee communication",
        "sample_tasks": [
            ("Job Description Writer", "Enter role requirements — get a complete, compliant job description.", "1 credit"),
            ("Performance Review Drafter", "Creates a structured performance review for any role and rating.", "1 credit"),
            ("HR Policy Generator", "Drafts a professional policy document for any HR topic.", "1 credit"),
        ],
    },
    "insurance": {
        "accent_color": "#1e40af",
        "occupation": "insurance professionals",
        "product_why": "every insurance professional deserves tools that handle research, proposals, and client communication",
        "sample_tasks": [
            ("Coverage Comparison Builder", "Compare multiple policies side-by-side with a clear summary.", "1 credit"),
            ("Client Proposal Generator", "Creates a professional insurance proposal for any coverage type.", "1 credit"),
            ("Claims Letter Drafter", "Generates a professional claims support letter for any situation.", "1 credit"),
        ],
    },
    "landlord": {
        "accent_color": "#059669",
        "occupation": "landlords",
        "product_why": "every landlord deserves tools that handle leases, notices, and tenant communication professionally",
        "sample_tasks": [
            ("Lease Agreement Generator", "Enter property and tenant details — get a state-compliant lease.", "1 credit"),
            ("Notice to Pay or Quit", "Generates a legally proper notice for your jurisdiction.", "1 credit"),
            ("Tenant Communication Drafter", "Creates professional letters for any landlord-tenant situation.", "1 credit"),
        ],
    },
    "militaryspouse": {
        "accent_color": "#1d4ed8",
        "occupation": "military spouses",
        "product_why": "every military spouse deserves tools that help them navigate career transitions, benefits, and relocation",
        "sample_tasks": [
            ("Resume Builder", "Creates a career-gap-friendly resume tailored to your background.", "1 credit"),
            ("Benefits Navigator", "Explains your entitlements for any PCS move or life event.", "1 credit"),
            ("Cover Letter Writer", "Generates a compelling cover letter for any job application.", "1 credit"),
        ],
    },
    "mortgage": {
        "accent_color": "#0369a1",
        "occupation": "mortgage professionals",
        "product_why": "every mortgage professional deserves tools that handle disclosures, client communication, and scenario analysis",
        "sample_tasks": [
            ("Loan Scenario Explainer", "Creates a clear comparison of loan options for any borrower situation.", "1 credit"),
            ("Pre-Approval Letter Drafter", "Generates a professional pre-approval letter for any loan type.", "1 credit"),
            ("Client Update Email", "Writes a professional status update for any stage of the loan process.", "1 credit"),
        ],
    },
    "mortuary": {
        "accent_color": "#4b5563",
        "occupation": "mortuary professionals",
        "product_why": "every mortuary deserves tools that handle documentation and family communication with dignity",
        "sample_tasks": [
            ("Obituary Writer", "Enter family details — get a professionally written obituary.", "1 credit"),
            ("Family Letter Drafter", "Creates compassionate correspondence for any family situation.", "1 credit"),
            ("Arrangement Checklist", "Generates a complete arrangement checklist for any service type.", "1 credit"),
        ],
    },
    "nutritionist": {
        "accent_color": "#65a30d",
        "occupation": "nutritionists",
        "product_why": "every nutritionist deserves tools that handle meal planning, client education, and documentation",
        "sample_tasks": [
            ("Meal Plan Generator", "Enter dietary goals and restrictions — get a complete weekly meal plan.", "1 credit"),
            ("Client Education Handout", "Creates a professional nutrition handout for any topic.", "1 credit"),
            ("Progress Note Writer", "Generates a structured session note for any client visit.", "1 credit"),
        ],
    },
    "pastor": {
        "accent_color": "#7c3aed",
        "occupation": "pastors",
        "product_why": "every pastor deserves tools that handle sermon research, pastoral care letters, and administrative writing",
        "sample_tasks": [
            ("Sermon Outline Generator", "Enter your text and theme — get a complete sermon outline with illustrations.", "1 credit"),
            ("Pastoral Letter Drafter", "Creates a compassionate letter for any pastoral care situation.", "1 credit"),
            ("Newsletter Article Writer", "Generates engaging church newsletter content for any topic.", "1 credit"),
        ],
    },
    "personaltrainer": {
        "accent_color": "#dc2626",
        "occupation": "personal trainers",
        "product_why": "every trainer deserves tools that handle programming, client communication, and business admin",
        "sample_tasks": [
            ("Workout Program Builder", "Enter client goals and fitness level — get a complete training program.", "1 credit"),
            ("Client Progress Report", "Creates a professional progress summary for any client.", "1 credit"),
            ("Training Agreement Drafter", "Generates a professional training service agreement.", "1 credit"),
        ],
    },
    "plumber": {
        "accent_color": "#0284c7",
        "occupation": "plumbers",
        "product_why": "every plumber deserves tools that handle estimates, permits, and client communication",
        "sample_tasks": [
            ("Job Estimate Generator", "Enter project details — get a professional estimate with line items.", "1 credit"),
            ("Service Agreement Drafter", "Creates a basic service agreement for any job type.", "1 credit"),
            ("Code Compliance Checker", "Reviews your planned work against local plumbing code requirements.", "1 credit"),
        ],
    },
    "principal": {
        "accent_color": "#0f766e",
        "occupation": "school principals",
        "product_why": "every principal deserves tools that handle communication, policy drafting, and administrative writing",
        "sample_tasks": [
            ("Parent Newsletter Writer", "Creates a professional school newsletter for any topic.", "1 credit"),
            ("Staff Communication Drafter", "Generates clear, professional staff memos for any situation.", "1 credit"),
            ("Policy Document Generator", "Drafts a complete school policy for any topic.", "1 credit"),
        ],
    },
    "restaurant": {
        "accent_color": "#b45309",
        "occupation": "restaurant owners",
        "product_why": "every restaurant deserves tools that handle staff communication, menu writing, and operations",
        "sample_tasks": [
            ("Menu Description Writer", "Enter your dishes — get compelling, professional menu copy.", "1 credit"),
            ("Staff Policy Generator", "Creates a clear employee policy document for any restaurant situation.", "1 credit"),
            ("Health Inspection Prep Guide", "Generates a complete pre-inspection checklist for your kitchen type.", "1 credit"),
        ],
    },
    "salon": {
        "accent_color": "#db2777",
        "occupation": "salon owners",
        "product_why": "every salon owner deserves tools that handle client communication, policies, and business admin",
        "sample_tasks": [
            ("Service Menu Writer", "Creates professional service descriptions for your menu.", "1 credit"),
            ("Client Consultation Prep", "Generates a structured consultation guide for any service type.", "1 credit"),
            ("Cancellation Policy Drafter", "Creates a clear, professional cancellation and no-show policy.", "1 credit"),
        ],
    },
    "travelagent": {
        "accent_color": "#0891b2",
        "occupation": "travel agents",
        "product_why": "every travel agent deserves tools that handle itineraries, proposals, and client communication",
        "sample_tasks": [
            ("Itinerary Builder", "Enter destination and dates — get a complete day-by-day travel itinerary.", "1 credit"),
            ("Travel Proposal Generator", "Creates a professional travel proposal for any client and destination.", "1 credit"),
            ("Client Email Drafter", "Generates professional travel-related emails for any situation.", "1 credit"),
        ],
    },
    "vet": {
        "accent_color": "#16a34a",
        "occupation": "veterinarians",
        "product_why": "every vet deserves clinical documentation tools that keep up with their patient load",
        "sample_tasks": [
            ("SOAP Note Generator", "Enter visit details — get a complete veterinary SOAP note.", "1 credit"),
            ("Client Education Handout", "Creates a clear, friendly explanation of any condition or treatment.", "1 credit"),
            ("Discharge Instruction Writer", "Generates complete post-visit discharge instructions for any case.", "1 credit"),
        ],
    },
    "churchadmin": {
        "accent_color": "#6366f1",
        "occupation": "church administrators",
        "product_why": "every church deserves administrative tools that handle communication, policy, and planning",
        "sample_tasks": [
            ("Church Newsletter Writer", "Creates engaging newsletter content for any topic or event.", "1 credit"),
            ("Ministry Policy Generator", "Drafts a complete policy document for any church ministry area.", "1 credit"),
            ("Event Planning Checklist", "Generates a comprehensive checklist for any church event.", "1 credit"),
        ],
    },
}

# ── Platform install blocks ──────────────────────────────────────────────

def platform_install_block(platform: str, product_id: str, product_name: str, domain: str) -> str:
    gs_url = f"https://{domain}/getting-started"
    new_badge = "<span style='display:inline-block;background:#f59e0b;color:#ffffff;font-size:0.72rem;font-weight:800;padding:2px 8px;border-radius:20px;letter-spacing:0.04em;text-transform:uppercase;margin-left:8px;'>✨ New Installer!</span>"

    blocks = {
        "claude_desktop": f"""
<div style="background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;padding:12px 16px;margin-bottom:14px;font-size:0.85rem;color:#92400e;">
  ⚠️ <strong>Important:</strong> These instructions are for the <strong>Claude Desktop app</strong>, not claude.ai (the website). They're different products.
  <a href="https://claude.ai/download" style="color:#92400e;font-weight:700;">Download Claude Desktop free →</a>
</div>
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px 24px;margin:16px 0;">
  <h3 style="margin:0 0 12px;font-size:1rem;color:#1a1a2e;">Get set up in 5 minutes {new_badge}</h3>
  <p style="margin:0 0 12px;font-size:0.88rem;color:#4b5563;">Our new one-click installer handles everything automatically:</p>
  <ol style="margin:0;padding-left:20px;color:#4b5563;font-size:0.9rem;line-height:1.9;">
    <li>Click the download button above to get your installer</li>
    <li>Double-click <code style="background:#e5e7eb;padding:1px 5px;border-radius:3px;font-size:0.85em;">{product_name}-Setup.exe</code> (Windows) or <code style="background:#e5e7eb;padding:1px 5px;border-radius:3px;font-size:0.85em;">{product_name}-Setup</code> (Mac)</li>
    <li>Enter your license key when prompted</li>
    <li>Restart Claude Desktop</li>
  </ol>
  <p style="margin:14px 0 0;font-size:0.82rem;background:#ecfdf5;border-radius:6px;padding:10px 12px;color:#065f46;">
    ✅ The installer automatically configures Claude Desktop — no manual file editing needed.
  </p>
</div>
<p style="font-size:0.8rem;color:#9ca3af;text-align:center;margin-top:8px;">
  Using a different AI tool? <a href="{gs_url}" style="color:#6b7280;">Get updated install instructions →</a>
</p>""",

        "openclaw": f"""
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px 24px;margin:16px 0;">
  <h3 style="margin:0 0 12px;font-size:1rem;color:#1a1a2e;">Get set up in 5 minutes {new_badge}</h3>
  <p style="margin:0 0 10px;color:#4b5563;font-size:0.9rem;">Open OpenClaw and run:</p>
  <code style="background:#1a1a2e;color:#7dd3fc;padding:10px 16px;border-radius:6px;display:block;font-size:0.88rem;margin-bottom:10px;">openclaw skills install {product_id}</code>
  <p style="margin:0;color:#6b7280;font-size:0.85rem;">That's it. OpenClaw downloads and configures everything automatically.</p>
</div>
<p style="font-size:0.8rem;color:#9ca3af;text-align:center;margin-top:8px;">
  Using a different AI tool? <a href="{gs_url}" style="color:#6b7280;">Get updated install instructions →</a>
</p>""",

        "cursor": f"""
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px 24px;margin:16px 0;">
  <h3 style="margin:0 0 12px;font-size:1rem;color:#1a1a2e;">Get set up in 5 minutes {new_badge}</h3>
  <p style="margin:0 0 10px;font-size:0.88rem;color:#4b5563;">Our new one-click installer handles everything automatically:</p>
  <ol style="margin:0;padding-left:20px;color:#4b5563;font-size:0.9rem;line-height:1.9;">
    <li>Click the download button above to get your installer</li>
    <li>Double-click the installer and enter your license key</li>
    <li>In Cursor: <strong>Settings → MCP</strong> — confirm {product_name} appears</li>
    <li>Restart Cursor</li>
  </ol>
</div>
<p style="font-size:0.8rem;color:#9ca3af;text-align:center;margin-top:8px;">
  Using a different AI tool? <a href="{gs_url}" style="color:#6b7280;">Get updated install instructions →</a>
</p>""",

        "windsurf": f"""
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px 24px;margin:16px 0;">
  <h3 style="margin:0 0 12px;font-size:1rem;color:#1a1a2e;">Get set up in 5 minutes {new_badge}</h3>
  <p style="margin:0 0 10px;font-size:0.88rem;color:#4b5563;">Our new one-click installer handles everything automatically:</p>
  <ol style="margin:0;padding-left:20px;color:#4b5563;font-size:0.9rem;line-height:1.9;">
    <li>Click the download button above to get your installer</li>
    <li>Double-click the installer and enter your license key</li>
    <li>In Windsurf: click the <strong>MCPs icon</strong> (top-right of Cascade panel) — confirm {product_name} appears</li>
    <li>Restart Windsurf</li>
  </ol>
</div>
<p style="font-size:0.8rem;color:#9ca3af;text-align:center;margin-top:8px;">
  Using a different AI tool? <a href="{gs_url}" style="color:#6b7280;">Get updated install instructions →</a>
</p>""",

        "cline": f"""
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px 24px;margin:16px 0;">
  <h3 style="margin:0 0 12px;font-size:1rem;color:#1a1a2e;">Get set up in 5 minutes {new_badge}</h3>
  <p style="margin:0 0 10px;font-size:0.88rem;color:#4b5563;">Our new one-click installer handles everything automatically:</p>
  <ol style="margin:0;padding-left:20px;color:#4b5563;font-size:0.9rem;line-height:1.9;">
    <li>Click the download button above to get your installer</li>
    <li>Double-click the installer and enter your license key</li>
    <li>In VS Code: <strong>Cline sidebar → MCP Servers → Configure MCP Servers</strong></li>
    <li>Reload VS Code</li>
  </ol>
</div>
<p style="font-size:0.8rem;color:#9ca3af;text-align:center;margin-top:8px;">
  Using a different AI tool? <a href="{gs_url}" style="color:#6b7280;">Get updated install instructions →</a>
</p>""",

        "claude_code": f"""
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px 24px;margin:16px 0;">
  <h3 style="margin:0 0 12px;font-size:1rem;color:#1a1a2e;">Get set up in 5 minutes {new_badge}</h3>
  <p style="margin:0 0 10px;font-size:0.88rem;color:#4b5563;">Our new one-click installer handles everything automatically:</p>
  <ol style="margin:0;padding-left:20px;color:#4b5563;font-size:0.9rem;line-height:1.9;">
    <li>Click the download button above to get your installer</li>
    <li>Double-click the installer and enter your license key</li>
    <li>Start a new Claude Code session</li>
  </ol>
  <p style="margin:14px 0 0;font-size:0.82rem;background:#ecfdf5;border-radius:6px;padding:10px 12px;color:#065f46;">
    ✅ The installer writes to <code>~/.claude.json</code> automatically.
  </p>
</div>
<p style="font-size:0.8rem;color:#9ca3af;text-align:center;margin-top:8px;">
  Using a different AI tool? <a href="{gs_url}" style="color:#6b7280;">Get updated install instructions →</a>
</p>""",
    }

    return blocks.get(platform, f"""
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px 24px;margin:16px 0;">
  <h3 style="margin:0 0 12px;font-size:1rem;color:#1a1a2e;">Get installed</h3>
  <p style="margin:0 0 10px;color:#4b5563;font-size:0.9rem;">Visit your getting-started page for full instructions:</p>
  <a href="{gs_url}" style="color:#2563eb;font-weight:700;">→ {domain}/getting-started</a>
</div>""")


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

def build_drip_email(
    email_num: int,
    product_id: str,
    product_name: str,
    domain: str,
    skill_count: int,
    platform: str,
    first_name: str,
    user_email: str,
    license_key: str = "",
) -> str:
    template_path = DRIP_DIR / f"email{email_num}_template.html"
    html = template_path.read_text()

    cfg = VERTICAL_CONFIG.get(product_id, VERTICAL_CONFIG["law"])
    greeting = f"Hi {first_name}," if first_name else "Hi there,"
    occupation = cfg["occupation"]
    # Use singular for first prompt
    occ_singular = occupation.rstrip("s") if occupation.endswith("s") else occupation

    replacements = {
        "{{PRODUCT_NAME}}":        product_name,
        "{{PRODUCT_ID}}":          product_id,
        "{{DOMAIN}}":              domain,
        "{{ACCENT_COLOR}}":        cfg["accent_color"],
        "{{GREETING}}":            greeting,
        "{{SKILL_COUNT}}":         str(skill_count),
        "{{TASK_LIBRARY_URL}}":    f"https://{domain}/task-library",
        "{{GETTING_STARTED_URL}}": f"https://{domain}/getting-started",
        "{{UNSUBSCRIBE_URL}}":     f"https://api.lawtasksai.com/v1/unsubscribe?email={urllib.parse.quote(user_email)}&product={product_id}",
        "{{FEEDBACK_BASE}}":       "https://api.lawtasksai.com/v1/feedback",
        "{{USER_EMAIL}}":          urllib.parse.quote(user_email),
        "{{PRODUCT_WHY}}":         cfg["product_why"],
        "{{INSTALL_BLOCK}}":       platform_install_block(platform, product_id, product_name, domain),
        "{{TASK_CARDS}}":          task_cards_html(cfg["sample_tasks"]),
        "{{FIRST_PROMPT}}":        f'"What {occ_singular} tasks can you help me with?"',
        "{{LICENSE_KEY}}":         license_key,
    }

    for k, v in replacements.items():
        html = html.replace(k, v)

    return html


# ── Email subjects ───────────────────────────────────────────────────────

def drip_subject(email_num: int, product_name: str) -> str:
    subjects = {
        1: f"You're in — your {product_name} credits are ready",
        2: f"Have you run your first {product_name} task yet?",
        3: f"Quick question about {product_name}",
    }
    return subjects.get(email_num, f"{product_name} — update #{email_num}")
