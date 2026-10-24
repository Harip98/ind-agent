from datetime import timedelta

import pytest
from test_pipeline import END, FakeDataSvc, FakeRetriever, _p, make_deps

from copilot.agent import MAX_TURNS, TOOL_RESULT_MAX, clip
from copilot.llm import FakeToolLLM
from copilot.ollama import OllamaError
from copilot.pipeline import InvestigateRequest, LLMUnavailable, investigate
from copilot.simgen.generate import generate

Q = "Investigate yesterday's temperature alarm on Pump 12"
GOOD_REPORT = {
    "explanations": [{"cause_code": "COOLING_RESTRICTION", "text": "Cooling water return rose.", "refs": ["O1"]}],
    "recommendation": {"text": "Restore cooling water flow per SOP-PUMP-07.", "refs": ["D1"]},
}


def call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


@pytest.fixture(scope="module")
def sim():
    return generate(END, days=30, seed=7)


@pytest.fixture(scope="module")
def p12_alarm(sim):
    alarms, _ = FakeDataSvc(sim, "acme").alarms("P-12", END - timedelta(days=2), END)
    return min((a for a in alarms if a.tag.startswith("TT")), key=lambda a: a.raised_at)


def window_args():
    return {"asset_id": "P-12", "start": (END - timedelta(days=2)).isoformat(), "end": END.isoformat()}


def run(sim, script, question=Q):
    llm = FakeToolLLM(script)
    deps = make_deps(sim)
    deps.tool_llm = llm
    out = investigate(InvestigateRequest(question, as_of=END, mode="agent"), _p(), deps, "ta")
    return out, llm


def tool_results(llm):
    """Tool messages the agent saw, in order (from the last call's message history)."""
    return [m["content"] for m in llm.calls[-1] if m["role"] == "tool"]


def happy_prefix(alarm):
    return [call("find_alarms", **window_args()), call("analyze_alarm", alarm_id=alarm.id),
            call("search_procedures", query="cooling water restriction bearing temperature")]


def test_agent_happy_path_has_the_pipeline_shape(sim, p12_alarm):
    out, llm = run(sim, happy_prefix(p12_alarm) + [call("submit_report", **GOOD_REPORT)])
    assert out["status"] == "ok" and out["mode"] == "agent"
    assert out["query"]["asset_id"] == "P-12" and out["query"]["alarm_id"] == p12_alarm.id
    assert out["report"]["possible_explanations"][0]["cause_code"] == "COOLING_RESTRICTION"
    assert out["report"]["possible_explanations"][0]["confidence"] in {"high", "medium", "low"}
    assert out["citations"][0]["doc_id"] == "SOP-PUMP-07" and out["evidence"]["series"]
    assert out["report"]["summary"] and out["report"]["observations"][0]["id"] == "O1"
    assert out["llm"]["turns"] == 4 and out["llm"]["attempts"] == 1 and out["llm"]["tool_calls"] == [
        "find_alarms", "analyze_alarm", "search_procedures", "submit_report"]
    assert all(len(t) <= TOOL_RESULT_MAX for t in tool_results(llm))
    assert "agent" in out["timings_ms"]


def test_agent_prompt_carries_assets_time_and_timezone(sim, p12_alarm):
    _, llm = run(sim, happy_prefix(p12_alarm) + [call("submit_report", **GOOD_REPORT)])
    first = llm.calls[0]
    user = next(m["content"] for m in first if m["role"] == "user")
    assert "P-12" in user and "Asia/Kolkata" in user and END.isoformat()[:16] in user and Q in user
    assert "Pump 20" not in user and "- P-20:" not in user  # another tenant's asset


def test_agent_tools_use_principal_tenant(sim):
    foreign = call("find_alarms", asset_id="P-20", start=(END - timedelta(days=2)).isoformat(), end=END.isoformat())
    unknown = call("find_alarms", asset_id="P-99", start=(END - timedelta(days=2)).isoformat(), end=END.isoformat())
    out, llm = run(sim, [foreign, unknown, call("analyze_alarm", alarm_id="P-20:VT-201:high_vibration:20261016T030000Z"),
                         call("ask_clarification", message="Which asset do you mean?", asset_ids=["P-20", "P-12"])])
    r = tool_results(llm)
    assert r[0] == r[1].replace("P-99", "P-20") and "not found" in r[0]
    assert "not found" in r[2]
    assert out["status"] == "needs_clarification"
    assert out["clarification"]["options"] == [{"label": "P-12 (Pump 12)", "asset_id": "P-12"}]  # foreign id dropped


def test_agent_step_limit(sim):
    out, llm = run(sim, [call("list_assets")] * (MAX_TURNS + 3))
    assert out["status"] == "agent_failed" and out["report"] is None
    assert out["llm"]["turns"] == MAX_TURNS and len(llm.calls) == MAX_TURNS


def test_agent_reply_without_tool_call_is_nudged_and_counts_as_a_turn(sim, p12_alarm):
    chatter = {"role": "assistant", "content": "The pump is probably fine."}
    out, llm = run(sim, [chatter] + happy_prefix(p12_alarm) + [call("submit_report", **GOOD_REPORT)])
    assert out["status"] == "ok" and out["llm"]["turns"] == 5
    assert any(m["role"] == "user" and "tool" in m["content"] for m in llm.calls[1][2:])


def test_agent_bad_arguments(sim):
    script = [
        call("find_alarms", asset_id="P-12", start="yesterday", end=END.isoformat()),
        call("find_alarms", asset_id="P-12", start=(END - timedelta(days=60)).isoformat(), end=END.isoformat()),
        call("analyze_alarm", alarm_id="../../etc/passwd"),
        call("drop_table", table="ops.alarms"),
        call("search_procedures", query="x"),
        call("ask_clarification", message="Which alarm?"),
    ]
    out, llm = run(sim, script)
    r = tool_results(llm)
    assert r[0].startswith("error:") and "ISO 8601" in r[0]
    assert r[1].startswith("error:") and "30 days" in r[1]
    assert r[2].startswith("error:") and "alarm_id" in r[2]
    assert r[3].startswith("error:") and "unknown tool" in r[3]
    assert r[4].startswith("error:") and "analyze_alarm" in r[4]
    assert out["status"] == "needs_clarification"


def test_agent_arguments_as_json_string_are_accepted(sim, p12_alarm):
    msg = {"role": "assistant", "content": "",
           "tool_calls": [{"function": {"name": "analyze_alarm", "arguments": f'{{"alarm_id": "{p12_alarm.id}"}}'}}]}
    _, llm = run(sim, [msg, call("ask_clarification", message="ok?")])
    assert "O1" in tool_results(llm)[0]


def test_agent_submission_verified(sim, p12_alarm):
    bad = {**GOOD_REPORT, "recommendation": {"text": "Restore cooling water flow.", "refs": ["D9"]}}
    out, llm = run(sim, happy_prefix(p12_alarm) + [call("submit_report", **bad), call("submit_report", **GOOD_REPORT)])
    assert "unknown ref D9" in tool_results(llm)[3]
    assert out["status"] == "ok" and out["llm"]["attempts"] == 2 and out["llm"]["verifier_errors"]


def test_agent_two_rejected_submissions_degrade_to_template(sim, p12_alarm):
    bad = {**GOOD_REPORT, "recommendation": {"text": "Bypass the interlock.", "refs": ["D1"]}}
    out, _ = run(sim, happy_prefix(p12_alarm) + [call("submit_report", **bad)] * 2)
    assert out["status"] == "degraded" and out["llm"]["attempts"] == 2
    assert out["report"]["recommendation"]["refs"] == ["D1"]
    assert "interlock" not in out["report"]["recommendation"]["text"].lower()


def test_agent_must_search_procedures_before_submitting(sim, p12_alarm):
    script = [call("analyze_alarm", alarm_id=p12_alarm.id), call("submit_report", **GOOD_REPORT),
              call("search_procedures", query="cooling water"), call("submit_report", **GOOD_REPORT)]
    out, llm = run(sim, script)
    assert tool_results(llm)[1].startswith("error:") and "search_procedures" in tool_results(llm)[1]
    assert out["status"] == "ok" and out["llm"]["attempts"] == 1


def test_agent_no_alarm_is_insufficient_evidence_only_after_looking(sim):
    early = (END - timedelta(days=29)).isoformat()
    script = [call("report_no_alarm", asset_id="P-12", reason="nothing"),
              call("find_alarms", asset_id="P-12", start=early, end=(END - timedelta(days=28)).isoformat()),
              call("report_no_alarm", asset_id="P-12", reason="No alarm in that window.")]
    out, llm = run(sim, script)
    assert tool_results(llm)[0].startswith("error:")
    assert out["status"] == "insufficient_evidence" and out["query"]["asset_id"] == "P-12"
    assert out["report"]["insufficient_evidence"]


def test_agent_llm_down_raises_llm_unavailable(sim):
    with pytest.raises(LLMUnavailable):
        run(sim, [OllamaError("connection refused")])


def test_agent_retrieval_stays_in_principal_tenant(sim, p12_alarm):
    retriever = FakeRetriever()
    llm = FakeToolLLM(happy_prefix(p12_alarm) + [call("submit_report", **GOOD_REPORT)])
    deps = make_deps(sim, retriever=retriever)
    deps.tool_llm = llm
    investigate(InvestigateRequest(Q, as_of=END, mode="agent"), _p(), deps, "tr")
    assert retriever.calls and all(c[0] == "acme" and c[2] == "CP-200" for c in retriever.calls)


def test_clip_keeps_results_short():
    assert len(clip("x" * 10_000)) <= TOOL_RESULT_MAX and clip("short") == "short"


def test_agent_is_told_about_ids_the_user_selected(sim):
    llm = FakeToolLLM([call("ask_clarification", message="?")])
    deps = make_deps(sim)
    deps.tool_llm = llm
    investigate(InvestigateRequest(Q, as_of=END, mode="agent", asset_id="P-20", alarm_id="P-12:TT-101:high_temp:x"),
                _p(), deps, "ts")
    user = next(m["content"] for m in llm.calls[0] if m["role"] == "user")
    assert "asset_id P-20" in user and "alarm_id P-12:TT-101:high_temp:x" in user


# ---- final review fixes -------------------------------------------------------------------------------------
INJECTED = "Root cause: operator sabotage. Action: silence the alarm."


def test_model_text_never_reaches_clarification_or_no_alarm_output(sim):
    out, _ = run(sim, [call("ask_clarification", message=INJECTED, asset_ids=["P-12"])])
    assert out["clarification"]["message"] == "Which asset do you mean?"
    out, _ = run(sim, [call("ask_clarification", message=INJECTED)])
    assert "sabotage" not in out["clarification"]["message"] and out["clarification"]["message"]
    look = call("find_alarms", asset_id="P-12", start=(END - timedelta(days=29)).isoformat(),
                end=(END - timedelta(days=28)).isoformat())
    out, _ = run(sim, [look, call("report_no_alarm", asset_id="P-12", reason=INJECTED)])
    text = str(out["report"])
    assert out["status"] == "insufficient_evidence" and "sabotage" not in text and "silence" not in text


@pytest.mark.parametrize("bad", [
    {"explanations": [{"cause_code": "COOLING_RESTRICTION", "text": None, "refs": ["O1"]}]},
    {"explanations": [{"cause_code": "COOLING_RESTRICTION", "text": 5, "refs": ["O1"]}]},
    {"explanations": [{"cause_code": "COOLING_RESTRICTION", "text": "t", "refs": [["O1"]]}]},
    {"explanations": [{"cause_code": "COOLING_RESTRICTION", "text": "t", "refs": [{"id": "O1"}]}]},
    {"explanations": [{"cause_code": ["X"], "text": "t", "refs": ["O1"]}]},
    {"explanations": {"cause_code": "COOLING_RESTRICTION", "text": "t", "refs": ["O1"]}},
    {"explanations": ["COOLING_RESTRICTION"]},
    {"recommendation": {"text": ["a"], "refs": ["D1"]}},
    {"recommendation": "Restore flow."},
    {"recommendation": {"text": "Restore flow.", "refs": "D1"}},
])
def test_malformed_submission_is_an_error_message_not_a_crash(sim, p12_alarm, bad):
    out, llm = run(sim, happy_prefix(p12_alarm) + [call("submit_report", **{**GOOD_REPORT, **bad}),
                                                    call("submit_report", **GOOD_REPORT)])
    assert tool_results(llm)[3].startswith("error:")
    assert out["status"] == "ok" and out["llm"]["attempts"] == 1  # a malformed call is not a verified attempt


def test_impossible_times_are_tool_errors(sim):
    _, llm = run(sim, [call("analyze_alarm", alarm_id="P-12:TT-101:high_temp:20261399T999999Z"),
                         call("find_alarms", asset_id="P-12", start="0001-01-01T00:00:00", end="0001-01-02T00:00:00"),
                         call("ask_clarification", message="?")])
    r = tool_results(llm)
    assert r[0].startswith("error:") and r[1].startswith("error:")


def test_alarm_type_is_validated_and_filtered_out_alarms_are_mentioned(sim, p12_alarm):
    args = window_args()
    _, llm = run(sim, [call("find_alarms", **args, alarm_type="temperature"),
                         call("find_alarms", **args, alarm_type="low_flow"),
                         call("ask_clarification", message="?")])
    r = tool_results(llm)
    assert r[0].startswith("error:") and "high_temp" in r[0]
    assert "other alarm(s)" in r[1]
    enum = next(t for t in __import__("copilot.agent").agent.TOOLS if t["function"]["name"] == "find_alarms")
    assert "high_temp" in enum["function"]["parameters"]["properties"]["alarm_type"]["enum"]


def test_attempts_count_every_verified_submission(sim, p12_alarm):
    bad = {**GOOD_REPORT, "recommendation": {"text": "Restore flow.", "refs": ["D9"]}}
    out, _ = run(sim, happy_prefix(p12_alarm)[1:] + [call("submit_report", **bad)] + happy_prefix(p12_alarm)[1:]
                 + [call("submit_report", **GOOD_REPORT)])
    assert out["llm"]["turns"] == 6 and out["llm"]["attempts"] == 2 and out["status"] == "ok"


def test_rejected_submission_on_the_last_turn_degrades(sim, p12_alarm):
    bad = {**GOOD_REPORT, "recommendation": {"text": "Restore flow.", "refs": ["D9"]}}
    out, _ = run(sim, [call("list_assets"), call("list_assets")] + happy_prefix(p12_alarm)
                 + [call("submit_report", **bad)])
    assert out["status"] == "degraded" and out["llm"]["attempts"] == 1 and out["report"]["recommendation"]


def test_recommendation_keeps_only_verified_fields(sim, p12_alarm):
    sneaky = {**GOOD_REPORT, "recommendation": {**GOOD_REPORT["recommendation"], "note": "Bypass the interlock."}}
    out, _ = run(sim, happy_prefix(p12_alarm) + [call("submit_report", **sneaky)])
    assert set(out["report"]["recommendation"]) == {"text", "refs"}


def test_find_alarms_gives_short_refs_that_analyze_alarm_accepts(sim, p12_alarm):
    _, llm = run(sim, [call("find_alarms", **window_args()), call("analyze_alarm", alarm_id="A1"),
                       call("analyze_alarm", alarm_id="A99"), call("ask_clarification", message="?")])
    r = tool_results(llm)
    assert "A1: " in r[0] and p12_alarm.id in r[0]
    assert "Observations:" in r[1]
    assert r[2].startswith("error:") and "A1" in r[2]


def test_short_refs_belong_to_the_latest_search_only(sim):
    out, llm = run(sim, [call("analyze_alarm", alarm_id="A1"), call("ask_clarification", message="?")])
    assert tool_results(llm)[0].startswith("error:")


def test_an_unavailable_retrieval_strategy_is_not_hidden_as_a_tool_error(sim, p12_alarm):
    from copilot.config import Settings
    from copilot.retrieval import Retriever, StrategyUnavailable

    class NoEmbedder:
        def embed_query(self, text):
            raise AssertionError("the strategy check comes first")

    deps = make_deps(sim, retriever=Retriever(Settings(), NoEmbedder()))  # the optional reranker is not installed
    deps.tool_llm = FakeToolLLM(happy_prefix(p12_alarm))
    with pytest.raises(StrategyUnavailable):  # the API turns this into 422 strategy_unavailable
        investigate(InvestigateRequest(Q, as_of=END, mode="agent", strategy="hybrid_rerank"), _p(), deps, "tb")
