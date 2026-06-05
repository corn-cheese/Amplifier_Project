from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .ids import batch_id, candidate_id
from .schemas import Phase, RunnerState


@dataclass(frozen=True)
class CandidateAssignment:
    candidate_id: str
    batch_id: str
    role: str
    phase: Phase
    primary_objective: str


def roles_for_phase(phase: Phase) -> list[str]:
    if phase == Phase.PHASE1_PERFORMANCE:
        return ["architecture", "architecture", "optimizer"]
    if phase == Phase.PHASE2A_AREA:
        return ["optimizer", "architecture", "optimizer"]
    return ["optimizer", "diagnosis", "optimizer"]


def objective_for_phase(phase: Phase) -> str:
    if phase == Phase.PHASE1_PERFORMANCE:
        return "performance"
    if phase == Phase.PHASE2A_AREA:
        return "area"
    return "power"


def plan_batch(state: RunnerState, batch_size: int, now: datetime) -> list[CandidateAssignment]:
    next_batch_no = state.batch_no + 1
    roles = roles_for_phase(state.current_phase)
    assignments = []
    for index in range(batch_size):
        role = roles[index % len(roles)]
        assignments.append(
            CandidateAssignment(
                candidate_id=candidate_id(state.current_phase, next_batch_no, index + 1, role, now),
                batch_id=batch_id(state.current_phase, next_batch_no),
                role=role,
                phase=state.current_phase,
                primary_objective=objective_for_phase(state.current_phase),
            )
        )
    return assignments
