from datetime import UTC, datetime

import pytest
from simwindows import asset_info, to_alarm_info, window

from copilot.auth import Principal
from copilot.causes import load_signatures
from copilot.config import Settings
from copilot.datasvc import NotFound, TenantInfo
from copilot.llm import FakeLLM
from copilot.ollama import OllamaError
from copilot.pipeline import InvestigateRequest, LLMUnavailable, PipelineDeps, investigate
from copilot.retrieval import Hit
from copilot.simgen.catalog import ASSETS, TENANTS
from copilot.simgen.generate import generate

END = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def sim():
    return generate(END, days=30, seed=7)


class FakeDataSvc:
    """data-svc semantics over a SimResult for one tenant: unknown/foreign assets -> NotFound."""

    def __init__(self, sim, tenant):
        self.sim, self.tenant = sim, tenant

    def assets(self):
        t = next(x for x in TENANTS if x.id == self.tenant)
        return TenantInfo(t.id, t.name, t.timezone), [asset_info(self.tenant, a.id) for a in ASSETS
                                                      if a.tenant_id == self.tenant]

    def _check(self, asset_id):
        if not any(a.tenant_id == self.tenant and a.id == asset_id for a in ASSETS):
            raise NotFound(asset_id)

    def alarms(self, asset_id, start, end):
        self._check(asset_id)
        return [to_alarm_info(a) for a in self.sim.alarms
                if a.tenant_id == self.tenant and a.asset_id == asset_id and start <= a.raised_at < end], False

    def window(self, asset_id, tag, start, end):
        self._check(asset_id)
        return window(self.sim, self.tenant, asset_id, tag, start, end)


class FakeRetriever:
    def __init__(self):
        self.calls = []

    def search(self, tenant_id, query, asset_model=None, k=5, strategy="hybrid"):
        self.calls.append((tenant_id, query, asset_model, strategy))
        return [Hit("SOP-PUMP-07@v2#5.1/c0", "SOP-PUMP-07@v2#5.1", "SOP-PUMP-07", 2, "5.1",
                    "Cooling Water Restriction", "CP-200 Pump Bearing Temperature High",
                    "Restore cooling water flow (minimum 1.2 m3/h).", "_shared", True, 0.9)]


def make_deps(sim, llm=None, retriever=None):
    return PipelineDeps(datasvc=lambda principal, trace: FakeDataSvc(sim, principal.tenant_id),
                        retriever=retriever or FakeRetriever(), llm=llm, signatures=load_signatures(),
                        settings=Settings())


def _p(tenant="acme"):
    return Principal(tenant, "u", ("engineer",), "tok")


def _good_llm_answer(**over):
    r = {"summary": "Pump 12 bearing temperature alarm with rising cooling water return temperature.",
         "explanations": [{"cause_code": "COOLING_RESTRICTION", "text": "Cooling water return rose.",
                           "refs": ["O1"], "confidence": "high"}],
         "recommendation": {"text": "Restore cooling water flow per SOP-PUMP-07.", "refs": ["D1"]}}
    r.update(over)
    return r


Q = "Investigate yesterday's temperature alarm on Pump 12"


def test_demo_question_template_mode(sim):
    out = investigate(InvestigateRequest(Q, as_of=END, mode="template"), _p(), make_deps(sim), "t1")
    assert out["status"] == "ok"
    assert out["query"]["asset_id"] == "P-12" and out["query"]["tag"] in {"TT-101", "TT-102"}  # earliest of the event
    assert out["report"]["possible_explanations"][0]["cause_code"] == "COOLING_RESTRICTION"
    assert out["citations"][0]["doc_id"] == "SOP-PUMP-07"
    assert "TT-102" in out["evidence"]["series"]
    assert out["llm"] is None and set(out["timings_ms"]) >= {"intent", "data", "analyze", "retrieve", "report"}


def test_llm_mode_verified_answer(sim):
    llm = FakeLLM([_good_llm_answer()])
    out = investigate(InvestigateRequest(Q, as_of=END), _p(), make_deps(sim, llm=llm), "t2")
    assert out["status"] == "ok" and out["llm"]["attempts"] == 1
    assert "<documents>" in llm.calls[0][1]


def test_verifier_failure_twice_degrades_to_template(sim):
    bad = _good_llm_answer(summary="Temperature reached 123.4 degC.")
    out = investigate(InvestigateRequest(Q, as_of=END), _p(), make_deps(sim, llm=FakeLLM([bad, bad])), "t3")
    assert out["status"] == "degraded" and out["llm"]["attempts"] == 2
    assert out["llm"]["verifier_errors"]
    assert out["report"]["possible_explanations"][0]["cause_code"] == "COOLING_RESTRICTION"


def test_retry_succeeds_after_one_rejection(sim):
    bad = _good_llm_answer(summary="Temperature reached 123.4 degC.")
    llm = FakeLLM([bad, _good_llm_answer()])
    out = investigate(InvestigateRequest(Q, as_of=END), _p(), make_deps(sim, llm=llm), "t4")
    assert out["status"] == "ok" and out["llm"]["attempts"] == 2
    assert "123.4" in llm.calls[1][1]


def test_llm_down_raises_llm_unavailable(sim):
    with pytest.raises(LLMUnavailable):
        investigate(InvestigateRequest(Q, as_of=END), _p(), make_deps(sim, llm=FakeLLM([OllamaError("down")])), "t5")


def test_cross_tenant_asset_is_clarification(sim):
    out = investigate(InvestigateRequest(Q, as_of=END, mode="template"), _p("globex"), make_deps(sim), "t6")
    assert out["status"] == "needs_clarification"
    assert "Pump 12" not in str(out["clarification"]["options"])
    assert "P-12" not in str(out)


def test_no_matching_alarm_is_insufficient_evidence(sim):
    out = investigate(InvestigateRequest("vibration alarm on Pump 12 yesterday", as_of=END, mode="template"),
                      _p(), make_deps(sim), "t7")
    assert out["status"] == "insufficient_evidence"
    assert out["report"]["insufficient_evidence"]
    assert any(a["alarm_type"] == "high_temp" for a in out["evidence"]["alarms"])


def test_multiple_events_need_clarification(sim):
    out = investigate(InvestigateRequest("temperature alarm on Pump 12 in the last 14 days", as_of=END,
                                         mode="template"), _p(), make_deps(sim), "t8")
    assert out["status"] == "needs_clarification"
    assert len(out["clarification"]["options"]) >= 2


def test_assumed_time_window_is_reported(sim):
    out = investigate(InvestigateRequest("why did the cooling loop 1 alarm", as_of=END, mode="template"),
                      _p(), make_deps(sim), "t9")
    assert "last 7 days" in " ".join(out["query"]["assumptions"])


def test_retrieval_uses_principal_tenant_and_model(sim):
    r = FakeRetriever()
    investigate(InvestigateRequest(Q, as_of=END, mode="template"), _p(), make_deps(sim, retriever=r), "t10")
    assert r.calls[0][0] == "acme" and r.calls[0][2] == "CP-200" and r.calls[0][3] == "hybrid"
