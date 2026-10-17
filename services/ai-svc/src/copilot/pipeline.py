"""Investigation orchestration: intent -> alarms -> windows -> analysis -> causes -> docs -> report."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from copilot.analysis import LONG_AFTER, LONG_BEFORE, SHORT_AFTER, SHORT_BEFORE, TagWindows, analyze
from copilot.auth import Principal
from copilot.causes import SignatureSet, match
from copilot.config import Settings
from copilot.datasvc import AlarmInfo
from copilot.intent import alarm_types, resolve_asset, resolve_time
from copilot.llm import LLM
from copilot.ollama import OllamaError
from copilot.report import SYSTEM_PROMPT, Evidence, build_docs, render_prompt, report_schema, template_report, verify

EVENT_GAP = timedelta(hours=2)


class LLMUnavailable(RuntimeError):
    """Ollama is down or timed out in llm mode."""


@dataclass(frozen=True)
class InvestigateRequest:
    question: str
    as_of: datetime | None = None
    mode: str = "llm"  # llm | template
    strategy: str | None = None


@dataclass
class PipelineDeps:
    datasvc: Callable[[Principal, str], object]
    retriever: object
    llm: LLM | None
    signatures: SignatureSet
    settings: Settings


def _alarm_dict(a: AlarmInfo) -> dict:
    return {"id": a.id, "tag": a.tag, "alarm_type": a.alarm_type, "severity": a.severity,
            "raised_at": a.raised_at.isoformat(), "cleared_at": a.cleared_at.isoformat() if a.cleared_at else None,
            "value": a.value, "limit_value": a.limit_value, "message": a.message}


def _events(alarms: list[AlarmInfo]) -> list[list[AlarmInfo]]:
    """Alarms within EVENT_GAP of an event's first alarm belong to the same event."""
    events: list[list[AlarmInfo]] = []
    for a in sorted(alarms, key=lambda x: (x.raised_at, x.tag)):
        if events and a.raised_at - events[-1][0].raised_at <= EVENT_GAP:
            events[-1].append(a)
        else:
            events.append([a])
    return events


def _local(dt: datetime, tz: str) -> str:
    return dt.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M %Z")


def investigate(req: InvestigateRequest, principal: Principal, deps: PipelineDeps, trace_id: str) -> dict:
    timings: dict[str, int] = {}
    clock = time.perf_counter()

    def lap(name: str) -> None:
        nonlocal clock
        now = time.perf_counter()
        timings[name] = int((now - clock) * 1000)
        clock = now

    as_of = (req.as_of or datetime.now(UTC)).astimezone(UTC)
    out: dict = {"status": "ok", "trace_id": trace_id, "mode": req.mode, "query": {}, "report": None,
                 "evidence": {"alarm": None, "alarms": [], "observations": [], "series": {}},
                 "citations": [], "clarification": None, "llm": None, "timings_ms": timings}
    ds = deps.datasvc(principal, trace_id)
    tenant, assets = ds.assets()
    asset_match, via = resolve_asset(req.question, assets, deps.llm if req.mode == "llm" else None)
    rng = resolve_time(req.question, as_of, tenant.timezone)
    types = alarm_types(req.question)
    assumptions = [f"No usable time given; searched the {rng.label}."] if rng.assumed else []
    if via == "llm":
        assumptions.append("Asset identified by the language model from the question.")
    lap("intent")
    if asset_match.asset is None:
        out["status"] = "needs_clarification"
        options = [f"{a.id} ({a.name})" for a in asset_match.options]
        out["clarification"] = {
            "message": "Which asset do you mean?" if options else
                       "I could not find that asset. Name one of your assets (for example its id).",
            "options": options or [f"{a.id} ({a.name})" for a in assets]}
        out["query"] = {"time_label": rng.label, "assumptions": assumptions}
        return out

    asset = asset_match.asset
    out["query"] = {"asset_id": asset.id, "asset_name": asset.name, "model": asset.model,
                    "from": rng.start.isoformat(), "to": rng.end.isoformat(), "time_label": rng.label,
                    "alarm_types": list(types), "assumptions": assumptions}
    alarms, _ = ds.alarms(asset.id, rng.start, rng.end)
    out["evidence"]["alarms"] = [_alarm_dict(a) for a in alarms]
    matching = [a for a in alarms if not types or a.alarm_type in types]
    events = _events(matching)
    if not events:
        lap("data")
        out["status"] = "insufficient_evidence"
        what = " or ".join(types) if types else "any"
        out["report"] = {
            "summary": f"No {what} alarm was found for {asset.name} ({asset.id}) between "
                       f"{_local(rng.start, tenant.timezone)} and {_local(rng.end, tenant.timezone)}.",
            "observations": [], "possible_explanations": [], "recommendation": None,
            "insufficient_evidence": [f"No matching alarm in the {rng.label}."]
            + ([f"{len(alarms)} other alarm(s) exist in that window; see evidence.alarms."] if alarms else [])}
        return out
    if len(events) > 1:
        lap("data")
        out["status"] = "needs_clarification"
        out["clarification"] = {
            "message": f"{len(events)} separate alarm events match. Which one should I investigate?",
            "options": [f"{e[0].tag} {e[0].alarm_type} at {_local(e[0].raised_at, tenant.timezone)}"
                        for e in events]}
        return out
    alarm = events[0][0]
    out["query"].update(alarm_id=alarm.id, alarm_type=alarm.alarm_type, tag=alarm.tag)
    out["evidence"]["alarm"] = _alarm_dict(alarm)

    windows = {}
    t = alarm.raised_at
    for s in asset.sensors:
        windows[s.tag] = TagWindows(ds.window(asset.id, s.tag, t - SHORT_BEFORE, t + SHORT_AFTER),
                                    ds.window(asset.id, s.tag, t - LONG_BEFORE, t + LONG_AFTER))
    lap("data")
    roles = deps.signatures.roles.get(asset.model, {})
    analysis = analyze(asset, alarm, windows, roles, tenant.timezone)
    candidates = match(deps.signatures, asset.model, analysis.features, analysis.feature_refs)
    out["evidence"]["observations"] = [{"id": o.id, "kind": o.kind, "tag": o.tag, "text": o.text,
                                        "values": o.values} for o in analysis.observations]
    out["evidence"]["series"] = analysis.series
    lap("analyze")

    sensor = asset.sensor(alarm.tag)
    query = " ".join([sensor.description, alarm.alarm_type.replace("_", " ")] + [c.query for c in candidates[:2]])
    hits = deps.retriever.search(principal.tenant_id, query, asset.model, k=5,
                                 strategy=req.strategy or deps.settings.retrieval_strategy)
    docs = build_docs(hits)
    out["citations"] = [{"ref": d.id, "doc_id": d.hit.doc_id, "version": d.hit.version,
                         "section": d.hit.section_no, "title": d.hit.title, "heading": d.hit.heading,
                         "stale": d.hit.stale or not d.hit.is_current, "content": d.hit.content} for d in docs]
    lap("retrieve")

    ev = Evidence(req.question, asset, alarm, analysis.observations, candidates, docs, tenant.timezone)
    report = None
    if req.mode == "llm":
        if deps.llm is None:
            raise LLMUnavailable("no LLM configured")
        stats = {"model": deps.llm.name, "attempts": 0, "prompt_tokens": 0, "completion_tokens": 0,
                 "seconds": 0.0, "verifier_errors": []}
        errors: list[str] | None = None
        for _ in range(2):
            stats["attempts"] += 1
            try:
                result = deps.llm.generate_json(SYSTEM_PROMPT, render_prompt(ev, errors), report_schema(ev))
            except OllamaError as exc:
                raise LLMUnavailable(str(exc)) from exc
            stats["prompt_tokens"] += result.prompt_tokens
            stats["completion_tokens"] += result.completion_tokens
            stats["seconds"] = round(stats["seconds"] + result.seconds, 2)
            errors = verify(result.data, ev)
            if not errors:
                report = result.data
                break
            stats["verifier_errors"] += errors
        out["llm"] = stats
        lap("verify")
    if report is None:
        report = template_report(ev)
        if req.mode == "llm":
            out["status"] = "degraded"
    lap("report")

    labels = {c.code: (c.label, c.score) for c in candidates}
    insufficient = []
    if not candidates:
        insufficient.append("The readings match no known fault signature.")
    if not any(d.hit.is_current and not d.hit.stale for d in docs):
        insufficient.append("No current procedure matched; only superseded or no documents were found.")
    if any(d.hit.stale for d in docs):
        insufficient.append("Some matching procedures are SUPERSEDED versions; confirm the current procedure.")
    out["report"] = {
        "summary": report["summary"],
        "observations": [{"id": o.id, "text": o.text} for o in analysis.observations],
        "possible_explanations": [
            {**e, "label": labels.get(e["cause_code"], (e["cause_code"], 0.0))[0],
             "score": labels.get(e["cause_code"], ("", 0.0))[1]} for e in report["explanations"]],
        "recommendation": report["recommendation"],
        "insufficient_evidence": insufficient,
    }
    return out
