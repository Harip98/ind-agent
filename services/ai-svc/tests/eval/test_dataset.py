import json
from collections import Counter
from datetime import UTC, datetime

import pytest

from copilot.config import REPO_ROOT
from copilot.eval.dataset import (
    DatasetError, build_cases, load_dataset, split_cases, write_dataset,
)
from copilot.kb.corpus import CORPUS_DIR, SHARED, load_corpus
from copilot.simgen.catalog import ASSETS


@pytest.fixture(scope="module")
def inputs():
    from copilot.simgen.generate import generate

    sim = generate(datetime(2026, 10, 17, 12, 52, tzinfo=UTC), days=30, seed=7)
    gt = [g.to_json() for g in sim.ground_truth]
    alarms = [{"tenant_id": a.tenant_id, "asset_id": a.asset_id, "tag": a.tag, "alarm_type": a.alarm_type,
               "raised_at": a.raised_at.isoformat()} for a in sim.alarms]
    anchor = {"start": sim.start.isoformat(), "end": sim.end.isoformat(), "days": 30, "seed": 7}
    return gt, anchor, alarms


@pytest.fixture(scope="module")
def cases(inputs):
    return build_cases(*inputs)


def test_size_and_categories(cases):
    counts = Counter(c.category for c in cases)
    assert set(counts) == {"normal", "missing_evidence", "stale", "unauthorized", "ambiguous", "injection"}
    assert 100 <= len(cases) <= 200
    assert len({c.id for c in cases}) == len(cases)
    assert len({(c.tenant, c.question, c.request_asset_id, c.request_alarm_id) for c in cases}) == len(cases)


def test_answerable_cases_have_labels(cases):
    for c in cases:
        if c.category in ("normal", "stale", "injection"):
            assert c.expected_status == "ok" and c.expected_cause and c.acceptable_docs, c.id


def test_acceptable_docs_are_visible_to_the_tenant(cases):
    visible = {}
    for d in load_corpus(CORPUS_DIR):
        visible.setdefault(d.tenant_id, set()).add(d.doc_id)
    for c in cases:
        assert set(c.acceptable_docs) <= visible[SHARED] | visible.get(c.tenant, set()), c.id


def test_stale_cases_target_removed_sections(cases):
    stale = [c for c in cases if c.category == "stale"]
    assert stale and all(c.expected_stale in {"SOP-PUMP-07#5.3", "SOP-AC-05#4.3"} for c in stale)
    assert any("v1" in c.question for c in stale)


def test_unauthorized_cases_cover_names_ids_and_request_fields(cases):
    owner = {a.id: a.tenant_id for a in ASSETS}
    unauth = [c for c in cases if c.category == "unauthorized"]
    assert all(c.expected_status == "needs_clarification" for c in unauth)
    assert any(c.request_asset_id for c in unauth) and any(c.request_alarm_id for c in unauth)
    for c in unauth:
        target = c.request_asset_id or (c.request_alarm_id or "").split(":")[0] or c.asset_id
        if target:
            assert owner[target] != c.tenant, c.id


def test_missing_evidence_includes_days_with_other_alarms(cases, inputs):
    _, _, alarms = inputs
    from zoneinfo import ZoneInfo
    tz = {"acme": ZoneInfo("Asia/Kolkata"), "globex": ZoneInfo("Europe/Berlin")}
    busy = {(a["tenant_id"], a["asset_id"], datetime.fromisoformat(a["raised_at"]).astimezone(tz[a["tenant_id"]]).date().isoformat())
            for a in alarms}
    missing = [c for c in cases if c.category == "missing_evidence"]
    with_other = [c for c in missing if (c.tenant, c.asset_id, c.question[-11:-1]) in busy]
    assert len(with_other) >= 8


def test_split_is_grouped_by_incident_and_deterministic(cases):
    dev, test = split_cases(cases)
    assert split_cases(cases) == (dev, test)
    assert len(dev) + len(test) == len(cases) and 0.25 <= len(test) / len(cases) <= 0.4
    dev_groups, test_groups = {c.group for c in dev}, {c.group for c in test}
    assert not dev_groups & test_groups
    test_incidents = {c.incident_id for c in test if c.incident_id}
    assert len(test_incidents) == 5
    causes = {c.expected_cause for c in test if c.expected_cause}
    assert len(causes) == 5


def test_generator_refuses_two_events_on_one_day(inputs):
    gt, anchor, alarms = inputs
    inc = next(g for g in gt if g["incident_id"] == "INC-01")
    extra = dict(alarms[0], tenant_id="acme", asset_id="P-12", tag="TT-101", alarm_type="high_temp",
                 raised_at=inc["alarms"][0]["raised_at"].replace("T0", "T1", 1))
    with pytest.raises(DatasetError, match="INC-01"):
        build_cases(gt, anchor, alarms + [extra])


def test_write_load_and_refreeze_guard(cases, inputs, tmp_path):
    dev, test = split_cases(cases)
    write_dataset(dev, test, inputs[1], tmp_path)
    assert load_dataset(tmp_path, "test") == test and load_dataset(tmp_path, "dev") == dev
    with pytest.raises(DatasetError, match="refreeze"):
        write_dataset(dev, test[:-1], inputs[1], tmp_path)
    write_dataset(dev, test[:-1], inputs[1], tmp_path, refreeze="test")
    assert "test" in (tmp_path / "FREEZE_LOG.md").read_text(encoding="utf-8")
    (tmp_path / "test.jsonl").write_bytes((tmp_path / "test.jsonl").read_bytes() + b"\n")
    with pytest.raises(DatasetError, match="hash"):
        load_dataset(tmp_path, "test")
    (tmp_path / "test.sha256").unlink()
    with pytest.raises(DatasetError, match="hash"):
        load_dataset(tmp_path, "test")


def test_repo_dataset_matches_generator(cases, inputs):
    d = REPO_ROOT / "eval" / "datasets"
    assert json.loads((d / "anchor.json").read_text(encoding="utf-8")) == inputs[1]
    dev, test = split_cases(cases)
    assert load_dataset(d, "test") == test and load_dataset(d, "dev") == dev
