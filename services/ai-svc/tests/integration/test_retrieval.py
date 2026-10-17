import pytest

from copilot.kb.corpus import CORPUS_DIR, load_corpus
from copilot.kb.embed import HashEmbedder
from copilot.kb.ingest import ingest
from copilot.retrieval import STRATEGIES, Retriever

pytestmark = pytest.mark.integration


class FakeReranker:
    """Scores by word overlap so the rerank path runs without a model download."""

    def score(self, query, passages):
        q = set(query.lower().split())
        return [len(q & set(p.lower().split())) / (len(q) or 1) for p in passages]


@pytest.fixture(scope="module")
def retriever(settings):
    ingest(load_corpus(CORPUS_DIR), settings, HashEmbedder())
    return Retriever(settings, HashEmbedder(), FakeReranker())


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_strategies_return_relevant_current_sections(retriever, strategy):
    hits = retriever.search("acme", "cooling water restriction bearing temperature high",
                            asset_model="CP-200", strategy=strategy)
    assert hits and len(hits) <= 5
    assert any(h.doc_id == "SOP-PUMP-07" and h.version == 2 for h in hits)
    assert len({h.section_id for h in hits}) == len(hits)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_tenant_local_doc_invisible_to_other_tenant(retriever, strategy):
    q = "Plant A Pump 12 cooling water isolation valve CW-V-112 flush"
    assert any(h.doc_id == "SOP-ACME-LOC-01" for h in retriever.search("acme", q, strategy=strategy))
    assert all(h.tenant_id in ("_shared", "globex") for h in retriever.search("globex", q, strategy=strategy))


def test_asset_model_filter_keeps_generic_docs(retriever):
    hits = retriever.search("acme", "nuisance chattering alarm deadband", asset_model="AC-75", k=10,
                            strategy="hybrid")
    docs = {h.doc_id for h in hits}
    assert "GUIDE-ALARM-01" in docs
    assert not docs & {"SOP-PUMP-07", "SOP-PUMP-11", "MAN-CP200"}


def test_tsquery_syntax_in_query_is_safe(retriever):
    retriever.search("acme", "valve & !strainer | 'cooling':*", strategy="hybrid")


def test_unknown_strategy_rejected(retriever):
    with pytest.raises(ValueError):
        retriever.search("acme", "pump", strategy="magic")


def test_vector_search_survives_reingest_churn(settings, retriever):
    """Re-ingesting replaces rows (delete + insert); vector search must still return results."""
    from copilot.kb.corpus import load_corpus as load

    docs = load(CORPUS_DIR)
    for i in range(6):
        churn = HashEmbedder()
        churn.name = f"churn-{i}"
        ingest(docs, settings, churn)
    hits = retriever.search("acme", "cooling water restriction bearing temperature high",
                            asset_model="CP-200", strategy="vector")
    assert len(hits) == 5


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_renumbered_superseded_section_never_outranks_current(retriever, strategy):
    """SOP-AC-05 v1 §5 'Return to Service' became v2 §6; only v2 may be returned."""
    hits = retriever.search("acme", "compressor restart return to service after the discharge temperature trip",
                            asset_model="AC-75", k=5, strategy=strategy)
    assert not any(h.stale for h in hits)
    assert not any(h.doc_id == "SOP-AC-05" and h.version == 1 for h in hits)
    assert any(h.section_id == "SOP-AC-05@v2#6" for h in hits)
