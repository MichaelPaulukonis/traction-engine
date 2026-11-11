# traction-engine M2: match first, slim eval after

Date: 2026-11-11. Tracking: `traction-engine-bmr` (M2b), `traction-engine-c5w` (M2a slim), `traction-engine-p0n` (M3). Parent: [2026-10-23-traction-engine-design.md](2026-10-23-traction-engine-design.md). Status: draft, awaiting review.

## Decision: split M2, match goes first

The parent spec's M2 bundled four things: a golden set, `skills.yaml`, `match`, and an eval runner with CI. It is now two pieces:

- **M2b, `skills.yaml` + `match`: build first.** It is the step that turns a posting parser into a triage tool. Goal: usable as soon as possible, then iterate.
- **M2a, slim extraction eval: after match is in daily use.** Timeboxed to about one day.

Why: an adversarial review of the full M2a design (prosecution, defense, judge; 2026-11-09) returned RESHAPE. The design called itself "mostly the portfolio piece" while match, the only job-search utility, had not started at day 13 of the 30-day clock. Both sides agreed the expensive parts (quote-set labeling, spliced private injection cases, labeling tooling, paid CI) could be deferred. The defense's point stands: `variance` measures agreement between runs, so it cannot see consistent errors (an agency posting tagged `direct` in every run). Some labeled ground truth is still needed, which is why M2a is slimmed rather than dropped.

## M2b design

### Skills inventory: `skills.yaml`

Private, lives in `hunting/`; `skills_path` in `config.local.yaml` points at it (omitted in public `config.yaml`). `config.yaml` adds `models.match` and `effort.match` (Haiku 5.5 at `low`, matching `extract`).

Source: `hunting/resume/canonical_resume.md` (Technical Skills section, tiered by the rule "Primary = could do it live in a technical screen today; Familiar = used in real work") plus `hunting/resume/claims_ledger.md` (each claim traced to a graded fact). Both are already hand-reviewed, so the inventory is built from them instead of having an LLM derive skills freely from resume text, as the parent spec proposed.

```yaml
profile:                      # inputs for flags, not skills
  years: 15
  seniority: [senior, staff, lead]
  workplace_ok: [remote, hybrid]      # hybrid/onsite only within locations_ok
  locations_ok: [Boston, Framingham, MA]
  contract_ok: [full_time, contract_to_hire, contract]
skills:
  - name: TypeScript
    tier: primary             # primary -> met, familiar -> partial
    evidence: "Travelers microservices; Aetna starter kit (ledger: Backed)"
  - name: Insurance
    tier: primary
    evidence: "Travelers 2017-2025, Insurity"
```

- **Drafted once in an agent session, not by a CLI command.** The agent reads the canonical resume's skills section and the ledger, writes `skills.yaml`, and the owner reviews it and extends it by hand, adding domain knowledge and soft skills, since without entries those will match as `missing`. It is never regenerated: regenerating would wipe hand edits, so later resume changes are applied by hand. A one-off doesn't need a command, a prompt file, and tests.
- **No `id` or `aliases` in `skills.yaml`.** `name` is the single identifier and uses the canonical name from `aliases.yaml`, which stays the only list of synonyms (different labels for the same thing). New synonyms go there, so `--summary` benefits too.
- **No coverage mappings yet** ("this skill satisfies that requirement", e.g. Lambda covering "serverless"). Those aren't synonyms, so they must not go in `aliases.yaml`, where they would distort `--summary` counts. The LLM pass handles them for now. Add a `covers:` list per skill only if logs show the LLM repeatedly missing, or flipping on, the same requirement.
- Skill names must be unique after `aliases.key(name, lookup)` normalization (which applies `aliases.yaml` and `aliases.spelling_key`); loading fails otherwise.
- The `profile` values above are defaults to confirm, not decisions.

### Matching: `match.py`

`match(requirements: Requirements, skills: Skills) -> Match`

1. **Deterministic pass.** Normalize `[r.name, *r.alternatives]` with `aliases.key(name, lookup)` and look up against skill keys. Take the best hit across the requirement and its `alternatives` (`met` > `partial`): a primary-tier skill gives `met`, a familiar-tier skill gives `partial`. Any hit (`met` or `partial`) short-circuits the LLM pass for that requirement. This pass exists for **consistency, not cost**: common skills (Python, AWS, TypeScript) get the same verdict on every posting. It rarely saves the LLM call, because nearly every posting has some long-tail, posting-specific requirement that won't match a skill name.
2. **LLM pass, for what is left.** One call per posting, only if anything is unmatched. Model: Haiku via the existing wrapper (cost log, budget cap, schema validation, one retry). Input: the unmatched requirements (`index` `0..N-1`, `name`, `alternatives` if any, and `quote`) plus a compact skills list (`name`, `tier`, `evidence`), delimited as data. Output per requirement: `index`, `verdict` (`met` / `partial` / `missing` / `unsure`), `skill` (nullable), `reason`. Pydantic validates that returned indices match `0..N-1` one-to-one so joining back never depends on the model echoing requirement names verbatim.
3. **Code-side guards**, which put "a false `met` is worse than a false `missing`" into code instead of the prompt:
   - resolve `skill` through `aliases.key(skill, lookup)` against `skills.yaml` so casing or synonym variants (`"typescript"`, `"TS"`) resolve to `"TypeScript"`;
   - a `met` or `partial` whose `skill` is null or doesn't resolve to a skill in `skills.yaml` becomes `unsure`;
   - a `met` that resolves to a familiar-tier skill becomes `partial`;
   - `skill` on `missing` or `unsure` is cleared to `null`.
4. **Flags**, computed by rules with no LLM, and kept separate from the score:
   - `years_min` greater than profile years;
   - seniority with no overlap with the profile;
   - workplace not in `workplace_ok`, or hybrid/onsite with non-empty `locations` where **no** listed location matches `locations_ok` on word/token boundaries (so `"MA"` matches `"Boston, MA"` but not `"Madison, WI"`, and `["New York, NY", "Boston, MA"]` passes);
   - `employment_type` not in `contract_ok`.

   A field that is `unstated` (or empty `locations`) never raises a flag.
5. **Score**: must-have coverage, (met + 0.5 × partial) / count of must-haves. Nice-to-haves are shown but not scored, because weighting them would be invented precision. With no must-haves, the score is `null` and the report shows a warning instead of 100%.

Sonnet only if Haiku's verdicts prove bad in use.

Plain code, not a LangGraph graph. An earlier plan (`hunting-m87.3`) proposed building match and retrieve together as a LangGraph StateGraph, with the condition "if the graph ends up linear, drop LangGraph." Match on its own is linear (lookup, one LLM call, rules), so LangGraph moves to M3, where retrieve adds real branching (low retrieval confidence → "I don't know" / human review).

### CLI and storage

- `triage posting.md` (and `triage --backfill`) runs extract and then match whenever `skills_path` is configured. The report (`reports/<stem>.md`) gains a fit section:
  - a headline, e.g. `Fit 72% | Must 6✓ 2~ 1✗ (Kubernetes) 1? | flags: onsite NYC`;
  - a must-have table (requirement, verdict, evidence);
  - a nice-to-have table.
- Match results are stored under the `"match"` key inside `reports/<stem>.json` (recording `skills_hash`, `matched_at`, `run_id`, `score`, `flags`, `must_have`, `nice_to_have`), keeping one JSON and one Markdown file per posting so `reports.glob("*.json")` in `--summary`, `--rematch`, and `--rank` reads a single record without file-glob collisions or cross-file joins.
- `triage --rematch` re-runs match over saved extractions in `reports/*.json` with no new extraction calls, updating `"match"` and re-rendering `reports/<stem>.md` after `skills.yaml` edits.
- `triage --rank [--since DATE]` prints a table of postings sorted by score, with flags and the top missing must-haves. This is the daily view.

### Testing

Unit tests with a fake LLM, following the existing pattern. Cases:

- name hits via `aliases.yaml` synonyms, and either/or alternatives (best hit wins, any hit short-circuits LLM);
- the downgrade rules (unknown/null `skill`, synonym/case resolution on `skill`, `met` on a familiar skill);
- each flag rule, including `unstated` never flagging, multi-location hybrid/onsite, and token-boundary matching on state abbreviations (`"MA"` vs `"Madison, WI"`);
- score edge cases (no must-haves, all partial);
- a `skills.yaml` with duplicate skill names (after `aliases.key` normalization) fails to load.

No match eval in M2b. Verdict errors found in daily use get logged in `breaks.md` and seed the later match golden set.

### Also in M2b

- Update the README status order: match, then the eval set, then retrieval.
- Add a `breaks.md` entry for the first real run.

### Out of scope

Nice-to-have weighting, outcomes data, cover letters, Sonnet by default, apply/skip thresholds.

## M2a, slim (after match is in daily use)

Timebox: about one day.

- **Golden set**: 10-15 live postings, hand-picked to cover known failure types (agencies including an unnamed client, aggregator, poster ≠ company, multiple locations, seniority range, multiple years values) plus a few plain direct postings as controls. Field labels only (source, intermediary, company, workplace, locations, seniority, years_min), hand-written as one YAML file per posting under `golden_dir`. No quote sets yet.
- **Scorer**: `eval score`, reusing the `variance.py` run and score structure. Per-field accuracy, a "stable" count (correct in every run), and a Wilson interval over postings.
- **Injection**: a `forbidden` assertion on the existing synthetic injection sample.
- **Framing**: in the write-up, a regression set stratified toward already-fixed failures, with intervals of about ±20 points. Not an accuracy claim.
- **CI**: pytest only.

Deferred:

- **Paid CI job.** Its budget cap reads spend from the local `calls.jsonl` (`llm.spent_today()`), which an ephemeral Actions runner starts empty, so the cap needs another mechanism first.
- Quote-set labels.
- Spliced private injection cases.
- `eval label` pre-fill tooling.
- The poster ≠ company `direct` validator for the Joblet-AI/micro1 case. It is a known bug, logged for later.
