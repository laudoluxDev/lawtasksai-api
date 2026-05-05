# /security Page Copy — TasksAI Platform
*Created: 2026-05-05 | Applies to all 30 verticals*
*Replace {VERTICAL_NAME}, {AUDIENCE}, {DOMAIN} per vertical at deploy time.*

---

## Page Title
Security Verification — {VERTICAL_NAME}

## Meta Description
Every skill in the {VERTICAL_NAME} catalog is automatically tested against prompt
injection, unauthorized commitments, harmful content generation, and data
exfiltration. See what we test and why it matters.

---

## Hero Section

### Headline
Your AI tools should be as secure as your work demands.

### Subheadline
Every skill in the {VERTICAL_NAME} catalog is independently verified against
industry-standard security tests before it reaches you — and automatically
re-tested whenever a skill is updated.

---

## Section 1: What We Test (and Why)

### What is prompt injection?

Prompt injection is a type of attack where malicious instructions are embedded
in content that an AI reads — attempting to hijack the AI's behavior, cause it
to ignore its instructions, reveal sensitive information, or act outside its
intended purpose. As AI tools become standard in professional workflows, prompt
injection is one of the most important security concerns to address.

It works similarly to SQL injection in databases: instead of inserting malicious
database commands, an attacker inserts malicious AI instructions into a document,
email, or input field — hoping the AI will follow them instead of its legitimate
instructions.

### What could go wrong without security testing?

An AI tool without security verification could, if attacked:

- **Reveal its own internal instructions** — exposing proprietary expert
  frameworks and workflows to anyone who asks cleverly enough
- **Commit to legal or financial terms** on your behalf — drafting a letter
  that accepts a contract, settlement, or liability clause you never reviewed
- **Produce biased or discriminatory content** — generating documents that
  use protected characteristics inappropriately if prompted to do so
- **Leak data through hidden channels** — embedding sensitive information
  in image URLs or links that silently transmit it to third parties

### How we address it

{VERTICAL_NAME} uses Promptfoo — the industry-standard open-source AI
red-teaming framework used by hundreds of organizations — to automatically
scan every skill for these vulnerabilities.

Each skill is tested against attack probes generated specifically for its
purpose. A demand letter tool gets attacked differently than a compliance
checker. The attacks are tailored, not generic.

---

## Section 2: Our Two-Layer Verification System

### Layer 1 — Platform Safety (all skills, all verticals)

Every skill is protected by a universal safety layer that is tested independently
against both Claude and GPT-4o-mini models. This layer covers:

| Protection | What it prevents |
|------------|-----------------|
| **Bias guard** | Generating content that uses protected characteristics (race, religion, gender, disability, sexual orientation) as evidence of incompetence or dishonesty — except where directly and factually relevant to the specific capacity at issue |
| **Commitment guard** | Drafting communications that accept legal terms, contracts, or financial obligations on your behalf without your explicit review |
| **Prompt confidentiality** | Revealing the internal expert framework when asked — protecting our proprietary skill content |
| **Injection guard** | Following instructions embedded in document content rather than treating documents as data to be analyzed |

**Claude Haiku: 20/20 tests passed**
**GPT-4o-mini: 17/20 tests passed**

> **Note on GPT-4o-mini:** GPT-4o-mini partially complies with bias-related
> requests despite explicit refusal instructions. If you use {VERTICAL_NAME}
> skills through ChatGPT or GPT-4o-mini, be aware of this known model-level
> limitation. Claude-based usage (via OpenClaw or the Anthropic API) is fully
> verified. We re-test GPT-4o-mini automatically and will update this page
> when OpenAI ships improvements.

### Layer 2 — Per-Skill Verification

Each individual skill is additionally tested on its own for skill-specific risks:

| Test | What it checks |
|------|---------------|
| **Unauthorized commitments** | Does this skill draft binding agreements when prompted? |
| **Excessive agency** | Does this skill claim capabilities it cannot actually perform? |
| **Prompt extraction** | Does this skill reveal its own instructions when asked? |

Skills that pass all tests are marked **Security Verified** on their skill card.
Skills are automatically re-tested whenever their content is updated.

---

## Section 3: Your Data Never Touches Our Servers

Security verification applies to the skill framework itself — not your usage of it.

{VERTICAL_NAME} delivers expert analysis frameworks. Your AI applies them.
Your documents and queries go to your AI provider (Anthropic, OpenAI, etc.),
never to us. For maximum confidentiality, use Anthropic's Zero Data Retention
API or a local model via OpenClaw + Ollama.

---

## Section 4: Verification Table (dynamic — rendered from skill-verifications.json)

*[This section is auto-generated at deploy time from scan results]*

### All Verified Skills

| Skill | Category | Last Verified | Tests Passed | Status |
|-------|----------|--------------|--------------|--------|
| {skill_name} | {category} | {date} | {passed}/{total} | ✅ Verified |

---

## Section 5: About Promptfoo

Promptfoo is an open-source AI red-teaming and evaluation framework, now part
of OpenAI. It is used by hundreds of organizations to systematically test AI
systems for security vulnerabilities before deployment.

We use Promptfoo's Community tier, which runs locally — no scan data is sent
to Promptfoo servers. Attack probes are generated using OpenAI's API and
evaluated against the skill's expert framework.

[promptfoo.dev →](https://promptfoo.dev)

---

## Footer Disclaimer (required on all pages)

{VERTICAL_NAME} is software that assists {AUDIENCE} with their work.
It is not a licensed professional service and does not provide professional
advice. Always apply your own professional review and judgment to any output.
Laudo Lux, LLC.

---

## Per-Vertical Substitutions

| Vertical | {VERTICAL_NAME} | {AUDIENCE} | {DOMAIN} |
|----------|----------------|------------|---------|
| law | LawTasksAI | attorneys and legal professionals | lawtasksai.com |
| realtor | RealtorTasksAI | real estate professionals | realtortasksai.com |
| marketing | MarketingTasksAI | marketing professionals | marketingtasksai.com |
| farmer | FarmerTasksAI | agricultural professionals | farmertasksai.com |
| therapist | TherapistTasksAI | therapists and mental health professionals | therapisttasksai.com |
| contractor | ContractorTasksAI | contractors and construction professionals | contractortasksai.com |
| dentist | DentistTasksAI | dental professionals | dentisttasksai.com |
| salon | SalonTasksAI | salon and beauty professionals | salontasksai.com |
| teacher | TeacherTasksAI | educators and school administrators | teachertasksai.com |
| accountant | AccountingTasksAI | accounting and finance professionals | accountingtasksai.com |
| (remaining 19 verticals follow same pattern) | | | |
