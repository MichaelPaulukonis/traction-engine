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


@pytest.mark.parametrize("name", ["LinkedIn", "LinkedIn Jobs", "linkedin.com"])
def test_job_board_is_not_an_intermediary(name):
    r = Requirements(**{**req().model_dump(), "source": "aggregator", "intermediary": name})
    assert (r.source, r.intermediary) == ("direct", None)


def test_real_aggregator_and_agency_are_kept():
    base = req().model_dump()
    assert Requirements(**{**base, "source": "aggregator", "intermediary": "Dice"}).intermediary == "Dice"
    assert Requirements(**{**base, "source": "agency", "intermediary": "Beacon Hill"}).source == "agency"
