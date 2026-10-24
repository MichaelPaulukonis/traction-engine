# traction-engine: design

Date: 2026-10-23. Tracking: `hunting-m87` (Jev comparison: `hunting-m87.1`), in the private `hunting` repo.

## Purpose

A job-posting triage pipeline: given one posting, decide how well it fits, what the gaps are, and which past postings and cover letters are most similar. It is built to be used daily during an active job search, and to serve as public proof of production LLM work: structured extraction, model selection by cost, per-step evals, and cost guardrails, operated for 30 days with a log of what broke.

Why precision, not volume: an AI auto-apply service sent 400-600 applications (Jan-Feb 2026) for 2-3 interviews, and both jobs actually landed came through recruiters. The value is in picking the few postings worth real effort.

Not an agent. The task has a fixed shape, so it is a pipeline of small steps, each testable and evaluable on its own.

## Scope

In v1: local Python CLI, four-step pipeline, cost log and daily budget cap, three golden eval sets, CI eval on a synthetic sample set.

Out of v1: web UI, database, auth, scraping, email/Gmail intake, cover-letter generation, multiple providers in the CLI, AWS deployment, multi-agent orchestration. Each can be added later if the 30-day log shows a need.

## Architecture

Repo: public, Python, managed with `uv`. Code only. A config file points at private data; the repo ships a small synthetic dataset for CI and demos.

CLI: `triage path/to/posting.md` writes a Markdown report and prints a summary (score, top gaps, cost of the run).

Pipeline steps. Each takes and returns Pydantic models:

1. `extract`: posting text → `Requirements` (must-have and nice-to-have requirements, years, domain, remote/location, comp if listed). Model: Haiku, structured output validated by Pydantic.
2. `match`: `Requirements` + skills inventory → per-requirement `met` / `partial` / `missing`, with the resume line cited as evidence. Deterministic keyword match first; Sonnet judges only the fuzzy cases.
3. `retrieve`: nearest past postings and cover letters by embedding similarity, with their outcomes. Local sentence-transformers embeddings, cached on disk. No vector DB.
4. `report`: score, gaps, what to emphasize, closest past examples, run cost.

LLM access goes through one thin wrapper module (Anthropic SDK only in v1). The wrapper does the cost logging and budget check, so no step talks to the SDK directly. Model IDs live in config, so the eval can compare models per step (e.g. Haiku vs Sonnet on `match`).

Cost guardrail: every LLM call appends a line to `calls.jsonl` (run ID, step, model, input/output tokens, cost, latency). Before each call the wrapper checks today's spend against a daily cap from config; over the cap, it refuses with a clear message.

## Data

Private (lives in `hunting/`, never committed to this repo):

- Postings: existing `postings/*.md`.
- `outcomes.csv`: one row per posting: file, decision (applied/skipped), result (no reply, rejected, interview, offer). Built once by trawling 2025-26 notes (overlaps `hunting-ptr`).
- `skills.yaml`: skills inventory. Generated once by the LLM from the resume Markdown, then hand-edited. Treated as reviewed data, not re-derived each run.

Public: a synthetic sample set (a handful of fake postings, a fake skills inventory, labels) for CI and the README demo.

Intake in v1 is a Markdown file (saved posting, pasted text, or notes from a recruiter call). No scrapers.

## Evaluation

Three golden sets, one per step that matters:

1. Extraction: 15-20 postings with hand-labeled must-have and nice-to-have requirements. Metric: precision and recall on must-haves.
2. Match: about 50 requirement/resume pairs hand-labeled `met` / `partial` / `missing`. Metric: accuracy and a confusion matrix. Errors are asymmetric: calling a missing requirement "met" (wasted application, awkward interview) is worse than the reverse (one skipped job). This is the set Jev competes on in v1.1.
3. End-to-end: agreement between the score and past apply/skip decisions. These labels are noisy; disagreements are cases to inspect, not failures.

Stats: report accuracy with Wilson confidence intervals and state sample sizes plainly. This method is shared with `hunting-0ac` (statistically sound personal eval).

Labeling the extraction and match sets is the main cost (about 2-3 hours), and the part to explain in interviews.

## CI

- Every PR: unit tests only, using recorded LLM responses. No API cost.
- Paid eval on the synthetic set runs only when model behavior can change: GitHub Actions `paths:` filter on `prompts/**`, pipeline modules, Pydantic models, and model config. Also a manual `workflow_dispatch` trigger. The CI run has its own hard budget cap via the same wrapper. A drop below the recorded baseline fails the build.
- The full private eval runs locally on demand.

## Failure handling

- Schema validation failure: retry once with the validation error fed back to the model, then fail loudly. Both attempts logged.
- API errors: Anthropic SDK built-in retries; no custom retry logic.
- Junk input (e.g. a pasted LinkedIn page full of navigation): if extraction finds fewer than about 3 requirements, the report leads with a warning instead of a confident score.
- Low confidence on a requirement: marked "unsure, check this" in the report rather than guessed. This is the human-in-the-loop path.
- Budget cap reached: refuse with a clear message.

## Testing

Unit tests (recorded responses as fixtures) test the plumbing. Evals test model behavior. They stay separate so CI stays cheap.

## Milestones

- M1 (weekend 1): repo scaffold, wrapper with cost log and budget cap, `extract`, CLI. Start running on real postings daily and keep `breaks.md`.
- M2: label the extraction and match sets; `match`; eval runner and CI.
- M3: `retrieve` and the full `report`.
- v1.1: Jev (TypeSafe AI) as a challenger on `match`: accuracy, cost, latency, and whether its calibrated confidence gives a better "flag for review" threshold. Jev is discriminative (no free-text output), so it does not fit `extract`.
- Done: in daily local use, eval in CI, 30-day log (`calls.jsonl` + `breaks.md`) written up as a blog/LinkedIn post.

## Ownership note

The code is written with Claude Code. Prompts, eval design, and guardrail thresholds are worked through and decided by the author, since those are the parts interviewers ask about.
