"""preferences: rank-time adjustments to the skills fit, from rules only.

Spec: docs/specs/2026-11-20-preferences-design.md. Reads the saved extraction, the posting
text, and the preferences section of skills.yaml. No LLM, so edits apply on the next --rank.

Rank score = skills fit as points (0-100) + adjustments, floored at 0. Closed or excluded
postings score 0 but stay listed, with their other adjustments still shown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from traction_engine import aliases
if TYPE_CHECKING:  # models imports Preferences, so only the type comes from there
    from traction_engine.models import Requirements

Size = Literal["small", "medium", "large"]


class Placed(BaseModel):
    """Size by where a requirement appears: a must-have, or anywhere else (nice-to-have, an alternative)."""
    must: Size
    other: Size


class Exclude(BaseModel):
    companies: list[str] = []
    domains: list[str] = []


class CompOver(BaseModel):
    amount: float
    size: Size


class ApplicantsOver(BaseModel):
    count: int
    size: Size


class Penalize(BaseModel):
    requirements: dict[str, Size | Placed] = {}
    domains: dict[str, Size] = {}
    title: dict[str, Size] = {}
    text: dict[str, Size] = {}
    comp_min_over: CompOver | None = None
    applicants_over: ApplicantsOver | None = None


class Bonus(BaseModel):
    requirements: dict[str, Size | Placed] = {}


class Preferences(BaseModel):
    weights: dict[Size, int] = {"small": 5, "medium": 15, "large": 30}
    allow_companies: list[str] = []
    exclude: Exclude = Exclude()
    penalize: Penalize = Penalize()
    bonus: Bonus = Bonus()


@dataclass
class Assessment:
    zero: str | None = None  # CLOSED / EXCLUDED reason: score is 0
    adjustments: list[tuple[int, str]] = field(default_factory=list)

    def score(self, fit: float | None) -> int:
        if self.zero:
            return 0
        return max(0, round((fit or 0) * 100) + sum(points for points, _ in self.adjustments))


def _has_word(term: str, text: str | None) -> bool:
    return bool(text) and re.search(rf"\b{re.escape(term)}\b", text, re.I) is not None


APPLICANTS = re.compile(r"\b(over )?(\d+) (?:applicants|people clicked apply)", re.I)
RECRUITING_ENDS = re.compile(r"recruiting for this role ends on (\d{1,2})/(\d{1,2})/(\d{4})", re.I)


def applicants(text: str) -> int | None:
    """LinkedIn's count. 'Over 100' reads as 100, its cap."""
    m = APPLICANTS.search(text)
    return int(m[2]) if m else None


def closed(text: str, posting_date: str | None) -> str | None:
    """'Until the position is filled' is open; only explicit closings count."""
    if re.search(r"no longer accepting applications", text, re.I):
        return "CLOSED"
    m = RECRUITING_ENDS.search(text)
    if m:
        month, day, year = (int(g) for g in m.groups())
        seen = date.fromisoformat(posting_date) if posting_date else date.today()
        if date(year, month, day) < seen:
            return f"CLOSED: recruiting ended {month}/{day}/{year}"
    return None


def _placement(name: str, req: Requirements, lookup: dict[str, str]) -> str | None:
    """'must' if a must-have's name hits; 'other' if a nice-to-have or any alternative does.

    A hit is the term as a whole word ('clearance' in 'Top Secret/SCI clearance'; 'Java' in
    'Java/Spring Boot' but not 'JavaScript') or the same aliases.key.
    """
    k = aliases.key(name, lookup)

    def hit(n: str) -> bool:
        return _has_word(name, n) or aliases.key(n, lookup) == k

    if any(hit(r.name) for r in req.must_have):
        return "must"
    if any(hit(n) for r in req.must_have + req.nice_to_have for n in [r.name, *r.alternatives]):
        return "other"
    return None


def _requirement_adjustments(rules: dict[str, Size | Placed], sign: int, req: Requirements,
                             lookup: dict[str, str], weights: dict[str, int]) -> list[tuple[int, str]]:
    out = []
    for name, size in rules.items():
        where = _placement(name, req, lookup)
        if where is None:
            continue
        s = getattr(size, where) if isinstance(size, Placed) else size
        out.append((sign * weights[s], f"{name} (must)" if where == "must" and sign < 0 else name))
    return out


def assess(req: Requirements, text: str | None, posting_date: str | None,
           prefs: Preferences, lookup: dict[str, str]) -> Assessment:
    w = prefs.weights
    pen = prefs.penalize
    a = Assessment()
    allowed = any(_has_word(c, req.company) or _has_word(c, req.intermediary) for c in prefs.allow_companies)

    excluded = [c for c in prefs.exclude.companies if _has_word(c, req.company) or _has_word(c, req.intermediary)]
    if not allowed:
        excluded += [d for d in prefs.exclude.domains if _has_word(d, req.domain)]
    if excluded:
        a.zero = f"EXCLUDED: {', '.join(excluded)}"
    if text and (why := closed(text, posting_date)):
        a.zero = why

    a.adjustments += _requirement_adjustments(pen.requirements, -1, req, lookup, w)
    a.adjustments += _requirement_adjustments(prefs.bonus.requirements, 1, req, lookup, w)
    if not allowed:
        a.adjustments += [(-w[s], f"domain: {d}") for d, s in pen.domains.items() if _has_word(d, req.domain)]
    a.adjustments += [(-w[s], f"title: {t}") for t, s in pen.title.items() if _has_word(t, req.title)]
    c = pen.comp_min_over
    if c and req.comp_period == "year" and req.comp_min is not None and req.comp_min > c.amount:
        a.adjustments.append((-w[c.size], f"comp min ${req.comp_min:,.0f}"))
    if text:
        a.adjustments += [(-w[s], f"text: {pat}") for pat, s in pen.text.items() if re.search(pat, text, re.I)]
        n = applicants(text)
        ao = pen.applicants_over
        if ao and n is not None and n >= ao.count:
            a.adjustments.append((-w[ao.size], f"{n}+ applicants" if n == 100 else f"{n} applicants"))
    return a
