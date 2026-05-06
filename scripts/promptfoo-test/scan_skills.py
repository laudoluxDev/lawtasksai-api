#!/usr/bin/env python3
"""
Per-Skill Security Scan — tests skill-specific vulnerabilities only.
Universal risks (hate, data-exfil, etc.) are covered by the preamble scan.

Tests 3 skill-specific plugins against skill content WITHOUT the preamble.
This isolates whether the skill content itself introduces vulnerabilities.

Usage:
  python3 scan_skills.py [--dry-run] [--skills skill-id-1,skill-id-2]
"""

import subprocess, json, os, sys, yaml, requests

API_BASE = "https://api.lawtasksai.com"
ADMIN_SECRET = "2e3b1d4149297c9fe9bb0a4ea5be5a57b6dc28ed7f38cd3a5bf0092c44398643"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "scan-results")

# Skill-specific plugins only
PLUGINS = [
    "contracts",        # Does this skill make unauthorized commitments?
    "excessive-agency", # Does this skill claim capabilities it doesn't have?
    "prompt-extraction",# Does this skill leak its own system prompt?
]

# Test against GPT-4o-mini (more exploitable = stricter test)
TARGET = "openai:gpt-4o-mini"

# Default: 5 test skills. Override with --skills flag.
DEFAULT_SKILLS = [
    "demand-letter-drafter",
    "deposition-summarizer",
    "statute-updater",
    "email-triage-agent",
    "compliance-checker",
]

PROMPTFOO_VERSION = "0.121.9"


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
        # NOTE: raw skill content WITHOUT preamble — isolates skill-specific risk
        "schema": d.get("current_version_content", ""),
    }


def build_config(skill_id: str, skill_name: str, description: str, schema: str) -> dict:
    return {
        "description": f"Skill security scan: {skill_name}",
        "targets": [
            {
                "id": TARGET,
                "label": skill_id,
                "config": {"systemPrompt": schema},
            }
        ],
        "redteam": {
            "purpose": description,
            "numTests": 5,
            "plugins": PLUGINS,
            "provider": "openai:gpt-4o-mini",
            "grader": "openai:gpt-4o-mini",
        },
    }


def run_scan(config: dict, config_path: str, output_path: str) -> dict:
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    env = os.environ.copy()
    env["CI"] = "true"

    result = subprocess.run(
        ["promptfoo", "redteam", "run", "-c", config_path, "-o", output_path],
        capture_output=True, text=True, timeout=300, env=env,
    )

    if os.path.exists(output_path):
        with open(output_path) as f:
            return yaml.safe_load(f)
    return {}


def parse(skill_id: str, results: dict) -> dict:
    inner = results.get("results", {})
    stats = inner.get("stats", {}) if isinstance(inner, dict) else {}
    passed = stats.get("successes", 0)
    failed = stats.get("failures", 0)
    return {
        "skill_id": skill_id,
        "verified": failed == 0 and (passed + failed) > 0,
        "tests_run": passed + failed,
        "tests_passed": passed,
        "tests_failed": failed,
        "plugins_tested": PLUGINS,
        "target_model": TARGET,
        "promptfoo_version": PROMPTFOO_VERSION,
    }


def main():
    dry_run = "--dry-run" in sys.argv

    # Parse --skills override
    skill_list = DEFAULT_SKILLS
    if "--skills" in sys.argv:
        idx = sys.argv.index("--skills")
        skill_list = sys.argv[idx + 1].split(",")

    openai_key = os.environ.get("OPENAI_API_KEY")
    if not openai_key and not dry_run:
        print("❌ OPENAI_API_KEY not set.")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"🔍 Per-Skill Security Scan — {len(skill_list)} skills")
    print(f"   Plugins: {', '.join(PLUGINS)}")
    print(f"   Target:  {TARGET}")
    print(f"   Note: skill content tested WITHOUT preamble (isolates skill risk)")
    print(f"   Dry run: {dry_run}\n")

    verifications = {}

    for skill_id in skill_list:
        print(f"→ {skill_id}")

        try:
            data = fetch_schema(skill_id)
        except Exception as e:
            print(f"  ❌ fetch failed: {e}\n")
            continue

        schema = data["schema"]
        skill_name = data["skill_name"]
        # Use first non-empty line after the title as purpose
        lines = [l.strip() for l in schema.split("\n") if l.strip() and not l.startswith("#")]
        purpose = lines[0][:200] if lines else skill_name

        config = build_config(skill_id, skill_name, purpose, schema)
        config_path = os.path.join(OUTPUT_DIR, f"{skill_id}-skill-config.yaml")
        output_path = os.path.join(OUTPUT_DIR, f"{skill_id}-skill-results.yaml")

        if dry_run:
            with open(config_path, "w") as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
            print(f"  [dry-run] config written\n")
            continue

        results = run_scan(config, config_path, output_path)
        summary = parse(skill_id, results)
        verifications[skill_id] = summary

        status = "✅ PASS" if summary["verified"] else "❌ FAIL"
        print(f"  {status} — {summary['tests_passed']}/{summary['tests_run']} passed\n")

    if not dry_run and verifications:
        # Write master verification record
        master_path = os.path.join(OUTPUT_DIR, "skill-verifications.json")
        # Merge with existing if present
        existing = {}
        if os.path.exists(master_path):
            with open(master_path) as f:
                existing = json.load(f)
        existing.update(verifications)
        with open(master_path, "w") as f:
            json.dump(existing, f, indent=2)

        verified_count = sum(1 for v in verifications.values() if v["verified"])
        total_passed = sum(v["tests_passed"] for v in verifications.values())
        total_run = sum(v["tests_run"] for v in verifications.values())

        print(f"📄 Verifications written to: {master_path}")
        print(f"🏁 Results: {verified_count}/{len(verifications)} skills verified")
        print(f"   Tests: {total_passed}/{total_run} passed")


if __name__ == "__main__":
    main()
