from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VerifierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    timeout_seconds: int = Field(gt=0)
    min_interval_seconds: int = Field(ge=0)
    required_outputs: list[str] = Field(min_length=1)


class AgentBackendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["codex_exec", "local_deterministic"] = "codex_exec"


class RunnerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_root: str
    contract_path: str
    amptest_dir: str
    dut_netlist: str
    devices_csv: str
    amptest_config: str
    candidate_generation_batch_size: int = Field(gt=0)
    max_active_primes_per_subagent: int = Field(gt=0)
    max_total_primes_per_subagent: int = Field(gt=0)
    agent_timeouts_seconds: dict[str, int]
    agent_backend: AgentBackendConfig = Field(default_factory=AgentBackendConfig)
    verifier: VerifierConfig


def load_runner_config(path: Path) -> RunnerConfig:
    return RunnerConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
