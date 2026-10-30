# What broke

A running log of failures, surprises, and wrong assumptions while operating traction-engine. Cost and call data live in `calls.jsonl`; this file is for the things a log line doesn't explain.

## Before the clock started (M1 build, 2026-10-26 to 2026-10-27)

- **Cost estimate off by 10x.** I priced the backfill with Haiku 4.5 rates ($1/$5 per M tokens) when the model in use was Haiku 5.5 ($0.10/$0.50). Caught by per-call cost logging on the first real calls. Lesson: price from the model you actually call, and let the log check you.
- **Direct API credit ran out between two calls.** The account had enough for a smoke call one day and nothing the next (`400 credit balance is too low`). It never reached `calls.jsonl`, because nothing was billed. Switched to OpenRouter, which reports the actual charge in `usage.cost`, so the log now matches the bill.
- **`messages.parse` would have hidden failed calls.** The SDK validates the structured output inside the call, so a validation failure raises before the wrapper sees token usage: billed, but never logged. The wrapper now calls `messages.create` with the JSON schema, logs, then validates. Without this, the failure counts in the write-up would have been biased toward success.
- **"Small file" is not "junk".** A 600-byte file assumed to be junk was a real posting. The junk warning uses the number of extracted requirements, not file size.
- **"React or Angular" collapsed to one option.** First version flattened either/or requirements, losing the options. Added `alternatives`, so `--summary` can count "accepted as an option" separately from "required".
- **Aggregators are not agencies.** A posting relisted by an aggregator site came back with an intermediary named but `source: unstated`. Added an `aggregator` source and a validator that rejects a named intermediary with a direct or unstated source.

- **Two populations, not one.** The backfill archive was written by hand for human reading: summaries, partial copies, my own notes mixed in, boilerplate already cut. Live intake (via a LinkedIn bookmarklet) will be verbatim postings with benefits and EEO text included. Extraction quality and requirement counts on one say little about the other, so every extraction is tagged `cohort: backfill` or `cohort: live`, and `--summary --cohort` keeps them apart. The backfill is day 0: a stress test of the schema and a look at a year of history, not the baseline for live performance.

## Day 1 (2026-10-27): backfill

Backfill of 97 archived and current postings: 0 failed, 0 validation retries, $0.10 total, median 4.3s per posting (max 12.6s), largest input 6.1K tokens.

- **Duplicate files overwrote reports.** Two postings existed as identical `.md` and `.txt` copies; reports are named by stem, so the second silently replaced the first. Harmless this time (identical content), but two different postings sharing a name would have lost one, and the run paid for two duplicate extractions. `find_postings` now keeps one file per stem and says which it skipped.
- **The archive is not all summaries.** Some archived files are verbatim pastes (Greenhouse, LinkedIn) with full boilerplate. The backfill cohort is mixed, not purely hand-written.
- **Requirement names don't normalize well enough to count.** 818 distinct names across 95 postings, 643 used only once. Some are case and punctuation variants ("Problem-solving" / "Problem solving", "Node.js" / "NodeJS"); most are long, posting-specific phrases. Frequencies for common skills (React, TypeScript, AWS) look usable; the long tail is noise.
- **OpenRouter disagrees with itself about what the backfill cost.** `calls.jsonl` (built from `usage.cost` on each response) says $0.1029. OpenRouter's `/api/v1/key` endpoint, for both the key and the workspace budget, also says $0.1029455. The OpenRouter dashboard, same workspace, same 19 minutes, says $0.18. The log matches one of their numbers to the last digit; which one is the bill is still open. Ground truth needs the credit balance before and after, and that endpoint needs a management key. Next: log each response ID and reconcile per call against `/api/v1/generation`.
- **Zero validation failures means the validators are rarely exercised.** Structured output makes schema failures rare; only the two cross-field checks can fail. The retry path is tested by unit tests only, not by real traffic yet.

## Day 1 live (2026-10-29): first verbatim postings

Four postings captured with the LinkedIn bookmarklet, extracted as `cohort: live`: 0 failed, $0.005 total. Each file carries a "Source: LinkedIn" capture line, which the backfill never had.

- **The job board became an intermediary, sometimes.** One of four came back `source: aggregator, intermediary: LinkedIn`; the other three said `direct`. Re-running the same posting with no prompt change gave `direct`. A prompt rule would only lower the flip rate, so it is now a lookup: a `JOB_BOARDS` list checked before validation clears the intermediary and sets `direct`. Repost sites (Dice, Built In relistings) stay out of the list, since those really are aggregators.
- **The schema couldn't say "Senior / Staff".** `seniority` was a single enum, so range titles collapsed to the lower level with no sign anything was lost: a staff-reachable role filtered as senior. It is now a sorted, deduplicated list (`["senior", "staff"]`); summaries count a posting under each level it names. Old extractions stored a string and still load. None of the backfill titles were ranges; two of the first four live ones were.
- **The posting disagrees with itself.** One listing's title says "Senior/Lead"; its body says "Senior" twice, in a bold header and the first paragraph. The model took the body's version. Left alone: that's a defensible reading, not an extraction bug.
- **Same input, different counts.** Re-extracting the four postings moved must-have plus nice-to-have counts from 20/29/18 to 22/33/15. That run-to-run spread is the floor any extraction eval has to beat; it needs measuring (repeat runs per posting) before precision/recall numbers mean much.
