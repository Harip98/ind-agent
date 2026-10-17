import pytest

from copilot.retrieval.fusion import build_tsquery, rrf


def test_rrf_rewards_agreement():
    scores = rrf([["a", "b", "c"], ["b", "a", "d"]])
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["c"] == pytest.approx(1 / 63) and scores["d"] == pytest.approx(1 / 63)


def test_build_tsquery_ors_content_words():
    assert build_tsquery("High bearing temperature on the pump") == "high | bearing | temperature | pump"


def test_build_tsquery_strips_operators():
    q = build_tsquery("valve & !strainer | 'cooling':* (flow)")
    assert q == "valve | strainer | cooling | flow"


def test_build_tsquery_empty():
    assert build_tsquery("the and of") is None
