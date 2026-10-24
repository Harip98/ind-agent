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
    combo: str | None = None  # incidents sharing a combo id overlap on one asset (holdout only)


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
    # holdout tenant: single faults, overlapping faults and faults no signature describes
    Incident("INC-21", "hollow", "P-30", "BEARING_WEAR", 12, "07:00"),
    Incident("INC-22", "hollow", "P-31", "CAVITATION", 4, "09:00"),
    Incident("INC-23", "hollow", "P-32", "DRY_RUN", 8, "13:30"),
    Incident("INC-24", "hollow", "P-30", "SENSOR_FAILURE", 3, "02:00"),
    Incident("INC-25", "hollow", "CL-3", "HX_FOULING", 10, "00:00"),
    Incident("INC-26", "hollow", "AC-4", "COOLER_FAN_FAILURE", 6, "14:00"),
    # CMB-01: bearing wear develops for a day before a cooling-water restriction trips the temperature alarm
    Incident("INC-27", "hollow", "P-32", "BEARING_WEAR", 4, "08:00", combo="CMB-01"),
    Incident("INC-28", "hollow", "P-32", "COOLING_RESTRICTION", 3, "06:00", combo="CMB-01"),
    # CMB-02 / CMB-03: a real fault with an unrelated nuisance spike on another signal in the same hour
    Incident("INC-29", "hollow", "CL-3", "HX_FOULING", 4, "00:00", combo="CMB-02"),
    Incident("INC-30", "hollow", "CL-3", "NUISANCE_SPIKE", 3, "03:50", tag="FT-201", combo="CMB-02"),
    Incident("INC-31", "hollow", "AC-4", "COOLER_FAN_FAILURE", 2, "10:00", combo="CMB-03"),
    Incident("INC-32", "hollow", "AC-4", "NUISANCE_SPIKE", 2, "12:05", tag="IT-301", combo="CMB-03"),
    # novel: no fault signature exists, so the right answer names no confident cause
    Incident("INC-33", "hollow", "P-31", "SEAL_LEAK", 9, "05:00"),
    # 06:00 so it ends more than 48 h before CMB-02's alarm on the same loop (no undeclared confounder there)
    Incident("INC-34", "hollow", "CL-3", "CIRCULATION_PUMP_WEAR", 6, "06:00"),
)
# Causes the simulator can produce but signatures.yaml does not describe.
NOVEL_CAUSES = frozenset({"SEAL_LEAK", "CIRCULATION_PUMP_WEAR"})


def resolve_start(incident: Incident, timezone: str, end: datetime) -> datetime:
    zone = ZoneInfo(timezone)
    day = end.astimezone(zone).date() - timedelta(days=incident.days_ago)
    hh, mm = (int(part) for part in incident.local_start.split(":"))
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone).astimezone(UTC)
