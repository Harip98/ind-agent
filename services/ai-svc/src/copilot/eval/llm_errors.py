"""LLM error analysis: run the cases that expect a written report, keep every draft, and sort each
verifier rejection into a root cause.

Drafts stay in the analysis output only; the pipeline never returns rejected model text to a client.
"""

import json
import re
import secrets
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from copilot.auth import Principal
from copilot.eval.dataset import EvalCase
from copilot.llm import LLMResult
from copilot.ollama import OllamaOutputError
from copilot.pipeline import InvestigateRequest, PipelineDeps, investigate
from copilot.report import SYSTEM_PROMPT

_NUMBER = re.compile(r"(?<![A-Za-z0-9])-?\d+(?:\.\d+)?")
# Tags (TT-101), document ids (SOP-PUMP-07), dates, clock times, O#/D# refs, versions and section numbers.
_IDENTIFIER = re.compile(r"\b[A-Z]{1,8}(?:-[A-Z0-9]+)*-\d+\w*|\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?"
                         r"|\b\d{1,2}:\d{2}\b|\b[OD]\d+\b|§\s?\d+(?:\.\d+)*|\bv\d+")
_FIELD = re.compile(r"^(explanation (\d+)|recommendation)")
_NUM_MSG = re.compile(r"number (\S+) does not appear")

# Root causes, in the order the report lists them. `true_catch` = the verifier did its job.
CLASSES = {
    "runaway_generation": ("Runaway generation: the model repeated itself until the token limit (num_predict) cut the JSON off",
                           False),
    "invalid_json": ("Invalid JSON for another reason", False),
    "identifier_as_number": ("Verifier false positive: a tag, document id or date was read as a number "
                             "(TT-101 -> 101)", False),
    "missing_observation_ref": ("Explanation cites no observation in `refs` (cited D#, or O# only in the text)",
                                False),
    "number_from_uncited_evidence": ("Number is in the prompt, but not in the refs the sentence cites", False),
    "number_not_in_evidence": ("Number appears nowhere in the evidence (invented or computed)", True),
    "wrong_procedure": ("Recommendation does not cite the procedure for the top cause", False),
    "unknown_ref": ("Cites a ref that does not exist", False),
    "cause_structure": ("Explanations miss the top cause, repeat one or invent a code", False),
    "superseded_advice": ("Repeats advice from a superseded procedure", True),
    "unsafe_or_injected": ("Unsafe advice or an injected claim (sabotage) in the draft", True),
}


@dataclass
class RecordingLLM:
    """Wraps the real LLM; remembers the prompt and draft of each report attempt (parsed JSON, or raw text when
    it did not parse). Other calls pass through unrecorded, so drafts line up with the attempt log."""

    inner: object
    drafts: list[dict] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.inner.name

    def generate_json(self, system: str, user: str, schema: dict) -> LLMResult:
        if system != SYSTEM_PROMPT:  # e.g. the intent step naming an asset: not a report attempt
            return self.inner.generate_json(system, user, schema)
        try:
            result = self.inner.generate_json(system, user, schema)
        except OllamaOutputError as exc:
            self.drafts.append({"prompt": user, "raw": exc.raw, "data": None})
            raise
        self.drafts.append({"prompt": user, "raw": None, "data": result.data})
        return result


def _field_text(draft: dict, msg: str) -> str:
    data = draft.get("data")
    if not isinstance(data, dict):
        return draft.get("raw") or ""
    m = _FIELD.match(msg)
    try:
        if m and m.group(2):
            return data["explanations"][int(m.group(2)) - 1]["text"]
        if m:
            return data["recommendation"]["text"]
    except (KeyError, IndexError, TypeError):
        pass
    return json.dumps(data)


def _plain_numbers(text: str) -> list[tuple[str, bool]]:
    """Every number in `text`, with whether it sits inside an identifier."""
    spans = [m.span() for m in _IDENTIFIER.finditer(text)]
    return [(m.group(), any(a <= m.start() and m.end() <= b for a, b in spans)) for m in _NUMBER.finditer(text)]


def _inside_identifier(text: str, raw: str) -> bool:
    hits = [inside for n, inside in _plain_numbers(text) if n == raw]
    return bool(hits) and all(hits)


def classify(msg: str, draft: dict, done_reason: str = "") -> str:
    """Root-cause class of one verifier message, given the draft that produced it."""
    if "not valid JSON" in msg:
        return "runaway_generation" if done_reason == "length" else "invalid_json"
    if "unsafe advice" in msg or "is not supported by the observations" in msg:
        return "unsafe_or_injected"
    if "superseded" in msg:
        return "superseded_advice" if "repeats advice" in msg else "wrong_procedure"
    if "must cite an observation" in msg:
        return "missing_observation_ref"
    if m := _NUM_MSG.search(msg):
        raw = m.group(1)
        if _inside_identifier(_field_text(draft, msg), raw):
            return "identifier_as_number"
        in_prompt = any(_same(n, raw) for n, inside in _plain_numbers(draft.get("prompt", "")) if not inside)
        return "number_from_uncited_evidence" if in_prompt else "number_not_in_evidence"
    if "must cite" in msg:
        return "wrong_procedure"
    if "unknown ref" in msg:
        return "unknown_ref"
    return "cause_structure"


def _same(a: str, b: str) -> bool:
    try:
        return abs(float(a) - float(b)) <= 0.051
    except ValueError:
        return False


def analyze_case(case: EvalCase, response: dict | None, drafts: list[dict], error: str | None) -> dict:
    llm = (response or {}).get("llm") or {}
    attempts = []
    for i, entry in enumerate(llm.get("attempt_log") or []):
        draft = drafts[i] if i < len(drafts) else {}
        classes = [classify(e, draft, entry.get("done_reason", "")) for e in entry.get("errors", [])]
        attempts.append({**entry, "classes": classes,
                         "draft": draft.get("data") if draft.get("data") is not None else (draft.get("raw") or "")[:600]})
    return {"id": case.id, "category": case.category, "incident_id": case.incident_id,
            "expected_cause": case.expected_cause, "status": (response or {}).get("status"), "error": error,
            "completion_tokens": llm.get("completion_tokens"), "attempts": attempts}


def run_analysis(cases: list[EvalCase], deps: PipelineDeps, principal_for: Callable[[EvalCase], Principal],
                 out_path: Path) -> list[dict]:
    """Run llm mode on every case that expects a written report; one JSONL row per case."""
    rows = []
    recorder = RecordingLLM(deps.llm)
    deps.llm = recorder
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as fh:
        for case in [c for c in cases if c.expected_status == "ok"]:
            recorder.drafts = []
            req = InvestigateRequest(case.question, datetime.fromisoformat(case.as_of), "llm",
                                     asset_id=case.request_asset_id, alarm_id=case.request_alarm_id)
            try:
                response, error = investigate(req, principal_for(case), deps, secrets.token_hex(8)), None
            except Exception as exc:  # recorded, never fatal to the run
                response, error = None, f"{type(exc).__name__}: {exc}"
            row = analyze_case(case, response, recorder.drafts, error)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            rows.append(row)
    return rows


def _fixable(row: dict, fixed: set[str]) -> bool:
    """A degraded case some attempt of which failed only for reasons in `fixed`."""
    return row["status"] == "degraded" and any(a["classes"] and set(a["classes"]) <= fixed for a in row["attempts"])


def summarize_errors(rows: list[dict]) -> dict:
    rejections = Counter(c for r in rows for a in r["attempts"] for c in a["classes"])
    attempts_with = Counter(c for r in rows for a in r["attempts"] for c in set(a["classes"]))
    degraded = [r for r in rows if r["status"] == "degraded"]
    final_classes = Counter(c for r in degraded for c in set(r["attempts"][-1]["classes"]) if r["attempts"])
    failed_attempts = sum(1 for r in rows for a in r["attempts"] if a["outcome"] != "accepted")
    tool_fixes = {"identifier_as_number"}
    output_fixes = tool_fixes | {"runaway_generation", "missing_observation_ref"}
    return {
        "cases": len(rows), "ok": sum(r["status"] == "ok" for r in rows), "degraded": len(degraded),
        "errors": sum(bool(r["error"]) for r in rows),
        "attempts": sum(len(r["attempts"]) for r in rows), "failed_attempts": failed_attempts,
        "rejections": dict(rejections), "attempts_with": dict(attempts_with), "final_classes": dict(final_classes),
        "fixable_verifier": sum(_fixable(r, tool_fixes) for r in rows),
        "fixable_output_constraints": sum(_fixable(r, output_fixes) for r in rows),
        "degraded_ids": [r["id"] for r in degraded],
    }


def render_errors_report(summary: dict, rows: list[dict], title: str, notes: list[str]) -> str:
    s = summary
    lines = [f"# {title}", "",
             f"**Cases that expect a written report:** {s['cases']} — verified {s['ok']}, degraded {s['degraded']}, "
             f"errors {s['errors']}. **Attempts:** {s['attempts']}, of which {s['failed_attempts']} failed.", "",
             "| Root cause | Verifier messages | Failed attempts with it | Degraded cases, final attempt | "
             "Verifier did its job |", "|---|---|---|---|---|"]
    for key, (label, true_catch) in CLASSES.items():
        if s["rejections"].get(key):
            lines.append(f"| {label} | {s['rejections'][key]} | {s['attempts_with'].get(key, 0)} | "
                         f"{s['final_classes'].get(key, 0)} | {'yes' if true_catch else 'no'} |")
    lines += ["", f"**Counterfactuals.** Degraded cases where some attempt failed *only* for an identifier read as "
              f"a number: {s['fixable_verifier']}. Only for that, a runaway generation or a missing O# ref: "
              f"{s['fixable_output_constraints']}.", "", "## Degraded cases", "",
              "| Case | Attempt 1 | Attempt 2 |", "|---|---|---|"]
    for r in rows:
        if r["status"] == "degraded":
            cells = [", ".join(f"{c} ×{n}" if n > 1 else c for c, n in Counter(a["classes"]).items()) or "–"
                     for a in r["attempts"]] + ["–", "–"]
            lines.append(f"| {r['id']} | {cells[0]} | {cells[1]} |")
    lines += ["", "## Method", ""] + [f"- {n}" for n in notes]
    return "\n".join(lines) + "\n"
