from datetime import UTC, datetime, timedelta

import numpy as np
import psycopg
import pytest

from copilot.simgen.alarms import Alarm
from copilot.simgen.catalog import ASSETS, MODELS, TENANTS
from copilot.simgen.generate import SimResult
from copilot.simgen.loader import load
from copilot.simgen.signals import normal_series

from .conftest import as_tenant

pytestmark = pytest.mark.integration

START = datetime(2026, 1, 1, tzinfo=UTC)


def _acme_rows(settings) -> tuple[int, int]:
    with psycopg.connect(settings.dsn("ops_reader")) as conn:
        as_tenant(conn, "acme")
        readings = conn.execute("SELECT count(*) FROM ops.sensor_readings").fetchone()[0]
        alarms = conn.execute("SELECT count(*) FROM ops.alarms").fetchone()[0]
    return readings, alarms


def test_load_is_all_or_nothing_across_tenants(settings):
    """A failure while loading globex must not leave acme replaced by the new run."""
    acme = next(a for a in ASSETS if (a.tenant_id, a.id) == ("acme", "P-12"))
    globex = next(a for a in ASSETS if (a.tenant_id, a.id) == ("globex", "P-20"))
    rng = np.random.default_rng(0)
    bad_alarm = Alarm("globex", "P-20", "NO-SUCH-TAG", "high_temp", "medium",
                      START, None, 1.0, 1.0, "references a sensor that does not exist")
    result = SimResult(
        start=START,
        end=START + timedelta(minutes=10),
        tenants=TENANTS,
        assets=(acme, globex),
        series={
            ("acme", "P-12"): normal_series(MODELS[acme.model], START, 10, rng),
            ("globex", "P-20"): normal_series(MODELS[globex.model], START, 10, rng),
        },
        alarms=[bad_alarm],
        ground_truth=[],
        seed=0,
    )
    before = _acme_rows(settings)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        load(result, settings)
    assert _acme_rows(settings) == before
