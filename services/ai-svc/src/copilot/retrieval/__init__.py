"""Tenant-scoped retrieval over kb.chunks: vector, hybrid (FTS + vector, RRF) and reranked."""

from dataclasses import dataclass, replace

import psycopg
from psycopg.rows import dict_row

from copilot.config import Settings
from copilot.kb.corpus import SHARED
from copilot.kb.embed import Embedder, to_pgvector
from copilot.retrieval.fusion import build_tsquery, rrf
from copilot.retrieval.rerank import Reranker

STRATEGIES = ("vector", "hybrid", "hybrid_rerank")
CANDIDATES = 20


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    section_id: str
    doc_id: str
    version: int
    section_no: str
    heading: str
    title: str
    content: str
    tenant_id: str
    is_current: bool
    score: float
    stale: bool = False
    replaced_by: str | None = None  # superseded sections: successor section id; None = content removed


def select_hits(candidates: list[Hit], k: int) -> list[Hit]:
    """Best chunk per section. A superseded section is returned only if its content was removed in
    the current version (replaced_by is None) and it outscores every current hit; it is flagged stale.
    Superseded sections that have a successor are never returned: the current version covers them."""
    best: dict[str, Hit] = {}
    for h in sorted(candidates, key=lambda h: (-h.score, h.section_id)):
        best.setdefault(h.section_id, h)
    current = [h for h in best.values() if h.is_current]
    top_current = max((h.score for h in current), default=float("-inf"))
    stale = [
        replace(h, stale=True)
        for h in best.values()
        if not h.is_current and h.replaced_by is None and h.score > top_current
    ]
    return sorted(current + stale, key=lambda h: (-h.score, h.section_id))[:k]


_SELECT = """
SELECT c.id AS chunk_id, c.section_id, s.doc_id, s.version, s.section_no, s.heading, d.title,
       c.content, c.tenant_id, c.is_current, {score} AS score, s.replaced_by
FROM kb.chunks c
JOIN kb.sections s ON s.tenant_id = c.tenant_id AND s.id = c.section_id
JOIN kb.documents d ON d.tenant_id = s.tenant_id AND d.id = s.doc_id
WHERE c.tenant_id IN (%(shared)s, %(tenant)s)
  AND (%(model)s::text IS NULL OR c.asset_models && ARRAY[%(model)s::text, 'ANY'])
  {extra}
ORDER BY {order}, c.id
LIMIT %(n)s
"""


class Retriever:
    def __init__(self, settings: Settings, embedder: Embedder, reranker: Reranker | None = None) -> None:
        self._settings, self._embedder, self._reranker = settings, embedder, reranker

    def search(
        self,
        tenant_id: str,
        query: str,
        asset_model: str | None = None,
        k: int = 5,
        strategy: str = "hybrid",
    ) -> list[Hit]:
        if strategy not in STRATEGIES:
            raise ValueError(f"unknown strategy {strategy!r}")
        if strategy == "hybrid_rerank" and self._reranker is None:
            raise ValueError("hybrid_rerank needs a reranker")
        params = {"shared": SHARED, "tenant": tenant_id, "model": asset_model, "n": CANDIDATES}
        with psycopg.connect(self._settings.dsn("kb_reader"), row_factory=dict_row) as conn:
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
            vector = self._vector(conn, params, query)
            if strategy == "vector":
                return select_hits(vector, k)
            fused = self._fuse(vector, self._fulltext(conn, params, query))
        if strategy == "hybrid":
            return select_hits(fused, k)
        scores = self._reranker.score(query, [h.content for h in fused])
        return select_hits([replace(h, score=s) for h, s in zip(fused, scores, strict=True)], k)

    def _vector(self, conn: psycopg.Connection, params: dict, query: str) -> list[Hit]:
        q = to_pgvector(self._embedder.embed_query(query))
        sql = _SELECT.format(score="1 - (c.embedding <=> %(q)s::vector)", extra="",
                             order="c.embedding <=> %(q)s::vector")
        return [Hit(**r) for r in conn.execute(sql, {**params, "q": q}).fetchall()]

    def _fulltext(self, conn: psycopg.Connection, params: dict, query: str) -> list[Hit]:
        tsq = build_tsquery(query)
        if tsq is None:
            return []
        sql = _SELECT.format(
            score="ts_rank_cd(c.tsv, to_tsquery('english', %(tsq)s))",
            extra="AND c.tsv @@ to_tsquery('english', %(tsq)s)",
            order="score DESC",
        )
        return [Hit(**r) for r in conn.execute(sql, {**params, "tsq": tsq}).fetchall()]

    @staticmethod
    def _fuse(vector: list[Hit], fulltext: list[Hit]) -> list[Hit]:
        scores = rrf([[h.chunk_id for h in vector], [h.chunk_id for h in fulltext]])
        by_id = {h.chunk_id: h for h in vector + fulltext}
        ranked = sorted(scores, key=scores.get, reverse=True)[:CANDIDATES]
        return [replace(by_id[cid], score=scores[cid]) for cid in ranked]
