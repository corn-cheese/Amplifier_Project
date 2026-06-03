from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict


PIN_ORDER = ["VIN", "VREF", "VDD", "GND", "VOUT"]
AMPT_EST_COMMAND = "python3 ppa_wrapper.py all --config ./config.json"


class CircuitSeed(TypedDict):
    seed_id: str
    topology_name: str
    subckt_name: str
    pins: list[str]
    netlist_template: str
    param_ranges: dict[str, dict[str, Any]]
    initial_params: dict[str, Any]
    device_manifest: list[dict[str, Any]]
    rationale: NotRequired[str]


class TrialResult(TypedDict):
    trial_id: str
    seed_id: str
    params: dict[str, Any]
    status: Literal["queued", "rendered", "simulated", "failed", "scored"]
    metrics: dict[str, Any] | None
    objective: float | None
    artifact_dir: str
    error: str | None
    optuna_trial_number: NotRequired[int]


class WorkflowState(TypedDict, total=False):
    spec: dict[str, Any]
    seeds: list[CircuitSeed]
    active_seed: CircuitSeed | None
    active_seed_index: int
    seed_valid: bool
    seed_smoke_passed: dict[str, bool]
    seed_repair_attempts: dict[str, int]
    consecutive_smoke_failures: int
    abandoned_seed_ids: list[str]
    study_name: str | None
    trial_results: list[TrialResult]
    best_result: TrialResult | None
    remote_run: dict[str, Any]
    failure_reasons: list[str]
    next_route: str
    interrupt: dict[str, Any] | None
    optimizer_state: dict[str, Any] | None


@dataclass(frozen=True)
class WorkflowConfig:
    backend: str = "eda_ssh"
    run_root: Path = Path("runs")
    amptest_local_dir: Path = Path("amptest")
    checkpoint_db: Path = Path("runs/langgraph_checkpoints.sqlite")
    optuna_storage: str = "sqlite:///runs/optuna_studies.sqlite3"
    max_seeds: int = 3
    max_trials_per_seed: int = 5
    objective_target: float = 0.25
    parallelism: int = 1
    min_interval_s: int = 60
    remote_timeout_s: int = 1200
    daily_max_trials: int = 20
    max_seed_repair_attempts: int = 2
    max_consecutive_smoke_failures: int = 6
    smoke_log_excerpt_chars: int = 12000
    seed_file: Path | None = None
    llm_seed_batch_size: int = 1
    llm_seed_attempts: int = 2
    codex_exec_model: str | None = "gpt-5.5"
    codex_exec_profile: str | None = None
    codex_exec_timeout_s: int = 1800
    codex_exec_sandbox: str = "read-only"
    mock_fixture_dir: Path | None = None
    remote: dict[str, Any] = field(default_factory=dict)
