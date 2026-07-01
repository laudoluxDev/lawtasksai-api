#!/usr/bin/env python3
"""
Full TasksAI Security Scan — all 4,117+ skills across 30 verticals.
Resumable: tracks progress in progress.json, skips already-processed skills.
Concurrent: scans MAX_WORKERS skills in parallel for ~6x speedup.

Usage:
  python3 run_scan.py                          # scan everything not yet done
  python3 run_scan.py --vertical law           # one vertical only
  python3 run_scan.py --force-skill demand-letter-drafter  # re-scan one skill
  python3 run_scan.py --dry-run                # generate configs only, no scan
  python3 run_scan.py --status                 # show progress summary and exit

See FULL_SCAN_PROCEDURE.md for full instructions.
"""
import os

import subprocess, json, os, sys, yaml, requests, time, threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

API_BASE       = "https://api.lawtasksai.com"
ADMIN_SECRET   = os.getenv("LAWTASKSAI_ADMIN_SECRET", "")
OUTPUT_DIR     = os.path.join(os.path.dirname(__file__), "scan-results")
PROGRESS_FILE  = os.path.join(OUTPUT_DIR, "progress.json")
VERIF_FILE     = os.path.join(OUTPUT_DIR, "skill-verifications.json")
PROMPTFOO_VER  = "0.121.9"
TARGET         = "openai:gpt-4o-mini"
BATCH_SIZE     = 10    # save progress every N completions
SCAN_TIMEOUT   = 300   # seconds per skill
MAX_WORKERS    = 6     # parallel scans (~6x speedup, safe for OpenAI rate limits)
DISCORD_NOTIFY_EVERY = 300
DISCORD_CHANNEL_ID   = "1481071053311836172"

PLUGINS = ["contracts", "excessive-agency", "prompt-extraction"]

VERTICALS = [
    "law", "marketing", "farmer", "pastor", "contractor", "travelagent",
    "dentist", "realtor", "salon", "teacher", "militaryspouse", "chiropractor",
    "insurance", "accounting", "nutritionist", "restaurant", "therapist",
    "designer", "landlord", "principal", "mortuary", "eventplanner", "church",
    "personaltrainer", "electrician", "mortgage", "plumber", "vet", "funeral", "hr",
]

_lock = threading.Lock()


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": {},
        "failed_fetch": [],
        "errors": [],
    }

def save_progress(progress):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)

def load_verifications():
    if os.path.exists(VERIF_FILE):
        with open(VERIF_FILE) as f:
            return json.load(f)
    return {}

def save_verifications(verifications):
    with open(VERIF_FILE, "w") as f:
        json.dump(verifications, f, indent=2)


def fetch_all_skills(vertical):
    r = requests.get(f"{API_BASE}/v1/skills?product={vertical}", timeout=30)
    r.raise_for_status()
    data = r.json()
    skills = data if isinstance(data, list) else data.get("skills", data.get("items", []))
    return [{"id": s["id"], "name": s.get("name", s["id"]), "vertical": vertical} for s in skills]

def fetch_schema(skill_id):
    r = requests.get(
        f"{API_BASE}/admin/skills/{skill_id}",
        headers={"X-Admin-Secret": ADMIN_SECRET},
        timeout=15,
    )
    r.raise_for_status()
    d = r.json()
    return {
        "skill_id": skill_id,
        "skill_name": d.get("name", skill_id),
        "version": d.get("current_version", "1.0.0"),
        "schema": d.get("current_version_content", ""),
    }

def build_config(skill_id, skill_name, schema):
    lines = [l.strip() for l in schema.split("\n") if l.strip() and not l.startswith("#")]
    purpose = lines[0][:200] if lines else skill_name
    return {
        "description": f"Skill security scan: {skill_name}",
        "targets": [{"id": TARGET, "label": skill_id, "config": {"systemPrompt": schema}}],
        "redteam": {
            "purpose": purpose,
            "numTests": 5,
            "plugins": PLUGINS,
            # Explicitly set cheap models for attack generation and grading
            # Without this, Promptfoo defaults to gpt-5.5 which is very expensive
            "provider": "openai:gpt-4o-mini",   # attack probe generator
            "grader": "openai:gpt-4o-mini",      # result grader
        },
    }

def run_scan(skill_id, config):
    config_path = os.path.join(OUTPUT_DIR, f"{skill_id}-skill-config.yaml")
    output_path = os.path.join(OUTPUT_DIR, f"{skill_id}-skill-results.yaml")
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    env = os.environ.copy()
    env["CI"] = "true"
    subprocess.run(
        ["promptfoo", "redteam", "run", "-c", config_path, "-o", output_path],
        capture_output=True, text=True, timeout=SCAN_TIMEOUT, env=env,
    )
    if os.path.exists(output_path):
        with open(output_path) as f:
            return yaml.safe_load(f)
    return {}

def parse_results(skill_id, vertical, version, results):
    inner = results.get("results", {})
    stats = inner.get("stats", {}) if isinstance(inner, dict) else {}
    passed = stats.get("successes", 0)
    failed = stats.get("failures", 0)
    return {
        "skill_id": skill_id,
        "vertical": vertical,
        "version": version,
        "verified": failed == 0 and (passed + failed) > 0,
        "tests_run": passed + failed,
        "tests_passed": passed,
        "tests_failed": failed,
        "plugins_tested": PLUGINS,
        "target_model": TARGET,
        "promptfoo_version": PROMPTFOO_VER,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }

def scan_skill(skill):
    """Fetch schema and scan one skill. Runs in thread pool. Never raises."""
    skill_id   = skill["id"]
    skill_name = skill["name"]
    vertical   = skill["vertical"]
    try:
        return _scan_skill_inner(skill_id, skill_name, vertical)
    except Exception as e:
        return {"skill_id": skill_id, "error": f"unexpected: {e}", "vertical": vertical}

def _scan_skill_inner(skill_id, skill_name, vertical):
    try:
        data = fetch_schema(skill_id)
    except Exception as e:
        return {"skill_id": skill_id, "error": f"fetch: {e}", "vertical": vertical}
    schema  = data["schema"] or ""
    version = data["version"]
    if not schema.strip():
        return {"skill_id": skill_id, "error": "empty schema", "vertical": vertical}
    config = build_config(skill_id, skill_name, schema)
    try:
        results = run_scan(skill_id, config)
    except subprocess.TimeoutExpired:
        return {"skill_id": skill_id, "error": "timeout", "vertical": vertical}
    except Exception as e:
        return {"skill_id": skill_id, "error": str(e), "vertical": vertical}
    return parse_results(skill_id, vertical, version, results)


def notify_discord(message):
    try:
        cfg = json.loads(open('/Users/clio/.openclaw/openclaw-privileged.json').read())
        token = cfg.get('gateway', {}).get('auth', {}).get('token', '')
        requests.post(
            'http://127.0.0.1:18789/api/message/send',
            headers={'Authorization': f'Bearer {token}'},
            json={'channel': 'discord', 'target': DISCORD_CHANNEL_ID, 'message': message},
            timeout=10,
        )
    except Exception as e:
        print(f'  [discord notify failed: {e}]')


def show_status(progress, verifications):
    completed = progress.get("completed", {})
    total     = len(completed)
    verified  = sum(1 for v in verifications.values() if v.get("verified"))
    unverified = sum(1 for v in verifications.values() if not v.get("verified"))
    print(f"\n📊 Scan Progress")
    print(f"   Skills scanned:   {total}")
    print(f"   Verified (✅):    {verified}")
    print(f"   Unverified (❌):  {unverified}")
    print(f"   Fetch errors:     {len(progress.get('failed_fetch', []))}")
    print(f"   Scan errors:      {len(progress.get('errors', []))}")
    by_vertical = {}
    for v in verifications.values():
        vert = v.get("vertical", "?")
        if vert not in by_vertical:
            by_vertical[vert] = {"verified": 0, "total": 0}
        by_vertical[vert]["total"] += 1
        if v.get("verified"):
            by_vertical[vert]["verified"] += 1
    if by_vertical:
        print(f"\n   Per-vertical:")
        for vert in sorted(by_vertical):
            d = by_vertical[vert]
            pct = int(d["verified"] / d["total"] * 100) if d["total"] else 0
            bar = "✅" if pct == 100 else "⚠️ " if pct >= 80 else "❌"
            print(f"   {bar} {vert:22s} {d['verified']:3d}/{d['total']:3d} ({pct}%)")
    print()


def main():
    args = sys.argv[1:]
    dry_run       = "--dry-run" in args
    status_only   = "--status" in args
    force_skill   = None
    only_vertical = None

    if "--force-skill" in args:
        force_skill = args[args.index("--force-skill") + 1]
    if "--vertical" in args:
        only_vertical = args[args.index("--vertical") + 1]

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    progress      = load_progress()
    verifications = load_verifications()

    if status_only:
        show_status(progress, verifications)
        return

    if not os.environ.get("OPENAI_API_KEY") and not dry_run:
        print("❌ OPENAI_API_KEY not set.")
        sys.exit(1)

    verticals    = [only_vertical] if only_vertical else VERTICALS
    already_done = set(progress["completed"].keys())
    if force_skill:
        already_done.discard(force_skill)

    print(f"🔍 TasksAI Full Security Scan (concurrent, {MAX_WORKERS} workers)")
    print(f"   Verticals: {', '.join(verticals)}")
    print(f"   Plugins:   {', '.join(PLUGINS)}")
    print(f"   Target:    {TARGET}")
    print(f"   Dry run:   {dry_run}")
    print(f"   Already done: {len(already_done)} skills (will skip)")
    print()

    total_scanned  = 0
    total_verified = 0
    batch_count    = 0

    # Collect all skills across all verticals first
    all_skills = []
    for vertical in verticals:
        try:
            skills = fetch_all_skills(vertical)
            to_scan = [s for s in skills if s["id"] not in already_done]
            print(f"━━ {vertical}: {len(to_scan)} to scan ({len(skills)-len(to_scan)} already done)")
            all_skills.extend(to_scan)
        except Exception as e:
            print(f"  ❌ {vertical}: failed to fetch skill list ({e})")

    print(f"\n🚀 Starting scan of {len(all_skills)} skills with {MAX_WORKERS} workers...\n")

    if dry_run:
        for skill in all_skills:
            skill_id = skill["id"]
            config_path = os.path.join(OUTPUT_DIR, f"{skill_id}-skill-config.yaml")
            try:
                data = fetch_schema(skill_id)
                config = build_config(skill_id, skill["name"], data["schema"])
                with open(config_path, "w") as f:
                    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
                print(f"  [dry-run] {skill_id}")
            except Exception as e:
                print(f"  [dry-run] {skill_id}: {e}")
        return

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(scan_skill, skill): skill for skill in all_skills}

        for future in as_completed(futures):
            result = future.result()
            skill_id = result.get("skill_id", "?")

            if "error" in result:
                error = result["error"]
                print(f"  ⚠️  {skill_id}: {error}")
                with _lock:
                    if "fetch" in error:
                        progress["failed_fetch"].append(skill_id)
                    else:
                        progress["errors"].append({"skill_id": skill_id, "error": error})
            else:
                verified = result.get("verified", False)
                passed   = result.get("tests_passed", 0)
                total    = result.get("tests_run", 0)
                status   = "✅" if verified else "❌"
                print(f"  {status} {skill_id} — {passed}/{total} passed")

                with _lock:
                    verifications[skill_id] = result
                    progress["completed"][skill_id] = {
                        "verified": verified,
                        "passed": passed,
                        "failed": result.get("tests_failed", 0),
                        "timestamp": result.get("scanned_at", ""),
                    }
                    total_scanned  += 1
                    if verified:
                        total_verified += 1

            # Save every BATCH_SIZE completions
            batch_count += 1
            if batch_count % BATCH_SIZE == 0:
                with _lock:
                    save_progress(progress)
                    save_verifications(verifications)
                print(f"\n  💾 Progress saved ({total_scanned} scanned, {total_verified} verified)\n")

            # Discord every DISCORD_NOTIFY_EVERY skills
            if total_scanned > 0 and total_scanned % DISCORD_NOTIFY_EVERY == 0:
                pct = int(total_scanned / 4117 * 100)
                notify_discord(
                    f"🛡️ **TasksAI Security Scan Update**\n"
                    f"Skills scanned: **{total_scanned}** (~{pct}% of ~4,117)\n"
                    f"Verified ✅: {total_verified} | Unverified ❌: {total_scanned - total_verified}\n"
                    f"Still running — next update at {total_scanned + DISCORD_NOTIFY_EVERY} skills."
                )

    # Final save
    with _lock:
        save_progress(progress)
        save_verifications(verifications)

    print(f"\n🏁 Scan complete!")
    show_status(progress, verifications)
    print(f"📄 Verifications: {VERIF_FILE}")
    print(f"📄 Progress:      {PROGRESS_FILE}")

    if total_scanned > 0:
        notify_discord(
            f"🏁 **TasksAI Security Scan Complete!**\n"
            f"Total scanned: **{total_scanned}**\n"
            f"Verified ✅: **{total_verified}** | Unverified ❌: **{total_scanned - total_verified}**\n"
            f"Pass rate: **{int(total_verified/total_scanned*100)}%**\n"
            f"Results saved to `skill-verifications.json`"
        )


if __name__ == "__main__":
    main()
