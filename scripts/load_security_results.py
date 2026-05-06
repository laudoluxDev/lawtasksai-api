#!/usr/bin/env python3
"""
load_security_results.py
Load Promptfoo security scan results into the TasksAI API.

Usage:
    python scripts/load_security_results.py [--dry-run]

Reads:
    scripts/promptfoo-test/scan-results/skill-verifications.json

Posts each skill result to:
    POST https://api.lawtasksai.com/admin/security-scans
"""

import json
import sys
import time
import argparse
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' is not installed. Run: pip install requests")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────

SCAN_RESULTS_FILE = Path(__file__).parent / "promptfoo-test/scan-results/skill-verifications.json"
API_BASE          = "https://api.lawtasksai.com"
ADMIN_SECRET      = "2e3b1d4149297c9fe9bb0a4ea5be5a57b6dc28ed7f38cd3a5bf0092c44398643"
DEFAULT_SCAN_MODEL = "openai:gpt-4o-mini"

# Ordered longest-first so prefix matching works correctly
# (e.g. "militaryspouse" before "military", "travelagent" before "travel")
VERTICALS = [
    "militaryspouse",
    "travelagent",
    "eventplanner",
    "personaltrainer",
    "chiropractor",
    "electrician",
    "accounting",
    "contractor",
    "marketing",
    "insurance",
    "mortuary",
    "mortgage",
    "landlord",
    "designer",
    "therapist",
    "teacher",
    "realtor",
    "plumber",
    "funeral",
    "farmer",
    "pastor",
    "church",
    "dentist",
    "salon",
    "restaurant",
    "nutritionist",
    "principal",
    "mortgage",
    "law",
    "vet",
    "hr",
]

# Deduplicate while preserving order
_seen = set()
VERTICALS_DEDUPED = []
for v in VERTICALS:
    if v not in _seen:
        VERTICALS_DEDUPED.append(v)
        _seen.add(v)
VERTICALS = VERTICALS_DEDUPED


def detect_vertical(skill_id: str) -> str:
    """Return the longest-matching vertical prefix for a skill_id."""
    lower = skill_id.lower()
    # Sort by length descending so we match the longest prefix first
    for vertical in sorted(VERTICALS, key=len, reverse=True):
        # Accept both dash and underscore separators
        if lower.startswith(vertical + "-") or lower.startswith(vertical + "_"):
            return vertical
        if lower == vertical:
            return vertical
    return "unknown"


def load_results(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def post_scan(skill_id: str, payload: dict, dry_run: bool) -> dict:
    """POST a single scan result to the admin API."""
    url = f"{API_BASE}/admin/security-scans"
    headers = {
        "Content-Type": "application/json",
        "x-admin-secret": ADMIN_SECRET,
    }
    if dry_run:
        print(f"  [DRY-RUN] Would POST: {json.dumps(payload, default=str)[:120]}…")
        return {"ok": True, "dry_run": True}
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    return resp


def build_payload(skill_id: str, data: dict, vertical: str) -> dict:
    """Build the POST body from scan result data."""
    return {
        "skill_id":        skill_id,
        "vertical":        vertical,
        "verified":        data.get("verified", False),
        "tests_run":       data.get("tests_run", 0),
        "tests_passed":    data.get("tests_passed", 0),
        "tests_failed":    data.get("tests_failed", 0),
        "plugins_tested":  data.get("plugins_tested", []),
        "preamble_tested": True,
        "scan_model":      data.get("target_model", DEFAULT_SCAN_MODEL),
        "scanned_at":      data.get("scanned_at", data.get("timestamp")),
    }


def main():
    parser = argparse.ArgumentParser(description="Load security scan results into TasksAI API")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads without posting")
    args = parser.parse_args()

    if not SCAN_RESULTS_FILE.exists():
        print(f"ERROR: Scan results file not found: {SCAN_RESULTS_FILE}")
        sys.exit(1)

    results = load_results(SCAN_RESULTS_FILE)
    total   = len(results)
    print(f"Loaded {total} skill scan records from {SCAN_RESULTS_FILE}\n")

    success_count  = 0
    skipped_count  = 0
    error_count    = 0
    vertical_stats: dict[str, dict] = {}

    for i, (raw_skill_id, data) in enumerate(results.items(), start=1):
        # Normalise: some entries already have the vertical prefix in skill_id,
        # others (law skills) use the bare name without prefix.
        vertical = data.get("vertical") or detect_vertical(raw_skill_id)
        if vertical == "unknown":
            # For law skills (bare IDs like "demand-letter-drafter"), default to "law"
            vertical = "law"

        # Build canonical skill_id with vertical prefix
        if not raw_skill_id.lower().startswith(vertical.lower()):
            skill_id = f"{vertical}-{raw_skill_id}"
        else:
            skill_id = raw_skill_id

        payload = build_payload(skill_id, data, vertical)

        # Accumulate per-vertical stats
        vs = vertical_stats.setdefault(vertical, {"total": 0, "verified": 0, "passed": 0, "failed": 0})
        vs["total"]   += 1
        vs["passed"]  += data.get("tests_passed", 0)
        vs["failed"]  += data.get("tests_failed", 0)
        if data.get("verified"):
            vs["verified"] += 1

        print(f"[{i:>4}/{total}] {skill_id} (vertical: {vertical}, verified: {data.get('verified')})")

        if args.dry_run:
            post_scan(skill_id, payload, dry_run=True)
            success_count += 1
            continue

        try:
            resp = post_scan(skill_id, payload, dry_run=False)
            if resp.status_code in (200, 201):
                success_count += 1
                print(f"          ✓ {resp.status_code}")
            elif resp.status_code == 409:
                # Already exists — treat as success (idempotent)
                skipped_count += 1
                print(f"          ↩ {resp.status_code} already exists (skipped)")
            else:
                error_count += 1
                print(f"          ✗ {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:
            error_count += 1
            print(f"          ✗ ERROR: {exc}")

        # Polite rate limiting
        time.sleep(0.1)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    print(f"  Total skills processed : {total}")
    print(f"  Successfully posted    : {success_count}")
    print(f"  Skipped (409 exists)   : {skipped_count}")
    print(f"  Errors                 : {error_count}")
    print()
    print(f"{'Vertical':<20} {'Total':>6} {'Verified':>9} {'Pass Rate':>10}")
    print("-" * 50)
    for vertical, vs in sorted(vertical_stats.items()):
        total_tests = vs["passed"] + vs["failed"]
        rate = f"{vs['passed'] / total_tests * 100:.1f}%" if total_tests else "n/a"
        print(f"{vertical:<20} {vs['total']:>6} {vs['verified']:>9} {rate:>10}")
    print("=" * 60)


if __name__ == "__main__":
    main()
