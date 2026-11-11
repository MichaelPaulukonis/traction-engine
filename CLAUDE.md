# CLAUDE.md

## What this is

A job-posting triage pipeline, and a public 30-day experiment in operating an LLM tool and logging what breaks. Design: `docs/specs/2026-10-23-traction-engine-design.md` (parent) and `docs/specs/2026-11-11-m2-match-design.md` (M2: match first, slim eval after). Failure log: `breaks.md`. The 30-day clock started 2026-10-27.

A pipeline, not an agent: each step takes and returns Pydantic models (`models.py`) and is testable on its own. M1 `extract` is done. M2b `skills.yaml` + `match` is next, then a slim extraction eval (M2a), then M3 `retrieve`.

## Public repo, private data

This repo is public. Real data lives in the private `~/Documents/hunting` repo, and `config.local.yaml` (gitignored) points at it.

- Never commit or quote real posting text, resume content, `skills.yaml`, `reports/`, `calls.jsonl`, or anything from `hunting/`. `samples/` holds the only postings allowed in this repo, and they are synthetic.
- `breaks.md` and other public docs report aggregates and paraphrased failures. Naming the posting company is fine (it's already done); quoting private posting text is not.
- The LinkedIn bookmarklet that produces live postings lives in `hunting/linkedin/extractor/`, kept out of this repo on purpose.

## Commands

```bash
uv sync
uv run pytest                                   # unit tests, fake LLM, no API cost
uv run triage <posting.md>                      # extract one posting -> reports/<stem>.md + .json
uv run triage --backfill [DIR ...]              # all postings in configured or given dirs
uv run triage --summary [--cohort live]         # requirement frequencies
uv run variance run [--runs 5] <posting ...>    # run-to-run stability -> reports/variance/<ts>/
uv run variance score <dir>                     # re-score a saved variance run, free
uv run aliases propose                          # suggest name aliases for aliases.yaml
```

Real runs call OpenRouter and cost money (about $0.001 per posting with Haiku 5.5). The wrapper enforces `daily_cap_usd`. Tell the user the scope and rough cost before runs over many postings (backfills, variance, re-extraction).

## Conventions

- **Every LLM call goes through `llm.py`.** It logs each call to `calls.jsonl` (tokens, cost from OpenRouter's `usage.cost`, latency), checks the daily cap, and validates after logging so failed billed calls still get logged. No step talks to the SDK directly. Keep the wrapper small and hand-written; that's part of what the repo demonstrates.
- Model IDs and effort levels live in `config.yaml`, not in code.
- Prompts live in `prompts/`. Each extraction stores a `prompt_hash`.
- Postings are untrusted input: delimited as data, no tools, schema-only output.
- When the model flips on something that has a right answer, prefer a deterministic rule or validator after the LLM over more prompt text (see `JOB_BOARDS`, `poster_is_intermediary`, `intermediary_is_not_company`). Re-run affected saved postings to check a rule doesn't overcorrect.
- Extractions carry `cohort: backfill | live`. Don't mix their numbers.
- Unit tests use fakes/recorded responses. Evals (model behavior) stay separate from unit tests.
- When a real run surprises you, add a dated entry to `breaks.md`: what broke, why, what changed, with numbers. Plain prose, no hype.

## Ownership

Code is written with Claude Code. Prompts, eval design, and guardrail thresholds are the author's calls, since those are what interviewers ask about. Propose them with reasoning; don't settle them silently.

## Task tracking

There is no beads database here. Issues live in `~/Documents/hunting` under epic `hunting-m87`: run `bd` from that directory.

## Git

Public GitHub repo, one maintainer. Commit and push only when asked in the session.


<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:46cd31e7 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/core-concepts/sync-concepts.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   bd dolt push
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->
