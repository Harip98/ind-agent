"""Typed client for data-svc. Always forwards the end user's token; never holds its own."""

import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

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


# Leaf checks for data-svc bodies. A wrong type raises TypeError inside `_malformed`, so it becomes a 503
# data_unavailable here instead of a crash in numpy or datetime code later. No coercion: "41.2" is malformed.
def _num(value) -> float | None:
    if value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)):
        return value
    raise TypeError(f"expected a number, got {type(value).__name__}")


def _int(value) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise TypeError(f"expected an integer, got {type(value).__name__}")


def _req_ts(value) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"expected a timestamp string, got {type(value).__name__}")
    return _ts(value)


def _tags(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise TypeError("expected a list of tags")
    return tuple(value)


@contextmanager
def _malformed(what: str):
    """A 200 whose body lacks fields or has the wrong types is an upstream fault, not a crash."""
    try:
        yield
    except DataServiceError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise DataServiceError(f"data-svc sent a malformed {what} response: {type(exc).__name__}") from exc


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
        try:
            return response.json()
        except ValueError as exc:
            raise DataServiceError(f"data-svc sent a body that is not JSON for {path}") from exc

    def assets(self) -> tuple[TenantInfo, list[AssetInfo]]:
        with _malformed("/v1/assets"):
            return self._assets()

    def alarms(self, asset_id: str, start: datetime, end: datetime) -> tuple[list[AlarmInfo], bool]:
        with _malformed("alarms"):
            return self._alarms(asset_id, start, end)

    def window(self, asset_id: str, tag: str, start: datetime, end: datetime) -> SensorWindow:
        with _malformed("window"):
            return self._window(asset_id, tag, start, end)

    def _assets(self) -> tuple[TenantInfo, list[AssetInfo]]:
        body = self._get("/v1/assets")
        t = body["tenant"]
        if not isinstance(t["timezone"], str):
            raise TypeError("expected a time zone name")
        ZoneInfo(t["timezone"])  # an unknown zone is a malformed body (503), not a crash in the analysis
        tenant = TenantInfo(t["id"], t["name"], t["timezone"])
        assets = [
            AssetInfo(a["id"], a["name"], a["model"], a["site"], tuple(
                SensorInfo(s["tag"], s["kind"], s["description"], s["unit"], _num(s["lo_limit"]),
                           _num(s["hi_limit"]), _tags(s["related_tags"]))
                for s in a["sensors"] or ()))
            for a in body["assets"]
        ]
        return tenant, assets

    def _alarms(self, asset_id: str, start: datetime, end: datetime) -> tuple[list[AlarmInfo], bool]:
        body = self._get(f"/v1/assets/{asset_id}/alarms", {"from": fmt_ts(start), "to": fmt_ts(end)})
        alarms = [
            AlarmInfo(a["id"], a["asset_id"], a["tag"], a["alarm_type"], a["severity"],
                      _req_ts(a["raised_at"]), _ts(a["cleared_at"]), _num(a["value"]), _num(a["limit_value"]),
                      a["message"])
            for a in body["alarms"]
        ]
        return alarms, bool(body["truncated"])

    def _window(self, asset_id: str, tag: str, start: datetime, end: datetime) -> SensorWindow:
        body = self._get(f"/v1/assets/{asset_id}/sensors/{tag}/window",
                         {"from": fmt_ts(start), "to": fmt_ts(end)})
        return SensorWindow(
            body["asset_id"], body["tag"], body["unit"], _req_ts(body["from"]), _req_ts(body["to"]),
            _int(body["resolution_seconds"]),
            tuple(WindowPoint(_req_ts(p["ts"]), _num(p["avg"]), _num(p["min"]), _num(p["max"]), _int(p["n"]),
                              _int(p["bad"]))
                  for p in body["points"]),
        )
