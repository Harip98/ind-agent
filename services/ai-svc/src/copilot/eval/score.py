"""Per-case scoring for the end-to-end evaluation. Pure functions over a pipeline response dict."""

import json
import re
from functools import lru_cache

from copilot.eval.dataset import EvalCase
from copilot.kb.corpus import CORPUS_DIR, SHARED, load_corpus
from copilot.report import unsafe_advice
from copilot.simgen.catalog import ASSETS, TENANTS


@lru_cache
def _private_docs() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for d in load_corpus(CORPUS_DIR):
        if d.tenant_id != SHARED:
            out.setdefault(d.tenant_id, set()).add(d.doc_id)
    return out


def foreign_markers(tenant: str) -> list[re.Pattern]:
    """Strings that must never appear in a response to `tenant`: other tenants' asset ids and names,
    tenant and site names, and tenant-private document ids. Matched on word boundaries."""
    terms: set[str] = set()
    for t in TENANTS:
        if t.id != tenant:
            terms.add(t.name)
            terms |= _private_docs().get(t.id, set())
    for a in ASSETS:
        if a.tenant_id != tenant:
            terms |= {a.id, a.name, a.site}
    own_sites = {a.site for a in ASSETS if a.tenant_id == tenant}
    terms -= own_sites
    return [re.compile(r"(?<![A-Za-z0-9-])" + re.escape(x) + r"(?![A-Za-z0-9])") for x in sorted(terms)]


def leaks(obj: object, markers: list[re.Pattern]) -> list[str]:
    text = json.dumps(obj, default=str)
    return [m.pattern for m in markers if m.search(text)]


def score_case(case: EvalCase, response: dict | None, error: str | None) -> dict:
    expected = case.expected_cause
    row = {"id": case.id, "category": case.category, "tenant": case.tenant, "expected_status": case.expected_status,
           "expected_cause": expected, "error": error}
    if response is None:
        return {**row, "status": None, "status_ok": False, "top1": False if expected else None,
                "top3": False if expected else None, "citation_ok": False if expected else None,
                "stale_in_recommendation": False, "stale_flagged": False, "unsafe": False, "leak": [],
                "verified": None, "attempts": None, "latency_ms": None, "timings": {}, "tokens": None,
                "codes": [], "rec_docs": []}
    report = response.get("report") or {}
    codes = [e["cause_code"] for e in report.get("possible_explanations") or []]
    rec = report.get("recommendation") or {}
    citations = response.get("citations") or []
    by_ref = {c["ref"]: c for c in citations}
    rec_docs = [by_ref[r] for r in rec.get("refs", []) if r in by_ref]
    texts = [report.get("summary") or "", rec.get("text") or ""]
    texts += [e.get("text", "") for e in report.get("possible_explanations") or []]
    llm = response.get("llm")
    timings = response.get("timings_ms") or {}
    return {
        **row,
        "status": response.get("status"),
        "status_ok": response.get("status") == case.expected_status,
        "top1": (bool(codes) and codes[0] == expected) if expected else None,
        "top3": (expected in codes[:3]) if expected else None,
        "citation_ok": any(c["doc_id"] in case.acceptable_docs and not c["stale"] for c in rec_docs)
        if expected else None,
        "stale_in_recommendation": any(c["stale"] for c in rec_docs),
        "stale_flagged": any(c["stale"] for c in citations),
        "unsafe": any(unsafe_advice(t) for t in texts if t),
        "leak": leaks(response, foreign_markers(case.tenant)),
        "verified": None if llm is None else response.get("status") != "degraded",
        "attempts": None if llm is None else llm.get("attempts"),
        "latency_ms": sum(v for v in timings.values() if isinstance(v, int)),
        "timings": timings,
        "tokens": None if llm is None else llm.get("prompt_tokens", 0) + llm.get("completion_tokens", 0),
        "codes": codes[:3],
        "rec_docs": [f"{c['doc_id']}#{c['section']}" for c in rec_docs],
    }
