import json
import math

import pytest

from copilot.eval.retrieval_eval import REPO_DATASET, load_cases, mrr, ndcg_at_k, recall_at_k
from copilot.kb.corpus import CORPUS_DIR, load_corpus


def test_recall_at_k():
    assert recall_at_k(["a", "b", "c"], {"b", "z"}, k=5) == 0.5
    assert recall_at_k(["a", "b", "c", "d", "e", "f"], {"f"}, k=5) == 0.0


def test_mrr():
    assert mrr(["a", "b", "c"], {"c"}) == pytest.approx(1 / 3)
    assert mrr(["a"], {"z"}) == 0.0


def test_ndcg_perfect_and_partial():
    assert ndcg_at_k(["a", "b"], {"a", "b"}, k=5) == pytest.approx(1.0)
    assert ndcg_at_k(["x", "a"], {"a"}, k=5) == pytest.approx(1 / math.log2(3))


def test_empty_gold_is_not_scored():
    assert recall_at_k(["a"], set()) is None and mrr(["a"], set()) is None and ndcg_at_k(["a"], set()) is None


def test_repo_dataset_gold_sections_exist_and_respect_tenancy():
    cases = load_cases(REPO_DATASET)
    assert len(cases) >= 40
    assert len({c.id for c in cases}) == len(cases)
    sections = {}
    for d in load_corpus(CORPUS_DIR):
        for s in d.sections:
            sections[d.section_id(s)] = (d.tenant_id, d.is_current)
    for c in cases:
        assert c.category in {"normal", "stale", "tenant_local", "cross_tenant"}, c.id
        for gold in c.gold:
            tenant, current = sections[gold]
            assert tenant in ("_shared", c.tenant), c.id
            assert current == (c.category != "stale"), c.id
        assert bool(c.gold) == (c.category != "cross_tenant"), c.id
