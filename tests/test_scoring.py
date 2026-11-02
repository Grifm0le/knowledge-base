import math

import pytest

from kb.errors import UnknownScorerError
from kb.index import InvertedIndex
from kb.scoring import (
    Bm25Scorer,
    HybridScorer,
    Scorer,
    TfIdfScorer,
    _registry,
    available_scorers,
    create_scorer,
    register_scorer,
)
from kb.textproc import terms

BODY_ONLY = {"body": 1.0}


def build(*bodies: str) -> InvertedIndex:
    index = InvertedIndex()
    for number, body in enumerate(bodies):
        index.add(f"d{number}", {"body": body})
    return index


def test_bm25_matches_hand_calculation():
    index = build("кот кот пес", "пес")
    score = Bm25Scorer(BODY_ONLY).score(terms("кот"), index)
    # N=2, df=1, tf=2, dl=3, avgdl=2, k1=1.2, b=0.75.
    idf = math.log(1 + (2 - 1 + 0.5) / (1 + 0.5))
    expected = idf * 2 * 2.2 / (2 + 1.2 * (1 - 0.75 + 0.75 * 3 / 2))
    assert score == {"d0": pytest.approx(expected)}
    assert expected == pytest.approx(0.83555, abs=1e-4)


def test_tfidf_matches_hand_calculation():
    index = build("кот кот пес", "пес")
    score = TfIdfScorer(BODY_ONLY).score(terms("кот"), index)
    expected = (math.log(3 / 2) + 1) * math.log1p(2) / math.sqrt(3)
    assert score == {"d0": pytest.approx(expected)}


@pytest.mark.parametrize("method", ["bm25", "tfidf", "hybrid"])
def test_more_occurrences_score_higher(method):
    index = build("кот кот кот пес слон", "кот пес слон волк заяц", "волк заяц")
    scores = create_scorer(method).score(terms("кот"), index)
    assert scores["d0"] > scores["d1"]
    assert "d2" not in scores


@pytest.mark.parametrize("method", ["bm25", "tfidf"])
def test_rare_term_weighs_more_than_common(method):
    index = build("редкий общий", "общий слон", "общий волк", "общий заяц")
    scorer = create_scorer(method)
    rare = scorer.score(terms("редкий"), index)["d0"]
    common = scorer.score(terms("общий"), index)["d0"]
    assert rare > common


def test_bm25_saturates_term_frequency():
    index = build("кот " * 1 + "слон " * 9, "кот " * 2 + "слон " * 8, "кот " * 9 + "слон " * 1)
    scores = Bm25Scorer(BODY_ONLY).score(terms("кот"), index)
    assert scores["d1"] - scores["d0"] > (scores["d2"] - scores["d1"]) / 7


def test_shorter_document_scores_higher_with_same_frequency():
    index = build("кот", "кот слон волк заяц лиса")
    scores = Bm25Scorer(BODY_ONLY).score(terms("кот"), index)
    assert scores["d0"] > scores["d1"]


@pytest.mark.parametrize("method", ["bm25", "tfidf", "hybrid"])
def test_match_in_title_outranks_match_in_body(method):
    index = InvertedIndex()
    index.add("title", {"title": "кот", "body": "слон волк"})
    index.add("body", {"title": "слон", "body": "кот волк"})
    index.add("other", {"title": "заяц", "body": "лиса"})
    scores = create_scorer(method).score(terms("кот"), index)
    assert scores["title"] > scores["body"]


def test_field_weights_can_be_customized():
    index = InvertedIndex()
    index.add("title", {"title": "кот", "body": "слон"})
    index.add("body", {"title": "слон", "body": "кот"})
    scores = Bm25Scorer({"title": 0.1, "body": 5.0}).score(terms("кот"), index)
    assert scores["body"] > scores["title"]


def test_small_field_weight_never_gives_negative_score():
    index = build("кот слон")
    assert TfIdfScorer({"body": 0.01}).score(terms("кот"), index)["d0"] > 0


def test_unknown_terms_and_empty_index_give_no_scores():
    assert Bm25Scorer().score(["нет"], InvertedIndex()) == {}
    assert TfIdfScorer().score(["нет"], build("кот")) == {}
    assert HybridScorer().score(["нет"], build("кот")) == {}


def test_repeated_query_term_counts_more():
    index = build("кот слон", "пес волк")
    scorer = Bm25Scorer(BODY_ONLY)
    once = scorer.score(["кот"], index)["d0"]
    assert scorer.score(["кот", "кот"], index)["d0"] == pytest.approx(once * 2)


def test_hybrid_combines_normalized_scores():
    index = build("кот кот кот слон", "кот волк заяц лиса", "пес")
    scores = HybridScorer(method_weights={"bm25": 0.5, "tfidf": 0.5}).score(terms("кот"), index)
    assert scores["d0"] == pytest.approx(1.0)
    assert scores["d1"] == pytest.approx(0.0)


def test_hybrid_with_single_match_gets_full_weight():
    scores = HybridScorer().score(terms("кот"), build("кот", "пес"))
    assert scores == {"d0": pytest.approx(1.0)}


def test_hybrid_weights_can_be_customized():
    scorer = HybridScorer(method_weights={"bm25": 1.0})
    assert scorer.method_weights == {"bm25": 1.0}


def test_registry_lists_methods_and_rejects_unknown():
    assert {"bm25", "tfidf", "hybrid"} <= set(available_scorers())
    with pytest.raises(UnknownScorerError, match="Доступны"):
        create_scorer("нет-такого")


def test_new_method_can_be_registered():
    @register_scorer
    class CountScorer(Scorer):
        name = "count-test"

        def score(self, query_terms, index):
            return {doc: 1.0 for term in query_terms for doc in index.postings(term)}

    try:
        assert create_scorer("count-test").score(terms("кот"), build("кот")) == {"d0": 1.0}
    finally:
        del _registry["count-test"]
