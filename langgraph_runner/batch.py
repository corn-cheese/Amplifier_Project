from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .ids import batch_id, candidate_id
from .schemas import Phase, RunnerState


PHASE1_STAGNATION_MIN_VERIFIED = 12
PHASE1_STAGNATION_RECENT_WINDOW = 5
PHASE1_STAGNATION_MIN_IMPROVEMENT = 0.005

DEFAULT_AVOID_PATTERNS = [
    "same 2-stage NPN common-emitter retune",
    "bias branch retune without changing the signal path",
    "bypass/shunt capacitor retune around recent values",
    "resistor/capacitor multiplier-only edits",
    "corrupt or hand-wrapped patches",
]

MACRO_TOPOLOGY_DIRECTIVES = [
    {
        "stage_count": 1,
        "signal_path_class": "single_gain_stage_with_explicit_output_load_bias",
        "feedback_class": "local_degeneration_or_passive_load_shaping",
        "topology_intent": "single gain stage with explicit output load and bias, not a two-stage CE chain",
    },
    {
        "stage_count": 2,
        "signal_path_class": "non_ce_retune_two_stage_with_different_interstage_coupling",
        "feedback_class": "explicit_interstage_coupling_or_local_feedback",
        "topology_intent": "two-stage candidate that changes interstage coupling and device roles instead of retuning the recent 2-stage NPN CE shape",
    },
    {
        "stage_count": 3,
        "signal_path_class": "input_bias_stage_gain_stage_output_buffer",
        "feedback_class": "local_degeneration_or_passive_feedback",
        "topology_intent": "input/bias stage plus gain stage plus explicit output or buffer stage",
    },
]


@dataclass(frozen=True)
class CandidateAssignment:
    candidate_id: str
    batch_id: str
    role: str
    phase: Phase
    primary_objective: str
    macro_topology_directive: dict[str, Any] | None = None
    avoid_patterns: list[str] | None = None


def roles_for_phase(phase: Phase) -> list[str]:
    if phase == Phase.PHASE1_PERFORMANCE:
        return ["diagnosis", "architecture", "optimizer"]
    if phase == Phase.PHASE2A_AREA:
        return ["optimizer", "architecture", "optimizer"]
    return ["optimizer", "diagnosis", "optimizer"]


def objective_for_phase(phase: Phase) -> str:
    if phase == Phase.PHASE1_PERFORMANCE:
        return "performance"
    if phase == Phase.PHASE2A_AREA:
        return "area"
    return "power"


def plan_batch(
    state: RunnerState,
    batch_size: int,
    now: datetime,
    recent_ledger: list[dict[str, Any]] | None = None,
) -> list[CandidateAssignment]:
    next_batch_no = state.batch_no + 1
    macro_topology_mode = _phase1_macro_topology_stagnated(state, recent_ledger or [])
    roles = ["architecture"] if macro_topology_mode and state.current_phase == Phase.PHASE1_PERFORMANCE else roles_for_phase(state.current_phase)
    assignments = []
    for index in range(batch_size):
        role = roles[index % len(roles)]
        macro_topology_directive = None
        avoid_patterns = None
        if macro_topology_mode and state.current_phase == Phase.PHASE1_PERFORMANCE:
            macro_topology_directive = dict(MACRO_TOPOLOGY_DIRECTIVES[index % len(MACRO_TOPOLOGY_DIRECTIVES)])
            avoid_patterns = list(DEFAULT_AVOID_PATTERNS)
        assignments.append(
            CandidateAssignment(
                candidate_id=candidate_id(state.current_phase, next_batch_no, index + 1, role, now),
                batch_id=batch_id(state.current_phase, next_batch_no),
                role=role,
                phase=state.current_phase,
                primary_objective=objective_for_phase(state.current_phase),
                macro_topology_directive=macro_topology_directive,
                avoid_patterns=avoid_patterns,
            )
        )
    return assignments


def _phase1_macro_topology_stagnated(state: RunnerState, recent_ledger: list[dict[str, Any]]) -> bool:
    if state.current_phase != Phase.PHASE1_PERFORMANCE:
        return False
    if state.three_bjt_verified_count < PHASE1_STAGNATION_MIN_VERIFIED:
        return False
    if state.three_bjt_stagnated:
        return True

    phase1_rows = [
        row
        for row in recent_ledger
        if str(row.get("phase", "")) == Phase.PHASE1_PERFORMANCE.value
        and _performance_metric(row) is not None
    ]
    if len(phase1_rows) <= PHASE1_STAGNATION_RECENT_WINDOW:
        return False

    previous_rows = phase1_rows[:-PHASE1_STAGNATION_RECENT_WINDOW]
    recent_rows = phase1_rows[-PHASE1_STAGNATION_RECENT_WINDOW:]
    previous_best = min(_performance_metric(row) for row in previous_rows if _performance_metric(row) is not None)
    recent_best = min(_performance_metric(row) for row in recent_rows if _performance_metric(row) is not None)
    return (previous_best - recent_best) < PHASE1_STAGNATION_MIN_IMPROVEMENT


def _performance_metric(row: dict[str, Any]) -> float | None:
    metrics = row.get("metrics")
    if not isinstance(metrics, dict):
        return None
    value = metrics.get("performance_nrmse_combined")
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
