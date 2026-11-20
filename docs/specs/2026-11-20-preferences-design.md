# traction-engine: preferences and rank score

Date: 2026-11-20. Extends [2026-11-11-m2-match-design.md](2026-11-11-m2-match-design.md). Status: agreed in session, building phase 1.

## Why

`match` answers one question: do the job seeker's skills cover a posting's requirements. That is only part of whether a posting is worth applying to. Postings are pulled by title alone, so a ranked list also has to account for things a skills inventory can't see: industries and companies the job seeker won't work for, languages that signal an engineering culture they want to avoid, postings that are closed or swamped, and pay that suggests a different job than the title.

So ranking is three layers:

1. **Skills fit**: the existing `match` score, unchanged. Shown as "Skills fit". `prompts/match.md` stays about skills only.
2. **Preferences**: hard exclusions, penalties, and bonuses, from a `preferences:` section in the private `skills.yaml`.
3. **Freshness**: closed postings and applicant counts, read from the posting text.

**Rank score** = skills fit (as points, 0-100) minus penalties plus bonuses, floored at 0. A closed or excluded posting scores 0. Every adjustment is listed in the rank row, so a score always explains itself.

## Design rules

- **Rules first.** Everything that has a right answer (a company name, a listed requirement, a date, a number) is a deterministic rule. Only culture, which doesn't, goes to an LLM, and it comes in phase 2.
- **Nothing disappears.** Closed and excluded postings stay in the table at score 0, marked, at the bottom. A run that triaged six postings shows six rows.
- **Computed at rank time.** Preferences read the saved extraction, the posting text, and the current `skills.yaml`. No LLM call, so editing preferences takes effect on the next `--rank` with no `--rematch`.
- **Coarse weights.** Three sizes, not per-signal tuning: small, medium, large (default 5 / 15 / 30 points). Finer weights would be invented precision.
- **An allowlist beats domain rules.** Some companies sit in an industry the job seeker otherwise avoids but are fine (a shipbuilder inside a defense conglomerate; a fintech group inside a healthcare company). `allow_companies` skips domain-based exclusions and penalties for them.

## Config

In `skills.yaml` (private). A synthetic example ships as `samples/skills.example.yaml`.

```yaml
preferences:
  weights: {small: 5, medium: 15, large: 30}
  allow_companies: [Example Shipyard]
  exclude:                       # score 0, still listed
    companies: [Example Defense Corp]
    domains: [defense, military]
  penalize:
    requirements:                # whole word in a name or alternative, or same aliases.key
      Java: {must: large, other: medium}   # 'other' = nice-to-have or one of several alternatives
      clearance: large                     # 'Top Secret/SCI clearance', 'clearance eligibility', ...
    domains: {gambling: medium, sports betting: medium}
    title: {founding engineer: medium}
    text: {"fast[- ]paced": small}         # regex on the posting text
    comp_min_over: {amount: 300000, size: medium}   # annual only
    out_of_area: large           # workplace not OK, or hybrid/onsite outside profile.locations_ok
    applicants_over: {count: 100, size: medium}
  bonus:
    requirements: {COBOL: medium}
```

Company, domain, and title terms match case-insensitively on word boundaries, like the existing flag rules.

## Freshness (built in, not configured)

- **Closed**: "No longer accepting applications", or "Recruiting for this role ends on M/D/YYYY" with a date before the posting's capture date. "Until the position is filled" is open. Score 0, marked `CLOSED`.
- **Applicants**: LinkedIn's "Over 100 applicants" / "Over 100 people clicked apply" count as 100+; smaller counts are parsed as numbers. At or over `applicants_over.count`, penalty.

Survey of 229 saved postings (2026-11-20): 53 at 100+ applicants, 1 closed by date, 47 mention Java (10 must-have, 34 as an alternative), 9 clearance requirements, 30 "fast-paced", 2 with `comp_min` over $300k (20 if `comp_max` were used, which would catch ordinary ranges topping out above it).

## Output

`triage --rank` columns: Score, Skills fit, Posting, Company, Title, Adjustments (e.g. `-30 Java (must); -15 100+ applicants`), Flags, Missing must-haves. `reports/rank.md` as now.

## Phase 2: culture check

One LLM call per posting, judging the posting text and company against a prose description of what the job seeker avoids (e.g. trading-floor finance culture, sports-betting culture, hype-driven startups), with stated carve-outs. Output: a flag, a size, and a one-line reason, shown in the row. Kept separate from the rules because it is the one judgment without a right answer, and it may flip between runs. Stored on the record so `--rank` stays free.

## Out of scope for now

Combination bonuses (e.g. agentic work plus COBOL): no saved posting has one yet. Renaming the `match` module or JSON key: display label only.
