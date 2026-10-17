from datetime import date
from pathlib import Path

import pytest

from copilot.kb.corpus import (
    CORPUS_DIR, SHARED, CorpusError, chunk_section, load_corpus, parse_document,
)

DOC = """---
doc_id: SOP-TEST-01
version: 2
doc_type: sop
effective_date: 2026-03-01
superseded_by: null
tenant: _shared
asset_models: [CP-200]
license: self-authored
---
# Test Procedure

Intro paragraph.

## 1 Scope

Applies to pumps.

## 4 Diagnosis

### 4.1 Cooling water

Check the valve.

Second paragraph.
"""


def _write(tmp_path: Path, text: str, name: str = "SOP-TEST-01.v2.md") -> Path:
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))
    return p


def test_parse_document_reads_meta_and_numbered_sections(tmp_path):
    doc = parse_document(_write(tmp_path, DOC), tmp_path)
    assert (doc.doc_id, doc.version, doc.tenant_id, doc.title) == ("SOP-TEST-01", 2, SHARED, "Test Procedure")
    assert doc.effective_date == date(2026, 3, 1)
    assert doc.is_current
    assert [s.section_no for s in doc.sections] == ["0", "1", "4", "4.1"]
    assert doc.sections[3].heading == "Cooling water"
    assert "Second paragraph." in doc.sections[3].text
    assert doc.section_id(doc.sections[3]) == "SOP-TEST-01@v2#4.1"
    assert doc.source_path == "SOP-TEST-01.v2.md"
    assert len(doc.sha256) == 64


def test_crlf_and_lf_hash_the_same(tmp_path):
    a = parse_document(_write(tmp_path, DOC, "a.md"), tmp_path)
    b = parse_document(_write(tmp_path, DOC.replace("\n", "\r\n"), "b.md"), tmp_path)
    assert a.sha256 == b.sha256


@pytest.mark.parametrize("broken, match", [
    (DOC.replace("doc_id: SOP-TEST-01\n", ""), "doc_id"),
    (DOC.replace("---\ndoc_id", "doc_id", 1), "front-matter"),
    (DOC.replace("superseded_by: null", "superseded_by: 1"), "superseded_by"),
    (DOC.replace("## 1 Scope", "## 4 Scope"), "duplicate"),
    (DOC.replace("asset_models: [CP-200]", "asset_models: []"), "asset_models"),
])
def test_invalid_documents_rejected(tmp_path, broken, match):
    with pytest.raises(CorpusError, match=match):
        parse_document(_write(tmp_path, broken), tmp_path)


def test_chunks_carry_heading_and_respect_size(tmp_path):
    long_section = "## 1 Scope\n\n" + "\n\n".join(" ".join(["word"] * 100) for _ in range(5))
    doc = parse_document(_write(tmp_path, DOC.split("## 1 Scope")[0] + long_section), tmp_path)
    scope = next(s for s in doc.sections if s.section_no == "1")
    chunks = chunk_section(doc, scope, max_words=220)
    assert len(chunks) == 3
    assert all(c.content.startswith("Test Procedure — §1 Scope") for c in chunks)
    assert [c.id for c in chunks] == [f"SOP-TEST-01@v2#1/c{i}" for i in range(3)]


def test_repo_corpus_is_consistent():
    docs = load_corpus(CORPUS_DIR)
    by_id = {}
    for d in docs:
        by_id.setdefault(d.doc_id, []).append(d)
    assert len(docs) == 13
    for doc_id, versions in by_id.items():
        assert sum(v.is_current for v in versions) == 1, doc_id
        assert len({v.tenant_id for v in versions}) == 1, doc_id
    assert {d.tenant_id for d in docs} == {SHARED, "acme", "globex"}
    stale = {d.doc_id for d in docs if not d.is_current}
    assert stale == {"SOP-PUMP-07", "SOP-INST-03", "SOP-AC-05"}
    bulletin = next(d for d in docs if d.doc_id == "BULLETIN-VEND-14")
    assert any("Ignore all previous instructions" in s.text for s in bulletin.sections)


def test_load_corpus_rejects_two_current_versions(tmp_path):
    _write(tmp_path, DOC, "SOP-TEST-01.v2.md")
    _write(tmp_path, DOC.replace("version: 2", "version: 3"), "SOP-TEST-01.v3.md")
    with pytest.raises(CorpusError, match="current"):
        load_corpus(tmp_path)


SUPERSEDED = DOC.replace("version: 2", "version: 1").replace("superseded_by: null", "superseded_by: 2")


def test_superseded_version_must_map_every_section(tmp_path):
    with pytest.raises(CorpusError, match="replaced_by"):
        parse_document(_write(tmp_path, SUPERSEDED, "SOP-TEST-01.v1.md"), tmp_path)


def test_replaced_by_is_parsed_per_section(tmp_path):
    text = SUPERSEDED.replace("license: self-authored\n", "license: self-authored\n"
                              "replaced_by: {'0': '0', '1': '1', '4': '4', '4.1': null}\n")
    doc = parse_document(_write(tmp_path, text, "SOP-TEST-01.v1.md"), tmp_path)
    assert {s.section_no: s.replaced_by for s in doc.sections} == {"0": "0", "1": "1", "4": "4", "4.1": None}


def test_replaced_by_target_must_exist_in_successor(tmp_path):
    text = SUPERSEDED.replace("license: self-authored\n", "license: self-authored\n"
                              "replaced_by: {'0': '0', '1': '9', '4': '4', '4.1': null}\n")
    _write(tmp_path, text, "SOP-TEST-01.v1.md")
    _write(tmp_path, DOC, "SOP-TEST-01.v2.md")
    with pytest.raises(CorpusError, match="9"):
        load_corpus(tmp_path)


def test_tenant_must_match_folder(tmp_path):
    (tmp_path / "acme").mkdir()
    _write(tmp_path / "acme", DOC, "SOP-TEST-01.v2.md")   # front-matter says _shared
    with pytest.raises(CorpusError, match="folder"):
        load_corpus(tmp_path)


def test_literal_shared_tenant_rejected(tmp_path):
    with pytest.raises(CorpusError, match="tenant"):
        parse_document(_write(tmp_path, DOC.replace("tenant: _shared", "tenant: shared")), tmp_path)


def test_shared_documents_do_not_name_tenant_private_documents_or_sites():
    docs = load_corpus(CORPUS_DIR)
    private = {d.doc_id for d in docs if d.tenant_id != SHARED}
    sites = {"Plant A", "North Station", "South Station"}
    for d in docs:
        if d.tenant_id == SHARED:
            text = " ".join(s.text for s in d.sections)
            assert not [x for x in private | sites if x in text], d.doc_id
