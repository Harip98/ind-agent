"""Investigation orchestration: intent -> alarms -> windows -> analysis -> causes -> docs -> report."""

import re
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
from copilot.intent import AssetMatch, TimeRange, alarm_types, resolve_asset, resolve_time
from copilot.llm import LLM, ToolLLM
from copilot.ollama import OllamaError, OllamaOutputError
from copilot.report import (
    SYSTEM_PROMPT,
    Evidence,
    build_docs,
    confidence,
    render_prompt,
    report_schema,
    summary_text,
    template_report,
    verify,
)
from copilot.retrieval import Hit

MAX_DOCS = 7

EVENT_GAP = timedelta(hours=2)
_ALARM_ID = re.compile(r"^([A-Za-z0-9_-]{1,32}):([A-Za-z0-9_-]{1,32}):([a-z_]{1,32}):(\d{8}T\d{6}Z)$")


class LLMUnavailable(RuntimeError):
    """Ollama is down or timed out in llm mode."""


@dataclass(frozen=True)
class InvestigateRequest:
    question: str
    as_of: datetime | None = None
    mode: str = "llm"  # llm | template | agent
    strategy: str | None = None
    asset_id: str | None = None  # answer to an asset clarification
    alarm_id: str | None = None  # answer to an event clarification (id from data-svc)


@dataclass
class PipelineDeps:
    datasvc: Callable[[Principal, str], object]
    retriever: object
    llm: LLM | None
    signatures: SignatureSet
    settings: Settings
    tool_llm: ToolLLM | None = None  # mode=agent only


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


def collect(ds, asset, alarm: AlarmInfo, deps: PipelineDeps, timezone: str,
            lap: Callable[[str], None] = lambda _: None):
    """Reading windows around the alarm -> deterministic analysis -> ranked candidate causes."""
    windows = {}
    t = alarm.raised_at
    for s in asset.sensors:
        windows[s.tag] = TagWindows(ds.window(asset.id, s.tag, t - SHORT_BEFORE, t + SHORT_AFTER),
                                    ds.window(asset.id, s.tag, t - LONG_BEFORE, t + LONG_AFTER))
    lap("data")
    roles = deps.signatures.roles.get(asset.model, {})
    analysis = analyze(asset, alarm, windows, roles, timezone)
    return analysis, match(deps.signatures, asset.model, analysis.features, analysis.feature_refs)


def observations_dict(analysis) -> list[dict]:
    return [{"id": o.id, "kind": o.kind, "tag": o.tag, "text": o.text, "values": o.values}
            for o in analysis.observations]


def citations(docs) -> list[dict]:
    return [{"ref": d.id, "doc_id": d.hit.doc_id, "version": d.hit.version, "section": d.hit.section_no,
             "title": d.hit.title, "heading": d.hit.heading, "stale": d.hit.stale or not d.hit.is_current,
             "content": d.hit.content} for d in docs]


def final_report(ev: Evidence, report: dict) -> dict:
    """The response report: deterministic summary, ranking and confidence; the writer's texts and refs."""
    texts = {e["cause_code"]: e for e in report["explanations"]}
    explanations = [
        {"cause_code": c.code, "text": texts[c.code]["text"], "refs": texts[c.code]["refs"],
         "confidence": confidence(c, rank), "label": c.label, "score": c.score}
        for rank, c in enumerate(ev.candidates) if c.code in texts
    ]
    insufficient = []
    if not ev.candidates:
        insufficient.append("The readings match no known fault signature.")
    if not any(d.hit.is_current and not d.hit.stale for d in ev.docs):
        insufficient.append("No current procedure matched; only superseded or no documents were found.")
    if any(d.hit.stale for d in ev.docs):
        insufficient.append("Some matching procedures are SUPERSEDED versions; confirm the current procedure.")
    return {
        "summary": summary_text(ev),
        "observations": [{"id": o.id, "text": o.text} for o in ev.observations],
        "possible_explanations": explanations,
        "recommendation": report["recommendation"],
        "insufficient_evidence": insufficient,
    }


def investigate(req: InvestigateRequest, principal: Principal, deps: PipelineDeps, trace_id: str) -> dict:
    if req.mode == "agent":
        from copilot.agent import run_agent

        return run_agent(req, principal, deps, trace_id)
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
    by_id = {a.id: a for a in assets}
    picked = _ALARM_ID.match(req.alarm_id) if req.alarm_id else None
    if req.alarm_id:
        # an explicit alarm: its asset and time come from the id; unknown/foreign ids look the same
        asset_match, via = AssetMatch(by_id.get(picked.group(1)) if picked else None), "request"
        when = datetime.strptime(picked.group(4), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC) if picked else as_of
        rng = TimeRange(when - timedelta(minutes=1), when + timedelta(minutes=1), "selected alarm", False)
        types: tuple[str, ...] = ()
    else:
        if req.asset_id:
            asset_match, via = AssetMatch(by_id.get(req.asset_id)), "request"
        else:
            asset_match, via = resolve_asset(req.question, assets, deps.llm if req.mode == "llm" else None)
        rng = resolve_time(req.question, as_of, tenant.timezone)
        types = alarm_types(req.question)
    assumptions = [f"No usable time given; searched the {rng.label}."] if rng.assumed else []
    if via == "llm":
        assumptions.append("Asset identified by the language model from the question.")
    lap("intent")
    if asset_match.asset is None:
        out["status"] = "needs_clarification"
        listed = asset_match.options or tuple(assets)
        out["clarification"] = {
            "message": "Which asset do you mean?" if asset_match.options else
                       "I could not find that asset. Name one of your assets (for example its id).",
            "options": [{"label": f"{a.id} ({a.name})", "asset_id": a.id} for a in listed]}
        out["query"] = {"time_label": rng.label, "timezone": tenant.timezone, "assumptions": assumptions}
        return out

    asset = asset_match.asset
    out["query"] = {"asset_id": asset.id, "asset_name": asset.name, "model": asset.model,
                    "from": rng.start.isoformat(), "to": rng.end.isoformat(), "time_label": rng.label,
                    "timezone": tenant.timezone,
                    "alarm_types": list(types), "assumptions": assumptions}
    alarms, _ = ds.alarms(asset.id, rng.start, rng.end)
    out["evidence"]["alarms"] = [_alarm_dict(a) for a in alarms]
    matching = ([a for a in alarms if a.id == req.alarm_id] if req.alarm_id
                else [a for a in alarms if not types or a.alarm_type in types])
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
            "options": [{"label": f"{e[0].tag} {e[0].alarm_type} at {_local(e[0].raised_at, tenant.timezone)}",
                         "alarm_id": e[0].id} for e in events]}
        return out
    alarm = events[0][0]
    out["query"].update(alarm_id=alarm.id, alarm_type=alarm.alarm_type, tag=alarm.tag)
    out["evidence"]["alarm"] = _alarm_dict(alarm)

    analysis, candidates = collect(ds, asset, alarm, deps, tenant.timezone, lap)
    out["evidence"]["observations"] = observations_dict(analysis)
    out["evidence"]["series"] = analysis.series
    lap("analyze")

    sensor = asset.sensor(alarm.tag)
    strategy = req.strategy or deps.settings.retrieval_strategy
    query = " ".join([req.question, sensor.description, alarm.alarm_type.replace("_", " ")]
                     + [c.query for c in candidates[:2]])
    general = deps.retriever.search(principal.tenant_id, query, asset.model, k=5, strategy=strategy)
    targeted: list[Hit] = []
    if candidates:  # make sure the procedure for the top cause is in front of the writer
        targeted = deps.retriever.search(principal.tenant_id, candidates[0].query, asset.model, k=3,
                                         strategy=strategy)
    # the question on its own, so a procedure the user asks about (possibly superseded) can surface
    asked = deps.retriever.search(principal.tenant_id, req.question, asset.model, k=2, strategy=strategy)
    hits: list[Hit] = []
    for h in targeted + asked + general:
        if h.section_id not in {x.section_id for x in hits}:
            hits.append(h)
    docs = build_docs(hits[:MAX_DOCS])
    out["citations"] = citations(docs)
    lap("retrieve")

    ev = Evidence(req.question, asset, alarm, analysis.observations, candidates, docs, tenant.timezone)
    report = None
    if req.mode == "llm":
        if deps.llm is None:
            raise LLMUnavailable("no LLM configured")
        stats = {"model": deps.llm.name, "attempts": 0, "prompt_tokens": 0, "completion_tokens": 0,
                 "seconds": 0.0, "verifier_errors": []}
        errors: list[str] | None = None
        gen_s = ver_s = 0.0
        for _ in range(2):
            stats["attempts"] += 1
            t0 = time.perf_counter()
            try:
                result = deps.llm.generate_json(SYSTEM_PROMPT, render_prompt(ev, errors), report_schema(ev))
            except OllamaOutputError as exc:  # the model answered badly: a failed attempt, not an outage
                errors = [f"your output was not valid JSON ({exc}); keep it short and complete"]
                stats["verifier_errors"] += errors
                gen_s += time.perf_counter() - t0
                continue
            except OllamaError as exc:
                raise LLMUnavailable(str(exc)) from exc
            t1 = time.perf_counter()
            stats["prompt_tokens"] += result.prompt_tokens
            stats["completion_tokens"] += result.completion_tokens
            stats["seconds"] = round(stats["seconds"] + result.seconds, 2)
            errors = verify(result.data, ev)
            gen_s, ver_s = gen_s + (t1 - t0), ver_s + (time.perf_counter() - t1)
            if not errors:
                report = result.data
                break
            stats["verifier_errors"] += errors
        out["llm"] = stats
        timings["llm"], timings["verify"] = int(gen_s * 1000), int(ver_s * 1000)
        clock = time.perf_counter()
    if report is None:
        report = template_report(ev)
        if req.mode == "llm":
            out["status"] = "degraded"
    # Ranking and confidence come from the signatures, never from the LLM: keep its texts, in candidate order.
    out["report"] = final_report(ev, report)
    lap("report")
    return out
