"""Static catalog of synthetic tenants, asset models, sensors and assets."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SensorSpec:
    tag: str
    kind: str  # temperature | flow | pressure | current | vibration
    description: str
    unit: str
    baseline: float
    noise: float  # standard deviation of the AR(1) noise
    daily_amplitude: float
    lo_limit: float | None
    hi_limit: float | None
    related: tuple[str, ...]


@dataclass(frozen=True)
class AssetModel:
    code: str
    description: str
    sensors: tuple[SensorSpec, ...]

    def sensor(self, tag: str) -> SensorSpec:
        for spec in self.sensors:
            if spec.tag == tag:
                return spec
        raise KeyError(f"{self.code} has no sensor {tag}")


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    timezone: str


@dataclass(frozen=True)
class Asset:
    tenant_id: str
    id: str
    name: str
    model: str
    site: str


CP_200 = AssetModel("CP-200", "Centrifugal process pump, 75 kW", (
    SensorSpec("TT-101", "temperature", "Pump bearing temperature", "degC",
               62.0, 0.4, 2.0, None, 85.0, ("TT-102", "FT-101", "IT-101", "VT-101")),
    SensorSpec("TT-102", "temperature", "Bearing cooling water return temperature", "degC",
               28.0, 0.3, 1.5, None, 38.0, ("TT-101", "FT-101")),
    SensorSpec("FT-101", "flow", "Discharge flow", "m3/h",
               120.0, 1.5, 5.0, 60.0, None, ("PT-101", "IT-101")),
    SensorSpec("PT-101", "pressure", "Discharge pressure", "bar",
               6.5, 0.05, 0.1, 3.0, 9.0, ("FT-101",)),
    SensorSpec("IT-101", "current", "Motor current", "A",
               42.0, 0.5, 1.0, None, 55.0, ("FT-101", "TT-101")),
    SensorSpec("VT-101", "vibration", "Bearing vibration velocity", "mm/s",
               2.2, 0.1, 0.1, None, 7.1, ("TT-101",)),
))

CL_50 = AssetModel("CL-50", "Closed cooling water loop with plate heat exchanger", (
    SensorSpec("TT-201", "temperature", "Cooling water supply temperature", "degC",
               24.0, 0.2, 1.5, None, 32.0, ("TT-202", "FT-201")),
    SensorSpec("TT-202", "temperature", "Cooling water return temperature", "degC",
               31.0, 0.2, 1.5, None, 40.0, ("TT-201", "FT-201")),
    SensorSpec("FT-201", "flow", "Loop flow", "m3/h",
               300.0, 3.0, 0.0, 200.0, None, ("PT-201", "TT-201")),
    SensorSpec("PT-201", "pressure", "Loop pressure", "bar",
               3.2, 0.03, 0.0, 2.0, None, ("FT-201",)),
))

AC_75 = AssetModel("AC-75", "Oil-injected rotary screw air compressor, 75 kW", (
    SensorSpec("TT-301", "temperature", "Compressor discharge temperature", "degC",
               88.0, 0.5, 2.0, None, 105.0, ("IT-301", "PT-301")),
    SensorSpec("PT-301", "pressure", "Discharge pressure", "bar",
               7.5, 0.05, 0.1, 6.0, 8.6, ("IT-301", "TT-301")),
    SensorSpec("IT-301", "current", "Motor current", "A",
               120.0, 1.2, 3.0, None, 140.0, ("PT-301", "TT-301")),
    SensorSpec("VT-301", "vibration", "Airend vibration velocity", "mm/s",
               2.8, 0.1, 0.1, None, 7.1, ("TT-301",)),
))

MODELS: dict[str, AssetModel] = {m.code: m for m in (CP_200, CL_50, AC_75)}

TENANTS: tuple[Tenant, ...] = (
    Tenant("acme", "Acme Process Plant", "Asia/Kolkata"),
    Tenant("globex", "Globex Utilities", "Europe/Berlin"),
)

ASSETS: tuple[Asset, ...] = (
    Asset("acme", "P-10", "Pump 10", "CP-200", "Plant A"),
    Asset("acme", "P-11", "Pump 11", "CP-200", "Plant A"),
    Asset("acme", "P-12", "Pump 12", "CP-200", "Plant A"),
    Asset("acme", "CL-1", "Cooling Loop 1", "CL-50", "Plant A"),
    Asset("acme", "AC-1", "Air Compressor 1", "AC-75", "Plant A"),
    Asset("globex", "P-20", "Pump 20", "CP-200", "North Station"),
    Asset("globex", "P-21", "Pump 21", "CP-200", "North Station"),
    Asset("globex", "CL-2", "Cooling Loop 2", "CL-50", "North Station"),
    Asset("globex", "AC-2", "Air Compressor 2", "AC-75", "North Station"),
    Asset("globex", "AC-3", "Air Compressor 3", "AC-75", "South Station"),
)
