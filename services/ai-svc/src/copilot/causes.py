"""Fault signatures (signatures.yaml) and deterministic candidate ranking."""

from dataclasses import dataclass
from pathlib import Path

import yaml

SIGNATURES_PATH = Path(__file__).with_name("signatures.yaml")


@dataclass(frozen=True)
class CauseSignature:
    code: str
    label: str
    description: str
    require: tuple[str, ...]
    support: tuple[str, ...]
    exclude: tuple[str, ...]
    query: str
    procedures: tuple[str, ...] = ()  # "DOC" or "DOC#section" patterns, preferred first


@dataclass(frozen=True)
class Candidate:
    code: str
    label: str
    description: str
    score: float
    refs: tuple[str, ...]
    query: str
    procedures: tuple[str, ...] = ()


@dataclass(frozen=True)
class SignatureSet:
    roles: dict[str, dict[str, str]]
    causes: dict[str, list[CauseSignature]]
    generic: list[CauseSignature]

    def for_model(self, model: str) -> list[CauseSignature]:
        return [*self.causes.get(model, []), *self.generic]


def _sig(code: str, d: dict) -> CauseSignature:
    return CauseSignature(code, d["label"], d["description"], tuple(d.get("require", ())),
                          tuple(d.get("support", ())), tuple(d.get("exclude", ())), d["query"],
                          tuple(d.get("procedures", ())))


def load_signatures(path: Path = SIGNATURES_PATH) -> SignatureSet:
    raw = yaml.safe_load(path.read_bytes().decode("utf-8"))
    roles = {m: dict(v["roles"]) for m, v in raw["models"].items()}
    causes = {m: [_sig(c, d) for c, d in v["causes"].items()] for m, v in raw["models"].items()}
    generic = [_sig(c, d) for c, d in raw["generic"]["causes"].items()]
    return SignatureSet(roles, causes, generic)


def match(sigs: SignatureSet, model: str, features: dict[str, bool],
          feature_refs: dict[str, list[str]]) -> list[Candidate]:
    out: list[tuple[float, int, Candidate]] = []
    for order, sig in enumerate(sigs.for_model(model)):
        if not all(features.get(f, False) for f in sig.require):
            continue
        if any(features.get(f, False) for f in sig.exclude):
            continue
        matched = [f for f in sig.support if features.get(f, False)]
        score = 1.0 if not sig.support else 0.5 + 0.5 * len(matched) / len(sig.support)
        refs: list[str] = []
        for f in (*sig.require, *matched):
            for r in feature_refs.get(f, []):
                if r not in refs:
                    refs.append(r)
        out.append((score, order, Candidate(sig.code, sig.label, sig.description, round(score, 2),
                                             tuple(refs), sig.query, sig.procedures)))
    out.sort(key=lambda x: (-x[0], x[1]))
    return [c for _, _, c in out]
