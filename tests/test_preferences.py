import pytest

from traction_engine import aliases
from traction_engine.models import Profile, Requirement, Requirements
from traction_engine.preferences import Preferences, applicants, assess, closed


def lookup_from(table):
    return {aliases.spelling_key(v): c for c, vs in table.items() for v in [c, *vs]}


LOOKUP = lookup_from({"Security clearance": ["Active clearance", "Clearance eligibility"]})

PREFS = Preferences.model_validate({
    "allow_companies": ["Electric Boat"],
    "exclude": {"companies": ["Anduril"], "domains": ["defense"]},
    "penalize": {
        "requirements": {"Java": {"must": "large", "other": "medium"}, "Security clearance": "large"},
        "domains": {"sports betting": "medium"},
        "title": {"founding engineer": "medium"},
        "text": {"fast[- ]paced": "small"},
        "comp_min_over": {"amount": 300000, "size": "medium"},
        "applicants_over": {"count": 100, "size": "medium"},
    },
    "bonus": {"requirements": {"COBOL": "medium"}},
})


def r(name, alternatives=()):
    return Requirement(name=name, category="other", alternatives=list(alternatives), quote=name)


def req(must=(), nice=(), **fields):
    base = dict(
        title="Staff Engineer", company="Acme", seniority=["staff"], employment_type="full_time",
        contract_terms="unstated", source="direct", intermediary=None, workplace="remote", locations=[],
        years_min=None, domain=None, comp_min=None, comp_max=None, comp_period="unstated",
    )
    return Requirements(**{**base, **fields, "must_have": list(must), "nice_to_have": list(nice)})


def run(requirements, text=None, posting_date="2026-11-01", prefs=PREFS):
    return assess(requirements, text, posting_date, prefs, LOOKUP)


def labels(a):
    return [(points, label) for points, label in a.adjustments]


def test_no_preferences_no_adjustments():
    a = run(req(must=[r("Java")]), prefs=Preferences())
    assert (a.zero, a.adjustments) == (None, [])
    assert a.score(0.5) == 50


@pytest.mark.parametrize("must, nice, expected", [
    ([r("Java")], [], (-30, "Java (must)")),
    ([r("Backend language", ["Java", "Go"])], [], (-15, "Java")),
    ([], [r("Java")], (-15, "Java")),
])
def test_requirement_penalty_depends_on_placement(must, nice, expected):
    assert labels(run(req(must=must, nice=nice))) == [expected]


def test_requirement_penalty_resolves_aliases_and_applies_once():
    a = run(req(must=[r("Active clearance")], nice=[r("Clearance eligibility")]))
    assert labels(a) == [(-30, "Security clearance (must)")]


def test_bonus():
    assert labels(run(req(nice=[r("COBOL")]))) == [(15, "COBOL")]


def test_score_floors_at_zero_and_treats_no_fit_as_zero():
    a = run(req(must=[r("Java"), r("Security clearance")]))
    assert a.score(0.4) == 0
    assert run(req(nice=[r("COBOL")])).score(None) == 15


@pytest.mark.parametrize("fields, zero", [
    ({"company": "Anduril Industries"}, "EXCLUDED: Anduril"),
    ({"company": None, "source": "agency", "intermediary": "Anduril"}, "EXCLUDED: Anduril"),
    ({"domain": "defense tech"}, "EXCLUDED: defense"),
])
def test_exclusions_zero_the_score(fields, zero):
    a = run(req(**fields))
    assert a.zero == zero
    assert a.score(0.9) == 0


def test_allowlist_beats_domain_rules_only():
    assert run(req(company="General Dynamics Electric Boat", domain="defense")).zero is None
    assert labels(run(req(company="Electric Boat", domain="sports betting"))) == []
    assert labels(run(req(company="Electric Boat", must=[r("Java")]))) == [(-30, "Java (must)")]


def test_domain_title_text_and_comp_penalties():
    a = run(req(domain="sports betting", title="Founding Engineer", comp_min=310000, comp_max=400000,
                comp_period="year"), text="A fast-paced team.")
    assert labels(a) == [(-15, "domain: sports betting"), (-15, "title: founding engineer"),
                         (-15, "comp min $310,000"), (-5, "text: fast[- ]paced")]


def test_comp_rule_ignores_hourly_and_ranges_topping_out_above():
    assert labels(run(req(comp_min=320, comp_max=400, comp_period="hour"))) == []
    assert labels(run(req(comp_min=200000, comp_max=320000, comp_period="year"))) == []


@pytest.mark.parametrize("text, count", [
    ("Over 100 applicants", 100),
    ("Over 100 people clicked apply", 100),
    ("36 applicants", 36),
    ("19 people clicked apply", 19),
    ("no count here", None),
])
def test_applicants(text, count):
    assert applicants(text) == count


def test_applicant_penalty_at_threshold():
    assert labels(run(req(), text="Over 100 applicants")) == [(-15, "100+ applicants")]
    assert labels(run(req(), text="99 applicants")) == []


@pytest.mark.parametrize("text, posting_date, expected", [
    ("No longer accepting applications", "2026-11-01", "CLOSED"),
    ("Recruiting for this role ends on 8/31/2026.", "2026-10-29", "CLOSED: recruiting ended 8/31/2026"),
    ("Recruiting for this role ends on 1/1/2027.", "2026-11-01", None),
    ("Applications accepted until the position is filled.", "2026-11-01", None),
])
def test_closed(text, posting_date, expected):
    assert closed(text, posting_date) == expected


def test_closed_zeroes_score_but_keeps_adjustments_visible():
    a = run(req(must=[r("Java")]), text="No longer accepting applications")
    assert a.zero == "CLOSED"
    assert a.score(1.0) == 0
    assert labels(a) == [(-30, "Java (must)")]


def test_no_text_skips_text_rules():
    assert labels(run(req(), text=None)) == []


@pytest.mark.parametrize("name, hit", [
    ("Top Secret/SCI clearance", True), ("DoD Secret clearance eligibility", True),
    ("Java/Spring Boot", True), ("JavaScript", False), ("TypeScript/JavaScript", False),
])
def test_requirement_terms_match_whole_words(name, hit):
    prefs = Preferences.model_validate({"penalize": {"requirements": {"clearance": "large", "Java": "large"}}})
    assert bool(run(req(must=[r(name)]), prefs=prefs).adjustments) == hit


PROFILE = Profile(years=15, seniority=["senior"], workplace_ok=["remote", "hybrid"],
                  locations_ok=["Boston"], contract_ok=["full_time"])
AREA = Preferences.model_validate({"penalize": {"out_of_area": "large"}})


@pytest.mark.parametrize("fields, expected", [
    ({"workplace": "hybrid", "locations": ["Santa Monica, CA"]}, [(-30, "hybrid: Santa Monica, CA")]),
    ({"workplace": "onsite", "locations": ["Boston, MA"]}, [(-30, "workplace: onsite")]),
    ({"workplace": "hybrid", "locations": ["Boston, MA"]}, []),
    ({"workplace": "remote", "locations": ["Santa Monica, CA"]}, []),
    ({"workplace": "hybrid", "locations": []}, []),
])
def test_out_of_area_penalty(fields, expected):
    assert labels(assess(req(**fields), None, None, AREA, LOOKUP, PROFILE)) == expected


def test_out_of_area_needs_a_profile():
    assert labels(run(req(workplace="hybrid", locations=["Santa Monica, CA"]), prefs=AREA)) == []
