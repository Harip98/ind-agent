"""Write corpus versions into kb.* through RLS, idempotent by content hash and embedding model."""

from dataclasses import dataclass

import psycopg

from copilot.config import Settings
from copilot.kb.corpus import SHARED, DocVersion, chunk_section
from copilot.kb.embed import Embedder, to_pgvector


@dataclass(frozen=True)
class IngestStats:
    written: int
    skipped: int
    chunks: int


def _scope(conn: psycopg.Connection, tenant_id: str) -> None:
    conn.execute(
        "SELECT set_config('app.tenant_id', %s, true)", ("" if tenant_id == SHARED else tenant_id,)
    )


def _delete_version(conn: psycopg.Connection, tenant_id: str, doc_id: str, version: int) -> None:
    conn.execute(
        "DELETE FROM kb.chunks c USING kb.sections s"
        " WHERE c.tenant_id = s.tenant_id AND c.section_id = s.id"
        " AND s.tenant_id = %s AND s.doc_id = %s AND s.version = %s",
        (tenant_id, doc_id, version),
    )
    conn.execute(
        "DELETE FROM kb.sections WHERE tenant_id = %s AND doc_id = %s AND version = %s",
        (tenant_id, doc_id, version),
    )
    conn.execute(
        "DELETE FROM kb.doc_versions WHERE tenant_id = %s AND doc_id = %s AND version = %s",
        (tenant_id, doc_id, version),
    )


def ingest(docs: list[DocVersion], settings: Settings, embedder: Embedder) -> IngestStats:
    written = skipped = chunks = 0
    with psycopg.connect(settings.dsn("kb_writer")) as conn:
        for doc in docs:
            with conn.transaction():
                _scope(conn, doc.tenant_id)
                row = conn.execute(
                    "SELECT sha256, embed_model FROM kb.doc_versions"
                    " WHERE tenant_id = %s AND doc_id = %s AND version = %s",
                    (doc.tenant_id, doc.doc_id, doc.version),
                ).fetchone()
                if row and row == (doc.sha256, embedder.name):
                    skipped += 1
                    continue
                _delete_version(conn, doc.tenant_id, doc.doc_id, doc.version)
                conn.execute(
                    "INSERT INTO kb.documents (tenant_id, id, title, doc_type) VALUES (%s, %s, %s, %s)"
                    " ON CONFLICT (tenant_id, id) DO UPDATE"
                    " SET title = EXCLUDED.title, doc_type = EXCLUDED.doc_type",
                    (doc.tenant_id, doc.doc_id, doc.title, doc.doc_type),
                )
                conn.execute(
                    "INSERT INTO kb.doc_versions (tenant_id, doc_id, version, effective_date,"
                    " superseded_by, is_current, source_path, sha256, license, embed_model)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (doc.tenant_id, doc.doc_id, doc.version, doc.effective_date, doc.superseded_by,
                     doc.is_current, doc.source_path, doc.sha256, doc.license, embedder.name),
                )
                for section in doc.sections:
                    section_id = doc.section_id(section)
                    conn.execute(
                        "INSERT INTO kb.sections (tenant_id, id, doc_id, version, section_no,"
                        " heading, text) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (doc.tenant_id, section_id, doc.doc_id, doc.version, section.section_no,
                         section.heading, section.text),
                    )
                    parts = chunk_section(doc, section)
                    vectors = embedder.embed_documents([c.content for c in parts])
                    with conn.cursor() as cur:
                        cur.executemany(
                            "INSERT INTO kb.chunks (tenant_id, id, section_id, asset_models,"
                            " is_current, content, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s::vector)",
                            [
                                (doc.tenant_id, c.id, c.section_id, list(doc.asset_models),
                                 doc.is_current, c.content, to_pgvector(v))
                                for c, v in zip(parts, vectors, strict=True)
                            ],
                        )
                    chunks += len(parts)
                written += 1
    return IngestStats(written, skipped, chunks)


def delete_documents(settings: Settings, doc_ids: list[str], tenant_id: str) -> None:
    with psycopg.connect(settings.dsn("kb_writer")) as conn, conn.transaction():
        _scope(conn, tenant_id)
        for doc_id in doc_ids:
            versions = conn.execute(
                "SELECT version FROM kb.doc_versions WHERE tenant_id = %s AND doc_id = %s",
                (tenant_id, doc_id),
            ).fetchall()
            for (version,) in versions:
                _delete_version(conn, tenant_id, doc_id, version)
            conn.execute("DELETE FROM kb.documents WHERE tenant_id = %s AND id = %s", (tenant_id, doc_id))
