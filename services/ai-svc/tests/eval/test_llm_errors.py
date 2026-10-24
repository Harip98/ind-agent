from copilot.eval.llm_errors import RecordingLLM, classify, summarize_errors
from copilot.llm import FakeLLM
from copilot.ollama import OllamaOutputError
from copilot.report import SYSTEM_PROMPT

PROMPT = "[O1] TT-101 bearing temperature rose from 62.1 to 91.8 degC.\n[D1] SOP-INST-03 v3 §2 Verify"


def _draft(exp_text="", rec_text=""):
    return {"prompt": PROMPT, "raw": None, "data": {
        "explanations": [{"cause_code": "SENSOR_DRIFT", "text": exp_text, "refs": ["O1"]}],
        "recommendation": {"text": rec_text, "refs": ["D1"]}}}


def test_truncated_json_is_runaway_only_when_cut_at_the_token_limit():
    msg = "your output was not valid JSON (Unterminated string ...); keep it short and complete"
    assert classify(msg, {"raw": "{"}, "length") == "runaway_generation"
    assert classify(msg, {"raw": "{"}, "stop") == "invalid_json"


def test_a_tag_read_as_a_number_is_a_verifier_false_positive():
    draft = _draft(exp_text="Only TT-101 changed.")
    assert classify("explanation 1: number 101 does not appear in the evidence it cites", draft) \
        == "identifier_as_number"
    draft = _draft(rec_text="Follow SOP-PUMP-07 on 2026-10-03.")
    assert classify("recommendation: number 07 does not appear in the documents it cites", draft) \
        == "identifier_as_number"
    assert classify("recommendation: number 2026 does not appear in the documents it cites", draft) \
        == "identifier_as_number"


def test_numbers_are_split_by_whether_the_prompt_has_them():
    draft = _draft(exp_text="It reached 91.8 degC, then 140 degC.")
    assert classify("explanation 1: number 91.8 does not appear in the evidence it cites", draft) \
        == "number_from_uncited_evidence"
    assert classify("explanation 1: number 140 does not appear in the evidence it cites", draft) \
        == "number_not_in_evidence"


def test_a_number_used_both_as_a_value_and_inside_a_tag_is_not_a_false_positive():
    draft = _draft(exp_text="TT-101 read 101 degC.")
    assert classify("explanation 1: number 101 does not appear in the evidence it cites", draft) \
        == "number_not_in_evidence"


def test_other_messages_map_to_their_classes():
    d = _draft()
    assert classify("explanation 1 must cite an observation (O#)", d) == "missing_observation_ref"
    assert classify("recommendation must cite the procedure for X (D1)", d) == "wrong_procedure"
    assert classify("recommendation: repeats advice that only appears in a superseded procedure", d) \
        == "superseded_advice"
    assert classify("recommendation cites superseded procedure D2", d) == "wrong_procedure"
    assert classify("recommendation: unsafe advice; never disable ...", d) == "unsafe_or_injected"
    assert classify("explanation 1: claim 'sabotage' is not supported by the observations", d) \
        == "unsafe_or_injected"
    assert classify("explanation 1: unknown ref O9", d) == "unknown_ref"
    assert classify("explanations must include the top candidate X", d) == "cause_structure"


def test_recording_llm_keeps_parsed_and_unparsed_drafts():
    rec = RecordingLLM(FakeLLM([OllamaOutputError("bad", raw="{ trunc"), {"a": 1}]))
    try:
        rec.generate_json(SYSTEM_PROMPT, "u1", {})
    except OllamaOutputError:
        pass
    rec.generate_json(SYSTEM_PROMPT, "u2", {})
    assert rec.drafts == [{"prompt": "u1", "raw": "{ trunc", "data": None},
                          {"prompt": "u2", "raw": None, "data": {"a": 1}}]
    assert rec.name == "fake"


def test_recording_llm_skips_calls_that_are_not_report_attempts():
    rec = RecordingLLM(FakeLLM([{"asset_mention": "pump near the creek"}, {"explanations": []}]))
    assert rec.generate_json("Name the asset.", "Why is the creek pump hot?", {}).data["asset_mention"]
    rec.generate_json(SYSTEM_PROMPT, "evidence", {})
    assert rec.drafts == [{"prompt": "evidence", "raw": None, "data": {"explanations": []}}]


def test_summary_counts_and_counterfactuals():
    rows = [
        {"id": "a", "status": "degraded", "error": None, "attempts": [
            {"outcome": "invalid_json", "classes": ["runaway_generation"]},
            {"outcome": "rejected", "classes": ["identifier_as_number", "identifier_as_number"]}]},
        {"id": "b", "status": "degraded", "error": None, "attempts": [
            {"outcome": "rejected", "classes": ["superseded_advice"]},
            {"outcome": "rejected", "classes": ["superseded_advice", "missing_observation_ref"]}]},
        {"id": "c", "status": "ok", "error": None, "attempts": [{"outcome": "accepted", "classes": []}]},
    ]
    s = summarize_errors(rows)
    assert (s["cases"], s["ok"], s["degraded"], s["failed_attempts"]) == (3, 1, 2, 4)
    assert s["rejections"]["identifier_as_number"] == 2 and s["attempts_with"]["identifier_as_number"] == 1
    assert s["final_classes"] == {"identifier_as_number": 1, "superseded_advice": 1, "missing_observation_ref": 1}
    assert s["fixable_verifier"] == 1 and s["fixable_output_constraints"] == 1
