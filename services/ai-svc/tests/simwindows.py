"""Build data-svc-shaped windows and alarms straight from a SimResult (1-minute points)."""

from datetime import timedelta

from copilot.analysis import LONG_AFTER, LONG_BEFORE, SHORT_AFTER, SHORT_BEFORE, TagWindows
from copilot.datasvc import AlarmInfo, AssetInfo, SensorInfo, SensorWindow, WindowPoint
from copilot.simgen.catalog import ASSETS, MODELS


def asset_info(tenant_id: str, asset_id: str) -> AssetInfo:
    a = next(x for x in ASSETS if (x.tenant_id, x.id) == (tenant_id, asset_id))
    return AssetInfo(a.id, a.name, a.model, a.site, tuple(
        SensorInfo(s.tag, s.kind, s.description, s.unit, s.lo_limit, s.hi_limit, s.related)
        for s in MODELS[a.model].sensors))


def window(result, tenant_id, asset_id, tag, start, end) -> SensorWindow:
    s = result.series[(tenant_id, asset_id)]
    i0 = max(0, int((start - result.start).total_seconds() // 60))
    i1 = min(len(s.values[tag]), int((end - result.start).total_seconds() // 60))
    pts = []
    for i in range(i0, i1):
        if not s.present[tag][i]:
            continue
        v = round(float(s.values[tag][i]), 3)
        good = s.quality[tag][i] == 192
        pts.append(WindowPoint(result.start + timedelta(minutes=i), v if good else None,
                               v if good else None, v if good else None, 1, 0 if good else 1))
    unit = next(x.unit for x in asset_info(tenant_id, asset_id).sensors if x.tag == tag)
    return SensorWindow(asset_id, tag, unit, start, end, 60, tuple(pts))


def windows_for(result, tenant_id, asset_id, alarm_at) -> dict[str, TagWindows]:
    info = asset_info(tenant_id, asset_id)
    return {
        s.tag: TagWindows(
            window(result, tenant_id, asset_id, s.tag, alarm_at - SHORT_BEFORE, alarm_at + SHORT_AFTER),
            window(result, tenant_id, asset_id, s.tag, alarm_at - LONG_BEFORE, alarm_at + LONG_AFTER))
        for s in info.sensors
    }


def to_alarm_info(a) -> AlarmInfo:
    return AlarmInfo(f"{a.asset_id}:{a.tag}:{a.alarm_type}:{a.raised_at:%Y%m%dT%H%M%SZ}", a.asset_id, a.tag,
                     a.alarm_type, a.severity, a.raised_at, a.cleared_at, a.value, a.limit_value, a.message)


def primary_alarm(result, gt) -> AlarmInfo:
    a = next(x for x in result.alarms if x.tenant_id == gt.tenant_id and x.asset_id == gt.asset_id
             and x.tag == gt.primary_tag and gt.start <= x.raised_at)
    return to_alarm_info(a)
