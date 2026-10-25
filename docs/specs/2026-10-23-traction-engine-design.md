# traction-engine: design

Date: 2026-10-23, revised 2026-10-24 after an adversarial review (verdict: RESHAPE). Tracking: `hunting-m87` (Jev comparison: `hunting-m87.1`), in the private `hunting` repo.

## Purpose

A job-posting triage pipeline: given one posting, decide how well it fits and what the gaps are. It is built to be used during an active job search, and to serve as public proof of production LLM work: structured extraction, model selection by cost, evals, and cost guardrails, operated for 30 days with a log of what broke.

Why precision, not volume: an AI auto-apply service sent 400-600 applications (Jan-Feb 2026) for 2-3 interviews, and both jobs actually landed came through recruiters. The value is in picking the few postings worth real effort.

Not an agent. The task has a fixed shape, so it is a pipeline of small steps, each testable and evaluable on its own. Agent design and multi-agent orchestration are a known gap, owned in interviews rather than built here.

The portfolio value is the cost-logged wrapper, an honest eval, and a real 30-day log of what broke. Everything that delays starting that clock without strengthening those three is deferred.

## Time budget

Weekdays belong to the job search (applications, recruiter outreach). Build work is sized to weekends. M1 is timeboxed to one weekend; if M1 is not running on real postings by the end of the second weekend, stop and reassess before building anything else.

## Scope

In v1 (M1): local Python CLI, thin LLM wrapper with cost log and daily budget cap, `extract` step, a backfill run over the existing postings archive, `breaks.md`.

Gated (M2+, only after M1 has run on real postings for about a week): `match`, one golden eval set, eval runner and CI.

Deferred until the 30-day log shows a need: `retrieve`, a full `report`, additional golden sets, Jev comparison.

Out of scope: web UI, database, auth, scraping, email/Gmail intake, cover-letter generation, multiple providers in the CLI, multi-agent orchestration. Cloud deployment is deliberately out: the author has years of AWS Lambda ops (auth, secrets, rollback), so deploying would prove the least new. It is a cheap add-on later (e.g. a scheduled Lambda) if a role needs it shown.

## Architecture

Repo: public, Python, managed with `uv`. Code only. A config file points at private data; the repo ships a small synthetic set (3-5 fake postings) for CI and the README demo.

CLI: `triage path/to/posting.md` writes a Markdown report and prints a summary (top requirements now; score and gaps once `match` exists; cost of the run). `triage --backfill DIR` runs over a directory of postings.

Pipeline steps. Each takes and returns Pydantic models:

1. `extract` (M1): posting text → `Requirements` (must-have and nice-to-have requirements, years, domain, remote/location, comp if listed). Model: Haiku, structured output validated by Pydantic.
2. `match` (M2): `Requirements` + skills inventory → per-requirement `met` / `partial` / `missing`, with the resume line cited as evidence. Deterministic keyword match first; Sonnet judges only the fuzzy cases.
3. `retrieve` (deferred): nearest past postings and cover letters by embedding similarity, with their outcomes. Local embeddings, no vector DB. Until then, `rg` over `hunting/postings/` does the job.
4. `report`: grows with the steps above.

LLM access goes through one thin wrapper module (Anthropic SDK only). The wrapper does the cost logging and budget check, so no step talks to the SDK directly. Model IDs live in config. The wrapper is deliberately hand-written rather than LiteLLM/Langfuse: it is about 100 lines, and writing it is part of what the repo demonstrates. If it grows past that, swap in a library.

Cost guardrail: every LLM call appends a line to `calls.jsonl` (run ID, step, model, input/output tokens, cost, latency). Before each call the wrapper checks today's spend against a daily cap from config; over the cap, it refuses with a clear message.

## Data

Private (lives in `hunting/`, never committed to this repo):

- Postings: existing `postings/*.md`. Intake is a Markdown file (saved posting, pasted text, or notes from a recruiter call). No scrapers.
- `skills.yaml` (M2): skills inventory. Generated once by the LLM from the resume Markdown, then hand-edited. Treated as reviewed data, not re-derived each run.
- `outcomes.csv` (deferred, overlaps `hunting-ptr`): one row per posting with decision and result. Only needed for the end-to-end eval.

Public: the synthetic set, plus the 30-day write-up.

Write-up and privacy: the public write-up reports aggregates (runs, cost, latency, failure counts by type) and paraphrased failure examples. No resume content and no outcomes. Posting text is quoted only from publicly listed postings.

## Evaluation

Start with one golden set, chosen after a week of M1 use shows where the real failures are. The likely first set is extraction (15-20 hand-labeled postings; precision and recall on must-haves), since that is the only step that exists early.

Later sets, if the log justifies them:

- Match: requirement/resume pairs hand-labeled `met` / `partial` / `missing`. Errors are asymmetric: calling a missing requirement "met" (wasted application, awkward interview) is worse than the reverse. This is the set Jev would compete on.
- End-to-end: agreement with past apply/skip decisions. Labels are noisy; disagreements are cases to inspect, not failures.

Honest stats: report every accuracy number with a Wilson confidence interval and the sample size. At about 50 items the interval is roughly ±12 points, too wide to separate two similar models, and the write-up says so rather than claiming a winner. This method is shared with `hunting-0ac`.

Labeling cost: realistically 1-2 days across all sets, done incrementally, one set at a time. It is the part to explain in interviews.

## CI

- Every PR: unit tests only, using recorded LLM responses. No API cost.
- Paid eval on the synthetic set runs only when model behavior can change: GitHub Actions `paths:` filter on `prompts/**`, pipeline modules, Pydantic models, and model config, plus a manual `workflow_dispatch` trigger. It has its own hard budget cap via the same wrapper.
- The CI gate checks things a small set can actually detect: schema-valid output on every sample, required fields present, cost per run under a ceiling. Accuracy is reported, not gated, because the sample is too small to gate on without flaking.
- The full private eval runs locally on demand.

## Failure handling

- Schema validation failure: retry once with the validation error fed back to the model, then fail loudly. Both attempts logged.
- API errors: Anthropic SDK built-in retries; no custom retry logic.
- Junk input (e.g. a pasted LinkedIn page full of navigation): if extraction finds fewer than about 3 requirements, the report leads with a warning instead of a confident result.
- Low confidence on a requirement (M2): marked "unsure, check this" in the report rather than guessed. This is the human-in-the-loop path.
- Budget cap reached: refuse with a clear message.

## Testing

Unit tests (recorded responses as fixtures) test the plumbing. Evals test model behavior. They stay separate so CI stays cheap.

## Milestones

- M1 (one weekend, timeboxed): repo scaffold, wrapper with cost log and budget cap, `extract`, CLI with `--backfill`. Same day: backfill over the `hunting/postings` archive for immediate volume and failure data, and start `breaks.md`. The 30-day clock starts here.
- Gate: about a week of M1 on real postings, and at least 5 new ones, before starting M2.
- M2: first golden set, `skills.yaml`, `match`, eval runner and CI.
- Later, only if the log shows a need: `retrieve`, more golden sets, cloud deployment, Jev (`m87.1`, explicitly optional).
- Done: in daily use, at least one honest eval with confidence intervals, 30-day log (`calls.jsonl` + `breaks.md`) written up as a blog/LinkedIn post.

## Ownership note

The code is written with Claude Code. Prompts, eval design, and guardrail thresholds are worked through and decided by the author, since those are the parts interviewers ask about.
