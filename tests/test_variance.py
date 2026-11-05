from traction_engine import variance


def item(name, quote=None):
    return {"name": name, "category": "other", "alternatives": [], "quote": quote or name}


def run(must=(), nice=(), **fields):
    base = {"title": "T", "company": "C", "seniority": ["senior"], "source": "direct", "intermediary": None,
            "locations": ["Boston"], "comp_min": None}
    return {**base, **fields, "must_have": list(must), "nice_to_have": list(nice)}


def test_norm_ignores_case_whitespace_and_edge_punctuation():
    assert variance.norm('  "Experience  with\nRAG pipelines."  ') == "experience with rag pipelines"


def test_mean_pairwise_jaccard():
    assert variance.mean_pairwise_jaccard([{"a", "b"}, {"a", "b"}]) == 1.0
    assert variance.mean_pairwise_jaccard([{"a", "b", "c", "d"}, {"a", "b", "c", "e"}]) == 0.6
    # three runs: pairs score 1, 0.5, 0.5
    assert variance.mean_pairwise_jaccard([{"a"}, {"a"}, {"a", "b"}]) == 2 / 3
    assert variance.mean_pairwise_jaccard([set(), set()]) == 1.0


def test_contained_jaccard_matches_truncated_quotes_one_to_one():
    long = "experience defining ai model governance processes, including lineage tracking"
    short = "experience defining ai model governance processes"
    assert variance.contained_jaccard({long, "python"}, {short, "python"}) == 1.0
    # one long quote can't absorb two short ones
    assert variance.contained_jaccard({"python, go, java"}, {"python", "go"}) == 0.5
    assert variance.contained_jaccard(set(), set()) == 1.0


def test_score_posting_separates_quote_from_name_agreement():
    runs = [
        run(must=[item("RAG", "built RAG pipelines"), item("Python")]),
        run(must=[item("Retrieval-Augmented Generation", "Built RAG pipelines."), item("python")]),
    ]
    s = variance.score_posting(runs)
    assert s["quote_jaccard"] == 1.0
    assert s["name_jaccard"] == 1 / 3


def test_score_posting_counts_and_placement_flips():
    runs = [
        run(must=[item("A"), item("B")], nice=[item("C")]),
        run(must=[item("A")], nice=[item("B"), item("C")]),
    ]
    s = variance.score_posting(runs)
    assert s["must_counts"] == [2, 1]
    assert s["nice_counts"] == [1, 2]
    assert s["total_counts"] == [3, 3]
    assert s["placement_flips"] == ["b"]


def test_score_posting_field_flips_compare_lists_as_sets():
    runs = [
        run(seniority=["senior", "staff"], intermediary="deCircle", source="agency"),
        run(seniority=["staff", "senior"], intermediary="Paraform", source="agency"),
    ]
    s = variance.score_posting(runs)
    assert s["field_flips"] == {"intermediary": ["Paraform", "deCircle"]}
