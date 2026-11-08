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

## 2026-11-04: agencies under their own name

- **A recruiter's name on the posting read as the employer.** Six live postings were tagged `direct` or wrong: DeepRec.ai (x2) and Strativ Group ("our client is", "we are partnered with"), deCircle reposting a Paraform recruiter link for Loancrate, and ITSMF (a forum) reposting Capital One's own text. The opposite error too: Lifted, an Upwork Company, tagged `aggregator` with Upwork as intermediary, when Lifted is the agency placing contractors with "an enterprise client". The prompt now compares the poster (the capture heading) with the hiring company and lists the agency and aggregator signs. Six postings x 3 runs: source correct 18/18.
- **The agency also landed in `company`.** For unnamed clients the model filled `company` with the agency's own name in about 1 run in 3. A validator now clears `company` when it matches the intermediary (prefix match, so "Lifted" and "Lifted, an Upwork Company" count as the same).
- **The first fix overcorrected.** A blunt "any mention of a client means agency" rule turned Charles River Associates, a consulting firm describing its own clients, into `aggregator` with no intermediary. Caught by re-running all 63 previously-direct live postings: 1 changed. Narrowed the rule, and a validator now rejects `agency`/`aggregator` with no intermediary named, so that combination goes back through the retry.
- **Still flips: which intermediary.** deCircle vs Paraform for the Loancrate posting (2:1 and 1:2 across two sets of runs). Both are defensible (poster vs the recruiting platform it links to); left alone.
- **A call hung for 96 minutes.** Mid-regression, one extraction never returned and never reached `calls.jsonl`. The client had no explicit timeout (SDK default: 10 min per attempt plus retries, so this was past even that). Now `timeout=60, max_retries=2`; extractions take about 6s. Cause unknown; a silent hang is the failure the log can't see.

## 2026-11-05: run-to-run variance

First variance smoke test (`variance run`): 10 live postings x 5 runs, same prompt, $0.07. Five runs is a shakedown, not a measurement; no confidence intervals from this.

- **Exact quote matching undercounted agreement.** Runs quote the same requirement but cut it off at different points ("experience defining AI model governance processes" vs the same phrase plus ", including lineage tracking"). Exact match scored that as three different requirements. Counting a quote as matching one that contains it (one-to-one, exact matches first) moved mean quote Jaccard from 0.71 to 0.84. Name Jaccard is 0.58: the runs mostly find the same requirements but label them differently, so name normalization is the bigger gap.
- **Requirement counts swing.** Mean stdev of the total is 1.2, but ITSMF ranged 17 to 27, all of it in nice-to-have. No requirement moved between must-have and nice-to-have across runs.
- **`source` still flips on one recruiter.** Motion Recruitment came back `agency` in some runs and `unstated` with no intermediary in others. The validator only catches the reverse case.
- **`locations` truncated.** In 4 of 10 postings some runs listed every city and others only the first. ITSMF lost "Remote" in some runs, which matters for matching.
- **`years_min` picks a different number** when a posting lists several (Capital One 2 vs 4, Motion Recruitment 2 vs 5).

## 2026-11-07: name aliases

- **Spelling variants were the small problem.** Merging names that differ only in case, spacing, punctuation, or a trailing plural (`Node.js` / `NodeJS`, `REST API` / `REST APIs`) is now automatic. It moved run-to-run name Jaccard from 0.575 to 0.582, and distinct names across all saved extractions from 1387 to 1350. Runs rarely misspell; they relabel (`Git` vs `Version control`).
- **Relabels need a human.** `aliases propose` suggests groups where one quote got different names across runs; `aliases.yaml` holds the ones I accepted (42 groups, 133 names). The first version proposed 414 groups, mostly junk: one quote listing "Python, Docker, AWS" is three requirements, not three names for one. A quote that any single extraction split is now skipped, leaving 52 real candidates.
- **Name Jaccard 0.575 to 0.787, but that's tuned on the test data.** The aliases came from the same 5-run outputs they're scored on. A fresh variance run on other postings is the honest number. Across the whole corpus distinct names only drop to about 1318: the long tail of posting-specific phrases is still there.
- **Degree requirements don't alias cleanly.** "Master's or PhD" is a master's for matching purposes (the lowest acceptable bar), so it sits under `Master's degree`, not `PhD degree`. Where a name could go under two canonicals, loading fails and lists every conflict.

## 2026-11-07: source and locations flips

Variance re-run on the five postings with known flips (`reports/variance/2026-11-07T2018`), 5 runs each, $0.04.

- **Motion Recruitment's `source` is now a rule.** The model said `agency` 4 runs in 5; the fifth said `unstated` with no intermediary, which passes validation. Now a bookmarklet capture with no company and no source gets its poster (from the `# Poster: Title` heading) as the agency. Hand-written postings use the same heading style for other things, so only captures with the bookmarklet `Source:` line count. Re-run: `agency` 5/5. Replayed over 164 saved extractions: nothing changed, so it only catches the stray run.
- **`locations` truncation mostly fixed by one prompt line.** The capture's `Location:` line shows only the first city; the prompt now says to use the posting's full list and to leave "Remote" to `workplace`. Capital One, deCircle and ITSMF are stable. Ropes & Gray still splits 3:2 between Boston alone and seven cities, but there the posting is ambiguous: the capture says Boston, while the pay table lists ranges for seven offices. Left alone.
- **Fresh name Jaccard (merged): 0.701**, on outputs the aliases weren't proposed from (raw 0.571). Lower than the 0.787 on the tuning run, as expected; the aliases still help.
