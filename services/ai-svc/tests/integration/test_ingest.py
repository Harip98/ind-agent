from pathlib import Path

import psycopg
import pytest

from copilot.kb.corpus import load_corpus
from copilot.kb.embed import HashEmbedder
from copilot.kb.ingest import delete_documents, ingest

from .conftest import as_tenant

pytestmark = pytest.mark.integration

DOC = """---
doc_id: {doc_id}
version: {version}
doc_type: sop
effective_date: 2026-01-01
superseded_by: {sup}
tenant: {tenant}
asset_models: [CP-200]
license: self-authored
{extra}---
# {doc_id} title

## 1 Scope

{body}
"""


def _corpus(tmp_path: Path, body: str = "Check the cooling water valve.") -> Path:
    root = tmp_path / "corpus"
    root.mkdir(exist_ok=True)
    for doc_id, version, sup, tenant in [
        ("ITEST-KB-SHARED", 1, "2", "_shared"),
        ("ITEST-KB-SHARED", 2, "null", "_shared"),
        ("ITEST-KB-A", 1, "null", "itest_a"),
    ]:
        (root / f"{doc_id}.v{version}.md").write_bytes(
            DOC.format(doc_id=doc_id, version=version, sup=sup, tenant=tenant, body=body,
                       extra="" if sup == "null" else "replaced_by: {'1': '1'}\n").encode()
        )
    return root


@pytest.fixture
def clean(settings):
    yield
    delete_documents(settings, ["ITEST-KB-SHARED"], "_shared")
    delete_documents(settings, ["ITEST-KB-A"], "itest_a")


def _rows(settings, tenant):
    with psycopg.connect(settings.dsn("kb_reader")) as conn:
        as_tenant(conn, tenant)
        return conn.execute(
            "SELECT tenant_id, section_id, is_current FROM kb.chunks"
            " WHERE section_id LIKE 'ITEST-KB-%' ORDER BY section_id"
        ).fetchall()


def test_ingest_writes_tenant_and_current_flags(settings, tmp_path, clean):
    stats = ingest(load_corpus(_corpus(tmp_path)), settings, HashEmbedder())
    assert (stats.written, stats.skipped) == (3, 0)
    assert _rows(settings, "itest_a") == [
        ("itest_a", "ITEST-KB-A@v1#1", True),
        ("_shared", "ITEST-KB-SHARED@v1#1", False),
        ("_shared", "ITEST-KB-SHARED@v2#1", True),
    ]
    assert all(r[0] == "_shared" for r in _rows(settings, "itest_b"))


def test_changing_embedder_reembeds(settings, tmp_path, clean):
    root = _corpus(tmp_path)
    ingest(load_corpus(root), settings, HashEmbedder(dims=768))
    other = HashEmbedder(dims=768)
    other.name = "another-model"
    assert ingest(load_corpus(root), settings, other).written == 3


def test_prune_removes_moved_and_deleted_documents(settings, tmp_path, clean):
    root = _corpus(tmp_path)
    move = root / "ITEST-KB-MOVE.v1.md"
    move.write_bytes(DOC.format(doc_id="ITEST-KB-MOVE", version=1, sup="null", tenant="_shared",
                                body="Shared by mistake.", extra="").encode())
    ingest(load_corpus(root), settings, HashEmbedder(), prune=True)
    assert any(r[1] == "ITEST-KB-MOVE@v1#1" for r in _rows(settings, "itest_b"))
    move.write_bytes(move.read_bytes().replace(b"tenant: _shared", b"tenant: itest_a"))
    stats = ingest(load_corpus(root), settings, HashEmbedder(), prune=True)
    assert stats.removed == 1
    assert not any(r[1] == "ITEST-KB-MOVE@v1#1" for r in _rows(settings, "itest_b"))
    assert ("itest_a", "ITEST-KB-MOVE@v1#1", True) in _rows(settings, "itest_a")
    move.unlink()
    assert ingest(load_corpus(root), settings, HashEmbedder(), prune=True).removed == 1
    assert not any(r[1].startswith("ITEST-KB-MOVE") for r in _rows(settings, "itest_a"))
    delete_documents(settings, ["ITEST-KB-MOVE"], "itest_a")


def test_ingest_is_idempotent_and_detects_changes(settings, tmp_path, clean):
    root = _corpus(tmp_path)
    ingest(load_corpus(root), settings, HashEmbedder())
    again = ingest(load_corpus(root), settings, HashEmbedder())
    assert (again.written, again.skipped) == (0, 3)
    path = root / "ITEST-KB-A.v1.md"
    path.write_bytes(path.read_bytes().replace(b"valve", b"strainer"))
    third = ingest(load_corpus(root), settings, HashEmbedder())
    assert (third.written, third.skipped) == (1, 2)
    assert len(_rows(settings, "itest_a")) == 3
