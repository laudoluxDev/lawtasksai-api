#!/usr/bin/env python3
"""
Full TasksAI Security Scan — all 4,117+ skills across 30 verticals.
Resumable: tracks progress in progress.json, skips already-processed skills.

Usage:
  python3 run_scan.py                          # scan everything not yet done
  python3 run_scan.py --vertical law           # one vertical only
  python3 run_scan.py --force-skill demand-letter-drafter  # re-scan one skill
  python3 run_scan.py --dry-run                # generate configs only, no scan
  python3 run_scan.py --status                 # show progress summary and exit

See FULL_SCAN_PROCEDURE.md for full instructions.
"""

import subprocess, json, os, sys, yaml, requests, time
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE       = "https://api.lawtasksai.com"
ADMIN_SECRET   = "2e3b1d4149297c9fe9bb0a4ea5be5a57b6dc28ed7f38cd3a5bf0092c44398643"
OUTPUT_DIR     = os.path.join(os.path.dirname(__file__), "scan-results")
PROGRESS_FILE  = os.path.join(OUTPUT_DIR, "progress.json")
VERIF_FILE     = os.path.join(OUTPUT_DIR, "skill-verifications.json")
PROMPTFOO_VER  = "0.121.9"

TARGET = "openai:gpt-4o-mini"

# Discord notification every N skills
DISCORD_NOTIFY_EVERY = 300
DISCORD_CHANNEL_ID   = "1481071053311836172"  # General channel

# Skill-specific plugins only (universal risks covered by preamble)
PLUGINS = [
    "contracts",
    "excessive-agency",
    "prompt-extraction",
]

# All 30 verticals
VERTICALS = [
    "law", "marketing", "farmer", "pastor", "contractor", "travelagent",
    "dentist", "realtor", "salon", "teacher", "militaryspouse", "chiropractor",
    "insurance", "accounting", "nutritionist", "restaurant", "therapist",
    "designer", "landlord", "principal", "mortuary", "eventplanner", "church",
    "personaltrainer", "electrician", "mortgage", "plumber", "vet", "funeral", "hr",
]

BATCH_SIZE     = 10   # skills per batch before saving progress
SCAN_TIMEOUT   = 300  # seconds per skill scan

# ── Progress tracking ─────────────────────────────────────────────────────────

def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed": {},   # skill_id -> {passed, failed, verified, timestamp}
        "failed_fetch": [], # skill_ids that couldn't be fetched
        "errors": [],      # skill_ids that errored during scan
    }

def save_progress(progress: dict):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)

def load_verifications() -> dict:
    if os.path.exists(VERIF_FILE):
        with open(VERIF_FILE) as f:
            return json.load(f)
    return {}

def save_verifications(verifications: dict):
    with open(VERIF_FILE, "w") as f:
        json.dump(verifications, f, indent=2)

# ── API ───────────────────────────────────────────────────────────────────────

def fetch_all_skills(vertical: str) -> list:
    """Fetch all skill IDs for a vertical from the admin API."""
    r = requests.get(
        f"{API_BASE}/v1/skills?product={vertical}",
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    skills = data if isinstance(data, list) else data.get("skills", data.get("items", []))
    return [{"id": s["id"], "name": s.get("name", s["id"]), "vertical": vertical} for s in skills]

def fetch_schema(skill_id: str) -> dict:
    """Fetch skill content via admin endpoint (no credits burned)."""
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

# ── Scan ──────────────────────────────────────────────────────────────────────

def build_config(skill_id: str, skill_name: str, schema: str) -> dict:
    lines = [l.strip() for l in schema.split("\n") if l.strip() and not l.startswith("#")]
    purpose = lines[0][:200] if lines else skill_name
    return {
        "description": f"Skill security scan: {skill_name}",
        "targets": [{"id": TARGET, "label": skill_id, "config": {"systemPrompt": schema}}],
        "redteam": {"purpose": purpose, "numTests": 5, "plugins": PLUGINS},
    }

def run_scan(skill_id: str, config: dict) -> dict:
    config_path = os.path.join(OUTPUT_DIR, f"{skill_id}-skill-config.yaml")
    output_path = os.path.join(OUTPUT_DIR, f"{skill_id}-skill-results.yaml")

    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    env = os.environ.copy()
    env["CI"] = "true"

    result = subprocess.run(
        ["promptfoo", "redteam", "run", "-c", config_path, "-o", output_path],
        capture_output=True, text=True, timeout=SCAN_TIMEOUT, env=env,
    )

    if os.path.exists(output_path):
        with open(output_path) as f:
            return yaml.safe_load(f)
    return {}

def parse_results(skill_id: str, vertical: str, version: str, results: dict) -> dict:
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

# ── Discord notifications ────────────────────────────────────────────────────

def notify_discord(message: str):
    """Send a progress notification via OpenClaw gateway."""
    try:
        import json as _json
        cfg = _json.loads(open('/Users/clio/.openclaw/openclaw-privileged.json').read())
        token = cfg.get('gateway', {}).get('auth', {}).get('token', '')
        requests.post(
            'http://127.0.0.1:18789/api/message/send',
            headers={'Authorization': f'Bearer {token}'},
            json={
                'channel': 'discord',
                'target': DISCORD_CHANNEL_ID,
                'message': message,
            },
            timeout=10,
        )
    except Exception as e:
        print(f'  [discord notify failed: {e}]')


# ── Status display ────────────────────────────────────────────────────────────

def show_status(progress: dict, verifications: dict):
    completed = progress.get("completed", {})
    errors = progress.get("errors", [])
    failed_fetch = progress.get("failed_fetch", [])

    total = len(completed)
    verified = sum(1 for v in verifications.values() if v.get("verified"))
    unverified = sum(1 for v in verifications.values() if not v.get("verified"))

    print(f"\n📊 Scan Progress")
    print(f"   Skills scanned:   {total}")
    print(f"   Verified (✅):    {verified}")
    print(f"   Unverified (❌):  {unverified}")
    print(f"   Fetch errors:     {len(failed_fetch)}")
    print(f"   Scan errors:      {len(errors)}")

    # Per-vertical breakdown
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
            print(f"   {bar} {vert:20s} {d['verified']:3d}/{d['total']:3d} ({pct}%)")
    print()

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    dry_run      = "--dry-run" in args
    status_only  = "--status" in args
    force_skill  = None
    only_vertical = None

    if "--force-skill" in args:
        idx = args.index("--force-skill")
        force_skill = args[idx + 1]

    if "--vertical" in args:
        idx = args.index("--vertical")
        only_vertical = args[idx + 1]

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    progress      = load_progress()
    verifications = load_verifications()

    if status_only:
        show_status(progress, verifications)
        return

    openai_key = os.environ.get("OPENAI_API_KEY")
    if not openai_key and not dry_run:
        print("❌ OPENAI_API_KEY not set.")
        sys.exit(1)

    verticals = [only_vertical] if only_vertical else VERTICALS
    already_done = set(progress["completed"].keys())
    if force_skill:
        already_done.discard(force_skill)

    print(f"🔍 TasksAI Full Security Scan")
    print(f"   Verticals: {', '.join(verticals)}")
    print(f"   Plugins:   {', '.join(PLUGINS)}")
    print(f"   Target:    {TARGET}")
    print(f"   Dry run:   {dry_run}")
    print(f"   Already done: {len(already_done)} skills (will skip)")
    print()

    batch_count = 0
    total_scanned = 0
    total_verified = 0

    for vertical in verticals:
        print(f"━━ {vertical} ━━")

        try:
            skills = fetch_all_skills(vertical)
        except Exception as e:
            print(f"  ❌ Failed to fetch skill list: {e}\n")
            continue

        to_scan = [s for s in skills if s["id"] not in already_done]
        print(f"  {len(to_scan)} skills to scan ({len(skills) - len(to_scan)} already done)\n")

        for skill in to_scan:
            skill_id = skill["id"]
            skill_name = skill["name"]

            # Fetch schema
            try:
                data = fetch_schema(skill_id)
            except Exception as e:
                print(f"  ❌ {skill_id}: fetch failed ({e})")
                progress["failed_fetch"].append(skill_id)
                save_progress(progress)
                continue

            schema = data["schema"]
            version = data["version"]

            if not schema.strip():
                print(f"  ⚠️  {skill_id}: empty schema, skipping")
                continue

            config = build_config(skill_id, skill_name, schema)

            if dry_run:
                config_path = os.path.join(OUTPUT_DIR, f"{skill_id}-skill-config.yaml")
                with open(config_path, "w") as f:
                    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
                print(f"  [dry-run] {skill_id}")
                continue

            # Run scan
            try:
                results = run_scan(skill_id, config)
            except subprocess.TimeoutExpired:
                print(f"  ⏱️  {skill_id}: scan timed out, skipping")
                progress["errors"].append({"skill_id": skill_id, "error": "timeout"})
                save_progress(progress)
                continue
            except Exception as e:
                print(f"  ❌ {skill_id}: scan error ({e})")
                progress["errors"].append({"skill_id": skill_id, "error": str(e)})
                save_progress(progress)
                continue

            # Parse and record
            summary = parse_results(skill_id, vertical, version, results)
            verifications[skill_id] = summary

            status = "✅" if summary["verified"] else "❌"
            print(f"  {status} {skill_id} — {summary['tests_passed']}/{summary['tests_run']} passed")

            progress["completed"][skill_id] = {
                "verified": summary["verified"],
                "passed": summary["tests_passed"],
                "failed": summary["tests_failed"],
                "timestamp": summary["scanned_at"],
            }

            total_scanned += 1
            if summary["verified"]:
                total_verified += 1

            # Save progress every BATCH_SIZE skills
            batch_count += 1
            if batch_count % BATCH_SIZE == 0:
                save_progress(progress)
                save_verifications(verifications)
                print(f"\n  💾 Progress saved ({total_scanned} scanned, {total_verified} verified)\n")

            # Discord notification every DISCORD_NOTIFY_EVERY skills
            if not dry_run and total_scanned > 0 and total_scanned % DISCORD_NOTIFY_EVERY == 0:
                total_done = len(progress['completed'])
                total_all  = sum(len(fetch_all_skills(v)) for v in VERTICALS) if total_scanned == DISCORD_NOTIFY_EVERY else None
                pct = f" (~{int(total_scanned/4117*100)}% of ~4,117 total)"
                notify_discord(
                    f"🛡️ **TasksAI Security Scan Update**\n"
                    f"Skills scanned: **{total_scanned}**{pct}\n"
                    f"Verified ✅: {total_verified} | Unverified ❌: {total_scanned - total_verified}\n"
                    f"Still running — next update at {total_scanned + DISCORD_NOTIFY_EVERY} skills."
                )

        print()

    # Final save
    save_progress(progress)
    save_verifications(verifications)

    print(f"\n🏁 Scan complete!")
    show_status(progress, verifications)
    print(f"📄 Verifications: {VERIF_FILE}")
    print(f"📄 Progress:      {PROGRESS_FILE}")

    # Final Discord notification
    if not dry_run and total_scanned > 0:
        unverified = total_scanned - total_verified
        notify_discord(
            f"🏁 **TasksAI Security Scan Complete!**\n"
            f"Total skills scanned: **{total_scanned}**\n"
            f"Verified ✅: **{total_verified}** | Unverified ❌: **{unverified}**\n"
            f"Pass rate: **{int(total_verified/total_scanned*100)}%**\n"
            f"Results saved to `skill-verifications.json`"
        )


if __name__ == "__main__":
    main()
