"""FastAPI app: POST /investigate, GET /healthz. Handlers are sync, so FastAPI runs them in a thread pool."""

import re
import secrets
from collections.abc import Callable
from datetime import datetime
from functools import lru_cache
from typing import Literal

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from copilot.auth import TokenError, verify_token
from copilot.config import get_settings
from copilot.datasvc import DataServiceClient, DataServiceError, RateLimited, Rejected
from copilot.logjson import get_logger
from copilot.pipeline import InvestigateRequest, LLMUnavailable, PipelineDeps, investigate

_TRACE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


class InvestigateBody(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    as_of: datetime | None = None
    mode: Literal["llm", "template"] = "llm"
    strategy: Literal["vector", "hybrid", "hybrid_rerank"] | None = None


def default_deps() -> PipelineDeps:
    from copilot.causes import load_signatures
    from copilot.kb.embed import OllamaEmbedder
    from copilot.llm import OllamaLLM
    from copilot.ollama import OllamaClient
    from copilot.retrieval import Retriever
    from copilot.retrieval.rerank import CrossEncoderReranker

    s = get_settings()
    embed_client = OllamaClient(s.ollama_url, s.ollama_timeout_s)
    llm_client = OllamaClient(s.ollama_url, s.llm_timeout_s)
    return PipelineDeps(
        datasvc=lambda principal, trace: DataServiceClient(s.data_svc_url, principal.token, trace),
        retriever=Retriever(s, OllamaEmbedder(embed_client, s.embed_model), CrossEncoderReranker(s.reranker_model)),
        llm=OllamaLLM(llm_client, s.llm_model, s.llm_num_ctx),
        signatures=load_signatures(),
        settings=s,
    )


def _error(status: int, code: str, message: str, trace: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status,
                        headers={"X-Trace-Id": trace})


def create_app(deps_factory: Callable[[], PipelineDeps] | None = None) -> FastAPI:
    app = FastAPI(title="ind-agent copilot", version="0.1.0")
    deps = lru_cache(maxsize=1)(deps_factory or default_deps)
    log = get_logger()

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

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
        req = InvestigateRequest(body.question, body.as_of, body.mode, body.strategy)
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
