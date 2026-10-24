"""Failure modes end to end: dependency outages, timeouts, malformed payloads, partial sensor streams and
retrieval misses. Each must give a defined status or error code, never a 500 or a hang."""

import dataclasses

import httpx
import pytest
from fastapi.testclient import TestClient
from test_api import BODY, END, SECRET
from test_pipeline import FakeDataSvc, FakeRetriever, Q, _good_llm_answer, _p, make_deps

from copilot.api import create_app
from copilot.auth import issue_token
from copilot.config import Settings
from copilot.datasvc import DataServiceClient, DataServiceError, NotFound
from copilot.llm import FakeLLM, OllamaLLM
from copilot.ollama import OllamaClient, OllamaError, OllamaOutputError
from copilot.pipeline import InvestigateRequest, LLMUnavailable, investigate
from copilot.retrieval import EMBED_BACKOFF_S, KnowledgeBaseUnavailable, Retriever, StrategyUnavailable
from copilot.simgen.generate import generate


@pytest.fixture(scope="module")
def sim():
    return generate(END, days=30, seed=7)


def _api(deps):
    return TestClient(create_app(lambda: deps))


def _auth():
    return {"Authorization": f"Bearer {issue_token('acme', 'u', SECRET)}"}


def _req(mode="template"):
    return InvestigateRequest(Q, as_of=END, mode=mode)


# --- Ollama -------------------------------------------------------------------------------------------------

def _ollama(handler) -> OllamaClient:
    return OllamaClient("http://ollama", transport=httpx.MockTransport(handler))


def _raise(exc):
    def handler(request):
        raise exc
    return handler


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("timed out"), httpx.ConnectError("refused"),
                                 httpx.RemoteProtocolError("server disconnected")])
def test_ollama_timeout_or_disconnect_is_503_llm_unavailable(sim, exc):
    deps = make_deps(sim, llm=OllamaLLM(_ollama(_raise(exc)), "qwen"))
    with pytest.raises(LLMUnavailable):
        investigate(_req("llm"), _p(), deps, "f1")
    r = _api(deps).post("/investigate", json={**BODY, "mode": "llm"}, headers=_auth())
    assert r.status_code == 503 and r.json()["error"]["code"] == "llm_unavailable" and r.headers["X-Trace-Id"]
    assert _api(deps).post("/investigate", json=BODY, headers=_auth()).status_code == 200  # template still works


@pytest.mark.parametrize("body", [
    {"message": {"content": ""}},                 # empty answer
    {"message": {"content": "Sure! Here is"}},    # prose instead of JSON
    {"message": {"content": '{"explanations": [{"cause_code": "COOLING_RESTRICTION", "text": "loop loop'},
     "eval_count": 512, "done_reason": "length"},  # cut off at num_predict
])
def test_malformed_model_output_is_a_failed_attempt_then_degraded(sim, body):
    deps = make_deps(sim, llm=OllamaLLM(_ollama(lambda r: httpx.Response(200, json=body)), "qwen"))
    out = investigate(_req("llm"), _p(), deps, "f2")
    assert out["status"] == "degraded" and out["llm"]["attempts"] == 2
    assert [a["outcome"] for a in out["llm"]["attempt_log"]] == ["invalid_json", "invalid_json"]
    assert out["report"]["possible_explanations"][0]["cause_code"] == "COOLING_RESTRICTION"  # template report


@pytest.mark.parametrize("data", [{"foo": 1}, {"explanations": "none", "recommendation": None}, []])
def test_valid_json_in_the_wrong_shape_is_rejected_by_the_verifier(sim, data):
    out = investigate(_req("llm"), _p(), make_deps(sim, llm=FakeLLM([data, data])), "f3")
    assert out["status"] == "degraded"
    assert "report is missing required fields" in out["llm"]["verifier_errors"]


def test_ollama_envelope_without_a_message_is_unavailability_not_a_crash(sim):
    deps = make_deps(sim, llm=OllamaLLM(_ollama(lambda r: httpx.Response(200, json={"error": "model not found"})),
                                        "qwen"))
    with pytest.raises(LLMUnavailable):
        investigate(_req("llm"), _p(), deps, "f4")


def test_ollama_failing_on_the_retry_still_reports_unavailable(sim):
    llm = FakeLLM([_good_llm_answer(recommendation={"text": "Do it.", "refs": []}), OllamaError("timed out")])
    with pytest.raises(LLMUnavailable):
        investigate(_req("llm"), _p(), make_deps(sim, llm=llm), "f5")


def test_output_error_is_never_treated_as_an_outage(sim):
    llm = FakeLLM([OllamaOutputError("bad"), _good_llm_answer()])
    assert investigate(_req("llm"), _p(), make_deps(sim, llm=llm), "f6")["status"] == "ok"


# --- data-svc -----------------------------------------------------------------------------------------------

def _ds(handler) -> DataServiceClient:
    return DataServiceClient("http://data-svc", "tok", "trace", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="<html>proxy error</html>"),           # not JSON
    httpx.Response(200, json={"tenant": {"id": "acme"}}),           # JSON, missing fields
    httpx.Response(200, json={"tenant": None, "assets": "x"}),      # JSON, wrong types
    httpx.Response(502, text="bad gateway"),
])
def test_malformed_or_failed_data_svc_response_is_data_service_error(response):
    with pytest.raises(DataServiceError):
        _ds(lambda r: response).assets()


def test_malformed_window_payload_is_data_service_error():
    with pytest.raises(DataServiceError):
        _ds(lambda r: httpx.Response(200, json={"asset_id": "P-12", "points": [{"ts": "yesterday"}]})).window(
            "P-12", "TT-101", END, END)


_WINDOW = {"asset_id": "P-12", "tag": "TT-102", "unit": "degC", "from": "2026-10-16T00:00:00Z",
           "to": "2026-10-16T01:00:00Z", "resolution_seconds": 60,
           "points": [{"ts": "2026-10-16T00:00:00Z", "avg": 41.2, "min": 41.0, "max": 41.5, "n": 60, "bad": 0}]}
_ALARM = {"id": "a", "asset_id": "P-12", "tag": "TT-102", "alarm_type": "high_temp", "severity": "medium",
          "raised_at": "2026-10-16T00:30:00Z", "cleared_at": None, "value": 85.1, "limit_value": 85.0,
          "message": "m"}


@pytest.mark.parametrize("point", [{"avg": "41.2"}, {"n": "60"}, {"bad": None}, {"min": True}, {"ts": None}])
def test_wrong_typed_window_values_are_data_service_error(point):
    body = {**_WINDOW, "points": [{**_WINDOW["points"][0], **point}]}
    with pytest.raises(DataServiceError):
        _ds(lambda r: httpx.Response(200, json=body)).window("P-12", "TT-102", END, END)
    assert _ds(lambda r: httpx.Response(200, json=_WINDOW)).window("P-12", "TT-102", END, END).points[0].avg == 41.2


@pytest.mark.parametrize("alarm", [{"raised_at": None}, {"value": "85.1"}, {"limit_value": [85]}])
def test_wrong_typed_alarm_values_are_data_service_error(alarm):
    body = {"alarms": [{**_ALARM, **alarm}], "truncated": False}
    with pytest.raises(DataServiceError):
        _ds(lambda r: httpx.Response(200, json=body)).alarms("P-12", END, END)


def test_wrong_typed_values_reach_the_client_as_503_not_500(sim):
    class StringAverages(FakeDataSvc):
        def window(self, asset_id, tag, start, end):
            body = {**_WINDOW, "points": [{**_WINDOW["points"][0], "avg": "41.2"}]}
            return _ds(lambda r: httpx.Response(200, json=body)).window(asset_id, tag, start, end)

    deps = make_deps(sim)
    deps.datasvc = lambda p, t: StringAverages(sim, p.tenant_id)
    r = _api(deps).post("/investigate", json=BODY, headers=_auth())
    assert r.status_code == 503 and r.json()["error"]["code"] == "data_unavailable"


def test_data_svc_timeout_is_503_data_unavailable(sim):
    deps = make_deps(sim)
    deps.datasvc = lambda p, t: _ds(_raise(httpx.ReadTimeout("timed out")))
    r = _api(deps).post("/investigate", json=BODY, headers=_auth())
    assert r.status_code == 503 and r.json()["error"]["code"] == "data_unavailable"


def test_data_svc_dying_mid_investigation_is_503_not_a_partial_report(sim):
    class DiesOnWindows(FakeDataSvc):
        def window(self, *a):
            raise DataServiceError("data-svc returned 500")

    deps = make_deps(sim)
    deps.datasvc = lambda p, t: DiesOnWindows(sim, p.tenant_id)
    r = _api(deps).post("/investigate", json=BODY, headers=_auth())
    assert r.status_code == 503 and "report" not in r.json()


# --- partial sensor streams ---------------------------------------------------------------------------------

class PartialDataSvc(FakeDataSvc):
    """Drops some sensors' windows (404), empties others (no points) and thins one to every k-th point."""

    def __init__(self, sim, tenant, missing=(), empty=(), thin=None):
        super().__init__(sim, tenant)
        self.missing, self.empty, self.thin = set(missing), set(empty), thin or {}

    def window(self, asset_id, tag, start, end):
        if tag in self.missing:
            raise NotFound(f"{asset_id}/{tag}")
        w = super().window(asset_id, tag, start, end)
        if tag in self.empty:
            return dataclasses.replace(w, points=())
        if tag in self.thin:
            return dataclasses.replace(w, points=w.points[::self.thin[tag]])
        return w


def _partial(sim, **kw):
    deps = make_deps(sim)
    deps.datasvc = lambda p, t: PartialDataSvc(sim, p.tenant_id, **kw)
    return deps


@pytest.mark.parametrize("kw", [
    {"empty": {"FT-101", "IT-101"}},            # related streams stopped
    {"missing": {"VT-101"}},                    # a related sensor is gone from data-svc
    {"thin": {"TT-101": 7, "TT-102": 13}},      # sparse, irregular samples
    {"empty": {"TT-101", "FT-101", "PT-101", "IT-101", "VT-101"}},  # only the alarmed TT-102 left
])
def test_partial_sensor_streams_still_give_a_report(sim, kw):
    out = investigate(_req(), _p(), _partial(sim, **kw), "f7")
    assert out["status"] == "ok"
    assert out["report"]["observations"] and out["report"]["summary"]


def test_missing_related_streams_are_said_out_loud(sim):
    out = investigate(_req(), _p(), _partial(sim, missing={"VT-101"}, empty={"FT-101"}), "f8")
    texts = " ".join(o["text"] for o in out["report"]["observations"])
    assert "VT-101" in texts and "FT-101" in texts


def test_cooling_restriction_survives_losing_an_unrelated_stream(sim):
    out = investigate(_req(), _p(), _partial(sim, missing={"VT-101"}), "f9")
    assert out["report"]["possible_explanations"][0]["cause_code"] == "COOLING_RESTRICTION"


def test_alarmed_stream_empty_points_at_the_instrument_not_a_guess(sim):
    out = investigate(_req(), _p(), _partial(sim, empty={"TT-102"}), "f10")  # TT-102 is the alarmed tag
    assert out["status"] == "ok"
    codes = [e["cause_code"] for e in out["report"]["possible_explanations"]]
    assert codes[:1] == ["SENSOR_FAILURE"]


def test_alarmed_stream_missing_is_503_data_unavailable(sim):
    with pytest.raises(NotFound):
        investigate(_req(), _p(), _partial(sim, missing={"TT-102"}), "f10b")
    r = _api(_partial(sim, missing={"TT-102"})).post("/investigate", json=BODY, headers=_auth())
    assert r.status_code == 503 and r.json()["error"]["code"] == "data_unavailable" and "report" not in r.json()


# --- retrieval ----------------------------------------------------------------------------------------------

class EmptyRetriever(FakeRetriever):
    def search(self, *a, **k):
        super().search(*a, **k)
        return []


@pytest.mark.parametrize("mode", ["template", "llm"])
def test_retrieval_miss_escalates_instead_of_inventing_a_procedure(sim, mode):
    llm = FakeLLM([_good_llm_answer(recommendation={"text": "Escalate to the responsible engineer.",
                                                     "refs": []})])
    out = investigate(_req(mode), _p(), make_deps(sim, llm=llm, retriever=EmptyRetriever()), "f11")
    assert out["status"] == "ok" and out["citations"] == []
    assert out["report"]["recommendation"]["refs"] == []
    assert any("No current procedure" in w for w in out["report"]["insufficient_evidence"])
    if mode == "template":
        assert "escalate" in out["report"]["recommendation"]["text"]


def test_llm_citing_a_document_after_a_retrieval_miss_is_rejected(sim):
    bad = _good_llm_answer()  # cites D1, which does not exist
    out = investigate(_req("llm"), _p(), make_deps(sim, llm=FakeLLM([bad, bad]), retriever=EmptyRetriever()), "f12")
    assert out["status"] == "degraded" and any("unknown ref D1" in e for e in out["llm"]["verifier_errors"])


class DownEmbedder:
    calls = 0

    def embed_query(self, text):
        self.calls += 1
        raise OllamaError("embedding timed out")


def test_a_failed_embedding_is_not_retried_for_every_search(monkeypatch):
    import copilot.retrieval as retrieval

    now = [1000.0]
    monkeypatch.setattr(retrieval.time, "monotonic", lambda: now[0])
    emb = DownEmbedder()
    r = Retriever(Settings(), emb)
    assert r._query_vector("general") is None and r._query_vector("targeted") is None
    assert emb.calls == 1  # one timeout per investigation, not one per search
    now[0] += EMBED_BACKOFF_S + 1
    assert r._query_vector("later") is None and emb.calls == 2


def test_rerank_strategy_without_the_reranker_is_422_not_500(sim):
    with pytest.raises(StrategyUnavailable):
        Retriever(Settings(), DownEmbedder()).search("acme", "q", strategy="hybrid_rerank")
    deps = make_deps(sim, retriever=Retriever(Settings(), DownEmbedder()))
    r = _api(deps).post("/investigate", json={**BODY, "strategy": "hybrid_rerank"}, headers=_auth())
    assert r.status_code == 422 and r.json()["error"]["code"] == "strategy_unavailable"


def test_knowledge_base_down_is_503_knowledge_base_unavailable(sim):
    class DownRetriever(FakeRetriever):
        def search(self, *a, **k):
            raise KnowledgeBaseUnavailable("connection refused")

    r = _api(make_deps(sim, retriever=DownRetriever())).post("/investigate", json=BODY, headers=_auth())
    assert r.status_code == 503 and r.json()["error"]["code"] == "knowledge_base_unavailable"


@pytest.mark.parametrize("tz", [None, 5, "Mars/Olympus_Mons", ""])
def test_a_bad_tenant_time_zone_is_data_service_error(tz):
    body = {"tenant": {"id": "acme", "name": "Acme", "timezone": tz}, "assets": []}
    with pytest.raises(DataServiceError):
        _ds(lambda r: httpx.Response(200, json=body)).assets()


def test_extra_tenant_fields_are_ignored():
    body = {"tenant": {"id": "acme", "name": "Acme", "timezone": "Asia/Kolkata", "plan": "x"}, "assets": []}
    assert _ds(lambda r: httpx.Response(200, json=body)).assets()[0].timezone == "Asia/Kolkata"
