"""Normal-operation sensor series: level + daily cycle + AR(1) noise, one sample per minute."""

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from copilot.simgen.catalog import AssetModel, SensorSpec

GOOD = 192  # OPC quality codes
BAD = 0
AR_COEF = 0.9


@dataclass
class AssetSeries:
    values: dict[str, np.ndarray]
    quality: dict[str, np.ndarray]
    present: dict[str, np.ndarray]  # False = no row stored (data gap)


def baseline(spec: SensorSpec, start: datetime, n: int, rng: np.random.Generator) -> np.ndarray:
    minute_of_day = (start.hour * 60 + start.minute + np.arange(n)) % 1440
    daily = spec.daily_amplitude * np.sin(2 * np.pi * (minute_of_day - 360) / 1440)
    shocks = rng.normal(0.0, spec.noise * np.sqrt(1 - AR_COEF**2), n)
    noise = np.empty(n)
    acc = 0.0
    for i in range(n):
        acc = AR_COEF * acc + shocks[i]
        noise[i] = acc
    return spec.baseline + daily + noise


def normal_series(
    model: AssetModel, start: datetime, n: int, rng: np.random.Generator
) -> AssetSeries:
    values = {spec.tag: baseline(spec, start, n, rng) for spec in model.sensors}
    return AssetSeries(
        values=values,
        quality={tag: np.full(n, GOOD, dtype=np.int16) for tag in values},
        present={tag: np.ones(n, dtype=bool) for tag in values},
    )
