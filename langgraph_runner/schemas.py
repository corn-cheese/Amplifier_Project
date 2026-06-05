from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Phase(str, Enum):
    PHASE1_PERFORMANCE = "phase1_performance"
    PHASE2A_AREA = "phase2a_area"
    PHASE2B_POWER = "phase2b_power"


class AgentRole(str, Enum):
    SPEC = "spec"
    ARCHITECTURE = "architecture"
    DIAGNOSIS = "diagnosis"
    OPTIMIZER = "optimizer"
    REVIEWER = "reviewer"
    PRIME = "prime"


class PrimeRole(str, Enum):
    BIAS = "bias-prime"
    RESISTOR = "R-prime"
    CAPACITOR = "C-prime"
    LOW = "LOW-prime"
    HIGH = "HIGH-prime"
    GAIN_STAGE = "gain-stage-prime"
    OUTPUT_STAGE = "output-stage-prime"


class CandidateStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ERROR = "error"
    INTERRUPTED = "interrupted"


MetricEffect = Literal["decrease", "increase", "no_major_change", "unknown"]
PrimaryObjective = Literal["performance", "area", "power"]


class ExpectedEffect(StrictModel):
    performance_nrmse_combined: MetricEffect
    area_total_p: MetricEffect
    power_score_basis_w: MetricEffect


class Proposal(StrictModel):
    candidate_id: str
    phase: Phase
    agent: AgentRole
    hypothesis: str = Field(min_length=1)
    primary_objective: PrimaryObjective
    changed_blocks: list[str] = Field(min_length=1)
    files_touched: list[str] = Field(min_length=1)
    expected_effect: ExpectedEffect
    risk: str = Field(min_length=1)
    patch: str = Field(min_length=1)


class ReviewResult(StrictModel):
    candidate_id: str
    passed: bool
    checks: dict[str, bool]
    errors: list[str]
    warnings: list[str] = Field(default_factory=list)


class VerificationResult(StrictModel):
    candidate_id: str
    status: Literal["passed", "failed", "error"]
    metrics_path: str
    report_path: str
    spectre_logs: list[str]
    performance_nrmse_combined: StrictFloat
    area_total_p: StrictFloat
    power_score_basis_w: StrictFloat
    errors: list[str]

    @field_validator("performance_nrmse_combined", "area_total_p", "power_score_basis_w")
    @classmethod
    def finite_metric(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("metric must be finite")
        return value


class HumanInterrupt(StrictModel):
    required: bool
    question: str | None
    recommended_action: Literal["continue", "reject", "rerun_verification", "stop"] | None
    evidence_paths: list[str]


class TopDecision(StrictModel):
    decision: Literal["continue", "reject_batch", "rerun_verification", "human_interrupt", "stop"]
    reason: str
    anomaly_level: Literal["none", "info", "warning", "critical"]
    candidate_ids: list[str]
    next_batch_strategy: str
    human_interrupt: HumanInterrupt


class PrimeRequest(StrictModel):
    prime_role: PrimeRole
    prompt: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class RunnerState(StrictModel):
    current_phase: Phase
    baseline_candidate_id: str | None
    accepted_candidate_id: str | None
    accepted_metrics: dict[str, float] | None
    accepted_ppa_surrogate_score: float | None
    ppa_baseline_metrics: dict[str, float] | None = None
    best_failed_candidate_id: str | None
    best_failed_metrics: dict[str, float] | None
    batch_no: int
    three_bjt_verified_count: int
    three_bjt_stagnated: bool
    phase2a_verified_count: int
    phase2a_stagnated: bool
    last_verification_at: str | None
    last_top_decision_path: str | None
    contract_hash: str

    @classmethod
    def initial(cls, contract_hash: str) -> "RunnerState":
        return cls(
            current_phase=Phase.PHASE1_PERFORMANCE,
            baseline_candidate_id=None,
            accepted_candidate_id=None,
            accepted_metrics=None,
            accepted_ppa_surrogate_score=None,
            ppa_baseline_metrics=None,
            best_failed_candidate_id=None,
            best_failed_metrics=None,
            batch_no=0,
            three_bjt_verified_count=0,
            three_bjt_stagnated=False,
            phase2a_verified_count=0,
            phase2a_stagnated=False,
            last_verification_at=None,
            last_top_decision_path=None,
            contract_hash=contract_hash,
        )


class LedgerEntry(StrictModel):
    candidate_id: str
    batch_id: str
    phase: Phase
    agent: AgentRole
    status: CandidateStatus
    reason: str
    metrics: dict[str, float]
    ppa_surrogate_score: float | None
    artifact_dir: str
    workspace_dir: str
    created_at: datetime
    contract_hash: str
