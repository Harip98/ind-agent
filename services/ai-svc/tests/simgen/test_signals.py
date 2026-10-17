from datetime import UTC, datetime

import numpy as np

from copilot.simgen.catalog import ASSETS, MODELS, TENANTS
from copilot.simgen.signals import GOOD, baseline, normal_series

START = datetime(2026, 9, 1, tzinfo=UTC)
N = 14 * 1440


def test_catalog_has_two_tenants_ten_assets_and_no_globex_p12():
    assert {t.id for t in TENANTS} == {"acme", "globex"}
    assert len(ASSETS) == 10
    assert ("globex", "P-12") not in {(a.tenant_id, a.id) for a in ASSETS}
    assert all(a.model in MODELS for a in ASSETS)


def test_related_tags_exist_on_the_same_model():
    for model in MODELS.values():
        tags = {s.tag for s in model.sensors}
        for s in model.sensors:
            assert set(s.related) <= tags, (model.code, s.tag)


def test_baseline_is_deterministic_for_a_seed():
    spec = MODELS["CP-200"].sensor("TT-101")
    a = baseline(spec, START, N, np.random.default_rng(1))
    b = baseline(spec, START, N, np.random.default_rng(1))
    assert np.array_equal(a, b)


def test_baseline_mean_and_noise_match_spec():
    spec = MODELS["CP-200"].sensor("FT-101")
    v = baseline(spec, START, N, np.random.default_rng(2))
    assert abs(v.mean() - spec.baseline) < 0.5
    assert v.std() < spec.noise * 3 + spec.daily_amplitude


def test_normal_operation_never_crosses_limits():
    rng = np.random.default_rng(3)
    for model in MODELS.values():
        s = normal_series(model, START, N, rng)
        for spec in model.sensors:
            v = s.values[spec.tag]
            if spec.hi_limit is not None:
                assert v.max() < spec.hi_limit, (model.code, spec.tag)
            if spec.lo_limit is not None:
                assert v.min() > spec.lo_limit, (model.code, spec.tag)
            assert (s.quality[spec.tag] == GOOD).all()
            assert s.present[spec.tag].all()
