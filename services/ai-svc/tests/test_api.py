from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_pipeline import FakeDataSvc, FakeRetriever

from copilot.api import create_app
from copilot.auth import issue_token
from copilot.causes import load_signatures
from copilot.config import Settings
from copilot.datasvc import DataServiceError
from copilot.llm import FakeLLM
from copilot.ollama import OllamaError
from copilot.pipeline import PipelineDeps
from copilot.simgen.generate import generate

END = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)
SECRET = Settings().jwt_secret


@pytest.fixture(scope="module")
def sim():
    return generate(END, days=30, seed=7)


def _client(sim, llm=None, datasvc=None):
    deps = PipelineDeps(datasvc=datasvc or (lambda p, t: FakeDataSvc(sim, p.tenant_id)),
                        retriever=FakeRetriever(), llm=llm, signatures=load_signatures(), settings=Settings())
    return TestClient(create_app(lambda: deps))


def _auth(tenant="acme"):
    return {"Authorization": f"Bearer {issue_token(tenant, 'u', SECRET)}"}


BODY = {"question": "Investigate yesterday's temperature alarm on Pump 12", "as_of": END.isoformat(),
        "mode": "template"}


def test_healthz(sim):
    assert _client(sim).get("/healthz").json()["status"] == "ok"


def test_investigate_requires_token(sim):
    r = _client(sim).post("/investigate", json=BODY)
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


def test_bearer_scheme_is_case_insensitive(sim):
    tok = issue_token("acme", "u", SECRET)
    r = _client(sim).post("/investigate", json=BODY, headers={"Authorization": f"bearer {tok}"})
    assert r.status_code == 200


def test_investigate_ok_with_trace_header(sim):
    r = _client(sim).post("/investigate", json=BODY, headers={**_auth(), "X-Trace-Id": "abc-1"})
    assert r.status_code == 200 and r.headers["X-Trace-Id"] == "abc-1"
    body = r.json()
    assert body["status"] == "ok" and body["trace_id"] == "abc-1"
    assert body["report"]["possible_explanations"][0]["cause_code"] == "COOLING_RESTRICTION"


@pytest.mark.parametrize("body", [{"question": ""}, {"question": "x" * 1001},
                                  {"question": "q", "mode": "magic"}])
def test_bad_body_is_422(sim, body):
    assert _client(sim).post("/investigate", json=body, headers=_auth()).status_code == 422


def test_datasvc_down_is_503(sim):
    def down(p, t):
        raise DataServiceError("data-svc unreachable")

    r = _client(sim, datasvc=down).post("/investigate", json=BODY, headers=_auth())
    assert r.status_code == 503 and r.json()["error"]["code"] == "data_unavailable"


def test_llm_down_is_503_but_template_works(sim):
    c = _client(sim, llm=FakeLLM([OllamaError("down")]))
    r = c.post("/investigate", json={**BODY, "mode": "llm"}, headers=_auth())
    assert r.status_code == 503 and r.json()["error"]["code"] == "llm_unavailable"
    assert c.post("/investigate", json=BODY, headers=_auth()).status_code == 200


def test_rate_limited_and_rejected_and_unexpected_errors_keep_the_contract(sim):
    from copilot.datasvc import RateLimited, Rejected

    for exc, status, code in [(RateLimited("x"), 429, "rate_limited"), (Rejected("x"), 500, "internal"),
                              (ZeroDivisionError("x"), 500, "internal")]:
        def boom(p, t, exc=exc):
            raise exc

        r = _client(sim, datasvc=boom).post("/investigate", json=BODY, headers={**_auth(), "X-Trace-Id": "t-9"})
        assert r.status_code == status and r.json()["error"]["code"] == code
        assert r.headers["X-Trace-Id"] == "t-9"
