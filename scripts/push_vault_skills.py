#!/usr/bin/env python3
"""
Push skill content and triggers from vault files to the LawTasksAI database.

Reads from:
  {vault}/Projects/TasksAI-Verticals/{VerticalName}/skills/{skill_id}.md

Each .md file format:
  ---
  name: Prepare FSA program application
  description: Draft an application for a USDA Farm Service Agency program
  category: USDA & Government Program Documentation
  complexity: complex
  product_id: farmer
  triggers:
    - prepare fsa program application
    - help with fsa application
    - usda farm program paperwork
  ---
  
  [Content/prompt below the frontmatter]

Usage:
  python3 push_vault_skills.py                    # Push all verticals
  python3 push_vault_skills.py farmer             # Push one vertical
  python3 push_vault_skills.py farmer --dry-run   # Preview without pushing

Idempotent: uses /admin/skills/bulk which upserts by name+product_id.
Will NOT touch law skills unless explicitly targeted.
"""

import sys, os, json, re, glob, urllib.request

API_BASE = "https://api.lawtasksai.com"
ADMIN_SECRET_PATH = os.path.expanduser("~/.lawtasksai/admin.json")
VAULT_BASE = os.path.expanduser(
    "~/Library/CloudStorage/GoogleDrive-kentmercier@gmail.com/My Drive/"
    "clio-workspace/clio_obsidian_vault/Projects/TasksAI-Verticals"
)

# Map vault folder names to product_ids
VERTICAL_MAP = {
    "AccountingTasksAI": "accounting",
    "ChiropractorTasksAI": "chiropractor",
    "ChurchAdminTasksAI": "church",
    "ContractorTasksAI": "contractor",
    "DentistTasksAI": "dentist",
    "DesignerTasksAI": "designer",
    "ElectricianTasksAI": "electrician",
    "EventPlannerTasksAI": "eventplanner",
    "FarmerTasksAI": "farmer",
    "FuneralTasksAI": "funeral",
    "HRTasksAI": "hr",
    "InsuranceTasksAI": "insurance",
    "LandlordTasksAI": "landlord",
    "MilitarySpouseTasksAI": "militaryspouse",
    "MortgageTasksAI": "mortgage",
    "MorticiaryTasksAI": "mortuary",
    "NutritionistTasksAI": "nutritionist",
    "PastorTasksAI": "pastor",
    "PersonalTrainerTasksAI": "personaltrainer",
    "PlumberTasksAI": "plumber",
    "PrincipalTasksAI": "principal",
    "RealtorTasksAI": "realtor",
    "RestaurantTasksAI": "restaurant",
    "SalonTasksAI": "salon",
    "TeacherTasksAI": "teacher",
    "TherapistTasksAI": "therapist",
    "TravelAgentTasksAI": "travelagent",
    "VetTasksAI": "vet",
}


def load_admin_secret():
    with open(ADMIN_SECRET_PATH) as f:
        return json.load(f)["admin_secret"]


def parse_skill_file(filepath):
    """Parse a skill .md file with YAML-like frontmatter + content body."""
    with open(filepath, encoding="utf-8") as f:
        text = f.read()

    # Split frontmatter from content
    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)', text, re.DOTALL)
    if not fm_match:
        print(f"  SKIP {filepath}: no frontmatter")
        return None

    fm_text = fm_match.group(1)
    content = fm_match.group(2).strip()

    # Simple YAML-like parser (no pyyaml dependency)
    meta = {}
    triggers = []
    in_triggers = False
    for line in fm_text.split('\n'):
        if line.strip().startswith('- ') and in_triggers:
            triggers.append(line.strip()[2:].strip())
            continue
        in_triggers = False
        if ':' in line:
            key, val = line.split(':', 1)
            key = key.strip()
            val = val.strip()
            if key == 'triggers':
                in_triggers = True
                continue
            meta[key] = val

    if not meta.get('name'):
        print(f"  SKIP {filepath}: no name in frontmatter")
        return None

    return {
        "name": meta.get("name", ""),
        "description": meta.get("description", ""),
        "category": meta.get("category", "General"),
        "complexity": meta.get("complexity", "medium"),
        "product_id": meta.get("product_id", ""),
        "trigger_phrases": triggers,
        "content": content,
    }


def push_skills(skills, admin_secret, dry_run=False):
    """Push skills to API in batches. Returns (created, updated, errors)."""
    if dry_run:
        print(f"  DRY RUN: would push {len(skills)} skills")
        return 0, 0, []

    batch_size = 50
    total_created = 0
    total_updated = 0
    total_errors = []

    for i in range(0, len(skills), batch_size):
        batch = skills[i:i + batch_size]
        payload = json.dumps({"skills": batch}).encode()

        req = urllib.request.Request(
            f"{API_BASE}/admin/skills/bulk",
            data=payload,
            headers={
                "X-Admin-Secret": admin_secret,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.loads(r.read())
                total_created += result.get("created", 0)
                total_updated += result.get("updated", 0)
                if result.get("errors"):
                    total_errors.extend(result["errors"])
                print(f"    Batch {i // batch_size + 1}: "
                      f"+{result.get('created', 0)} created, "
                      f"{result.get('updated', 0)} updated")
        except Exception as e:
            print(f"    Batch {i // batch_size + 1}: ERROR - {e}")
            total_errors.append(str(e))

    return total_created, total_updated, total_errors


def main():
    target_product = None
    dry_run = False

    for arg in sys.argv[1:]:
        if arg == "--dry-run":
            dry_run = True
        else:
            target_product = arg

    admin_secret = load_admin_secret()

    # Safety: never touch law skills unless explicitly targeted
    if target_product == "law":
        print("WARNING: Targeting law skills. The 206 originals will be updated.")
        confirm = input("Type 'yes' to continue: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    grand_total_created = 0
    grand_total_updated = 0
    grand_total_errors = []
    grand_total_files = 0

    for folder_name, product_id in sorted(VERTICAL_MAP.items()):
        if target_product and product_id != target_product:
            continue

        skills_dir = os.path.join(VAULT_BASE, folder_name, "skills")
        if not os.path.isdir(skills_dir):
            continue

        md_files = glob.glob(os.path.join(skills_dir, "*.md"))
        if not md_files:
            continue

        print(f"\n{'='*60}")
        print(f"  {folder_name} ({product_id}): {len(md_files)} skill files")
        print(f"{'='*60}")

        skills = []
        for fp in sorted(md_files):
            skill = parse_skill_file(fp)
            if skill:
                skill["product_id"] = product_id  # ensure correct product_id
                skills.append(skill)

        if skills:
            grand_total_files += len(skills)
            c, u, e = push_skills(skills, admin_secret, dry_run)
            grand_total_created += c
            grand_total_updated += u
            grand_total_errors.extend(e)
        else:
            print("  No valid skill files found.")

    print(f"\n{'='*60}")
    print(f"SUMMARY: {grand_total_files} files processed")
    print(f"  Created: {grand_total_created}")
    print(f"  Updated: {grand_total_updated}")
    print(f"  Errors:  {len(grand_total_errors)}")
    if dry_run:
        print("  (DRY RUN — nothing was pushed)")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
