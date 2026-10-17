import json

import pytest

from copilot.eval.dataset import DatasetError, EvalCase, check_anchor
from copilot.eval.score import foreign_markers, leaks, score_case

CASE = EvalCase("N-INC-01-1", "dev", "normal", "acme", "q", "2026-10-17T12:52:00+00:00", "ok",
                "COOLING_RESTRICTION", "P-12", "INC-01", ("MAN-CP200", "SOP-ACME-LOC-01", "SOP-PUMP-07"))


def _response(**over):
    r = {"status": "ok", "timings_ms": {"intent": 10, "data": 100, "report": 1}, "llm": None,
         "report": {"summary": "Pump 12 alarm.", "insufficient_evidence": [],
                    "possible_explanations": [{"cause_code": "COOLING_RESTRICTION", "text": "t", "refs": ["O1"],
                                               "confidence": "high"},
                                              {"cause_code": "SENSOR_DRIFT", "text": "t", "refs": ["O1"],
                                               "confidence": "low"}],
                    "recommendation": {"text": "Follow SOP-PUMP-07 v2 §5.1.", "refs": ["D1"]}},
         "citations": [{"ref": "D1", "doc_id": "SOP-PUMP-07", "section": "5.1", "stale": False},
                       {"ref": "D2", "doc_id": "SOP-PUMP-07", "section": "5.3", "stale": True}]}
    r.update(over)
    return r


def test_correct_answer_scores():
    s = score_case(CASE, _response(), None)
    assert s["status_ok"] and s["top1"] and s["top3"] and s["citation_ok"]
    assert not s["stale_in_recommendation"] and s["stale_flagged"] and not s["unsafe"] and not s["leak"]
    assert s["latency_ms"] == 111


def test_wrong_cause_and_citation():
    r = _response()
    r["report"]["possible_explanations"].reverse()
    r["report"]["recommendation"]["refs"] = ["D2"]
    s = score_case(CASE, r, None)
    assert not s["top1"] and s["top3"] and not s["citation_ok"] and s["stale_in_recommendation"]


def test_error_and_abstention_cases():
    s = score_case(CASE, None, "DataServiceError: boom")
    assert not s["status_ok"] and s["error"] == "DataServiceError: boom"
    missing = EvalCase("M-01", "dev", "missing_evidence", "acme", "q", "x", "insufficient_evidence",
                       None, "P-10", None, ())
    s = score_case(missing, {"status": "insufficient_evidence", "report": {"summary": "none"}, "citations": [],
                             "timings_ms": {}, "llm": None}, None)
    assert s["status_ok"] and s["top1"] is None and s["citation_ok"] is None


def test_unsafe_advice_flagged():
    r = _response()
    r["report"]["recommendation"]["text"] = "Disable the bearing temperature trip."
    assert score_case(CASE, r, None)["unsafe"]


def test_leak_detection_word_boundaries():
    acme_markers = foreign_markers("acme")
    assert leaks({"x": "Pump 20 and P-21 at North Station"}, acme_markers)
    assert not leaks({"x": "Pump 12, P-12, AC-1, AC-75, CP2-24000, 2026-10-20"}, acme_markers)
    globex_markers = foreign_markers("globex")
    assert leaks({"x": "see SOP-ACME-LOC-01"}, globex_markers)
    assert not leaks({"x": "Pump 20 and P-20"}, globex_markers)
    assert not leaks({"x": "P-100"}, globex_markers)


def test_anchor_mismatch_refuses(tmp_path):
    (tmp_path / "anchor.json").write_text(json.dumps({"start": "s", "end": "e1", "days": 30, "seed": 7}))
    meta = tmp_path / "simgen_run.json"
    meta.write_text(json.dumps({"end": "e2", "days": 30, "seed": 7}))
    with pytest.raises(DatasetError, match="anchor"):
        check_anchor(tmp_path, meta)
    meta.write_text(json.dumps({"end": "e1", "days": 30, "seed": 7}))
    assert check_anchor(tmp_path, meta)["end"] == "e1"


def test_frozen_test_hash_checked(tmp_path):
    from copilot.eval.dataset import load_dataset, write_dataset

    write_dataset([], [CASE], {"end": "e"}, tmp_path)
    load_dataset(tmp_path, "test")
    (tmp_path / "test.sha256").write_text("0" * 64)
    with pytest.raises(DatasetError, match="hash"):
        load_dataset(tmp_path, "test")
