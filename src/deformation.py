"""Deformation time series: demo mode + CSV upload parsing."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DeformationMetrics:
    velocity_mm_month: float
    acceleration_mm_month2: float
    series: pd.DataFrame  # columns: date, los_mm


def parse_deformation_csv(text_or_buffer) -> pd.DataFrame:
    """Parse CSV with columns fecha/date and los_mm (or displacement_mm)."""
    if hasattr(text_or_buffer, "read"):
        raw = text_or_buffer.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        df = pd.read_csv(StringIO(raw))
    else:
        df = pd.read_csv(StringIO(str(text_or_buffer)))

    cols = {c.lower().strip(): c for c in df.columns}
    date_col = None
    for key in ("fecha", "date", "time", "datetime"):
        if key in cols:
            date_col = cols[key]
            break
    los_col = None
    for key in ("los_mm", "displacement_mm", "disp_mm", "los", "deformacion_mm"):
        if key in cols:
            los_col = cols[key]
            break
    if date_col is None or los_col is None:
        raise ValueError(
            "CSV debe incluir columnas de fecha (fecha/date) y desplazamiento "
            "(los_mm / displacement_mm)."
        )

    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df[date_col], errors="coerce"),
            "los_mm": pd.to_numeric(df[los_col], errors="coerce"),
        }
    ).dropna()
    out = out.sort_values("date").reset_index(drop=True)
    if out.empty:
        raise ValueError("CSV sin filas válidas tras parsear fechas y los_mm.")
    return out


def compute_metrics(series: pd.DataFrame) -> DeformationMetrics:
    """
    Velocity: linear fit of LOS vs time (mm/month).
    Acceleration: difference of velocities between first/second half windows (mm/month²),
    approximated as Δv / Δt_months between window midpoints.
    """
    df = series.sort_values("date").dropna().copy()
    if len(df) < 2:
        return DeformationMetrics(0.0, 0.0, df)

    t0 = df["date"].iloc[0]
    t_days = (df["date"] - t0).dt.total_seconds() / 86400.0
    t_months = t_days / 30.437
    y = df["los_mm"].to_numpy(dtype=float)
    tm = t_months.to_numpy(dtype=float)

    # Overall velocity (mm/month)
    if np.allclose(tm.max(), tm.min()):
        vel = 0.0
    else:
        vel = float(np.polyfit(tm, y, 1)[0])

    # Split-window acceleration
    mid = len(df) // 2
    if mid < 2 or len(df) - mid < 2:
        accel = 0.0
    else:
        def _vel(sub_t, sub_y):
            if np.allclose(sub_t.max(), sub_t.min()):
                return 0.0
            return float(np.polyfit(sub_t, sub_y, 1)[0])

        v1 = _vel(tm[:mid], y[:mid])
        v2 = _vel(tm[mid:], y[mid:])
        t_mid1 = float(tm[:mid].mean())
        t_mid2 = float(tm[mid:].mean())
        dt = t_mid2 - t_mid1
        accel = (v2 - v1) / dt if dt > 1e-6 else 0.0

    return DeformationMetrics(velocity_mm_month=vel, acceleration_mm_month2=accel, series=df)


def make_demo_series(
    *,
    scenario: str = "stable",
    start: str = "2025-01-08",
    end: str = "2025-08-18",
    step_days: int = 12,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Synthetic LOS series calibrated to Shirzaei-like magnitudes.

    scenarios:
      - stable: ~2 mm/mes
      - slow_shirzaei: ~10 mm/mes constant
      - accelerating: starts ~5 mm/mes then accelerates toward ~15+
      - seasonal_noise: slow trend + seasonal-looking oscillation (Sah caveat)
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start=start, end=end, freq=f"{step_days}D")
    t_months = np.arange(len(dates)) * (step_days / 30.437)

    if scenario == "stable":
        los = 2.0 * t_months + rng.normal(0, 0.8, size=len(dates))
    elif scenario == "slow_shirzaei":
        los = 10.0 * t_months + rng.normal(0, 1.2, size=len(dates))
    elif scenario == "accelerating":
        # quadratic-ish: velocity increases
        los = 5.0 * t_months + 1.2 * (t_months**2) + rng.normal(0, 1.0, size=len(dates))
    elif scenario == "seasonal_noise":
        seasonal = 4.0 * np.sin(2 * np.pi * t_months / 12.0)
        los = 3.0 * t_months + seasonal + rng.normal(0, 1.0, size=len(dates))
    else:
        raise ValueError(f"Escenario demo desconocido: {scenario}")

    return pd.DataFrame({"date": dates, "los_mm": los})
