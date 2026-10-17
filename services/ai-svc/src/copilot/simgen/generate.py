"""Generate a full synthetic dataset: series per asset, alarms and ground truth per incident."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np

from copilot.simgen.alarms import Alarm, limit_alarms, quality_alarms
from copilot.simgen.catalog import ASSETS, MODELS, TENANTS, Asset, Tenant
from copilot.simgen.faults import PROFILES, RECOVERY_MINUTES, apply_fault
from copilot.simgen.scenarios import INCIDENTS, resolve_start
from copilot.simgen.signals import AssetSeries, normal_series


@dataclass
class GroundTruth:
    incident_id: str
    tenant_id: str
    asset_id: str
    cause_code: str
    primary_tag: str
    start: datetime
    peak: datetime
    end: datetime
    alarms: list[dict]  # {"tag", "alarm_type", "raised_at"} with ISO timestamps

    def to_json(self) -> dict:
        return {
            "incident_id": self.incident_id,
            "tenant_id": self.tenant_id,
            "asset_id": self.asset_id,
            "cause_code": self.cause_code,
            "primary_tag": self.primary_tag,
            "start": self.start.isoformat(),
            "peak": self.peak.isoformat(),
            "end": self.end.isoformat(),
            "alarms": self.alarms,
            "synthetic": True,
        }


@dataclass
class SimResult:
    start: datetime
    end: datetime
    tenants: tuple[Tenant, ...]
    assets: tuple[Asset, ...]
    series: dict[tuple[str, str], AssetSeries]
    alarms: list[Alarm]
    ground_truth: list[GroundTruth]
    seed: int


def generate(end: datetime, days: int = 30, seed: int = 7) -> SimResult:
    if end.tzinfo is None:
        raise ValueError("end must be timezone-aware")
    end = end.astimezone(UTC).replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    n = days * 1440
    rng = np.random.default_rng(seed)
    timezones = {t.id: t.timezone for t in TENANTS}
    assets = {(a.tenant_id, a.id): a for a in ASSETS}
    series = {key: normal_series(MODELS[a.model], start, n, rng) for key, a in assets.items()}
    recovery = timedelta(minutes=RECOVERY_MINUTES)

    windows = []
    for inc in INCIDENTS:
        profile = PROFILES[inc.cause_code]
        asset = assets[(inc.tenant_id, inc.asset_id)]
        t0 = resolve_start(inc, timezones[inc.tenant_id], end)
        t_end = t0 + profile.ramp + profile.hold
        if t0 < start or t_end + recovery > end:
            raise ValueError(f"{inc.incident_id} falls outside the simulated window; increase days")
        i0 = int((t0 - start).total_seconds() // 60)
        apply_fault(series[(asset.tenant_id, asset.id)], MODELS[asset.model], profile, i0, inc.tag)
        windows.append((inc, profile, t0, t_end))

    alarms: list[Alarm] = []
    for (tenant_id, asset_id), asset in assets.items():
        s = series[(tenant_id, asset_id)]
        for spec in MODELS[asset.model].sensors:
            tag = spec.tag
            alarms += limit_alarms(tenant_id, asset_id, spec, s.values[tag], s.present[tag], start)
            alarms += quality_alarms(
                tenant_id, asset_id, spec, s.present[tag], s.quality[tag], start
            )
    alarms.sort(key=lambda a: (a.tenant_id, a.raised_at, a.asset_id, a.tag))

    ground_truth = []
    for inc, profile, t0, t_end in windows:
        linked = [
            {"tag": a.tag, "alarm_type": a.alarm_type, "raised_at": a.raised_at.isoformat()}
            for a in alarms
            if a.tenant_id == inc.tenant_id
            and a.asset_id == inc.asset_id
            and t0 <= a.raised_at <= t_end + recovery
        ]
        ground_truth.append(GroundTruth(
            incident_id=inc.incident_id,
            tenant_id=inc.tenant_id,
            asset_id=inc.asset_id,
            cause_code=inc.cause_code,
            primary_tag=profile.primary_tag or inc.tag,
            start=t0,
            peak=t0 + profile.ramp,
            end=t_end,
            alarms=linked,
        ))

    return SimResult(start, end, TENANTS, ASSETS, series, alarms, ground_truth, seed)
