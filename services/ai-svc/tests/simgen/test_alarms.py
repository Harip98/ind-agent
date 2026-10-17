from datetime import UTC, datetime, timedelta

import numpy as np

from copilot.simgen.alarms import limit_alarms, quality_alarms
from copilot.simgen.catalog import MODELS
from copilot.simgen.signals import BAD, GOOD

START = datetime(2026, 9, 1, tzinfo=UTC)
TT101 = MODELS["CP-200"].sensor("TT-101")  # hi 85
FT101 = MODELS["CP-200"].sensor("FT-101")  # lo 60


def _present(n):
    return np.ones(n, dtype=bool)


def test_high_alarm_raised_and_cleared_with_deadband():
    v = np.array([80, 86, 84, 84, 83.2, 80], dtype=float)  # 84 is inside the 1.7 deadband
    [a] = limit_alarms("acme", "P-12", TT101, v, _present(6), START)
    assert a.alarm_type == "high_temp"
    assert a.raised_at == START + timedelta(minutes=1)
    assert a.cleared_at == START + timedelta(minutes=4)
    assert a.value == 86.0 and a.limit_value == 85.0
    assert a.severity == "medium"
    assert "Pump bearing temperature" in a.message


def test_severity_high_when_exceeding_limit_by_over_ten_percent():
    v = np.array([80, 86, 95, 80], dtype=float)
    [a] = limit_alarms("acme", "P-12", TT101, v, _present(4), START)
    assert a.severity == "high"


def test_low_alarm_and_unclosed_alarm():
    v = np.array([120, 70, 50, 40], dtype=float)
    [a] = limit_alarms("acme", "P-12", FT101, v, _present(4), START)
    assert a.alarm_type == "low_flow"
    assert a.cleared_at is None


def test_missing_samples_do_not_raise_or_clear():
    v = np.array([80, 90, 0, 0, 90], dtype=float)
    present = np.array([True, True, False, False, True])
    [a] = limit_alarms("acme", "P-12", TT101, v, present, START)
    assert a.cleared_at is None


def test_no_alarm_when_within_limits():
    assert limit_alarms("acme", "P-12", TT101, np.full(100, 70.0), _present(100), START) == []


def test_bad_quality_alarm_needs_ten_minutes():
    present = np.ones(60, dtype=bool)
    present[5:12] = False  # 7 min: ignored
    present[30:45] = False  # 15 min: alarm
    quality = np.full(60, GOOD, dtype=np.int16)
    [a] = quality_alarms("acme", "P-11", TT101, present, quality, START)
    assert a.alarm_type == "bad_quality"
    assert a.raised_at == START + timedelta(minutes=30)
    assert a.cleared_at == START + timedelta(minutes=45)
    assert a.value is None and a.limit_value is None


def test_bad_quality_code_counts_as_bad():
    quality = np.full(30, BAD, dtype=np.int16)
    [a] = quality_alarms("acme", "P-11", TT101, np.ones(30, dtype=bool), quality, START)
    assert a.cleared_at is None
