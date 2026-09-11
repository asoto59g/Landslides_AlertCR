"""Alert levels from InSAR LOS velocity and acceleration (Shirzaei / Sah framing)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AlertLevel(str, Enum):
    VERDE = "Verde"
    AMARILLO = "Amarillo"
    NARANJA = "Naranja"
    ROJO = "Rojo"


LEVEL_COLOR = {
    AlertLevel.VERDE: "#2e7d32",
    AlertLevel.AMARILLO: "#f9a825",
    AlertLevel.NARANJA: "#ef6c00",
    AlertLevel.ROJO: "#c62828",
}


@dataclass(frozen=True)
class AlertThresholds:
    green_max_mm_month: float = 5.0
    orange_min_mm_month: float = 10.0
    accel_mild_mm_month2: float = 1.0
    accel_clear_mm_month2: float = 3.0


@dataclass(frozen=True)
class AlertResult:
    level: AlertLevel
    velocity_mm_month: float
    acceleration_mm_month2: float
    message: str
    action: str


def classify_alert(
    velocity_mm_month: float,
    acceleration_mm_month2: float,
    thresholds: AlertThresholds | None = None,
) -> AlertResult:
    """
    Classify monitoring priority.

    - Verde: |v| < 5 and no meaningful acceleration
    - Amarillo: 5–10 mm/mes OR mild acceleration
    - Naranja: |v| >= 10 mm/mes (Shirzaei reference)
    - Rojo: |v| >= 10 AND clear acceleration → prioritize field inspection
      (NOT 'imminent collapse')
    """
    th = thresholds or AlertThresholds()
    v = abs(float(velocity_mm_month))
    a = abs(float(acceleration_mm_month2))

    if v >= th.orange_min_mm_month and a >= th.accel_clear_mm_month2:
        return AlertResult(
            level=AlertLevel.ROJO,
            velocity_mm_month=velocity_mm_month,
            acceleration_mm_month2=acceleration_mm_month2,
            message=(
                f"Velocidad alta (|v|={v:.1f} mm/mes) con aceleración clara "
                f"(|a|={a:.1f} mm/mes²). Señal de inestabilidad a priorizar."
            ),
            action="Priorizar inspección de campo y seguimiento InSAR — no implica colapso inminente.",
        )

    if v >= th.orange_min_mm_month:
        return AlertResult(
            level=AlertLevel.NARANJA,
            velocity_mm_month=velocity_mm_month,
            acceleration_mm_month2=acceleration_mm_month2,
            message=(
                f"Velocidad ≥ {th.orange_min_mm_month:.0f} mm/mes "
                f"(|v|={v:.1f}), referencia Shirzaei para deformación medible."
            ),
            action="Mantener monitoreo intensivo; revisar aceleración en próximos pases S1.",
        )

    if v >= th.green_max_mm_month or a >= th.accel_mild_mm_month2:
        return AlertResult(
            level=AlertLevel.AMARILLO,
            velocity_mm_month=velocity_mm_month,
            acceleration_mm_month2=acceleration_mm_month2,
            message=(
                f"Deformación moderada (|v|={v:.1f} mm/mes) o aceleración leve "
                f"(|a|={a:.1f} mm/mes²)."
            ),
            action="Seguimiento rutinario; discriminar posibles efectos estacionales.",
        )

    return AlertResult(
        level=AlertLevel.VERDE,
        velocity_mm_month=velocity_mm_month,
        acceleration_mm_month2=acceleration_mm_month2,
        message=f"Movimiento bajo (|v|={v:.1f} mm/mes) sin aceleración significativa.",
        action="Continuar vigilancia de base; no hay priorización especial.",
    )
