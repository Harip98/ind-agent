"""Versioned maintenance documents: parse front-matter and numbered sections, chunk sections."""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from copilot.config import REPO_ROOT

SHARED = "_shared"
CORPUS_DIR = REPO_ROOT / "corpus"
_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_TITLE = re.compile(r"^#\s+(.+?)\s*$")
_HEADING = re.compile(r"^#{2,4}\s+(\d+(?:\.\d+)*)\s+(.+?)\s*$")
_REQUIRED = ("doc_id", "version", "doc_type", "effective_date", "tenant", "asset_models", "license")
_TENANT = re.compile(r"^[a-z][a-z0-9_]*$")


class CorpusError(ValueError):
    """A corpus file or the corpus as a whole is malformed."""


@dataclass(frozen=True)
class Section:
    section_no: str
    heading: str
    text: str
    replaced_by: str | None = None  # superseded versions only: successor section_no, None = removed


@dataclass(frozen=True)
class Chunk:
    id: str
    section_id: str
    content: str


@dataclass(frozen=True)
class DocVersion:
    doc_id: str
    version: int
    title: str
    doc_type: str
    tenant_id: str
    effective_date: date
    superseded_by: int | None
    asset_models: tuple[str, ...]
    license: str
    source_path: str
    sha256: str
    sections: tuple[Section, ...]

    @property
    def is_current(self) -> bool:
        return self.superseded_by is None

    def section_id(self, section: Section) -> str:
        return f"{self.doc_id}@v{self.version}#{section.section_no}"


def parse_document(path: Path, root: Path) -> DocVersion:
    raw = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    match = _FRONT.match(raw)
    if not match:
        raise CorpusError(f"{path.name}: missing front-matter")
    meta = yaml.safe_load(match.group(1)) or {}
    missing = [k for k in _REQUIRED if k not in meta]
    if missing:
        raise CorpusError(f"{path.name}: missing {', '.join(missing)}")

    title, current, buf = meta.get("title"), ("0", "Overview"), []
    sections: list[Section] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text or current[0] != "0":
            sections.append(Section(current[0], current[1], text))

    for line in raw[match.end():].split("\n"):
        if title is None and (t := _TITLE.match(line)):
            title = t.group(1)
            continue
        if h := _HEADING.match(line):
            flush()
            current, buf = (h.group(1), h.group(2)), []
            continue
        buf.append(line)
    flush()

    version, superseded = meta["version"], meta.get("superseded_by")
    models = meta["asset_models"]
    numbers = [s.section_no for s in sections]
    if not title:
        raise CorpusError(f"{path.name}: missing title")
    if not isinstance(version, int) or version < 1:
        raise CorpusError(f"{path.name}: version must be a positive integer")
    if superseded is not None and (not isinstance(superseded, int) or superseded <= version):
        raise CorpusError(f"{path.name}: superseded_by must be a later version")
    if not isinstance(models, list) or not models or not all(isinstance(m, str) for m in models):
        raise CorpusError(f"{path.name}: asset_models must be a non-empty list")
    if len(numbers) != len(set(numbers)):
        raise CorpusError(f"{path.name}: duplicate section numbers")
    tenant = str(meta["tenant"])
    if tenant != SHARED and (not _TENANT.match(tenant) or tenant == "shared"):
        raise CorpusError(f"{path.name}: tenant must be '{SHARED}' or a tenant id, got {tenant!r}")
    if superseded is not None:
        mapping = meta.get("replaced_by")
        if not isinstance(mapping, dict) or set(mapping) != set(numbers) or not all(
                isinstance(k, str) and (v is None or isinstance(v, str)) for k, v in mapping.items()):
            raise CorpusError(f"{path.name}: superseded versions need replaced_by mapping every section "
                              f"number (quoted strings) to its successor section or null")
        sections = [Section(s.section_no, s.heading, s.text, mapping[s.section_no]) for s in sections]
    if not [s for s in sections if s.section_no != "0"]:
        raise CorpusError(f"{path.name}: no numbered sections")

    return DocVersion(
        doc_id=str(meta["doc_id"]),
        version=version,
        title=title,
        doc_type=str(meta["doc_type"]),
        tenant_id=tenant,
        effective_date=meta["effective_date"],
        superseded_by=superseded,
        asset_models=tuple(models),
        license=str(meta["license"]),
        source_path=path.relative_to(root).as_posix(),
        sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        sections=tuple(sections),
    )


def chunk_section(doc: DocVersion, section: Section, max_words: int = 220) -> list[Chunk]:
    section_id = doc.section_id(section)
    header = f"{doc.title} — §{section.section_no} {section.heading}"
    paragraphs = [p.strip() for p in section.text.split("\n\n") if p.strip()] or [""]
    groups: list[list[str]] = [[]]
    words = 0
    for para in paragraphs:
        n = len(para.split())
        if groups[-1] and words + n > max_words:
            groups.append([])
            words = 0
        groups[-1].append(para)
        words += n
    return [
        Chunk(f"{section_id}/c{i}", section_id, (header + "\n\n" + "\n\n".join(g)).strip())
        for i, g in enumerate(groups)
    ]


def load_corpus(root: Path) -> list[DocVersion]:
    docs = [
        parse_document(p, root)
        for p in sorted(root.rglob("*.md"))
        if not p.name.lower().startswith("readme")
    ]
    seen: dict[tuple[str, int], str] = {}
    by_id: dict[str, list[DocVersion]] = {}
    for d in docs:
        key = (d.doc_id, d.version)
        if key in seen:
            raise CorpusError(f"{d.source_path}: duplicate of {seen[key]}")
        seen[key] = d.source_path
        by_id.setdefault(d.doc_id, []).append(d)
        parts = Path(d.source_path).parts
        if len(parts) > 1:
            expected = SHARED if parts[0] == "shared" else parts[0]
            if d.tenant_id != expected:
                raise CorpusError(f"{d.source_path}: tenant {d.tenant_id!r} does not match folder {parts[0]!r}")
    for doc_id, versions in by_id.items():
        if sum(v.is_current for v in versions) != 1:
            raise CorpusError(f"{doc_id}: exactly one current version required")
        if len({v.tenant_id for v in versions}) != 1:
            raise CorpusError(f"{doc_id}: versions disagree on tenant")
        numbers = {v.version for v in versions}
        by_version = {v.version: v for v in versions}
        for v in versions:
            if v.superseded_by is None:
                continue
            if v.superseded_by not in numbers:
                raise CorpusError(f"{doc_id} v{v.version}: superseded_by {v.superseded_by} missing")
            successor = {s.section_no for s in by_version[v.superseded_by].sections}
            for s in v.sections:
                if s.replaced_by is not None and s.replaced_by not in successor:
                    raise CorpusError(f"{doc_id} v{v.version} §{s.section_no}: replaced_by "
                                      f"{s.replaced_by} not in v{v.superseded_by}")
    return docs
