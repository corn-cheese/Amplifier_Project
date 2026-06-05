from __future__ import annotations

from datetime import datetime

from .schemas import Phase


ROLE_SLUGS = {
    "architecture": "arch",
    "optimizer": "opt",
    "diagnosis": "diag",
    "spec": "spec",
    "reviewer": "rev",
    "prime": "prime",
}


def phase_prefix(phase: str | Phase) -> str:
    value = phase.value if isinstance(phase, Phase) else phase
    prefixes = {
        Phase.PHASE1_PERFORMANCE.value: "p1",
        Phase.PHASE2A_AREA.value: "p2a",
        Phase.PHASE2B_POWER.value: "p2b",
    }
    return prefixes[value]


def batch_id(phase: str | Phase, batch_no: int) -> str:
    return f"{phase_prefix(phase)}-b{batch_no:03d}"


def candidate_id(phase: str | Phase, batch_no: int, candidate_no: int, role: str, now: datetime) -> str:
    slug = ROLE_SLUGS.get(role, role.replace("_", "-"))
    return f"{batch_id(phase, batch_no)}-c{candidate_no:02d}-{slug}-{now.strftime('%Y%m%d-%H%M%S')}"
