"""FastAPI app: POST /investigate, GET /healthz, GET /readyz. Handlers are sync, so FastAPI runs them in a
thread pool."""

import ipaddress
import json
import re
import secrets
from collections.abc import Callable
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from copilot.auth import TokenError, issue_token, verify_token
from copilot.config import REPO_ROOT, get_settings
from copilot.datasvc import DataServiceClient, DataServiceError, RateLimited, Rejected
from copilot.logjson import get_logger
from copilot.pipeline import InvestigateRequest, LLMUnavailable, PipelineDeps, investigate
from copilot.retrieval import KnowledgeBaseUnavailable, StrategyUnavailable

_TRACE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
WEB_DIR = Path(__file__).with_name("web")
LOOPBACK = frozenset({"127.0.0.1", "::1"})
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}


class DevTokenBody(BaseModel):
    tenant: str = Field(min_length=1, max_length=32)


class InvestigateBody(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    as_of: datetime | None = None
    mode: Literal["llm", "template", "agent"] = "llm"
    strategy: Literal["vector", "hybrid", "hybrid_rerank"] | None = None
    asset_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,32}$")
    alarm_id: str | None = Field(default=None, max_length=140)


def default_deps() -> PipelineDeps:
    from copilot.causes import load_signatures
    from copilot.kb.embed import OllamaEmbedder
    from copilot.llm import OllamaLLM, OllamaToolLLM
    from copilot.ollama import OllamaClient
    from copilot.retrieval import Retriever
    from copilot.retrieval.rerank import optional_reranker

    s = get_settings()
    embed_client = OllamaClient(s.ollama_url, s.embed_timeout_s)
    llm_client = OllamaClient(s.ollama_url, s.llm_timeout_s)
    return PipelineDeps(
        datasvc=lambda principal, trace: DataServiceClient(s.data_svc_url, principal.token, trace),
        retriever=Retriever(s, OllamaEmbedder(embed_client, s.embed_model), optional_reranker(s.reranker_model)),
        llm=OllamaLLM(llm_client, s.llm_model, s.llm_num_ctx),
        signatures=load_signatures(),
        settings=s,
        tool_llm=OllamaToolLLM(llm_client, s.llm_model, s.agent_num_ctx),
    )


def check_dependencies() -> dict:
    """Readiness: can a request be answered right now, and in which modes?"""
    import httpx
    import psycopg

    s = get_settings()
    checks: dict[str, dict] = {}
    try:
        r = httpx.get(f"{s.data_svc_url}/healthz", timeout=2.0)
        checks["data_svc"] = {"ok": r.status_code == 200}
    except httpx.HTTPError as exc:
        checks["data_svc"] = {"ok": False, "error": type(exc).__name__}
    try:
        with psycopg.connect(s.dsn("kb_reader"), connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        checks["knowledge_base"] = {"ok": True}
    except psycopg.Error as exc:
        checks["knowledge_base"] = {"ok": False, "error": type(exc).__name__}
    try:
        tags = httpx.get(f"{s.ollama_url}/api/tags", timeout=2.0).json().get("models", [])
        names = {m.get("name", "") for m in tags} | {m.get("name", "").removesuffix(":latest") for m in tags}
        checks["ollama"] = {"ok": True, "llm_model": s.llm_model in names, "embed_model": s.embed_model in names}
    except (httpx.HTTPError, ValueError) as exc:
        checks["ollama"] = {"ok": False, "error": type(exc).__name__}
    return checks


def readiness(checks: dict) -> tuple[int, dict]:
    """ready: every mode works at full quality. degraded: something is reduced, either the LLM is missing (template
    mode only) or the embedding model is (keyword-only retrieval, every mode still answers). unavailable: data-svc
    or the knowledge base is down, so nothing can be answered."""
    if not (checks["data_svc"]["ok"] and checks["knowledge_base"]["ok"]):
        return 503, {"status": "unavailable", "modes": [], "retrieval": None, "checks": checks}
    ollama = checks["ollama"]
    llm_ok = bool(ollama["ok"] and ollama.get("llm_model"))
    embed_ok = bool(ollama["ok"] and ollama.get("embed_model"))
    return 200, {"status": "ready" if llm_ok and embed_ok else "degraded",
                 "modes": ["llm", "template", "agent"] if llm_ok else ["template"],
                 "retrieval": "hybrid" if embed_ok else "keyword_only", "checks": checks}


def _error(status: int, code: str, message: str, trace: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status,
                        headers={"X-Trace-Id": trace})


def default_gateways(route_file: Path = Path("/proc/net/route")) -> frozenset[str]:
    """IPv4 default gateways from the Linux routing table; inside a container, the Docker network's gateway.
    Connections to a port published on the host arrive from it (docker-proxy / Docker Desktop forwarding)."""
    try:
        rows = route_file.read_text(encoding="ascii").splitlines()[1:]
    except OSError:
        return frozenset()
    gateways = set()
    for row in rows:
        f = row.split()
        if len(f) > 2 and f[1] == "00000000" and f[2] != "00000000":  # destination 0.0.0.0 = default route
            gateways.add(str(ipaddress.IPv4Address(bytes.fromhex(f[2])[::-1])))  # little-endian hex
    return frozenset(gateways)


def create_app(deps_factory: Callable[[], PipelineDeps] | None = None, dev: bool = False,
               dev_docker: bool = False, checks: Callable[[], dict] = check_dependencies,
               gateways: Callable[[], frozenset[str]] = default_gateways) -> FastAPI:
    """dev=True (only from `copilot serve --dev`) enables demo token minting for loopback clients;
    dev_docker=True (`--dev-docker`, refused outside the image) also for the container's Docker gateway, which is
    where the host's requests to the published 127.0.0.1 port come from. LAN clients are not trusted."""
    app = FastAPI(title="ind-agent copilot", version="0.1.0")
    deps = lru_cache(maxsize=1)(deps_factory or default_deps)
    log = get_logger()
    trusted = LOOPBACK | (gateways() if dev_docker else frozenset())

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    if dev:  # a browser page on another origin must not reach /dev/token through DNS rebinding
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        code, body = readiness(checks())
        return JSONResponse(body, status_code=code)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html", media_type="text/html")

    def _dev_only(request: Request) -> JSONResponse | None:
        if not dev or request.client is None or request.client.host not in trusted:
            return JSONResponse({"error": {"code": "not_found", "message": "not found"}}, status_code=404)
        return None

    @app.get("/dev/defaults", include_in_schema=False)
    def dev_defaults(request: Request):
        if blocked := _dev_only(request):
            return blocked
        meta = REPO_ROOT / "data" / "simgen_run.json"
        as_of = json.loads(meta.read_text(encoding="utf-8"))["end"] if meta.exists() else None
        return {"tenants": get_settings().dev_tenants, "as_of": as_of}

    @app.post("/dev/token", include_in_schema=False)
    def dev_token(body: DevTokenBody, request: Request):
        if blocked := _dev_only(request):
            return blocked
        s = get_settings()
        if body.tenant not in s.dev_tenants:
            return JSONResponse({"error": {"code": "unknown_tenant", "message": "unknown tenant"}}, status_code=400)
        log.warning("dev token issued", extra={"fields": {"tenant": body.tenant}})
        return {"token": issue_token(body.tenant, "demo-user", s.jwt_secret)}

    @app.post("/investigate")
    def post_investigate(body: InvestigateBody, authorization: str | None = Header(default=None),
                         x_trace_id: str | None = Header(default=None)):
        trace = x_trace_id if x_trace_id and _TRACE.match(x_trace_id) else secrets.token_hex(16)
        scheme, _, token = (authorization or "").partition(" ")
        try:
            if scheme.lower() != "bearer":
                raise TokenError("missing bearer token")
            principal = verify_token(token.strip(), get_settings().jwt_secret)
        except TokenError as exc:
            log.info("unauthorized", extra={"fields": {"trace_id": trace, "reason": str(exc)}})
            return _error(401, "unauthorized", "missing or invalid token", trace)
        req = InvestigateRequest(body.question, body.as_of, body.mode, body.strategy, body.asset_id, body.alarm_id)
        try:
            result = investigate(req, principal, deps(), trace)
        except RateLimited as exc:
            log.warning("rate limited", extra={"fields": {"trace_id": trace, "err": str(exc)}})
            return _error(429, "rate_limited", "too many requests; retry shortly", trace)
        except Rejected as exc:
            log.error("data-svc rejected request", extra={"fields": {"trace_id": trace, "err": str(exc)}})
            return _error(500, "internal", "internal error", trace)
        except DataServiceError as exc:
            log.warning("data unavailable", extra={"fields": {"trace_id": trace, "err": str(exc)}})
            return _error(503, "data_unavailable", "operational data service unavailable", trace)
        except StrategyUnavailable as exc:
            return _error(422, "strategy_unavailable", str(exc), trace)
        except KnowledgeBaseUnavailable as exc:
            log.warning("knowledge base unavailable", extra={"fields": {"trace_id": trace, "err": str(exc)}})
            return _error(503, "knowledge_base_unavailable", "procedure knowledge base unavailable", trace)
        except LLMUnavailable as exc:
            log.warning("llm unavailable", extra={"fields": {"trace_id": trace, "err": str(exc)}})
            return _error(503, "llm_unavailable", "language model unavailable; retry with mode=template", trace)
        except Exception as exc:  # keep the error contract and the trace id for anything unexpected
            log.exception("unhandled error", extra={"fields": {"trace_id": trace, "err": repr(exc)}})
            return _error(500, "internal", "internal error", trace)
        log.info("investigate", extra={"fields": {
            "trace_id": trace, "tenant": principal.tenant_id, "status": result["status"], "mode": body.mode,
            "timings_ms": result["timings_ms"], "llm": result["llm"],
            "citations": [f"{c['ref']}={c['doc_id']}@v{c['version']}#{c['section']}" for c in result["citations"]],
            "asset": result["query"].get("asset_id"), "alarm": result["query"].get("alarm_id")}})
        return JSONResponse(result, headers={"X-Trace-Id": trace})

    return app
