import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from traction_engine import cli
from traction_engine.llm import BudgetExceeded
from traction_engine.models import Requirements


def req(must=("Python", "SQL", "RAG"), alternatives=()):
    items = [{"name": n, "category": "other", "alternatives": [], "quote": n} for n in must]
    if alternatives:
        items.append({"name": "Frontend framework", "category": "framework",
                      "alternatives": list(alternatives), "quote": " or ".join(alternatives)})
    return Requirements(
        title="T", company="C", seniority="senior", employment_type="full_time", contract_terms="unstated",
        source="direct", intermediary=None, workplace="remote", locations=[], years_min=None, domain=None,
        comp_min=None, comp_max=None, comp_period="unstated", must_have=items, nice_to_have=[],
    )


def test_posting_date_from_filename_prefix():
    assert cli.posting_date(Path("2026.10.24.lyra.md")) == "2026-10-24"
    assert cli.posting_date(Path("# Monday.md")) is None


def test_find_postings_matches_globs_only(tmp_path):
    for name in ("a.md", "b.txt", "c.pdf"):
        (tmp_path / name).write_text("x")
    found = cli.find_postings([str(tmp_path)], ["*.md", "*.txt"])
    assert [p.name for p in found] == ["a.md", "b.txt"]


def test_find_postings_keeps_one_file_per_stem(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    for path in ("a/x.md", "a/x.txt", "b/x.md", "a/y.txt"):
        (tmp_path / path).write_text("x")
    found = cli.find_postings([str(tmp_path / "a"), str(tmp_path / "b")], ["*.md", "*.txt"])
    assert [p.relative_to(tmp_path).as_posix() for p in found] == ["a/x.md", "a/y.txt"]


def test_summary_counts_must_have_and_alternatives_separately():
    records = [{"requirements": req(alternatives=("React", "Vue")).model_dump()},
               {"requirements": req(must=("Vue",)).model_dump()}]
    text = cli.summarize(records)
    assert "| Vue | 1 | 0 | 1 |" in text
    assert "| React | 0 | 0 | 1 |" in text
    assert "seniority: 2 senior" in text


@pytest.fixture
def project(tmp_path, monkeypatch):
    postings = tmp_path / "postings"
    postings.mkdir()
    for name in ("2026.01.01.a.md", "2026.01.02.b.md", "2026.01.03.c.md"):
        (postings / name).write_text("posting")
    (tmp_path / "prompts").mkdir()
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "PROMPT_PATH", tmp_path / "prompts" / "extract.md")
    cli.PROMPT_PATH.write_text("prompt")
    monkeypatch.setattr(cli, "load_config", lambda: {
        "reports_dir": "reports", "log_path": "calls.jsonl", "daily_cap_usd": 2.0,
        "base_url": "http://unused", "models": {"extract": "m"}, "effort": {"extract": "low"},
        "postings_dirs": [str(postings)], "postings_glob": ["*.md"],
    })
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    return tmp_path


def fake_extract(outcomes):
    seen = []

    def _extract(llm, text, model, effort):
        seen.append(text)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    return _extract, seen


def test_backfill_writes_reports_and_skips_existing(project, monkeypatch):
    extract, seen = fake_extract([req(), req(), req()])
    monkeypatch.setattr(cli, "extract", extract)
    assert cli.main(["--backfill"]) == 0
    assert len(list((project / "reports").glob("*.json"))) == 3
    assert len(list((project / "reports").glob("*.md"))) == 3
    record = json.loads((project / "reports" / "2026.01.01.a.json").read_text())
    assert record["cohort"] == "backfill"
    assert record["posting_date"] == "2026-01-01"

    extract, seen = fake_extract([])
    monkeypatch.setattr(cli, "extract", extract)
    assert cli.main(["--backfill"]) == 0
    assert seen == []


def test_one_failure_does_not_stop_the_backfill(project, monkeypatch):
    try:
        Requirements.model_validate({})
    except ValidationError as e:
        error = e
    extract, seen = fake_extract([req(), error, req()])
    monkeypatch.setattr(cli, "extract", extract)
    assert cli.main(["--backfill"]) == 1
    assert len(seen) == 3
    assert len(list((project / "reports").glob("*.json"))) == 2


def test_budget_exceeded_stops_the_backfill(project, monkeypatch):
    extract, seen = fake_extract([req(), BudgetExceeded("cap"), req()])
    monkeypatch.setattr(cli, "extract", extract)
    assert cli.main(["--backfill"]) == 2
    assert len(seen) == 2


def test_few_requirements_gets_a_warning(project, monkeypatch):
    extract, _ = fake_extract([req(must=("Java",))])
    monkeypatch.setattr(cli, "extract", extract)
    cli.main([str(project / "postings" / "2026.01.01.a.md")])
    report = (project / "reports" / "2026.01.01.a.md").read_text()
    assert "Warning" in report
    record = json.loads((project / "reports" / "2026.01.01.a.json").read_text())
    assert record["cohort"] == "live"


def test_summary_filters_by_cohort(project, capsys):
    reports = project / "reports"
    reports.mkdir()
    for name, cohort in (("a", "backfill"), ("b", "backfill"), ("c", "live")):
        (reports / f"{name}.json").write_text(json.dumps({"cohort": cohort, "requirements": req().model_dump()}))
    cli.main(["--summary", "--cohort", "live"])
    assert "Summary of 1 postings" in capsys.readouterr().out


def test_seniority_is_a_sorted_deduped_list():
    base = req().model_dump()
    assert Requirements(**{**base, "seniority": ["staff", "senior", "staff"]}).seniority == ["senior", "staff"]
    assert Requirements(**{**base, "seniority": ["unstated", "lead"]}).seniority == ["lead"]
    assert Requirements(**{**base, "seniority": []}).seniority == ["unstated"]


def test_seniority_accepts_legacy_string():
    assert Requirements(**{**req().model_dump(), "seniority": "staff"}).seniority == ["staff"]


def test_summary_counts_a_range_under_each_level():
    ranged = {**req().model_dump(), "seniority": ["senior", "staff"]}
    legacy = {**req().model_dump(), "seniority": "senior"}
    text = cli.summarize([{"requirements": ranged}, {"requirements": legacy}])
    assert "seniority: 2 senior, 1 staff" in text


@pytest.mark.parametrize("name", ["LinkedIn", "LinkedIn Jobs", "linkedin.com", "Indeed", "indeed.com"])
def test_job_board_is_not_an_intermediary(name):
    r = Requirements(**{**req().model_dump(), "source": "aggregator", "intermediary": name})
    assert (r.source, r.intermediary) == ("direct", None)


def test_real_aggregator_and_agency_are_kept():
    base = req().model_dump()
    assert Requirements(**{**base, "source": "aggregator", "intermediary": "Dice"}).intermediary == "Dice"
    assert Requirements(**{**base, "source": "agency", "intermediary": "Beacon Hill"}).source == "agency"


def test_job_board_as_hiring_company_is_kept():
    # Indeed's own postings: Indeed is the employer, not an intermediary.
    r = Requirements(**{**req().model_dump(), "company": "Indeed", "source": "direct", "intermediary": None})
    assert (r.company, r.source, r.intermediary) == ("Indeed", "direct", None)


@pytest.mark.parametrize("company, intermediary", [
    ("DeepRec.ai", "DeepRec.ai"),
    ("Lifted", "Lifted, an Upwork Company"),
    ("Lifted, an Upwork Company", "Lifted"),
])
def test_intermediary_is_never_also_the_company(company, intermediary):
    # An agency posting for an unnamed client: the model sometimes fills company with the agency's name.
    r = Requirements(**{**req().model_dump(), "company": company, "source": "agency", "intermediary": intermediary})
    assert (r.company, r.intermediary) == (None, intermediary)


def test_named_client_is_kept_beside_its_agency():
    r = Requirements(**{**req().model_dump(), "company": "Loancrate", "source": "agency", "intermediary": "deCircle"})
    assert (r.company, r.intermediary) == ("Loancrate", "deCircle")


# M2b: match in triage, --rematch, --rank

from traction_engine.models import Match, RequirementMatch

SKILLS_YAML = """
profile: {years: 15, seniority: [senior], workplace_ok: [remote], locations_ok: [Boston], contract_ok: [full_time]}
skills:
  - {name: Python, tier: primary, evidence: py}
"""


def rmatch(name, verdict, via="alias", skill=None, evidence=None, reason=None):
    return RequirementMatch(name=name, alternatives=[], quote=name, verdict=verdict, skill=skill,
                            evidence=evidence, reason=reason, via=via)


def a_match(score=0.5, skills_hash="current", flags=(), must=None, nice=()):
    must = must if must is not None else [rmatch("Python", "met", skill="Python", evidence="py"),
                                          rmatch("Kubernetes", "missing", via="llm", reason="none")]
    return Match(skills_hash=skills_hash, matched_at="t", run_id="r", score=score, flags=list(flags),
                 must_have=must, nice_to_have=list(nice))


@pytest.fixture
def matched(project, monkeypatch):
    """project with skills_path set and match faked; returns the list of requirements matched."""
    (project / "skills.yaml").write_text(SKILLS_YAML)
    base = cli.load_config()
    monkeypatch.setattr(cli, "load_config", lambda: {
        **base, "skills_path": "skills.yaml", "models": {"extract": "m", "match": "m"},
        "effort": {"extract": "low", "match": "low"},
    })
    monkeypatch.setattr(cli, "skills_hash", lambda path: "current")
    seen = []

    def _match(llm, req, skills, **kw):
        seen.append(req)
        return a_match(skills_hash=kw["skills_hash"])
    monkeypatch.setattr(cli, "match", _match)
    return seen


def test_without_skills_path_there_is_no_match(project, monkeypatch):
    extract, _ = fake_extract([req()])
    monkeypatch.setattr(cli, "extract", extract)
    cli.main([str(project / "postings" / "2026.01.01.a.md")])
    assert "match" not in json.loads((project / "reports" / "2026.01.01.a.json").read_text())
    assert "## Fit" not in (project / "reports" / "2026.01.01.a.md").read_text()


def test_rematch_without_skills_path_exits(project):
    with pytest.raises(SystemExit, match="skills_path"):
        cli.main(["--rematch"])


def test_triage_runs_match_when_configured(project, monkeypatch, matched):
    extract, _ = fake_extract([req(), req(), req()])
    monkeypatch.setattr(cli, "extract", extract)
    assert cli.main(["--backfill"]) == 0
    assert len(matched) == 3
    record = json.loads((project / "reports" / "2026.01.01.a.json").read_text())
    assert record["match"]["skills_hash"] == "current"
    assert "## Fit" in (project / "reports" / "2026.01.01.a.md").read_text()


def test_fit_section_rendering():
    m = a_match(score=0.7, flags=["hybrid: New York, NY"], must=[
        rmatch("Python", "met", skill="Python", evidence="Acme | Beta"),
        rmatch("Serverless", "partial", via="llm", skill="AWS Lambda", evidence="ev", reason="close"),
        rmatch("Kubernetes", "missing", via="llm", reason="no k8s"),
        rmatch("Vague", "unsure", via="llm"),
    ])
    text = cli.render_report(Path("p.md"), req(), None, m)
    assert "Fit 70% | Must 1✓ 1~ 1✗ (Kubernetes) 1? | flags: hybrid: New York, NY" in text
    assert "| Requirement | Verdict | Evidence |" in text
    assert "| Python | met | Python - Acme \\| Beta |" in text
    assert "| Serverless | partial | AWS Lambda - close (ev) |" in text
    assert "| Kubernetes | missing | no k8s |" in text
    assert "| Vague | unsure |  |" in text


def test_fit_section_without_must_haves_warns():
    text = cli.render_report(Path("p.md"), req(), None, a_match(score=None, must=[]))
    assert "Fit n/a" in text
    assert "no must-haves" in text
    assert "flags: none" in text


def write_record(reports, stem, score=None, skills_hash=None, posting_date="2026-01-01", **match_kw):
    reports.mkdir(exist_ok=True)
    rec = {"posting": f"postings/{stem}.md", "posting_date": posting_date, "warning": None,
           "requirements": req().model_dump()}
    if skills_hash:
        rec["match"] = a_match(score=score, skills_hash=skills_hash, **match_kw).model_dump()
    (reports / f"{stem}.json").write_text(json.dumps(rec))


def test_rematch_skips_current_and_updates_the_rest(project, monkeypatch, matched):
    reports = project / "reports"
    write_record(reports, "a", 0.5, "current")
    write_record(reports, "b", 0.5, "old")
    write_record(reports, "c")
    monkeypatch.setattr(cli, "extract", lambda *a, **k: pytest.fail("rematch must not extract"))
    assert cli.main(["--rematch"]) == 0
    assert len(matched) == 2
    assert json.loads((reports / "b.json").read_text())["match"]["skills_hash"] == "current"
    assert "## Fit" in (reports / "c.md").read_text()
    assert not (reports / "a.md").exists()

    matched.clear()
    assert cli.main(["--rematch", "--force"]) == 0
    assert len(matched) == 3


def test_rematch_budget_exceeded_stops(project, monkeypatch, matched):
    reports = project / "reports"
    write_record(reports, "a")
    write_record(reports, "b")

    def _match(llm, req, skills, **kw):
        raise BudgetExceeded("cap")
    monkeypatch.setattr(cli, "match", _match)
    assert cli.main(["--rematch"]) == 2


def test_rank_sorts_by_score_filters_by_date_and_needs_no_key(project, monkeypatch, capsys):
    monkeypatch.setattr(cli, "api_key", lambda config: pytest.fail("--rank must work offline"))
    reports = project / "reports"
    write_record(reports, "low", 0.2, "h", flags=["workplace: onsite"])
    write_record(reports, "high", 0.9, "h")
    write_record(reports, "none", None, "h", must=[])
    write_record(reports, "old", 1.0, "h", posting_date=None)
    write_record(reports, "unmatched")
    assert cli.main(["--rank"]) == 0
    out = capsys.readouterr().out
    rows = [line for line in out.splitlines() if line.startswith("| ") and "Skills fit |" not in line]
    assert [row.split("|")[3].strip() for row in rows] == ["old", "high", "low", "none"]
    assert "workplace: onsite" in out
    assert "Kubernetes" in out
    assert (reports / "rank.md").read_text().startswith("# 4 matched postings")

    cli.main(["--rank", "--since", "2026-01-01"])
    out = capsys.readouterr().out
    assert "| old |" not in out and "| high |" in out



def test_rank_scores_with_preferences_and_keeps_excluded_at_zero(project, monkeypatch, capsys):
    (project / "skills.yaml").write_text(SKILLS_YAML + """
preferences:
  exclude: {companies: [Anduril]}
  penalize: {requirements: {SQL: large}}
""")
    base = cli.load_config()
    monkeypatch.setattr(cli, "load_config", lambda: {**base, "skills_path": "skills.yaml"})
    reports = project / "reports"
    write_record(reports, "ok", 0.5, "h")
    write_record(reports, "bad", 0.9, "h")
    rec = json.loads((reports / "bad.json").read_text())
    rec["requirements"]["company"] = "Anduril"
    (reports / "bad.json").write_text(json.dumps(rec))
    assert cli.main(["--rank"]) == 0
    rows = [line for line in capsys.readouterr().out.splitlines() if line.startswith("| ") and "Skills fit |" not in line]
    assert [row.split("|")[3].strip() for row in rows] == ["ok", "bad"]
    assert rows[0].split("|")[1].strip() == "20"      # 50 fit - 30 for SQL (must)
    assert "-30 SQL (must)" in rows[0]
    assert rows[1].split("|")[1].strip() == "0"
    assert "EXCLUDED: Anduril" in rows[1]


def test_posting_url_takes_the_labeled_line():
    assert cli.posting_url("# T\n- URL: https://www.linkedin.com/jobs/view/123/\nbody") == \
        "https://www.linkedin.com/jobs/view/123/"
    assert cli.posting_url("Source: <https://example.com/job/9>\n") == "https://example.com/job/9"
    assert cli.posting_url("About us: https://example.com/about\n") is None
    assert cli.posting_url(None) is None


def test_report_and_rank_link_the_posting_url(project, capsys):
    (project / "postings").mkdir(exist_ok=True)
    (project / "postings" / "high.md").write_text("# T\n- URL: https://jobs.example.com/1\n")
    reports = project / "reports"
    write_record(reports, "high", 0.9, "h")
    write_record(reports, "nourl", 0.5, "h")
    rec = json.loads((reports / "high.json").read_text())
    rec["posting"] = str(project / "postings" / "high.md")
    (reports / "high.json").write_text(json.dumps(rec))
    assert cli.main(["--rank"]) == 0
    rows = [l for l in capsys.readouterr().out.splitlines() if l.startswith("| ") and "Skills fit |" not in l]
    assert "](https://jobs.example.com/1)" in rows[0]
    assert "](" not in rows[1]

    cli.write_report(reports, "high", rec)
    assert "- URL: https://jobs.example.com/1" in (reports / "high.md").read_text()
