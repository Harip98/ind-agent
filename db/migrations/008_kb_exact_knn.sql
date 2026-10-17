-- Use exact KNN for the knowledge base. With ~100 chunks a sequential scan is sub-millisecond and
-- has perfect recall; the HNSW index was observed returning zero rows while dead tuples from
-- re-ingest were not yet vacuumed. Re-add HNSW (with hnsw.iterative_scan) above ~50k chunks.
DROP INDEX kb.chunks_embedding_hnsw;
