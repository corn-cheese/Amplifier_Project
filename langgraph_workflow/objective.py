from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ObjectiveResult:
    objective: float
    penalties: list[str]


def compute_objective(
    metrics: dict[str, Any] | None,
    *,
    simulation_failed: bool = False,
    forbidden_device: bool = False,
    invalid_pin_order: bool = False,
) -> ObjectiveResult:
    penalties: list[str] = []

    if simulation_failed or metrics is None:
        penalties.append("simulation failed or missing ppa_metrics.json")
        return ObjectiveResult(1000.0, penalties)

    base = metrics.get("performance_nrmse_combined")
    if base is None:
        base = 0.0
        penalties.append("missing performance_nrmse_combined (+25)")
        hard_penalty = 25.0
    else:
        hard_penalty = 0.0

    if forbidden_device:
        penalties.append("non-BJT or forbidden device discovered after render (+1000)")
        hard_penalty += 1000.0
    if invalid_pin_order:
        penalties.append("invalid pin order (+100)")
        hard_penalty += 100.0

    area_power = metrics.get("area_power") or {}
    ac = metrics.get("ac") or {}
    tran = metrics.get("tran") or {}
    area_total_p = _float(area_power.get("area_total_p"), 0.0)
    power_score_basis_w = _float(area_power.get("power_score_basis_w"), 0.0)

    if _missing_core_metrics(ac, tran):
        penalties.append("missing AC or transient metrics (+25)")
        hard_penalty += 25.0

    midband_gain_db = ac.get("midband_gain_db")
    lower_3db_hz = ac.get("lower_3db_hz")
    upper_3db_hz = ac.get("upper_3db_hz")
    if midband_gain_db is None or abs(_float(midband_gain_db, -999.0) - 40.0) > 6.0:
        penalties.append("midband gain outside 40 dB +/- 6 dB (+10)")
        hard_penalty += 10.0
    if lower_3db_hz is None or _float(lower_3db_hz, 1e300) > 20.0:
        penalties.append("lower 3 dB point above 20 Hz (+10)")
        hard_penalty += 10.0
    if upper_3db_hz is None or _float(upper_3db_hz, -1.0) < 10_000.0:
        penalties.append("upper 3 dB point below 10 kHz (+10)")
        hard_penalty += 10.0
    if _transient_clips(tran):
        penalties.append("transient output clips or mean is outside 2.5 V +/- 0.5 V (+10)")
        hard_penalty += 10.0

    objective = (
        float(base)
        + 0.15 * math.log10(1.0 + area_total_p / 100.0)
        + 0.15 * math.log10(1.0 + power_score_basis_w / 1e-3)
        + hard_penalty
    )
    return ObjectiveResult(objective, penalties)


def _missing_core_metrics(ac: dict[str, Any], tran: dict[str, Any]) -> bool:
    return not ac or not tran


def _transient_clips(tran: dict[str, Any]) -> bool:
    mean = tran.get("vout_mean_v")
    if mean is not None and abs(_float(mean, 0.0) - 2.5) > 0.5:
        return True
    v_min = tran.get("vout_min_v")
    v_max = tran.get("vout_max_v")
    if v_min is not None and _float(v_min, 999.0) < 0.1:
        return True
    if v_max is not None and _float(v_max, -999.0) > 4.9:
        return True
    return False


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

