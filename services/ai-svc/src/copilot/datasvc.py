"""Typed client for data-svc. Always forwards the end user's token; never holds its own."""

import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

RETRY_DELAYS = (0.25, 0.5, 1.0)  # seconds; Retry-After overrides


class DataServiceError(RuntimeError):
    """data-svc unreachable or returned an unexpected error."""


class NotFound(DataServiceError):
    """Unknown asset or tag for the caller's tenant (indistinguishable from another tenant's)."""


class RateLimited(DataServiceError):
    """data-svc kept answering 429 after retries."""


class Rejected(DataServiceError):
    """data-svc rejected the request (4xx other than 404/429): a bug in the caller, not an outage."""


@dataclass(frozen=True)
class TenantInfo:
    id: str
    name: str
    timezone: str


@dataclass(frozen=True)
class SensorInfo:
    tag: str
    kind: str
    description: str
    unit: str
    lo_limit: float | None
    hi_limit: float | None
    related_tags: tuple[str, ...]


@dataclass(frozen=True)
class AssetInfo:
    id: str
    name: str
    model: str
    site: str
    sensors: tuple[SensorInfo, ...]

    def sensor(self, tag: str) -> SensorInfo:
        for s in self.sensors:
            if s.tag == tag:
                return s
        raise KeyError(tag)


@dataclass(frozen=True)
class AlarmInfo:
    id: str
    asset_id: str
    tag: str
    alarm_type: str
    severity: str
    raised_at: datetime
    cleared_at: datetime | None
    value: float | None
    limit_value: float | None
    message: str


@dataclass(frozen=True)
class WindowPoint:
    ts: datetime
    avg: float | None
    min: float | None
    max: float | None
    n: int
    bad: int


@dataclass(frozen=True)
class SensorWindow:
    asset_id: str
    tag: str
    unit: str
    start: datetime
    end: datetime
    resolution_seconds: int
    points: tuple[WindowPoint, ...]


def fmt_ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value).astimezone(UTC)


class DataServiceClient:
    def __init__(self, base_url: str, token: str, trace_id: str, timeout: float = 15.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            base_url=base_url, timeout=timeout, transport=transport,
            headers={"Authorization": f"Bearer {token}", "X-Trace-Id": trace_id},
        )

    def _get(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                response = self._http.get(path, params=params)
            except httpx.HTTPError as exc:
                raise DataServiceError(f"data-svc unreachable: {exc}") from exc
            if response.status_code != 429:
                break
            if attempt == len(RETRY_DELAYS):
                raise RateLimited(f"data-svc rate limit persisted for {path}")
            retry_after = response.headers.get("Retry-After", "")
            time.sleep(float(retry_after) if retry_after.isdigit() else RETRY_DELAYS[attempt])
        if response.status_code == 404:
            raise NotFound(path)
        if 400 <= response.status_code < 500:
            try:
                code = response.json()["error"]["code"]
            except (ValueError, KeyError, TypeError):
                code = str(response.status_code)
            raise Rejected(f"data-svc rejected {path}: {code}")
        if response.status_code != 200:
            raise DataServiceError(f"data-svc returned {response.status_code} for {path}")
        return response.json()

    def assets(self) -> tuple[TenantInfo, list[AssetInfo]]:
        body = self._get("/v1/assets")
        tenant = TenantInfo(**body["tenant"])
        assets = [
            AssetInfo(a["id"], a["name"], a["model"], a["site"], tuple(
                SensorInfo(s["tag"], s["kind"], s["description"], s["unit"], s["lo_limit"],
                           s["hi_limit"], tuple(s["related_tags"] or ()))
                for s in a["sensors"] or ()))
            for a in body["assets"]
        ]
        return tenant, assets

    def alarms(self, asset_id: str, start: datetime, end: datetime) -> tuple[list[AlarmInfo], bool]:
        body = self._get(f"/v1/assets/{asset_id}/alarms", {"from": fmt_ts(start), "to": fmt_ts(end)})
        alarms = [
            AlarmInfo(a["id"], a["asset_id"], a["tag"], a["alarm_type"], a["severity"],
                      _ts(a["raised_at"]), _ts(a["cleared_at"]), a["value"], a["limit_value"], a["message"])
            for a in body["alarms"]
        ]
        return alarms, bool(body["truncated"])

    def window(self, asset_id: str, tag: str, start: datetime, end: datetime) -> SensorWindow:
        body = self._get(f"/v1/assets/{asset_id}/sensors/{tag}/window",
                         {"from": fmt_ts(start), "to": fmt_ts(end)})
        return SensorWindow(
            body["asset_id"], body["tag"], body["unit"], _ts(body["from"]), _ts(body["to"]),
            body["resolution_seconds"],
            tuple(WindowPoint(_ts(p["ts"]), p["avg"], p["min"], p["max"], p["n"], p["bad"])
                  for p in body["points"]),
        )
