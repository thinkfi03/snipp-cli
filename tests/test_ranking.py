"""Tests for BM25 intent ranking."""

from snipp.ranking import BM25, rank_chunks, score_chunk, select_relevant, tokenize


def test_tokenize_strips_stopwords():
    assert "the" not in tokenize("the quick brown fox")
    assert "quick" in tokenize("the quick brown fox")


def test_bm25_ranks_relevant_first():
    docs = [
        "completely unrelated content about cats",
        "authentication function for login flow",
        "more cat content",
    ]
    ranked = rank_chunks(docs, "auth login")
    assert ranked[0][0] == 1


def test_empty_query_returns_zero_scores():
    assert all(s == 0.0 for _, s in rank_chunks(["a", "b"], ""))


def test_select_relevant_preserves_order():
    chunks = ["alpha", "beta auth", "gamma auth", "delta"]
    keep = select_relevant(chunks, "auth", keep_count=2)
    assert keep == ["beta auth", "gamma auth"]


def test_score_chunk():
    assert score_chunk("login flow", "login") > 0
    assert score_chunk("nothing here", "login") == 0
