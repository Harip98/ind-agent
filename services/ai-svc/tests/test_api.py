from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_pipeline import FakeDataSvc, FakeRetriever

from copilot.api import WEB_DIR, create_app, default_gateways
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


def test_ui_is_served(sim):
    c = _client(sim)
    r = c.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "ind-agent" in r.text
    assert c.get("/static/app.js").status_code == 200 and c.get("/static/style.css").status_code == 200


def test_dev_endpoints_disabled_by_default(sim):
    c = _client(sim)
    assert c.get("/dev/defaults").status_code == 404
    assert c.post("/dev/token", json={"tenant": "acme"}).status_code == 404


def _dev_client(sim, host="127.0.0.1", client_ip="127.0.0.1", docker=False):
    deps = PipelineDeps(datasvc=lambda p, t: FakeDataSvc(sim, p.tenant_id), retriever=FakeRetriever(), llm=None,
                        signatures=load_signatures(), settings=Settings())
    return TestClient(create_app(lambda: deps, dev=True, dev_docker=docker,
                                 gateways=lambda: frozenset({"172.18.0.1"})),
                      base_url=f"http://{host}", client=(client_ip, 50000))


def test_dev_endpoints_in_dev_mode(sim):
    c = _dev_client(sim)
    d = c.get("/dev/defaults").json()
    assert d["tenants"] == ["acme", "globex"] and "as_of" in d
    tok = c.post("/dev/token", json={"tenant": "acme"}).json()["token"]
    assert c.post("/investigate", json=BODY, headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert c.post("/dev/token", json={"tenant": "evil"}).status_code == 400


def test_dev_mode_cannot_be_enabled_by_environment(sim, monkeypatch):
    monkeypatch.setenv("COPILOT_DEV_MODE", "true")
    from copilot.config import get_settings

    get_settings.cache_clear()
    try:
        assert _client(sim).get("/dev/defaults").status_code == 404
    finally:
        get_settings.cache_clear()


def test_dev_endpoints_refuse_non_loopback_clients_and_foreign_hosts(sim):
    assert _dev_client(sim, client_ip="192.168.1.20").post("/dev/token", json={"tenant": "acme"}).status_code == 404
    assert _dev_client(sim, host="attacker.example").post("/dev/token", json={"tenant": "acme"}).status_code == 400


@pytest.mark.parametrize("client_ip, code", [
    ("172.18.0.1", 200),    # the Docker gateway: the host, through the port published on 127.0.0.1
    ("127.0.0.1", 200),
    ("172.18.0.5", 404),    # another container on the network
    ("192.168.1.20", 404),  # a LAN peer that found a route to the container
    ("8.8.8.8", 404),
])
def test_dev_docker_trusts_only_the_docker_gateway(sim, client_ip, code):
    c = _dev_client(sim, client_ip=client_ip, docker=True)
    assert c.post("/dev/token", json={"tenant": "acme"}).status_code == code


def test_dev_docker_still_refuses_foreign_host_headers(sim):
    assert _dev_client(sim, host="attacker.example", client_ip="172.18.0.1", docker=True).post(
        "/dev/token", json={"tenant": "acme"}).status_code == 400


def test_default_gateways_reads_the_linux_routing_table(tmp_path):
    routes = tmp_path / "route"
    rows = [["Iface", "Destination", "Gateway", "Flags"],
            ["eth0", "00000000", "010012AC", "0003"],  # default route via 172.18.0.1 (little-endian hex)
            ["eth0", "000012AC", "00000000", "0001"]]  # the local network, no gateway
    routes.write_text("".join("\t".join(r) + "\n" for r in rows), encoding="ascii")
    assert default_gateways(routes) == frozenset({"172.18.0.1"})
    assert default_gateways(tmp_path / "missing") == frozenset()


def _ok(**kw):
    return {"data_svc": {"ok": True}, "knowledge_base": {"ok": True},
            "ollama": {"ok": True, "llm_model": True, "embed_model": True}} | kw


@pytest.mark.parametrize("checks,code,status,modes,retrieval", [
    (_ok(), 200, "ready", ["llm", "template", "agent"], "hybrid"),
    (_ok(ollama={"ok": False, "error": "ConnectError"}), 200, "degraded", ["template"], "keyword_only"),
    (_ok(ollama={"ok": True, "llm_model": False, "embed_model": True}), 200, "degraded", ["template"], "hybrid"),
    # no embedding model: retrieval falls back to keywords, but every mode still answers
    (_ok(ollama={"ok": True, "llm_model": True, "embed_model": False}), 200, "degraded",
     ["llm", "template", "agent"], "keyword_only"),
    (_ok(data_svc={"ok": False, "error": "ConnectError"}), 503, "unavailable", [], None),
    (_ok(knowledge_base={"ok": False, "error": "OperationalError"}), 503, "unavailable", [], None),
])
def test_readyz_reports_which_modes_work(sim, checks, code, status, modes, retrieval):
    deps = PipelineDeps(datasvc=lambda p, t: FakeDataSvc(sim, p.tenant_id), retriever=FakeRetriever(), llm=None,
                        signatures=load_signatures(), settings=Settings())
    r = TestClient(create_app(lambda: deps, checks=lambda: checks)).get("/readyz")
    assert r.status_code == code
    body = r.json()
    assert (body["status"], body["modes"], body["retrieval"], body["checks"]) == (status, modes, retrieval, checks)


def test_security_headers_and_no_third_party_assets(sim):
    r = _client(sim).get("/")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-cache"
    assert "googleapis" not in r.text and "cdnjs" not in r.text
    assert _client(sim).get("/static/chart.umd.min.js").status_code == 200


def test_ui_never_uses_innerhtml():
    from copilot.api import WEB_DIR

    assert "innerHTML" not in (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_ui_sets_no_inline_style_attributes():
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "style:" not in js and 'setAttribute("style"' not in js  # blocked by style-src 'self'


def test_agent_mode_is_accepted_and_reports_llm_down_as_503(sim):
    from copilot.llm import FakeToolLLM

    deps = PipelineDeps(datasvc=lambda p, t: FakeDataSvc(sim, p.tenant_id), retriever=FakeRetriever(), llm=None,
                        signatures=load_signatures(), settings=Settings(),
                        tool_llm=FakeToolLLM([{"role": "assistant", "content": "", "tool_calls": [
                            {"function": {"name": "ask_clarification", "arguments": {"message": "Which pump?"}}}]},
                            OllamaError("down")]))
    c = TestClient(create_app(lambda: deps))
    r = c.post("/investigate", json={**BODY, "mode": "agent"}, headers=_auth())
    assert r.status_code == 200 and r.json()["status"] == "needs_clarification" and r.json()["mode"] == "agent"
    r = c.post("/investigate", json={**BODY, "mode": "agent"}, headers=_auth())
    assert r.status_code == 503 and r.json()["error"]["code"] == "llm_unavailable"


def test_default_deps_build_a_tool_llm():
    from copilot.api import default_deps

    assert default_deps().tool_llm is not None
