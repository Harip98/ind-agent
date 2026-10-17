"""Rank fusion and safe full-text query construction."""

import re

_WORD = re.compile(r"[a-z][a-z0-9]+|\d{2,}")
_STOP = frozenset(
    "the and for with from that this what which when where why how are was were has have had "
    "not but can could should would into onto over under about after before then than show "
    "give tell me please our its it's yesterday today".split()
)


def rrf(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion: sum of 1 / (k + rank) over every ranking an item appears in."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def build_tsquery(text: str) -> str | None:
    """OR together content words. Only [a-z0-9] survive, so no tsquery syntax can be injected."""
    words: list[str] = []
    for w in _WORD.findall(text.lower()):
        if (len(w) >= 3 or w.isdigit()) and w not in _STOP and w not in words:
            words.append(w)
    return " | ".join(words) if words else None
