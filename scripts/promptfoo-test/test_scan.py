#!/usr/bin/env python3
"""
Promptfoo Phase 1 test scan — 5 law skills
Validates setup before running the full 4,117-skill batch.

Usage:
  python3 test_scan.py [--dry-run]
"""

import subprocess
import json
import os
import sys
import requests
import yaml
import tempfile

API_BASE = "https://api.lawtasksai.com"
LICENSE_KEY = "lt_fdc54ddb69e8cb04adfb568eab6a14e6"
PRODUCT = "law"

# 5 diverse law skills to test (different categories)
TEST_SKILLS = [
    "demand-letter-drafter",
    "deposition-summarizer",
    "statute-updater",
    "email-triage-agent",
    "compliance-checker",
]

# Plugin IDs valid for promptfoo 0.121.9
# NOTE: hijacking intentionally excluded — it tests off-topic responses, not security vulnerabilities.
# Legal AI tools answering adjacent legal questions is acceptable behavior, not a security risk.
PLUGINS = [
    "prompt-extraction",           # system prompt disclosure
    "harmful:hate",
    "harmful:self-harm",
    "data-exfil",                  # data exfiltration via URLs/markdown
    "excessive-agency",
    "pii:direct",
    "contracts",
]


ADMIN_SECRET = "2e3b1d4149297c9fe9bb0a4ea5be5a57b6dc28ed7f38cd3a5bf0092c44398643"

def fetch_schema(skill_id: str) -> dict:
    """Fetch via admin endpoint to avoid burning user credits."""
    url = f"{API_BASE}/admin/skills/{skill_id}"
    r = requests.get(url, headers={"X-Admin-Secret": ADMIN_SECRET}, timeout=15)
    r.raise_for_status()
    data = r.json()
    # Admin endpoint returns skill + version content directly
    return {
        "skill_id": skill_id,
        "skill_name": data.get("name", skill_id),
        "version": data.get("current_version", "1.0.0"),
        "schema": data.get("current_version_content", ""),
    }


def build_promptfoo_config(skill_id: str, skill_name: str, description: str, schema_text: str) -> dict:
    return {
        "description": f"Red-team scan: {skill_name}",
        "targets": [
            {
                "id": "openai:gpt-4o-mini",
                "label": skill_id,
                "config": {
                    "systemPrompt": schema_text,
                },
            }
        ],
        "redteam": {
            "purpose": description,
            "numTests": 5,
            "plugins": PLUGINS,
        },
    }


def run_scan(skill_id: str, config: dict, output_dir: str) -> dict:
    config_path = os.path.join(output_dir, f"{skill_id}-config.yaml")
    tests_path = os.path.join(output_dir, f"{skill_id}-tests.yaml")

    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    print(f"  Running promptfoo scan for {skill_id}...")
    env = os.environ.copy()
    env["CI"] = "true"  # bypass email verification prompt

    result = subprocess.run(
        ["promptfoo", "redteam", "run", "-c", config_path, "-o", tests_path],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )

    if result.returncode != 0:
        print(f"  ⚠️  promptfoo exited with code {result.returncode}")
        print(f"  stderr: {result.stderr[:500]}")

    # promptfoo redteam run writes YAML (not JSON) to -o path
    if os.path.exists(tests_path):
        with open(tests_path) as f:
            return yaml.safe_load(f)
    return {}


def parse_results(skill_id: str, results: dict) -> dict:
    """Extract pass/fail summary from promptfoo YAML output."""
    # Structure: results.results.stats (when -o produces YAML)
    inner = results.get("results", {})
    if isinstance(inner, dict):
        summary = inner.get("stats", {})
    else:
        summary = {}
    tests_passed = summary.get("successes", 0)
    tests_failed = summary.get("failures", 0)
    tests_run = tests_passed + tests_failed

    return {
        "skill_id": skill_id,
        "verified": tests_failed == 0 and tests_run > 0,
        "tests_run": tests_run,
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "plugins_tested": PLUGINS,
        "promptfoo_version": "0.121.9",
    }


def main():
    dry_run = "--dry-run" in sys.argv
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if not anthropic_key and not openai_key and not dry_run:
        print("❌ Neither ANTHROPIC_API_KEY nor OPENAI_API_KEY is set. Export one or use --dry-run.")
        sys.exit(1)

    output_dir = os.path.join(os.path.dirname(__file__), "scan-results")
    os.makedirs(output_dir, exist_ok=True)

    print(f"🔍 Promptfoo Phase 1 Test Scan — {len(TEST_SKILLS)} law skills")
    print(f"   Output dir: {output_dir}")
    print(f"   Dry run: {dry_run}\n")

    verifications = {}

    for skill_id in TEST_SKILLS:
        print(f"→ {skill_id}")

        try:
            data = fetch_schema(skill_id)
        except Exception as e:
            print(f"  ❌ Failed to fetch schema: {e}")
            continue

        skill_name = data.get("skill_name", skill_id)
        schema_text = data.get("schema", "")
        description = schema_text.split("\n")[2] if schema_text else "Legal AI assistant skill"

        config = build_promptfoo_config(skill_id, skill_name, description, schema_text)

        if dry_run:
            config_path = os.path.join(output_dir, f"{skill_id}-config.yaml")
            with open(config_path, "w") as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
            print(f"  [dry-run] Config written to {config_path}")
            verifications[skill_id] = {
                "skill_id": skill_id,
                "verified": None,
                "dry_run": True,
            }
        else:
            results = run_scan(skill_id, config, output_dir)
            summary = parse_results(skill_id, results)
            verifications[skill_id] = summary
            status = "✅ PASS" if summary["verified"] else "❌ FAIL"
            print(f"  {status} — {summary['tests_passed']}/{summary['tests_run']} passed")

        print()

    # Write verification summary
    summary_path = os.path.join(output_dir, "verification-summary.json")
    with open(summary_path, "w") as f:
        json.dump(verifications, f, indent=2)

    print(f"📄 Summary written to: {summary_path}")

    if not dry_run:
        total_passed = sum(v.get("tests_passed", 0) for v in verifications.values())
        total_run = sum(v.get("tests_run", 0) for v in verifications.values())
        total_verified = sum(1 for v in verifications.values() if v.get("verified"))
        print(f"\n🏁 Results: {total_verified}/{len(TEST_SKILLS)} skills fully verified")
        print(f"   Tests: {total_passed}/{total_run} passed")


if __name__ == "__main__":
    main()
