"""Incident schedule. Start times are local to the tenant, relative to the end of the data."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Incident:
    incident_id: str
    tenant_id: str
    asset_id: str
    cause_code: str
    days_ago: int  # calendar days before the end date, in tenant local time
    local_start: str  # "HH:MM" tenant local time
    tag: str | None = None  # only for NUISANCE_SPIKE


INCIDENTS: tuple[Incident, ...] = (
    Incident("INC-01", "acme", "P-12", "COOLING_RESTRICTION", 1, "06:00"),  # demo case
    Incident("INC-02", "acme", "P-10", "BEARING_WEAR", 12, "08:00"),
    Incident("INC-03", "acme", "P-11", "SENSOR_DRIFT", 6, "00:00"),
    Incident("INC-04", "acme", "P-12", "DRY_RUN", 9, "14:00"),
    Incident("INC-05", "acme", "CL-1", "HX_FOULING", 15, "00:00"),
    Incident("INC-06", "acme", "AC-1", "COOLER_FAN_FAILURE", 3, "11:00"),
    Incident("INC-07", "acme", "P-11", "SENSOR_FAILURE", 2, "13:00"),
    Incident("INC-08", "acme", "P-10", "NUISANCE_SPIKE", 4, "16:20", tag="TT-101"),
    Incident("INC-11", "globex", "P-20", "CAVITATION", 1, "10:00"),
    Incident("INC-12", "globex", "P-21", "COOLING_RESTRICTION", 5, "07:00"),
    Incident("INC-13", "globex", "CL-2", "HX_FOULING", 8, "00:00"),
    Incident("INC-14", "globex", "AC-2", "COOLER_FAN_FAILURE", 2, "15:00"),
    Incident("INC-15", "globex", "AC-3", "NUISANCE_SPIKE", 7, "03:10", tag="PT-301"),
    Incident("INC-16", "globex", "P-20", "SENSOR_DRIFT", 14, "00:00"),
    Incident("INC-17", "globex", "P-21", "DRY_RUN", 11, "09:00"),
)


def resolve_start(incident: Incident, timezone: str, end: datetime) -> datetime:
    zone = ZoneInfo(timezone)
    day = end.astimezone(zone).date() - timedelta(days=incident.days_ago)
    hh, mm = (int(part) for part in incident.local_start.split(":"))
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone).astimezone(UTC)
