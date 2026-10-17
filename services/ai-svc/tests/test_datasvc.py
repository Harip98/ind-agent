from datetime import UTC, datetime

import httpx
import pytest

from copilot.datasvc import DataServiceClient, DataServiceError, NotFound, fmt_ts

T0 = datetime(2026, 10, 16, 3, 41, tzinfo=UTC)


def _client(handler):
    return DataServiceClient("http://ds", "TOKEN", "trace-1", transport=httpx.MockTransport(handler))


def test_requests_forward_token_and_trace():
    seen = {}

    def handler(request):
        seen.update(auth=request.headers["authorization"], trace=request.headers["x-trace-id"])
        return httpx.Response(200, json={"tenant": {"id": "acme", "name": "A", "timezone": "Asia/Kolkata"},
                                         "assets": [{"id": "P-12", "name": "Pump 12", "model": "CP-200",
                                                     "site": "S", "sensors": [
                                             {"tag": "TT-101", "kind": "temperature", "description": "d",
                                              "unit": "degC", "lo_limit": None, "hi_limit": 85,
                                              "related_tags": ["TT-102"]}]}]})

    tenant, assets = _client(handler).assets()
    assert seen["auth"] == "Bearer TOKEN" and seen["trace"] == "trace-1"
    assert tenant.timezone == "Asia/Kolkata"
    assert assets[0].sensor("TT-101").related_tags == ("TT-102",)


def test_alarm_and_window_parsing():
    def handler(request):
        if request.url.path.endswith("/alarms"):
            assert request.url.params["from"] == "2026-10-16T03:41:00Z"
            return httpx.Response(200, json={"truncated": True, "alarms": [{
                "id": "P-12:TT-101:high_temp:20261016T034100Z", "asset_id": "P-12", "tag": "TT-101",
                "alarm_type": "high_temp", "severity": "medium", "raised_at": "2026-10-16T03:41:00Z",
                "cleared_at": None, "value": 85.1, "limit_value": 85, "message": "m"}]})
        return httpx.Response(200, json={"asset_id": "P-12", "tag": "TT-101", "unit": "degC",
                                         "from": "2026-10-16T03:41:00Z", "to": "2026-10-16T04:41:00Z",
                                         "resolution_seconds": 60, "points": [
                                             {"ts": "2026-10-16T03:41:00Z", "avg": 85.0, "min": 84.9,
                                              "max": 85.1, "n": 1, "bad": 0}]})

    c = _client(handler)
    alarms, truncated = c.alarms("P-12", T0, T0.replace(hour=5))
    assert truncated and alarms[0].raised_at == T0 and alarms[0].cleared_at is None
    w = c.window("P-12", "TT-101", T0, T0.replace(hour=4))
    assert w.points[0].avg == 85.0 and w.resolution_seconds == 60


def test_404_is_not_found_and_5xx_or_network_is_error():
    with pytest.raises(NotFound):
        _client(lambda r: httpx.Response(404, json={"error": {"code": "not_found"}})).alarms("X", T0, T0)
    with pytest.raises(DataServiceError):
        _client(lambda r: httpx.Response(500, json={})).alarms("X", T0, T0)

    def boom(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(DataServiceError, match="unreachable"):
        _client(boom).assets()


def test_fmt_ts_is_utc_z():
    assert fmt_ts(datetime(2026, 10, 16, 9, 11, tzinfo=UTC)) == "2026-10-16T09:11:00Z"


def test_429_is_retried_with_backoff(monkeypatch):
    import copilot.datasvc as ds

    sleeps = []
    monkeypatch.setattr(ds.time, "sleep", sleeps.append)
    responses = [httpx.Response(429, headers={"Retry-After": "1"}, json={}),
                 httpx.Response(429, json={}),
                 httpx.Response(200, json={"truncated": False, "alarms": []})]
    alarms, _ = _client(lambda r: responses.pop(0)).alarms("P-12", T0, T0.replace(hour=5))
    assert alarms == [] and len(sleeps) == 2 and sleeps[0] == 1.0


def test_persistent_429_is_rate_limited_error(monkeypatch):
    import copilot.datasvc as ds

    monkeypatch.setattr(ds.time, "sleep", lambda s: None)
    with pytest.raises(ds.RateLimited):
        _client(lambda r: httpx.Response(429, json={})).alarms("P-12", T0, T0)


def test_other_4xx_is_a_rejection_not_unavailable():
    from copilot.datasvc import Rejected

    with pytest.raises(Rejected, match="window_too_large"):
        _client(lambda r: httpx.Response(400, json={"error": {"code": "window_too_large"}})).alarms("X", T0, T0)
