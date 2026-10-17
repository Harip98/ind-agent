from datetime import UTC, datetime

import pytest

from copilot.datasvc import AssetInfo
from copilot.intent import alarm_types, find_asset, resolve_asset, resolve_time
from copilot.llm import FakeLLM

AS_OF = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)  # 13:30 in Asia/Kolkata
TZ = "Asia/Kolkata"


def _asset(aid, name, model="CP-200"):
    return AssetInfo(aid, name, model, "Plant A", ())


ASSETS = [_asset("P-10", "Pump 10"), _asset("P-12", "Pump 12"), _asset("CL-1", "Cooling Loop 1", "CL-50"),
          _asset("AC-1", "Air Compressor 1", "AC-75")]


@pytest.mark.parametrize("text, expected", [
    ("Investigate yesterday's temperature alarm on Pump 12", "P-12"),
    ("what happened on P-12", "P-12"),
    ("p12 vibration", "P-12"),
    ("air compressor 1 discharge temperature", "AC-1"),
    ("the cooling loop 1 alarm", "CL-1"),
    ("compressor 1", "AC-1"),
    ("AC-75 model, compressor 1 is hot", "AC-1"),
    ("TT-101 on pump 12", "P-12"),
])
def test_find_asset_by_rules(text, expected):
    assert find_asset(text, ASSETS).asset.id == expected


def test_generic_kind_with_one_asset_resolves_and_with_many_is_ambiguous():
    assert find_asset("why did the compressor trip", ASSETS).asset.id == "AC-1"
    m = find_asset("the pump alarm", ASSETS)
    assert m.asset is None and {a.id for a in m.options} == {"P-10", "P-12"}


def test_unknown_asset_needs_clarification():
    m = find_asset("Pump 20 alarm", ASSETS)
    assert m.asset is None and m.options == ()


def test_llm_fallback_only_when_rules_find_nothing():
    llm = FakeLLM([{"asset_mention": "Air Compressor 1"}])
    match, via = resolve_asset("what is wrong with the instrument air machine", ASSETS, llm)
    assert (match.asset.id, via) == ("AC-1", "llm")
    match, via = resolve_asset("Pump 12 alarm", ASSETS, FakeLLM([]))
    assert via == "rules"


def test_llm_failure_falls_back_to_no_match():
    from copilot.ollama import OllamaError

    match, via = resolve_asset("the instrument air machine", ASSETS, FakeLLM([OllamaError("down")]))
    assert match.asset is None and via == "none"


@pytest.mark.parametrize("q, start, end, label", [
    ("yesterday's alarm", "2026-10-15T18:30:00+00:00", "2026-10-16T18:30:00+00:00", "yesterday"),
    ("alarm today", "2026-10-16T18:30:00+00:00", "2026-10-17T08:00:00+00:00", "today"),
    ("last night", "2026-10-16T12:30:00+00:00", "2026-10-17T00:30:00+00:00", "last night"),
    ("3 days ago", "2026-10-13T18:30:00+00:00", "2026-10-14T18:30:00+00:00", "3 days ago"),
    ("in the last 2 days", "2026-10-15T08:00:00+00:00", "2026-10-17T08:00:00+00:00", "last 2 days"),
    ("last week", "2026-10-10T08:00:00+00:00", "2026-10-17T08:00:00+00:00", "last 7 days"),
    ("on 2026-10-12", "2026-10-11T18:30:00+00:00", "2026-10-12T18:30:00+00:00", "2026-10-12"),
    ("on 12 Oct", "2026-10-11T18:30:00+00:00", "2026-10-12T18:30:00+00:00", "2026-10-12"),
    ("October 12th", "2026-10-11T18:30:00+00:00", "2026-10-12T18:30:00+00:00", "2026-10-12"),
    ("pump 12 alarm on October 12th", "2026-10-11T18:30:00+00:00", "2026-10-12T18:30:00+00:00", "2026-10-12"),
])
def test_resolve_time(q, start, end, label):
    r = resolve_time(q, AS_OF, TZ)
    assert (r.start.isoformat(), r.end.isoformat(), r.label, r.assumed) == (start, end, label, False)


def test_no_time_defaults_to_last_7_days():
    r = resolve_time("why is pump 12 hot", AS_OF, TZ)
    assert r.assumed and r.label == "last 7 days" and (r.end - r.start).days == 7


def test_future_date_is_never_an_empty_or_inverted_window():
    r = resolve_time("on 2026-10-20", AS_OF, TZ)
    assert r.start < r.end <= AS_OF


def test_long_ranges_are_capped_at_30_days():
    r = resolve_time("in the last 60 days", AS_OF, TZ)
    assert (r.end - r.start).days == 30 and r.label == "last 30 days"


@pytest.mark.parametrize("q, types", [
    ("temperature alarm", ("high_temp",)),
    ("why is it vibrating", ("high_vibration",)),
    ("low flow", ("low_flow",)),
    ("pressure alarm", ("low_pressure", "high_pressure")),
    ("sensor has bad data", ("bad_quality",)),
    ("what happened", ()),
    ("take a screenshot of the photo", ()),
])
def test_alarm_types(q, types):
    assert alarm_types(q) == types
