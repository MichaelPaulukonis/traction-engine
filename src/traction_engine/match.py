"""match: judge a posting's requirements against skills.yaml.

1. Deterministic pass: requirement names and alternatives looked up through aliases.key.
   This is for consistency (Python gets the same verdict on every posting), not cost.
2. One LLM call per posting for whatever is left.
3. Code-side guards: a false `met` is worse than a false `missing`, so the rules live here,
   not in the prompt.
4. Rule-based flags from the profile, and a must-have coverage score.
"""

import hashlib
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from traction_engine import aliases
from traction_engine.models import (
    Match,
    Profile,
    Requirement,
    RequirementMatch,
    Requirements,
    Skill,
    Skills,
    Verdict,
)
from traction_engine.preferences import out_of_area

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "match.md"
TIER_VERDICT = {"primary": "met", "familiar": "partial"}


def skills_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def load_skills(path: Path, lookup: dict[str, str] | None = None) -> Skills:
    """Fails when two skill names share an aliases.key, listing every collision at once."""
    lookup = lookup if lookup is not None else aliases.load()
    skills = Skills.model_validate(yaml.safe_load(path.read_text()))
    seen: dict[str, str] = {}
    conflicts = []
    for s in skills.skills:
        k = aliases.key(s.name, lookup)
        if k in seen:
            conflicts.append(f"  {s.name!r} collides with {seen[k]!r} (key {k!r})")
        else:
            seen[k] = s.name
    if conflicts:
        raise ValueError(f"{path.name}: {len(conflicts)} duplicate skill(s)\n" + "\n".join(conflicts))
    return skills


def _result(r: Requirement, verdict: Verdict, skill: Skill | None, via: str, reason: str | None = None):
    return RequirementMatch(name=r.name, alternatives=r.alternatives, quote=r.quote, verdict=verdict,
                            skill=skill.name if skill else None, evidence=skill.evidence if skill else None,
                            reason=reason, via=via)


def _match_deterministic(r: Requirement, skill_by_key: dict[str, Skill],
                         lookup: dict[str, str]) -> RequirementMatch | None:
    """Best hit across the name and its alternatives: primary (met) beats familiar (partial)."""
    best = None
    for candidate in [r.name, *r.alternatives]:
        skill = skill_by_key.get(aliases.key(candidate, lookup))
        if skill and skill.tier == "primary":
            return _result(r, "met", skill, "alias")
        if skill and best is None:
            best = skill
    return _result(r, "partial", best, "alias") if best else None


class LLMVerdict(BaseModel):
    index: int
    verdict: Verdict
    skill: str | None = Field(description="Exact skill name from <skills>, or null for missing/unsure.")
    reason: str = Field(description="Under 15 words.")


def _verdicts_for(n: int) -> type[BaseModel]:
    """Response model that only validates with one item per index 0..n-1, in any order."""

    class Verdicts(BaseModel):
        items: list[LLMVerdict]

        @model_validator(mode="after")
        def one_per_requirement(self):
            got = sorted(item.index for item in self.items)
            if got != list(range(n)):
                raise ValueError(f"items must have each index 0..{n - 1} exactly once; got {got}")
            return self

    return Verdicts


def _guard(r: Requirement, v: LLMVerdict, skill_by_key: dict[str, Skill],
           lookup: dict[str, str]) -> RequirementMatch:
    if v.verdict in ("missing", "unsure"):
        return _result(r, v.verdict, None, "llm", v.reason)
    skill = skill_by_key.get(aliases.key(v.skill, lookup)) if v.skill else None
    if skill is None:
        return _result(r, "unsure", None, "llm", v.reason)
    verdict = "partial" if v.verdict == "met" and skill.tier == "familiar" else v.verdict
    return _result(r, verdict, skill, "llm", v.reason)


def _user_message(pending: list[Requirement], skills: Skills) -> str:
    skill_lines = [f"- {s.name} [{s.tier}]: {s.evidence}" for s in skills.skills]
    req_lines = []
    for i, r in enumerate(pending):
        options = f" (any of: {', '.join(r.alternatives)})" if r.alternatives else ""
        req_lines.append(f'{i}. {r.name}{options}: "{r.quote}"')
    return ("<skills>\n" + "\n".join(skill_lines) + "\n</skills>\n\n"
            "<requirements>\n" + "\n".join(req_lines) + "\n</requirements>")


def _llm_verdicts(llm, pending: list[Requirement], skills: Skills, *, model: str, effort: str) -> list[LLMVerdict]:
    """One call, one retry with the validation error fed back (as in extract)."""
    system = PROMPT_PATH.read_text()
    output_format = _verdicts_for(len(pending))
    first = {"role": "user", "content": _user_message(pending, skills)}
    try:
        out = llm.parse(step="match", model=model, system=system, messages=[first],
                        output_format=output_format, effort=effort)
    except ValidationError as e:
        retry = {"role": "user", "content": f"{first['content']}\n\nYour previous output failed validation:\n{e}\n"
                                            "Return a corrected result."}
        out = llm.parse(step="match_retry", model=model, system=system, messages=[retry],
                        output_format=output_format, effort=effort)
    return sorted(out.items, key=lambda item: item.index)


def compute_flags(req: Requirements, profile: Profile) -> list[str]:
    """Rule-based, no LLM. An unstated field (or empty locations) never flags."""
    flags = []
    if req.years_min is not None and req.years_min > profile.years:
        flags.append(f"years: {req.years_min}+ (profile: {profile.years})")
    named = [s for s in req.seniority if s != "unstated"]
    if named and not set(named) & set(profile.seniority):
        flags.append(f"seniority: {'/'.join(named)}")
    if where := out_of_area(req, profile):
        flags.append(where)
    if req.employment_type != "unstated" and req.employment_type not in profile.contract_ok:
        flags.append(f"employment: {req.employment_type}")
    return flags


def compute_score(must_have: list[RequirementMatch]) -> float | None:
    """(met + 0.5 * partial) / must-haves. None with no must-haves, so it never shows as 100%."""
    if not must_have:
        return None
    met = sum(m.verdict == "met" for m in must_have)
    partial = sum(m.verdict == "partial" for m in must_have)
    return (met + 0.5 * partial) / len(must_have)


def match(llm, req: Requirements, skills: Skills, *, model: str, effort: str = "low",
          lookup: dict[str, str] | None = None, skills_hash: str = "", run_id: str = "") -> Match:
    lookup = lookup if lookup is not None else aliases.load()
    skill_by_key = {aliases.key(s.name, lookup): s for s in skills.skills}
    results = {}  # (list name, position) -> RequirementMatch
    pending: list[tuple[tuple[str, int], Requirement]] = []
    for list_name in ("must_have", "nice_to_have"):
        for i, r in enumerate(getattr(req, list_name)):
            hit = _match_deterministic(r, skill_by_key, lookup)
            if hit:
                results[(list_name, i)] = hit
            else:
                pending.append(((list_name, i), r))
    if pending:
        verdicts = _llm_verdicts(llm, [r for _, r in pending], skills, model=model, effort=effort)
        for (slot, r), v in zip(pending, verdicts):
            results[slot] = _guard(r, v, skill_by_key, lookup)
    must = [results[("must_have", i)] for i in range(len(req.must_have))]
    nice = [results[("nice_to_have", i)] for i in range(len(req.nice_to_have))]
    return Match(skills_hash=skills_hash, matched_at=datetime.now().isoformat(timespec="seconds"),
                 run_id=run_id, score=compute_score(must), flags=compute_flags(req, skills.profile),
                 must_have=must, nice_to_have=nice)
