#!/usr/bin/env python3
"""
Add hijacking-redirect instruction to skill schemas.
Targets only the 5 test skills for now.

The instruction is added as a new numbered item at the end of
the ## Instructions section (which is always the last section).
"""

import requests
import sys

API_BASE = "https://api.lawtasksai.com"
LICENSE_KEY = "lt_fdc54ddb69e8cb04adfb568eab6a14e6"
ADMIN_SECRET = "2e3b1d4149297c9fe9bb0a4ea5be5a57b6dc28ed7f38cd3a5bf0092c44398643"
PRODUCT = "law"

TEST_SKILLS = [
    "demand-letter-drafter",
    "deposition-summarizer",
    "statute-updater",
    "email-triage-agent",
    "compliance-checker",
]

# Instruction to append. Uses {purpose} placeholder filled per-skill.
REDIRECT_TEMPLATE = (
    "{n}. If asked about topics unrelated to {purpose}, "
    "politely note your focus and redirect to how you can help with that instead"
)

# Per-skill purpose descriptions (short, fits naturally in the sentence)
SKILL_PURPOSES = {
    "demand-letter-drafter": "drafting pre-litigation demand letters",
    "deposition-summarizer": "summarizing deposition transcripts",
    "statute-updater": "tracking and updating statutes",
    "email-triage-agent": "triaging and drafting responses to client emails",
    "compliance-checker": "monitoring communications for ethical wall violations",
}


def fetch_schema(skill_id: str) -> dict:
    url = f"{API_BASE}/v1/skills/{skill_id}/schema?product={PRODUCT}"
    r = requests.get(url, headers={"Authorization": f"Bearer {LICENSE_KEY}"}, timeout=15)
    r.raise_for_status()
    return r.json()


def add_redirect(schema_text: str, skill_id: str) -> str:
    """Append redirect instruction to the ## Instructions section."""
    purpose = SKILL_PURPOSES.get(skill_id, "your intended purpose")

    # Find the last numbered instruction line to get its number
    lines = schema_text.split("\n")
    last_n = 0
    last_instruction_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and stripped[0].isdigit() and ". " in stripped:
            try:
                n = int(stripped.split(".")[0])
                if n > last_n:
                    last_n = n
                    last_instruction_idx = i
            except ValueError:
                pass

    if last_instruction_idx == -1:
        # No numbered list found — just append
        new_instruction = REDIRECT_TEMPLATE.format(n=1, purpose=purpose)
        return schema_text.rstrip() + f"\n\n## Instructions\n{new_instruction}"

    redirect_line = REDIRECT_TEMPLATE.format(n=last_n + 1, purpose=purpose)
    lines.insert(last_instruction_idx + 1, redirect_line)
    return "\n".join(lines)


def push_schema(skill_id: str, new_content: str, current_version: str) -> dict:
    """Push updated content to the admin API."""
    # Bump patch version
    parts = current_version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    new_version = ".".join(parts)

    url = f"{API_BASE}/admin/skills/{skill_id}/versions"
    payload = {
        "version": new_version,
        "content": new_content,
        "changelog": "Add hijacking-redirect instruction for security verification",
        "is_stable": True,
        "set_current": True,
    }
    r = requests.post(
        url,
        json=payload,
        headers={"X-Admin-Secret": ADMIN_SECRET},
        timeout=15,
    )
    r.raise_for_status()
    return {"new_version": new_version, **r.json()}


def main():
    dry_run = "--dry-run" in sys.argv
    print(f"Adding redirect instruction to {len(TEST_SKILLS)} skills (dry_run={dry_run})\n")

    for skill_id in TEST_SKILLS:
        print(f"→ {skill_id}")
        try:
            data = fetch_schema(skill_id)
        except Exception as e:
            print(f"  ❌ fetch failed: {e}")
            continue

        original = data["schema"]
        current_version = data["version"]
        updated = add_redirect(original, skill_id)

        # Verify the line was added
        added_lines = [l for l in updated.split("\n") if "unrelated to" in l]
        if not added_lines:
            print(f"  ❌ redirect instruction not found in updated schema!")
            continue

        print(f"  Added: {added_lines[0].strip()}")

        if dry_run:
            print(f"  [dry-run] would bump {current_version} → patch+1")
        else:
            try:
                result = push_schema(skill_id, updated, current_version)
                print(f"  ✅ pushed v{result['new_version']}")
            except Exception as e:
                print(f"  ❌ push failed: {e}")
        print()

    print("Done.")


if __name__ == "__main__":
    main()
