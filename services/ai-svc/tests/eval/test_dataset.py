import json
from collections import Counter
from datetime import UTC, datetime

import pytest

from copilot.config import REPO_ROOT
from copilot.eval.dataset import DatasetError, build_cases, load_dataset, split_cases, write_dataset
from copilot.simgen.catalog import ASSETS


@pytest.fixture(scope="module")
def inputs():
    from copilot.simgen.generate import generate

    sim = generate(datetime(2026, 10, 17, 12, 52, tzinfo=UTC), days=30, seed=7)
    gt = [g.to_json() for g in sim.ground_truth]
    anchor = {"start": sim.start.isoformat(), "end": sim.end.isoformat(), "days": 30, "seed": 7}
    return gt, anchor


def test_category_counts(inputs):
    cases = build_cases(*inputs)
    assert Counter(c.category for c in cases) == {
        "normal": 75, "missing_evidence": 22, "stale": 15, "unauthorized": 15, "ambiguous": 15,
        "injection": 8}
    assert len({c.id for c in cases}) == 150
    assert len({(c.tenant, c.question) for c in cases}) == 150


def test_normal_cases_have_a_unique_event(inputs):
    for c in build_cases(*inputs):
        if c.category in ("normal", "stale", "injection"):
            assert c.expected_status == "ok" and c.expected_cause and c.acceptable_docs


def test_unauthorized_cases_name_foreign_assets(inputs):
    owner = {a.id: a.tenant_id for a in ASSETS}
    for c in build_cases(*inputs):
        if c.category == "unauthorized":
            assert c.expected_status == "needs_clarification"
            assert owner[c.asset_id] != c.tenant


def test_split_is_stratified_and_deterministic(inputs):
    cases = build_cases(*inputs)
    dev, test = split_cases(cases)
    assert (len(dev), len(test)) == (100, 50)
    assert split_cases(cases) == (dev, test)
    assert {c.split for c in dev} == {"dev"} and {c.split for c in test} == {"test"}
    for cat, n in Counter(c.category for c in cases).items():
        assert abs(sum(c.category == cat for c in test) - n / 3) <= 1, cat


def test_write_and_load_roundtrip_with_hash(inputs, tmp_path):
    dev, test = split_cases(build_cases(*inputs))
    write_dataset(dev, test, inputs[1], tmp_path)
    assert load_dataset(tmp_path, "test") == test and load_dataset(tmp_path, "dev") == dev
    (tmp_path / "test.jsonl").write_bytes((tmp_path / "test.jsonl").read_bytes() + b"\n")
    with pytest.raises(DatasetError, match="hash"):
        load_dataset(tmp_path, "test")


def test_repo_dataset_matches_generator(inputs):
    d = REPO_ROOT / "eval" / "datasets"
    assert json.loads((d / "anchor.json").read_text(encoding="utf-8")) == inputs[1]
    dev, test = split_cases(build_cases(*inputs))
    assert load_dataset(d, "test") == test and load_dataset(d, "dev") == dev
