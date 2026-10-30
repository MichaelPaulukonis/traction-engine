"""triage: extract requirements from job postings and summarize them.

    triage POSTING.md             extract one posting, write its report
    triage --backfill [DIR ...]   extract every posting in DIRs (default: config postings_dirs)
    triage --summary [--cohort C] requirement frequencies across saved extractions

Extractions are tagged with a cohort: "backfill" for postings processed by
--backfill, "live" for single postings. The backfill archive was written by hand
for human reading, so its text differs from live intake; keep them apart.

The OpenRouter key comes from OPENROUTER_API_KEY if set, otherwise from the
macOS Keychain service named by keychain_service in config.
"""

import argparse
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

import anthropic
import yaml
from pydantic import ValidationError

from traction_engine.extract import PROMPT_PATH, extract
from traction_engine.llm import LLM, BudgetExceeded, LLMRefused
from traction_engine.models import Requirements

ROOT = Path(__file__).resolve().parents[2]
MIN_REQUIREMENTS = 3


def load_config() -> dict:
    config = yaml.safe_load((ROOT / "config.yaml").read_text())
    local = ROOT / "config.local.yaml"
    if local.exists():
        config.update(yaml.safe_load(local.read_text()) or {})
    return config


def api_key(config: dict) -> str:
    if key := os.environ.get("OPENROUTER_API_KEY"):
        return key
    result = subprocess.run(
        ["security", "find-generic-password", "-a", getpass.getuser(), "-s", config["keychain_service"], "-w"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"No OPENROUTER_API_KEY set and no Keychain entry for {config['keychain_service']!r}.")
    return result.stdout.strip()


def posting_date(path: Path) -> str | None:
    match = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", path.name)
    return "-".join(match.groups()) if match else None


def find_postings(dirs: list[str], globs: list[str]) -> list[Path]:
    """Postings in dirs, one per filename stem, since reports are named by stem."""
    found = sorted({p for d in dirs for g in globs for p in Path(d).expanduser().glob(g) if p.is_file()})
    by_stem: dict[str, Path] = {}
    for p in found:
        if p.stem in by_stem:
            print(f"skipping {p}: same name as {by_stem[p.stem]}")
        else:
            by_stem[p.stem] = p
    return list(by_stem.values())


def render_report(path: Path, req: Requirements, warning: str | None) -> str:
    lines = [f"# {req.title or path.stem}", ""]
    if warning:
        lines += [f"> **Warning:** {warning}", ""]
    comp = "unstated"
    if req.comp_min is not None:
        comp = f"{req.comp_min:,.0f}-{req.comp_max:,.0f} per {req.comp_period}"
    lines += [
        f"- Company: {req.company or 'unstated'}",
        f"- Seniority: {' / '.join(req.seniority)}",
        f"- Employment: {req.employment_type}, terms {req.contract_terms}",
        f"- Source: {req.source}" + (f" ({req.intermediary})" if req.intermediary else ""),
        f"- Workplace: {req.workplace}" + (f" ({', '.join(req.locations)})" if req.locations else ""),
        f"- Years: {req.years_min if req.years_min is not None else 'unstated'}",
        f"- Domain: {req.domain or 'unstated'}",
        f"- Comp: {comp}",
        f"- Posting: `{path}`",
        "",
    ]
    for heading, items in (("Must have", req.must_have), ("Nice to have", req.nice_to_have)):
        lines += [f"## {heading}", ""]
        for r in items:
            options = f" (any of: {', '.join(r.alternatives)})" if r.alternatives else ""
            lines.append(f"- **{r.name}**{options} [{r.category}]: \"{r.quote}\"")
        lines.append("")
    return "\n".join(lines)


def triage_one(llm: LLM, path: Path, config: dict, prompt_hash: str, cohort: str) -> Requirements:
    req = extract(llm, path.read_text(), model=config["models"]["extract"],
                  effort=config["effort"]["extract"])
    total = len(req.must_have) + len(req.nice_to_have)
    warning = None
    if total < MIN_REQUIREMENTS:
        warning = f"only {total} requirements found; this may not be a job posting, or the text is incomplete."
    out = ROOT / config["reports_dir"]
    out.mkdir(exist_ok=True)
    record = {
        "posting": str(path),
        "posting_date": posting_date(path),
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
        "run_id": llm.run_id,
        "cohort": cohort,
        "prompt_hash": prompt_hash,
        "warning": warning,
        "requirements": req.model_dump(),
    }
    (out / f"{path.stem}.json").write_text(json.dumps(record, indent=2))
    (out / f"{path.stem}.md").write_text(render_report(path, req, warning))
    return req


def summarize(records: list[dict]) -> str:
    must, nice, option = Counter(), Counter(), Counter()
    fields = {k: Counter() for k in ("seniority", "employment_type", "source", "workplace")}
    for rec in records:
        req = rec["requirements"]
        for k in fields:
            # seniority is a list (older records: a string); a "Senior / Staff" posting counts under both
            values = req[k] if isinstance(req[k], list) else [req[k]]
            fields[k].update(values)
        for counter, items in ((must, req["must_have"]), (nice, req["nice_to_have"])):
            for r in items:
                counter[r["name"]] += 1
                for alt in r["alternatives"]:
                    option[alt] += 1

    lines = [f"# Summary of {len(records)} postings", ""]
    for k, counter in fields.items():
        lines.append(f"- {k}: " + ", ".join(f"{count} {value}" for value, count in counter.most_common()))
    lines += ["", "| Requirement | Must | Nice | As an option |", "|---|---|---|---|"]
    names = must.keys() | nice.keys() | option.keys()
    ranked = sorted(names, key=lambda n: (-(must[n] + nice[n] + option[n]), n.lower()))
    for n in ranked[:50]:
        lines.append(f"| {n} | {must[n]} | {nice[n]} | {option[n]} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="triage", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("posting", nargs="?", type=Path)
    parser.add_argument("--backfill", nargs="*", metavar="DIR")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--cohort", help="with --summary: only this cohort (backfill or live)")
    parser.add_argument("--force", action="store_true", help="re-extract postings already extracted")
    args = parser.parse_args(argv)
    config = load_config()
    reports = ROOT / config["reports_dir"]

    if args.summary:
        records = [json.loads(p.read_text()) for p in sorted(reports.glob("*.json"))]
        if args.cohort:
            records = [r for r in records if r.get("cohort") == args.cohort]
        print(summarize(records))
        return 0

    if args.posting is None and args.backfill is None:
        parser.print_help()
        return 1

    client = anthropic.Anthropic(auth_token=api_key(config), base_url=config["base_url"])
    run_id = uuid.uuid4().hex[:8]
    llm = LLM(client, ROOT / config["log_path"], config["daily_cap_usd"], run_id)
    prompt_hash = hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()[:12]
    spent_before = llm.spent_today()

    cohort = "live" if args.posting else "backfill"
    if args.posting:
        paths = [args.posting]
    else:
        paths = find_postings(args.backfill or config["postings_dirs"], config["postings_glob"])
        if not args.force:
            paths = [p for p in paths if not (reports / f"{p.stem}.json").exists()]
    print(f"run {run_id}: {len(paths)} posting(s)")

    failed = []
    try:
        for i, path in enumerate(paths, 1):
            try:
                req = triage_one(llm, path, config, prompt_hash, cohort)
                total = len(req.must_have) + len(req.nice_to_have)
                print(f"[{i}/{len(paths)}] {path.name}: {'/'.join(req.seniority)}, {req.source}, {total} requirements")
            except (ValidationError, LLMRefused) as e:
                failed.append(path)
                print(f"[{i}/{len(paths)}] {path.name}: FAILED {type(e).__name__}: {str(e).splitlines()[0]}")
    except (BudgetExceeded, anthropic.APIError) as e:
        print(f"Stopped: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    finally:
        print(f"run {run_id}: ${llm.spent_today() - spent_before:.4f} spent, {len(failed)} failed")
    return 1 if failed else 0
