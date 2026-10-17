"""Retrieval metrics and the strategy comparison report."""

import json
import math
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from copilot.config import REPO_ROOT
from copilot.retrieval import Retriever

REPO_DATASET = REPO_ROOT / "eval" / "datasets" / "retrieval.jsonl"


@dataclass(frozen=True)
class RetrievalCase:
    id: str
    tenant: str
    asset_model: str | None
    query: str
    gold: tuple[str, ...]
    category: str


def load_cases(path: Path) -> list[RetrievalCase]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            cases.append(RetrievalCase(r["id"], r["tenant"], r.get("asset_model"), r["query"],
                                       tuple(r["gold"]), r["category"]))
    return cases


def recall_at_k(ranked: list[str], gold: set[str], k: int = 5) -> float | None:
    """None when there is no gold (leak probes): such cases are not ranking cases."""
    if not gold:
        return None
    return len(gold & set(ranked[:k])) / len(gold)


def mrr(ranked: list[str], gold: set[str]) -> float | None:
    if not gold:
        return None
    for i, item in enumerate(ranked, start=1):
        if item in gold:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: list[str], gold: set[str], k: int = 5) -> float | None:
    if not gold:
        return None
    dcg = sum(1.0 / math.log2(i + 1) for i, item in enumerate(ranked[:k], start=1) if item in gold)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(gold), k) + 1))
    return dcg / ideal


def run(cases: list[RetrievalCase], retriever: Retriever, strategies: list[str]) -> dict:
    results: dict = {}
    for strategy in strategies:
        rows = []
        for case in cases:
            start = time.perf_counter()
            hits = retriever.search(case.tenant, case.query, case.asset_model, k=5, strategy=strategy)
            ms = (time.perf_counter() - start) * 1000
            ranked = [h.section_id for h in hits]
            gold = set(case.gold)
            rows.append({
                "id": case.id, "category": case.category, "ms": ms, "ranked": ranked,
                "recall": recall_at_k(ranked, gold), "mrr": mrr(ranked, gold),
                "ndcg": ndcg_at_k(ranked, gold),
                "leak": any(h.tenant_id not in ("_shared", case.tenant) for h in hits),
                "stale_in_top5": any(h.stale for h in hits),
            })
        results[strategy] = rows
    return results


def _mean(rows: list[dict], key: str) -> str:
    vals = [r[key] for r in rows if r[key] is not None]
    return f"{statistics.fmean(vals):.2f}" if vals else "n/a"


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))] if s else float("nan")


def render_report(results: dict, title: str) -> str:
    lines = [f"# {title}", "", "| Strategy | Category | n | Recall@5 | MRR | nDCG@5 | Stale in top-5 | Leaks |",
             "|---|---|---|---|---|---|---|---|"]
    for strategy, rows in results.items():
        cats = sorted({r["category"] for r in rows}) + ["all"]
        for cat in cats:
            sub = rows if cat == "all" else [r for r in rows if r["category"] == cat]
            lines.append(
                f"| {strategy} | {cat} | {len(sub)} | {_mean(sub, 'recall')} | {_mean(sub, 'mrr')}"
                f" | {_mean(sub, 'ndcg')} | {sum(r['stale_in_top5'] for r in sub)}"
                f" | {sum(r['leak'] for r in sub)} |"
            )
    lines += ["", "| Strategy | p50 ms | p95 ms |", "|---|---|---|"]
    for strategy, rows in results.items():
        ms = [r["ms"] for r in rows]
        lines.append(f"| {strategy} | {_pct(ms, 0.5):.0f} | {_pct(ms, 0.95):.0f} |")
    return "\n".join(lines) + "\n"
