"""aliases: map variant requirement names to one canonical name.

    aliases propose    suggest alias groups from saved extractions, for review

aliases.yaml (repo root, reviewed data) maps a canonical name to its variants:

    Node.js: [NodeJS, Node]
    Problem-solving: [Problem solving, Problem-solving skills]

Spelling variants are merged automatically, no entry needed: names that match
after dropping case, spaces, punctuation (except # and +), and a trailing plural
"s" ('Node.js' / 'NodeJS', 'Problem-solving' / 'problem solving', 'REST API' /
'REST APIs') count as one, shown under their most-used spelling.

`propose` writes candidate groups to reports/aliases.proposed.yaml in the same
format. Nothing is applied until a group is moved into aliases.yaml: keep the
lines that really are the same requirement, fix the canonical name, delete the rest.

Candidates: one posting, one quote, one name per extraction, but different names
across runs or extractions. A quote that any single extraction split into several
names is skipped. Still noisy, hence the review.
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ALIASES_PATH = ROOT / "aliases.yaml"
REQUIREMENT_LISTS = ("must_have", "nice_to_have")


def norm(s: str) -> str:
    """Case, runs of whitespace, and surrounding punctuation don't count as differences."""
    s = re.sub(r"\s+", " ", s.casefold()).strip()
    return re.sub(r"^[\W_]+|[\W_]+$", "", s)


def spelling_key(name: str) -> str:
    """'Node.js' / 'NodeJS', 'Problem-solving' / 'problem solving', 'framework' / 'frameworks' collide.

    # and + stay, or C, C# and C++ would collide.
    """
    key = "".join(re.findall(r"[a-z0-9#+]+", name.casefold()))
    # Trailing plural only: per-word stripping would turn "NodeJS" into "nodej" but leave "Node.js" alone.
    return key[:-1] if len(key) > 3 and key.endswith("s") and not key.endswith("ss") else key


def load(path: Path | None = None) -> dict[str, str]:
    """spelling_key(variant) -> canonical. The canonical maps to itself. Missing file: no aliases."""
    path = path or ALIASES_PATH  # looked up at call time, so tests can point it elsewhere
    if not path.exists():
        return {}
    table = yaml.safe_load(path.read_text()) or {}
    lookup: dict[str, str] = {}
    conflicts = []
    for canonical, variants in table.items():
        for name in [canonical, *(variants or [])]:
            key = spelling_key(str(name))
            if lookup.get(key, canonical) != canonical:
                conflicts.append(f"  {name!r} is listed under both {lookup[key]!r} and {canonical!r}")
                continue
            lookup[key] = canonical
    if conflicts:
        # All at once, so fixing the file takes one pass, not one run per conflict.
        raise ValueError(f"{path.name}: {len(conflicts)} conflict(s)\n" + "\n".join(conflicts))
    return lookup


def canonical(name: str, lookup: dict[str, str]) -> str:
    """The aliases.yaml name, or the name unchanged. For display; compare with key()."""
    return lookup.get(spelling_key(name), name)


def key(name: str, lookup: dict[str, str]) -> str:
    """What two names must share to count as one requirement: aliases first, then spelling."""
    return spelling_key(canonical(name, lookup))


class Tally:
    """Counts names by key(); each key is shown under its most-used spelling."""

    def __init__(self, lookup: dict[str, str]):
        self.lookup = lookup
        self.spellings: dict[str, Counter[str]] = defaultdict(Counter)

    def add(self, name: str) -> str:
        k = key(name, self.lookup)
        self.spellings[k][canonical(name, self.lookup)] += 1
        return k

    def label(self, k: str) -> str:
        return self.spellings[k].most_common(1)[0][0]

    def count(self, k: str) -> int:
        return self.spellings[k].total()


def _extractions(reports: Path) -> list[tuple[str, dict]]:
    """(posting stem, requirements) for every saved extraction and variance run."""
    records = [(p.stem, json.loads(p.read_text())) for p in sorted(reports.glob("*.json"))]
    found = [(stem, rec["requirements"]) for stem, rec in records if "requirements" in rec]
    found += [(p.name.rsplit(".run", 1)[0], json.loads(p.read_text()))
              for p in sorted(reports.glob("variance/*/*.run*.json"))]
    return found


def propose(extractions: list[tuple[str, dict]], lookup: dict[str, str]) -> list[dict]:
    """Candidate groups, most-used first. Each: {"names": [...] most-used first, "why": [...], "count": n}."""
    tally = Tally(lookup)
    # (posting, quote) -> the set of name keys each extraction gave that quote
    by_quote: dict[tuple[str, str], list[set[str]]] = defaultdict(list)
    for stem, req in extractions:
        here: dict[str, set[str]] = defaultdict(set)
        for k in REQUIREMENT_LISTS:
            for r in req.get(k, []):
                here[norm(r["quote"])].add(tally.add(r["name"]))
        for quote, names in here.items():
            by_quote[(stem, quote)].append(names)

    quote_hits: Counter[frozenset[str]] = Counter()
    for per_extraction in by_quote.values():
        # One quote holding several requirements ("Python, Docker, AWS") is a split, not an alias.
        if any(len(names) > 1 for names in per_extraction):
            continue
        names = frozenset().union(*per_extraction)
        if len(names) > 1:
            quote_hits[names] += 1
    out = []
    for keys, n in quote_hits.items():
        ranked = sorted(keys, key=lambda k: (-tally.count(k), len(tally.label(k)), tally.label(k)))
        out.append({"names": [tally.label(k) for k in ranked], "why": [f"same quote x{n}"],
                    "count": sum(tally.count(k) for k in keys)})
    return sorted(out, key=lambda g: (-g["count"], g["names"][0]))


def render(groups: list[dict]) -> str:
    lines = [
        f"# {len(groups)} candidate groups. Not applied: move the ones you accept into aliases.yaml.",
        "# Keep only names that are the same requirement; the key is the canonical name (most-used first).",
        "",
    ]
    for g in groups:
        lines.append(f"# {', '.join(g['why'])}; {g['count']} uses")
        lines.append(yaml.safe_dump({g["names"][0]: g["names"][1:]}, default_flow_style=None,
                                    allow_unicode=True, sort_keys=False, width=10_000).rstrip())
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aliases", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("propose")
    parser.parse_args(argv)

    from traction_engine.cli import load_config
    reports = ROOT / load_config()["reports_dir"]
    groups = propose(_extractions(reports), load())
    out = reports / "aliases.proposed.yaml"
    out.write_text(render(groups))
    print(f"{len(groups)} candidate groups written to {out}")
    return 0
