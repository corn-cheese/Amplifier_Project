from __future__ import annotations

import math
from enum import Enum
from typing import Mapping

from .schemas import Phase, RunnerState


class AcceptanceDecision(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    ERROR = "error"


_PERFORMANCE_METRIC = "performance_nrmse_combined"
_POWER_METRIC = "power_score_basis_w"
_AREA_METRIC = "area_total_p"
_PPA_METRICS = (_PERFORMANCE_METRIC, _POWER_METRIC, _AREA_METRIC)


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _required_metric(metrics: Mapping[str, float], name: str) -> float:
    value = metrics.get(name)
    if not _finite_number(value):
        raise ValueError(f"metric '{name}' must be finite")
    return float(value)


def ppa_surrogate_score(metrics: dict[str, float], baseline: dict[str, float]) -> float:
    candidate_values = {}
    baseline_values = {}
    for name in _PPA_METRICS:
        candidate = _required_metric(metrics, name)
        base = _required_metric(baseline, name)
        if candidate < 0.0:
            raise ValueError(f"metric '{name}' must be non-negative")
        if base <= 0.0:
            raise ValueError(f"baseline metric '{name}' must be positive")
        candidate_values[name] = candidate
        baseline_values[name] = base

    perf = math.log(1 + candidate_values[_PERFORMANCE_METRIC] / baseline_values[_PERFORMANCE_METRIC])
    power = math.log(1 + candidate_values[_POWER_METRIC] / baseline_values[_POWER_METRIC])
    area = math.log(1 + candidate_values[_AREA_METRIC] / baseline_values[_AREA_METRIC])
    return 0.50 * perf + 0.25 * power + 0.25 * area


def ppa_scores_against_baseline(
    candidate_metrics: dict[str, float],
    accepted_metrics: dict[str, float],
    baseline_metrics: dict[str, float],
) -> tuple[float, float]:
    return (
        ppa_surrogate_score(candidate_metrics, baseline_metrics),
        ppa_surrogate_score(accepted_metrics, baseline_metrics),
    )


def evaluate_candidate(
    state: RunnerState,
    candidate_id: str,
    metrics: dict[str, float],
    *,
    review_passed: bool,
    verification_status: str,
    safety_passed: bool,
    ppa_baseline_metrics: dict[str, float] | None = None,
) -> AcceptanceDecision:
    del candidate_id

    if verification_status == "error":
        return AcceptanceDecision.ERROR
    if verification_status != "passed" or not review_passed or not safety_passed:
        return AcceptanceDecision.REJECT

    try:
        performance = _required_metric(metrics, _PERFORMANCE_METRIC)
    except ValueError:
        return AcceptanceDecision.ERROR

    if state.current_phase == Phase.PHASE1_PERFORMANCE:
        if performance <= 0.04:
            return AcceptanceDecision.ACCEPT
        return AcceptanceDecision.REJECT

    if performance > 0.10:
        return AcceptanceDecision.REJECT

    if state.accepted_metrics is None or state.accepted_ppa_surrogate_score is None:
        return AcceptanceDecision.ACCEPT

    if not _finite_number(state.accepted_ppa_surrogate_score):
        return AcceptanceDecision.ERROR

    try:
        baseline_metrics = (
            state.ppa_baseline_metrics
            if state.ppa_baseline_metrics is not None
            else ppa_baseline_metrics
        )
        if baseline_metrics is not None:
            score, accepted_score = ppa_scores_against_baseline(
                metrics,
                state.accepted_metrics,
                baseline_metrics,
            )
            if not math.isclose(
                accepted_score,
                float(state.accepted_ppa_surrogate_score),
                rel_tol=1e-9,
                abs_tol=1e-12,
            ):
                return AcceptanceDecision.ERROR
            return AcceptanceDecision.ACCEPT if score < accepted_score else AcceptanceDecision.REJECT

        score = ppa_surrogate_score(metrics, state.accepted_metrics)
    except ValueError:
        return AcceptanceDecision.ERROR

    if score < state.accepted_ppa_surrogate_score:
        return AcceptanceDecision.ACCEPT
    return AcceptanceDecision.REJECT


def three_bjt_fallback_allowed(verified_count: int, recent_improvements: list[float]) -> bool:
    if verified_count < 12:
        return False
    if len(recent_improvements) < 5:
        return False
    return sum(recent_improvements[-5:]) < 0.005
