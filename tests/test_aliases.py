import pytest

from traction_engine import aliases, cli, variance


def item(name, quote=None, alternatives=()):
    return {"name": name, "category": "other", "alternatives": list(alternatives), "quote": quote or name}


def req(must=(), nice=()):
    return {"must_have": list(must), "nice_to_have": list(nice)}


def test_spelling_key_collides_punctuation_case_and_plural_variants():
    assert aliases.spelling_key("Node.js") == aliases.spelling_key("NodeJS")
    assert aliases.spelling_key("Problem-solving") == aliases.spelling_key("problem solving")
    assert aliases.spelling_key("Automated testing frameworks") == aliases.spelling_key("Automated testing framework")
    assert aliases.spelling_key("AWS") != aliases.spelling_key("AW")
    assert aliases.spelling_key("Business") == "business"
    assert len({aliases.spelling_key(n) for n in ("C", "C#", "C++")}) == 3


def test_load_maps_variants_and_canonical_by_norm(tmp_path):
    path = tmp_path / "aliases.yaml"
    path.write_text("Node.js: [Node]\nPython: []\n")
    lookup = aliases.load(path)
    assert aliases.canonical("node", lookup) == "Node.js"
    assert aliases.canonical(" NodeJS ", lookup) == "Node.js"
    assert aliases.canonical("Python", lookup) == "Python"
    assert aliases.canonical("Go", lookup) == "Go"


def test_load_rejects_a_variant_under_two_names(tmp_path):
    path = tmp_path / "aliases.yaml"
    path.write_text("Node.js: [Node]\nNode runtime: [nodes]\n")
    with pytest.raises(ValueError, match="both"):
        aliases.load(path)


def test_load_missing_file_is_no_aliases(tmp_path):
    assert aliases.load(tmp_path / "nope.yaml") == {}


def test_propose_finds_same_quote_groups_and_leaves_spelling_to_the_key():
    extractions = [
        ("a", req(must=[item("NodeJS"), item("Problem-solving", "strong problem solving")])),
        ("a", req(must=[item("Node.js"), item("Problem-solving skills", "strong problem solving")])),
        ("b", req(must=[item("Node.js"), item("Python"), item("problem solving")])),
    ]
    groups = {tuple(g["names"]): g["why"] for g in aliases.propose(extractions, {})}
    assert groups == {("Problem-solving", "Problem-solving skills"): ["same quote x1"]}


def test_propose_skips_groups_already_aliased():
    extractions = [("a", req(must=[item("Version control", "git")])), ("a", req(must=[item("Git", "git")]))]
    assert len(aliases.propose(extractions, {})) == 1
    lookup = {aliases.spelling_key("Version control"): "Git", aliases.spelling_key("Git"): "Git"}
    assert aliases.propose(extractions, lookup) == []


def test_render_is_loadable_as_aliases_yaml(tmp_path):
    groups = [{"names": ["Bachelor's degree", "BS: CS"], "why": ["spelling"], "count": 3}]
    path = tmp_path / "aliases.yaml"
    path.write_text(aliases.render(groups))
    assert aliases.canonical("BS: CS", aliases.load(path)) == "Bachelor's degree"


def test_summary_counts_aliased_names_together():
    records = [{"requirements": {"seniority": ["senior"], "employment_type": "full_time", "source": "direct",
                                 "workplace": "remote", **req(must=[item("NodeJS")], nice=[item("x", alternatives=["Node"])])}},
               {"requirements": {"seniority": ["senior"], "employment_type": "full_time", "source": "direct",
                                 "workplace": "remote", **req(must=[item("Node.js")])}}]
    records.append({**records[1]})
    # spelling merged without aliases.yaml, shown under the most-used spelling
    text = cli.summarize(records)
    assert "| Node.js | 3 | 0 | 0 |" in text and "NodeJS" not in text
    lookup = {aliases.spelling_key("Node"): "Node.js", aliases.spelling_key("Node.js"): "Node.js"}
    assert "| Node.js | 3 | 0 | 1 |" in cli.summarize(records, lookup)


def test_score_posting_aliased_name_jaccard():
    runs = [req(must=[item("NodeJS", "node")]), req(must=[item("Node.js", "node")])]
    s = variance.score_posting(runs)
    assert s["name_jaccard"] == 0.0
    assert s["name_merged_jaccard"] == 1.0
    runs = [req(must=[item("Git", "git")]), req(must=[item("Version control", "git")])]
    assert variance.score_posting(runs)["name_merged_jaccard"] == 0.0
    lookup = {aliases.spelling_key("Version control"): "Git", aliases.spelling_key("Git"): "Git"}
    assert variance.score_posting(runs, lookup)["name_merged_jaccard"] == 1.0


def test_propose_ignores_a_quote_split_into_several_requirements():
    quote = "python, docker, and aws"
    extractions = [("a", req(must=[item("Python", quote), item("Docker", quote)])),
                   ("a", req(must=[item("Python", quote), item("AWS", quote)]))]
    assert aliases.propose(extractions, {}) == []
