"""variance: run-to-run stability of extraction (same posting, same prompt, N runs).

    variance run [--runs N] POSTING ...   extract each posting N times, save raw outputs
    variance score DIR                    score a saved run directory

Raw outputs go to reports/variance/<timestamp>/ (gitignored: quotes come from
private postings). Scoring reads only those files, so it can be re-run for free
when the metrics change (e.g. once requirement names are normalized).

Metrics per posting:
  quote_jaccard    mean pairwise Jaccard of requirement quotes: did runs find the same requirements?
  quote_contained_jaccard  same, but a quote matches one that contains it (same phrase, cut shorter)
  name_jaccard     same, on requirement names: did runs also label them the same?
  name_merged_jaccard  same, after merging spelling variants and aliases.yaml groups
  counts           must / nice / total per run
  placement_flips  quotes that are must-have in some runs and nice-to-have in others
  field_flips      non-requirement fields whose value differs across runs
"""

import argparse
import hashlib
import json
import statistics
import sys
import uuid
from datetime import datetime
from itertools import combinations
from pathlib import Path

import anthropic
from pydantic import ValidationError

from traction_engine import aliases
from traction_engine.aliases import norm

REQUIREMENT_LISTS = ("must_have", "nice_to_have")


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def mean_pairwise_jaccard(sets: list[set]) -> float:
    pairs = list(combinations(sets, 2))
    return sum(jaccard(a, b) for a, b in pairs) / len(pairs)


def contained_jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard where two quotes match if one contains the other: runs cut the same phrase at different points.

    Matching is one-to-one, exact matches first, so one long quote can't absorb several short ones.
    """
    if not a and not b:
        return 1.0
    exact = a & b
    rest_b = sorted(b - exact, key=len)
    matched = len(exact)
    for q in sorted(a - exact, key=len):
        hit = next((other for other in rest_b if q in other or other in q), None)
        if hit is not None:
            rest_b.remove(hit)
            matched += 1
    return matched / (len(a) + len(b) - matched)


def mean_pairwise(sets: list[set], fn) -> float:
    pairs = list(combinations(sets, 2))
    return sum(fn(a, b) for a, b in pairs) / len(pairs)


def _comparable(v):
    return tuple(sorted(v)) if isinstance(v, list) else v


def score_posting(runs: list[dict], lookup: dict[str, str] | None = None) -> dict:
    quotes = [{norm(r["quote"]) for k in REQUIREMENT_LISTS for r in run[k]} for run in runs]
    names = [{norm(r["name"]) for k in REQUIREMENT_LISTS for r in run[k]} for run in runs]
    merged = [{aliases.key(r["name"], lookup or {}) for k in REQUIREMENT_LISTS for r in run[k]} for run in runs]

    placements: dict[str, set[str]] = {}
    for run in runs:
        for k in REQUIREMENT_LISTS:
            for r in run[k]:
                placements.setdefault(norm(r["quote"]), set()).add(k)

    fields = [k for k in runs[0] if k not in REQUIREMENT_LISTS]
    field_flips = {}
    for k in fields:
        values = {_comparable(run.get(k)) for run in runs}
        if len(values) > 1:
            field_flips[k] = sorted(values, key=str)

    return {
        "quote_jaccard": mean_pairwise_jaccard(quotes),
        "quote_contained_jaccard": mean_pairwise(quotes, contained_jaccard),
        "name_jaccard": mean_pairwise_jaccard(names),
        "name_merged_jaccard": mean_pairwise_jaccard(merged),
        "must_counts": [len(run["must_have"]) for run in runs],
        "nice_counts": [len(run["nice_to_have"]) for run in runs],
        "total_counts": [len(run["must_have"]) + len(run["nice_to_have"]) for run in runs],
        "placement_flips": sorted(q for q, ks in placements.items() if len(ks) > 1),
        "field_flips": field_flips,
    }


def _spread(counts: list[int]) -> str:
    return f"{min(counts)}-{max(counts)}"


def render(scores: dict[str, dict]) -> str:
    lines = ["| posting | quote J | quote J (contained) | name J | name J (merged) | must | nice | total "
             "| placement flips | field flips |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for stem, s in scores.items():
        flips = ", ".join(f"{k}: {' / '.join(map(str, v))}" for k, v in s["field_flips"].items())
        lines.append(f"| {stem[11:50]} | {s['quote_jaccard']:.2f} | {s['quote_contained_jaccard']:.2f} "
                     f"| {s['name_jaccard']:.2f} | {s['name_merged_jaccard']:.2f} "
                     f"| {_spread(s['must_counts'])} | {_spread(s['nice_counts'])} "
                     f"| {_spread(s['total_counts'])} | {len(s['placement_flips'])} | {flips or '-'} |")
    all_scores = list(scores.values())
    field_counts: dict[str, int] = {}
    for s in all_scores:
        for k in s["field_flips"]:
            field_counts[k] = field_counts.get(k, 0) + 1
    lines += [
        "",
        f"postings: {len(all_scores)}, runs each: {len(all_scores[0]['total_counts'])}",
        f"mean quote Jaccard: {statistics.mean(s['quote_jaccard'] for s in all_scores):.3f}",
        f"mean quote Jaccard (contained): "
        f"{statistics.mean(s['quote_contained_jaccard'] for s in all_scores):.3f}",
        f"mean name Jaccard:  {statistics.mean(s['name_jaccard'] for s in all_scores):.3f}",
        f"mean name Jaccard (merged): {statistics.mean(s['name_merged_jaccard'] for s in all_scores):.3f}",
        f"mean total-count stdev: {statistics.mean(statistics.pstdev(s['total_counts']) for s in all_scores):.2f}",
        "postings with a field flip, by field: "
        + (", ".join(f"{k} {n}/{len(all_scores)}" for k, n in sorted(field_counts.items(), key=lambda kv: -kv[1]))
           or "none"),
    ]
    return "\n".join(lines)


def score_dir(run_dir: Path) -> dict[str, dict]:
    by_posting: dict[str, list[dict]] = {}
    for p in sorted(run_dir.glob("*.run*.json")):
        stem = p.name.rsplit(".run", 1)[0]
        by_posting.setdefault(stem, []).append(json.loads(p.read_text()))
    lookup = aliases.load()
    return {stem: score_posting(runs, lookup) for stem, runs in by_posting.items() if len(runs) > 1}


def collect(paths: list[Path], n_runs: int) -> Path:
    # Imported here so scoring works without credentials or network.
    from traction_engine.cli import ROOT, api_key, load_config
    from traction_engine.extract import PROMPT_PATH, extract
    from traction_engine.llm import LLM, LLMRefused

    config = load_config()
    client = anthropic.Anthropic(auth_token=api_key(config), base_url=config["base_url"],
                                 timeout=60, max_retries=2)
    run_id = uuid.uuid4().hex[:8]
    llm = LLM(client, ROOT / config["log_path"], config["daily_cap_usd"], run_id)
    out = ROOT / config["reports_dir"] / "variance" / datetime.now().strftime("%Y-%m-%dT%H%M")
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps({
        "run_id": run_id,
        "model": config["models"]["extract"],
        "effort": config["effort"]["extract"],
        "prompt_hash": hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()[:12],
        "runs": n_runs,
        "postings": [str(p) for p in paths],
    }, indent=2))
    spent_before = llm.spent_today()
    try:
        for path in paths:
            text = path.read_text()
            for i in range(1, n_runs + 1):
                try:
                    req = extract(llm, text, model=config["models"]["extract"], effort=config["effort"]["extract"])
                except (ValidationError, LLMRefused) as e:
                    print(f"{path.name} run {i}: FAILED {type(e).__name__}", file=sys.stderr)
                    continue
                (out / f"{path.stem}.run{i}.json").write_text(json.dumps(req.model_dump(), indent=2))
                print(f"{path.name} run {i}: {len(req.must_have)}+{len(req.nice_to_have)}")
    finally:
        print(f"run {run_id}: ${llm.spent_today() - spent_before:.4f} spent, outputs in {out}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="variance", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--runs", type=int, default=5)
    p_run.add_argument("postings", nargs="+", type=Path)
    p_score = sub.add_parser("score")
    p_score.add_argument("dir", type=Path)
    args = parser.parse_args(argv)

    run_dir = collect(args.postings, args.runs) if args.cmd == "run" else args.dir
    scores = score_dir(run_dir)
    (run_dir / "scores.json").write_text(json.dumps(scores, indent=2))
    report = render(scores)
    (run_dir / "scores.md").write_text(report + "\n")
    print(report)
    return 0
