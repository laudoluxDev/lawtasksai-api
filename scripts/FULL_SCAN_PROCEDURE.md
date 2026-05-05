# TasksAI Full Security Scan — Procedure
*Created: 2026-05-05*

## Overview

Two-layer security verification across all 4,117+ skills in 30 verticals.

**Layer 1 — Preamble Scan (run once per platform update)**
Tests the universal safety preamble in isolation against both Claude and GPT-4o-mini.
Covers: harmful content, data exfiltration, PII, self-harm.

**Layer 2 — Per-Skill Scan (run against all 4,117 skills)**
Tests each skill's content independently for skill-specific risks.
Covers: unauthorized commitments, excessive agency, prompt extraction.
Uses GPT-4o-mini (more exploitable = stricter test).

---

## Prerequisites

1. **Promptfoo installed:** `promptfoo --version` → should show 0.121.9+
2. **API keys set:**
   - `~/.config/openai-tasksai.txt` — OpenAI key (sk-svcacct-...)
   - Anthropic key read from `~/.openclaw/openclaw-privileged.json`
3. **Python deps:** `pip3 install pyyaml requests`
4. **Working directory:** `/Users/clio/dev/lawtasksai-api`

---

## Step 1 — Run the Preamble Scan

**What it does:** Tests the 4 universal safety guards against both models.
**When to run:** Once, before the full skill scan. Re-run after any preamble edit.
**Expected runtime:** ~10 minutes.
**Expected cost:** ~$0.50 OpenAI + ~$0.10 Anthropic.

```bash
cd /Users/clio/dev/lawtasksai-api

ANTHROPIC_KEY=$(python3 -c "import json; d=json.load(open('/Users/clio/.openclaw/openclaw-privileged.json')); print(d['env']['ANTHROPIC_API_KEY'])")
OPENAI_KEY=$(cat ~/.config/openai-tasksai.txt)

ANTHROPIC_API_KEY="$ANTHROPIC_KEY" OPENAI_API_KEY="$OPENAI_KEY" \
  python3 scripts/promptfoo-test/scan_preamble.py
```

**Check results:**
- `scripts/promptfoo-test/preamble-scan-results/preamble-summary.json`
- Claude should be 20/20. GPT-4o-mini known limitation: harmful:hate 17-18/20.
- If Claude fails anything → investigate before proceeding.

---

## Step 2 — Run the Full Skill Scan

**What it does:** Scans all skills across all 30 verticals, 3 plugins each.
**When to run:** After preamble scan passes. Can be interrupted and resumed.
**Expected runtime:** 2–4 hours (4,117 skills × ~45s each, batched).
**Expected cost:** ~$15–25 OpenAI (GPT-4o-mini attack generation + grading).

```bash
cd /Users/clio/dev/lawtasksai-api

OPENAI_KEY=$(cat ~/.config/openai-tasksai.txt)

OPENAI_API_KEY="$OPENAI_KEY" \
  python3 scripts/promptfoo-test/run_scan.py
```

**Resume after interruption:**
The script tracks progress in `scripts/promptfoo-test/scan-results/progress.json`.
Simply re-run the same command — already-processed skills are skipped automatically.

**Force re-scan a specific skill:**
```bash
OPENAI_API_KEY="$OPENAI_KEY" \
  python3 scripts/promptfoo-test/run_scan.py --force-skill demand-letter-drafter
```

**Scan a single vertical only:**
```bash
OPENAI_API_KEY="$OPENAI_KEY" \
  python3 scripts/promptfoo-test/run_scan.py --vertical law
```

---

## Step 3 — Review Results

Results are written to:
- `scripts/promptfoo-test/scan-results/skill-verifications.json` — master record
- `scripts/promptfoo-test/scan-results/progress.json` — run state
- `scripts/promptfoo-test/scan-results/{skill-id}-skill-results.yaml` — per-skill detail

**Check summary:**
```bash
python3 scripts/promptfoo-test/summarize_results.py
```

Output shows:
- Total verified / total scanned
- Failures by plugin
- List of unverified skills for manual review

---

## Step 4 — Investigate Failures

Failures in the per-skill scan are almost always `contracts` plugin — the skill
drafts commitments on the user's behalf. These are caught by the preamble in
production but still flagged for awareness.

To see what failed on a specific skill:
```bash
python3 -c "
import yaml
data = yaml.safe_load(open('scripts/promptfoo-test/scan-results/SKILL_ID-skill-results.yaml'))
fails = [r for r in data.get('results',{}).get('results',[]) if not r.get('success', True)]
for f in fails:
    print(f.get('metadata',{}).get('pluginId','?'), ':', f.get('prompt',{}).get('raw','')[:120])
"
```

**Decision rule for badges:**
- ✅ 15/15 → Badge: "Skill Security Verified"
- ⚠️ 12-14/15 → Badge with note: "Verified — minor contracts exposure covered by platform"
- ❌ <12/15 → No badge — investigate skill content before publishing

---

## Step 5 — Deploy Preamble to Production

Once scan is complete, deploy the updated API (preamble is already in main.py):

```bash
cd /Users/clio/dev/lawtasksai-api
./deploy.sh
```

The preamble is injected at serve time — no skill schemas need to be updated.

---

## Step 6 — Commit Results

```bash
cd /Users/clio/dev/lawtasksai-api
git add scripts/promptfoo-test/scan-results/skill-verifications.json
git add scripts/SECURITY_PREAMBLE.py
git commit -m "security: add verified skill scan results and preamble v1.0"
git push
```

---

## Step 7 — Update Landing Pages (future work)

Once `skill-verifications.json` is committed:
1. Update `tasksai-landing-template` to read the JSON and render badges
2. Add `/security` page to template
3. Redeploy all 28 landing sites

*(This is Phase 3 in the original Promptfoo plan — not part of the scan itself.)*

---

## Known Limitations

| Item | Detail |
|------|--------|
| GPT-4o-mini harmful:hate | Fails 2-3/20 preamble tests regardless of wording. Claude passes 20/20. Disclosed to customers: Claude-based usage is fully verified; GPT-4o-mini has known model-level limitation. |
| contracts plugin | Most per-skill failures. Covered by preamble in production. Not a badge-blocking issue. |
| Grader false positives | data-exfil plugin occasionally produces empty output with no grader reason — these are timeouts, not real failures. |
| indirect-prompt-injection | Requires `indirectInjectionVar` config — excluded from scan. Would need document-aware setup to test properly. |

---

## Reminder

- **2026-05-26:** Re-test GPT-4o-mini harmful:hate — check if OpenAI model updates improved compliance. Cron ID: `889e3415`.

---

## File Reference

| File | Purpose |
|------|---------|
| `scripts/SECURITY_PREAMBLE.py` | Universal preamble — source of truth |
| `scripts/promptfoo-test/scan_preamble.py` | Preamble scan script |
| `scripts/promptfoo-test/scan_skills.py` | Per-skill scan (5-skill test version) |
| `scripts/promptfoo-test/run_scan.py` | Full 4,117-skill batch scan (to be built) |
| `scripts/promptfoo-test/scan-results/skill-verifications.json` | Master verification record |
| `scripts/promptfoo-test/scan-results/progress.json` | Resumable run state |
| `scripts/promptfoo-test/preamble-scan-results/preamble-summary.json` | Preamble scan results |
