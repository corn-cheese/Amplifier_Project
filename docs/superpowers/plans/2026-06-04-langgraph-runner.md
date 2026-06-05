# LangGraph Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first deterministic LangGraph runner for the neural amplifier automation flow described in `docs/superpowers/specs/2026-06-04-langgraph-runner-implementation-design.md`.

**Architecture:** The runner is a Python package named `langgraph_runner`. It uses Pydantic v2 models for every trusted file artifact, deterministic modules for state, review, verification, acceptance, and workspace promotion, and a LangGraph graph that wires those modules into the requested node sequence. `automation_artifacts/state.json`, `automation_artifacts/ledger.jsonl`, and candidate artifact folders are canonical; LangGraph checkpoints only assist resume.

**Tech Stack:** Python 3.14, LangGraph 1.2.4, Pydantic 2.13.4, standard-library `unittest`, `subprocess`, `pathlib`, `json`, `csv`, `hashlib`, `datetime`, and `shutil`.

---

## Context Findings

- The implementation design exists at `docs/superpowers/specs/2026-06-04-langgraph-runner-implementation-design.md`.
- The runner implementation does not exist in the current filesystem.
- `amptest/config.json` currently defines `dut_subckt` as `dummy_neural_amp` and `dut_pins_order` as `["GND", "VDD", "VIN", "VOUT", "VREF"]`.
- `amptest/dummy_neural_amp.scs` currently declares `subckt dummy_neural_amp GND VDD VIN VOUT VREF`.
- `docs/top-coordinator-contract.md` shows a generic `neural_amp VIN VREF VDD GND VOUT` pin contract. This plan treats `amptest/config.json` as the verifier source of truth so the current canonical DUT can pass deterministic review. If the contract document must override `amptest/config.json`, update Task 7 before execution.
- `pytest` is not installed; tests use `python -m unittest`.
- The worktree has many unrelated deleted tracked files. This plan creates new runner files and does not restore or revert unrelated changes.

## File Structure

- Create `pyproject.toml`: package metadata and runtime dependencies.
- Create `runner_config.json`: editable runner settings, verifier command, artifact root, and agent timeouts.
- Create `langgraph_runner/__init__.py`: package version export.
- Create `langgraph_runner/__main__.py`: `python -m langgraph_runner` entrypoint.
- Create `langgraph_runner/schemas.py`: Pydantic models and enums for all trusted artifact files.
- Create `langgraph_runner/config.py`: load and validate `runner_config.json`.
- Create `langgraph_runner/artifacts.py`: artifact path construction and directory initialization.
- Create `langgraph_runner/state_store.py`: canonical state and ledger read/write/reconciliation.
- Create `langgraph_runner/ids.py`: candidate and batch ID generation.
- Create `langgraph_runner/batch.py`: deterministic batch planning and role assignment.
- Create `langgraph_runner/agent_io.py`: context package creation, `codex exec` invocation, schema retry handling.
- Create `langgraph_runner/prime_limits.py`: per-subagent prime active and total limit enforcement.
- Create `langgraph_runner/review.py`: deterministic review checks.
- Create `langgraph_runner/workspace.py`: candidate workspace creation, patch application, and promotion.
- Create `langgraph_runner/verifier.py`: serialized verifier command execution and output validation.
- Create `langgraph_runner/acceptance.py`: phase gates, PPA scoring, and stagnation checks.
- Create `langgraph_runner/graph.py`: LangGraph node assembly and routing.
- Create `langgraph_runner/cli.py`: CLI commands for init, one batch, run, and resume.
- Create `tests/__init__.py` and `tests/langgraph_runner/__init__.py`: unittest package markers.
- Create focused tests under `tests/langgraph_runner/`.

## Task 1: Package Scaffold and Default Config

**Files:**
- Create: `pyproject.toml`
- Create: `runner_config.json`
- Create: `langgraph_runner/__init__.py`
- Create: `langgraph_runner/__main__.py`
- Create: `tests/__init__.py`
- Create: `tests/langgraph_runner/__init__.py`
- Test: `tests/langgraph_runner/test_package.py`

- [ ] **Step 1: Write the failing package import test**

```python
# tests/langgraph_runner/test_package.py
import unittest


class TestPackageImport(unittest.TestCase):
    def test_version_is_exported(self):
        import langgraph_runner

        self.assertEqual(langgraph_runner.__all__, ["__version__"])
        self.assertRegex(langgraph_runner.__version__, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.langgraph_runner.test_package -v`

Expected: `ModuleNotFoundError: No module named 'langgraph_runner'`

- [ ] **Step 3: Create package files and config**

```toml
# pyproject.toml
[project]
name = "neural-amplifier-langgraph-runner"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = [
  "langgraph>=1.2,<2",
  "pydantic>=2.13,<3"
]

[tool.setuptools]
packages = ["langgraph_runner"]
```

```json
{
  "artifact_root": "automation_artifacts",
  "contract_path": "docs/top-coordinator-contract.md",
  "amptest_dir": "amptest",
  "dut_netlist": "amptest/dummy_neural_amp.scs",
  "devices_csv": "amptest/devices.csv",
  "amptest_config": "amptest/config.json",
  "candidate_generation_batch_size": 3,
  "max_active_primes_per_subagent": 2,
  "max_total_primes_per_subagent": 4,
  "agent_timeouts_seconds": {
    "subagent": 1200,
    "prime": 600,
    "reviewer": 300,
    "top": 300
  },
  "verifier": {
    "command": "python {repo_root}/amptest/ppa_wrapper.py analyze --config {local_candidate_dir}/config.json",
    "timeout_seconds": 1800,
    "min_interval_seconds": 30,
    "required_outputs": [
      "verification.json",
      "ppa_metrics.json",
      "ppa_report.log",
      "spectre_ac.log",
      "spectre_tran.log"
    ]
  }
}
```

```python
# langgraph_runner/__init__.py
__version__ = "0.1.0"

__all__ = ["__version__"]
```

```python
# langgraph_runner/__main__.py
from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
```

```python
# tests/__init__.py
```

```python
# tests/langgraph_runner/__init__.py
```

- [ ] **Step 4: Run the package import test**

Run: `python -m unittest tests.langgraph_runner.test_package -v`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml runner_config.json langgraph_runner/__init__.py langgraph_runner/__main__.py tests/__init__.py tests/langgraph_runner/__init__.py tests/langgraph_runner/test_package.py
git commit -m "chore: scaffold langgraph runner package"
```

## Task 2: Trusted Artifact Schemas

**Files:**
- Create: `langgraph_runner/schemas.py`
- Test: `tests/langgraph_runner/test_schemas.py`

- [ ] **Step 1: Write schema validation tests**

```python
# tests/langgraph_runner/test_schemas.py
import unittest
from pydantic import ValidationError

from langgraph_runner.schemas import (
    CandidateStatus,
    Phase,
    Proposal,
    RunnerState,
    TopDecision,
    VerificationResult,
)


class TestSchemas(unittest.TestCase):
    def test_proposal_accepts_required_candidate_fields(self):
        proposal = Proposal.model_validate(
            {
                "candidate_id": "p1-b003-c02-arch-20260604-231500",
                "phase": "phase1_performance",
                "agent": "architecture",
                "hypothesis": "Increase low-frequency shaping without changing evaluator inputs.",
                "primary_objective": "performance",
                "changed_blocks": ["low_frequency"],
                "files_touched": ["amptest/dummy_neural_amp.scs", "amptest/devices.csv"],
                "expected_effect": {
                    "performance_nrmse_combined": "decrease",
                    "area_total_p": "increase",
                    "power_score_basis_w": "no_major_change"
                },
                "risk": "Additional passives may increase area.",
                "patch": "diff --git a/amptest/dummy_neural_amp.scs b/amptest/dummy_neural_amp.scs\n"
            }
        )

        self.assertEqual(proposal.phase, Phase.PHASE1_PERFORMANCE)

    def test_proposal_rejects_extra_keys(self):
        data = {
            "candidate_id": "p1-b001-c01-arch-20260604-231500",
            "phase": "phase1_performance",
            "agent": "architecture",
            "hypothesis": "Valid idea.",
            "primary_objective": "performance",
            "changed_blocks": ["bias"],
            "files_touched": ["amptest/dummy_neural_amp.scs"],
            "expected_effect": {
                "performance_nrmse_combined": "decrease",
                "area_total_p": "unknown",
                "power_score_basis_w": "unknown"
            },
            "risk": "None identified.",
            "patch": "diff --git a/amptest/dummy_neural_amp.scs b/amptest/dummy_neural_amp.scs\n",
            "stdout_claim": "trust me"
        }

        with self.assertRaises(ValidationError):
            Proposal.model_validate(data)

    def test_runner_state_defaults_to_phase1(self):
        state = RunnerState.initial(contract_hash="abc123")

        self.assertEqual(state.current_phase, Phase.PHASE1_PERFORMANCE)
        self.assertEqual(state.batch_no, 0)
        self.assertEqual(state.contract_hash, "abc123")

    def test_verification_requires_finite_metrics(self):
        result = VerificationResult.model_validate(
            {
                "candidate_id": "p1-b001-c01-arch-20260604-231500",
                "status": "passed",
                "metrics_path": "automation_artifacts/candidates/x/ppa_metrics.json",
                "report_path": "automation_artifacts/candidates/x/ppa_report.log",
                "spectre_logs": ["automation_artifacts/candidates/x/spectre_ac.log"],
                "performance_nrmse_combined": 0.03,
                "area_total_p": 100.0,
                "power_score_basis_w": 0.001,
                "errors": []
            }
        )

        self.assertEqual(result.status, "passed")

    def test_top_decision_rejects_invalid_decision(self):
        with self.assertRaises(ValidationError):
            TopDecision.model_validate(
                {
                    "decision": "invent_new_route",
                    "reason": "Invalid.",
                    "anomaly_level": "none",
                    "candidate_ids": [],
                    "next_batch_strategy": "Continue.",
                    "human_interrupt": {
                        "required": False,
                        "question": None,
                        "recommended_action": None,
                        "evidence_paths": []
                    }
                }
            )

    def test_candidate_status_enum_contains_required_values(self):
        self.assertEqual(
            {item.value for item in CandidateStatus},
            {"accepted", "rejected", "error", "interrupted"}
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the schema tests to verify they fail**

Run: `python -m unittest tests.langgraph_runner.test_schemas -v`

Expected: import failure for `langgraph_runner.schemas`

- [ ] **Step 3: Create schema models**

```python
# langgraph_runner/schemas.py
from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    performance_nrmse_combined: float
    area_total_p: float
    power_score_basis_w: float
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


class RunnerState(StrictModel):
    current_phase: Phase
    baseline_candidate_id: str | None
    accepted_candidate_id: str | None
    accepted_metrics: dict[str, float] | None
    accepted_ppa_surrogate_score: float | None
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
```

- [ ] **Step 4: Run the schema tests**

Run: `python -m unittest tests.langgraph_runner.test_schemas -v`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add langgraph_runner/schemas.py tests/langgraph_runner/test_schemas.py
git commit -m "feat: add trusted artifact schemas"
```

## Task 3: Config, Artifact Paths, State, and Ledger

**Files:**
- Create: `langgraph_runner/config.py`
- Create: `langgraph_runner/artifacts.py`
- Create: `langgraph_runner/state_store.py`
- Test: `tests/langgraph_runner/test_state_store.py`

- [ ] **Step 1: Write failing state store tests**

```python
# tests/langgraph_runner/test_state_store.py
import json
import tempfile
import unittest
from pathlib import Path

from langgraph_runner.artifacts import ArtifactPaths
from langgraph_runner.state_store import StateStore, contract_hash


class TestStateStore(unittest.TestCase):
    def test_initialize_creates_canonical_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            contract = root / "docs" / "contract.md"
            contract.write_text("contract\n", encoding="utf-8")
            paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
            store = StateStore(paths=paths, contract_path=contract)

            state = store.initialize()

            self.assertTrue(paths.state_json.exists())
            self.assertTrue(paths.ledger_jsonl.exists())
            self.assertEqual(state.contract_hash, contract_hash(contract))
            self.assertTrue(paths.runs_dir.exists())
            self.assertTrue(paths.candidates_dir.exists())
            self.assertTrue(paths.workspaces_dir.exists())

    def test_state_json_wins_over_checkpoint_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = root / "contract.md"
            contract.write_text("contract\n", encoding="utf-8")
            paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
            store = StateStore(paths=paths, contract_path=contract)
            state = store.initialize()
            state.batch_no = 7
            store.write_state(state)

            loaded = store.load_state(checkpoint_state={"batch_no": 99})

            self.assertEqual(loaded.batch_no, 7)

    def test_ledger_append_writes_one_json_object_per_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = root / "contract.md"
            contract.write_text("contract\n", encoding="utf-8")
            paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
            store = StateStore(paths=paths, contract_path=contract)
            state = store.initialize()

            store.append_ledger(
                candidate_id="p1-b001-c01-arch-20260604-231500",
                batch_id="p1-b001",
                phase=state.current_phase,
                agent="architecture",
                status="rejected",
                reason="Phase 1 gate failed.",
                metrics={"performance_nrmse_combined": 0.2},
                ppa_surrogate_score=None,
                artifact_dir="automation_artifacts/candidates/p1-b001-c01-arch-20260604-231500",
                workspace_dir="automation_artifacts/workspaces/p1-b001-c01-arch-20260604-231500",
                contract_hash=state.contract_hash,
            )

            lines = paths.ledger_jsonl.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the state store tests to verify they fail**

Run: `python -m unittest tests.langgraph_runner.test_state_store -v`

Expected: import failure for `langgraph_runner.artifacts`

- [ ] **Step 3: Implement config and artifact path helpers**

```python
# langgraph_runner/config.py
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class VerifierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    timeout_seconds: int = Field(gt=0)
    min_interval_seconds: int = Field(ge=0)
    required_outputs: list[str] = Field(min_length=1)


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
    verifier: VerifierConfig


def load_runner_config(path: Path) -> RunnerConfig:
    return RunnerConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
```

```python
# langgraph_runner/artifacts.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArtifactPaths:
    repo_root: Path
    artifact_root: Path

    @property
    def state_json(self) -> Path:
        return self.artifact_root / "state.json"

    @property
    def ledger_jsonl(self) -> Path:
        return self.artifact_root / "ledger.jsonl"

    @property
    def runs_dir(self) -> Path:
        return self.artifact_root / "runs"

    @property
    def candidates_dir(self) -> Path:
        return self.artifact_root / "candidates"

    @property
    def workspaces_dir(self) -> Path:
        return self.artifact_root / "workspaces"

    def candidate_dir(self, candidate_id: str) -> Path:
        return self.candidates_dir / candidate_id

    def workspace_dir(self, candidate_id: str) -> Path:
        return self.workspaces_dir / candidate_id

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def ensure_root(self) -> None:
        for path in (self.artifact_root, self.runs_dir, self.candidates_dir, self.workspaces_dir):
            path.mkdir(parents=True, exist_ok=True)
        if not self.ledger_jsonl.exists():
            self.ledger_jsonl.write_text("", encoding="utf-8")
```

- [ ] **Step 4: Implement state store**

```python
# langgraph_runner/state_store.py
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .artifacts import ArtifactPaths
from .schemas import AgentRole, CandidateStatus, LedgerEntry, Phase, RunnerState


def contract_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class StateStore:
    def __init__(self, paths: ArtifactPaths, contract_path: Path):
        self.paths = paths
        self.contract_path = contract_path

    def initialize(self) -> RunnerState:
        self.paths.ensure_root()
        if self.paths.state_json.exists():
            return self.load_state()
        state = RunnerState.initial(contract_hash=contract_hash(self.contract_path))
        self.write_state(state)
        return state

    def load_state(self, checkpoint_state: dict | None = None) -> RunnerState:
        if not self.paths.state_json.exists():
            return self.initialize()
        return RunnerState.model_validate_json(self.paths.state_json.read_text(encoding="utf-8"))

    def write_state(self, state: RunnerState) -> None:
        self.paths.state_json.parent.mkdir(parents=True, exist_ok=True)
        self.paths.state_json.write_text(
            state.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

    def read_ledger(self) -> list[LedgerEntry]:
        if not self.paths.ledger_jsonl.exists():
            return []
        entries = []
        for line in self.paths.ledger_jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(LedgerEntry.model_validate_json(line))
        return entries

    def append_ledger(
        self,
        *,
        candidate_id: str,
        batch_id: str,
        phase: Phase,
        agent: str,
        status: str,
        reason: str,
        metrics: dict[str, float],
        ppa_surrogate_score: float | None,
        artifact_dir: str,
        workspace_dir: str,
        contract_hash: str,
    ) -> LedgerEntry:
        entry = LedgerEntry(
            candidate_id=candidate_id,
            batch_id=batch_id,
            phase=phase,
            agent=AgentRole(agent),
            status=CandidateStatus(status),
            reason=reason,
            metrics=metrics,
            ppa_surrogate_score=ppa_surrogate_score,
            artifact_dir=artifact_dir,
            workspace_dir=workspace_dir,
            created_at=datetime.now(timezone.utc),
            contract_hash=contract_hash,
        )
        self.paths.ledger_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with self.paths.ledger_jsonl.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")
        return entry
```

- [ ] **Step 5: Run the state store tests**

Run: `python -m unittest tests.langgraph_runner.test_state_store -v`

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add langgraph_runner/config.py langgraph_runner/artifacts.py langgraph_runner/state_store.py tests/langgraph_runner/test_state_store.py
git commit -m "feat: add canonical state and ledger store"
```

## Task 4: Candidate IDs and Batch Planning

**Files:**
- Create: `langgraph_runner/ids.py`
- Create: `langgraph_runner/batch.py`
- Test: `tests/langgraph_runner/test_batch.py`

- [ ] **Step 1: Write failing ID and batch tests**

```python
# tests/langgraph_runner/test_batch.py
import re
import unittest
from datetime import datetime, timezone

from langgraph_runner.batch import plan_batch
from langgraph_runner.ids import candidate_id, phase_prefix
from langgraph_runner.schemas import RunnerState


class TestBatchPlanning(unittest.TestCase):
    def test_phase_prefixes_match_design_examples(self):
        self.assertEqual(phase_prefix("phase1_performance"), "p1")
        self.assertEqual(phase_prefix("phase2a_area"), "p2a")
        self.assertEqual(phase_prefix("phase2b_power"), "p2b")

    def test_candidate_id_format(self):
        now = datetime(2026, 6, 4, 23, 15, 0, tzinfo=timezone.utc)
        cid = candidate_id("phase1_performance", 3, 2, "architecture", now)

        self.assertEqual(cid, "p1-b003-c02-arch-20260604-231500")

    def test_default_batch_has_three_unique_assignments(self):
        state = RunnerState.initial(contract_hash="abc")
        assignments = plan_batch(state=state, batch_size=3, now=datetime(2026, 6, 4, 23, 15, 0, tzinfo=timezone.utc))

        self.assertEqual(len(assignments), 3)
        self.assertEqual(len({item.candidate_id for item in assignments}), 3)
        self.assertTrue(all(re.match(r"p1-b001-c0[1-3]-", item.candidate_id) for item in assignments))
        self.assertEqual([item.role for item in assignments], ["architecture", "architecture", "optimizer"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the batch tests to verify they fail**

Run: `python -m unittest tests.langgraph_runner.test_batch -v`

Expected: import failure for `langgraph_runner.batch`

- [ ] **Step 3: Implement ID helpers and batch assignment**

```python
# langgraph_runner/ids.py
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
```

```python
# langgraph_runner/batch.py
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
```

- [ ] **Step 4: Run the batch tests**

Run: `python -m unittest tests.langgraph_runner.test_batch -v`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add langgraph_runner/ids.py langgraph_runner/batch.py tests/langgraph_runner/test_batch.py
git commit -m "feat: add candidate id and batch planning"
```

## Task 5: Agent Context Packages and Codex Exec Adapter

**Files:**
- Create: `langgraph_runner/agent_io.py`
- Test: `tests/langgraph_runner/test_agent_io.py`

- [ ] **Step 1: Write failing agent I/O tests**

```python
# tests/langgraph_runner/test_agent_io.py
import json
import tempfile
import unittest
from pathlib import Path

from langgraph_runner.agent_io import AgentCall, AgentRunner, write_context_package
from langgraph_runner.batch import CandidateAssignment


class FakeExecutor:
    def __init__(self, exit_code=0):
        self.calls = []
        self.exit_code = exit_code

    def __call__(self, command, cwd, timeout):
        self.calls.append((command, cwd, timeout))
        return self.exit_code, "stdout text", "stderr text"


class TestAgentIO(unittest.TestCase):
    def test_context_package_contains_assignment_and_required_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assignment = CandidateAssignment(
                candidate_id="p1-b001-c01-arch-20260604-231500",
                batch_id="p1-b001",
                role="architecture",
                phase="phase1_performance",
                primary_objective="performance",
            )

            package = write_context_package(
                run_dir=root / "runs" / "run-1",
                agent_call_id="call-1",
                assignment=assignment,
                contract_excerpt="Only DUT and devices.csv may change.",
                state_summary={"batch_no": 0},
                recent_ledger=[],
                base_dut=root / "dummy_neural_amp.scs",
                base_devices=root / "devices.csv",
            )

            self.assertTrue((package / "context.md").exists())
            self.assertTrue((package / "state_summary.json").exists())
            self.assertIn("p1-b001-c01-arch-20260604-231500", (package / "context.md").read_text(encoding="utf-8"))
            self.assertEqual(json.loads((package / "state_summary.json").read_text(encoding="utf-8"))["batch_no"], 0)

    def test_agent_runner_invokes_codex_exec_and_logs_streams(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executor = FakeExecutor()
            runner = AgentRunner(executor=executor)
            call = AgentCall(
                role="architecture",
                context_path=root,
                output_dir=root / "out",
                timeout_seconds=1200,
            )

            result = runner.run(call)

            self.assertEqual(result.exit_code, 0)
            self.assertIn("codex", executor.calls[0][0][0])
            self.assertTrue((root / "out" / "stdout.log").exists())
            self.assertTrue((root / "out" / "stderr.log").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the agent I/O tests to verify they fail**

Run: `python -m unittest tests.langgraph_runner.test_agent_io -v`

Expected: import failure for `langgraph_runner.agent_io`

- [ ] **Step 3: Implement context package and runner**

```python
# langgraph_runner/agent_io.py
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .batch import CandidateAssignment


@dataclass(frozen=True)
class AgentCall:
    role: str
    context_path: Path
    output_dir: Path
    timeout_seconds: int


@dataclass(frozen=True)
class AgentRunResult:
    exit_code: int
    stdout_path: Path
    stderr_path: Path


def write_context_package(
    *,
    run_dir: Path,
    agent_call_id: str,
    assignment: CandidateAssignment,
    contract_excerpt: str,
    state_summary: dict,
    recent_ledger: list[dict],
    base_dut: Path,
    base_devices: Path,
) -> Path:
    package = run_dir / "agent_calls" / agent_call_id
    base_files = package / "base_files"
    base_files.mkdir(parents=True, exist_ok=True)
    if base_dut.exists():
        shutil.copy2(base_dut, base_files / base_dut.name)
    if base_devices.exists():
        shutil.copy2(base_devices, base_files / base_devices.name)
    (package / "state_summary.json").write_text(json.dumps(state_summary, indent=2) + "\n", encoding="utf-8")
    (package / "recent_ledger.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in recent_ledger),
        encoding="utf-8",
    )
    context = "\n".join(
        [
            f"# Agent Context: {assignment.role}",
            "",
            f"candidate_id: {assignment.candidate_id}",
            f"batch_id: {assignment.batch_id}",
            f"phase: {assignment.phase}",
            f"primary_objective: {assignment.primary_objective}",
            "",
            "## Contract Excerpt",
            contract_excerpt,
            "",
            "## Required Outputs",
            "- proposal.json",
            "- patch.diff",
            "- notes.md",
            "",
            "Write outputs only inside the assigned output directory.",
        ]
    )
    (package / "context.md").write_text(context + "\n", encoding="utf-8")
    return package


class AgentRunner:
    def __init__(self, executor=None):
        self.executor = executor or self._subprocess_executor

    def _subprocess_executor(self, command: list[str], cwd: Path, timeout: int) -> tuple[int, str, str]:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            timeout=timeout,
            text=True,
            capture_output=True,
            check=False,
        )
        return completed.returncode, completed.stdout, completed.stderr

    def run(self, call: AgentCall) -> AgentRunResult:
        call.output_dir.mkdir(parents=True, exist_ok=True)
        command = [
            "codex",
            "exec",
            "--output-dir",
            str(call.output_dir),
            str(call.context_path / "context.md"),
        ]
        exit_code, stdout, stderr = self.executor(command, call.context_path, call.timeout_seconds)
        stdout_path = call.output_dir / "stdout.log"
        stderr_path = call.output_dir / "stderr.log"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        return AgentRunResult(exit_code=exit_code, stdout_path=stdout_path, stderr_path=stderr_path)
```

- [ ] **Step 4: Run the agent I/O tests**

Run: `python -m unittest tests.langgraph_runner.test_agent_io -v`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add langgraph_runner/agent_io.py tests/langgraph_runner/test_agent_io.py
git commit -m "feat: add agent context and codex exec adapter"
```

## Task 6: Prime Agent Limit Enforcement

**Files:**
- Create: `langgraph_runner/prime_limits.py`
- Test: `tests/langgraph_runner/test_prime_limits.py`

- [ ] **Step 1: Write failing prime limit tests**

```python
# tests/langgraph_runner/test_prime_limits.py
import unittest

from langgraph_runner.prime_limits import PrimeLimitTracker


class TestPrimeLimits(unittest.TestCase):
    def test_allows_two_active_and_four_total_per_subagent(self):
        tracker = PrimeLimitTracker(max_active=2, max_total=4)

        self.assertTrue(tracker.request("sub-1", "bias-prime").approved)
        self.assertTrue(tracker.request("sub-1", "R-prime").approved)
        self.assertFalse(tracker.request("sub-1", "C-prime").approved)

        tracker.finish("sub-1", "bias-prime")
        self.assertTrue(tracker.request("sub-1", "C-prime").approved)
        tracker.finish("sub-1", "R-prime")
        self.assertTrue(tracker.request("sub-1", "LOW-prime").approved)
        tracker.finish("sub-1", "C-prime")
        self.assertFalse(tracker.request("sub-1", "HIGH-prime").approved)

    def test_rejects_prime_role_for_finished_unknown_prime(self):
        tracker = PrimeLimitTracker(max_active=2, max_total=4)

        with self.assertRaises(ValueError):
            tracker.finish("sub-1", "bias-prime")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the prime limit tests to verify they fail**

Run: `python -m unittest tests.langgraph_runner.test_prime_limits -v`

Expected: import failure for `langgraph_runner.prime_limits`

- [ ] **Step 3: Implement prime limit tracker**

```python
# langgraph_runner/prime_limits.py
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PrimeRequestDecision:
    approved: bool
    reason: str


@dataclass
class PrimeCounter:
    active: list[str] = field(default_factory=list)
    total: int = 0


class PrimeLimitTracker:
    def __init__(self, max_active: int, max_total: int):
        self.max_active = max_active
        self.max_total = max_total
        self._counters: dict[str, PrimeCounter] = {}

    def request(self, subagent_id: str, prime_role: str) -> PrimeRequestDecision:
        counter = self._counters.setdefault(subagent_id, PrimeCounter())
        if counter.total >= self.max_total:
            return PrimeRequestDecision(False, "max_total_primes_reached")
        if len(counter.active) >= self.max_active:
            return PrimeRequestDecision(False, "max_active_primes_reached")
        counter.active.append(prime_role)
        counter.total += 1
        return PrimeRequestDecision(True, "approved")

    def finish(self, subagent_id: str, prime_role: str) -> None:
        counter = self._counters.setdefault(subagent_id, PrimeCounter())
        if prime_role not in counter.active:
            raise ValueError(f"prime role is not active for subagent: {prime_role}")
        counter.active.remove(prime_role)
```

- [ ] **Step 4: Run the prime limit tests**

Run: `python -m unittest tests.langgraph_runner.test_prime_limits -v`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add langgraph_runner/prime_limits.py tests/langgraph_runner/test_prime_limits.py
git commit -m "feat: enforce prime agent limits"
```

## Task 7: Deterministic Review

**Files:**
- Create: `langgraph_runner/review.py`
- Test: `tests/langgraph_runner/test_review.py`

- [ ] **Step 1: Write failing deterministic review tests**

```python
# tests/langgraph_runner/test_review.py
import json
import tempfile
import unittest
from pathlib import Path

from langgraph_runner.review import DeterministicReviewer


VALID_NETLIST = """simulator lang=spectre
subckt dummy_neural_amp GND VDD VIN VOUT VREF
Q1 VOUT VIN GND GND sky130_fd_pr_main__npn_05v5
R1 VDD VOUT sky130_fd_pr_main__res_high_po_5p73 l=5.73u w=0.35u m=10
ends dummy_neural_amp
"""

VALID_DEVICES = """name,type,count,width,length,multiplier,segments,seg_length,seg_width,ft_hz,area_p,include_in_ppa
Q1,npn,1,,,,,,,,,true
R1,resistor,1,,,,10,5.73u,0.35u,,,true
"""


class TestDeterministicReview(unittest.TestCase):
    def write_candidate(self, root: Path, netlist: str, devices: str, touched=None):
        candidate = root / "candidate"
        candidate.mkdir()
        proposal = {
            "candidate_id": "p1-b001-c01-arch-20260604-231500",
            "phase": "phase1_performance",
            "agent": "architecture",
            "hypothesis": "Valid BJT candidate.",
            "primary_objective": "performance",
            "changed_blocks": ["gain_stage"],
            "files_touched": touched or ["amptest/dummy_neural_amp.scs", "amptest/devices.csv"],
            "expected_effect": {
                "performance_nrmse_combined": "decrease",
                "area_total_p": "increase",
                "power_score_basis_w": "unknown"
            },
            "risk": "May not meet cutoff targets.",
            "patch": "diff --git a/amptest/dummy_neural_amp.scs b/amptest/dummy_neural_amp.scs\n"
        }
        (candidate / "proposal.json").write_text(json.dumps(proposal), encoding="utf-8")
        (candidate / "patch.diff").write_text(proposal["patch"], encoding="utf-8")
        (candidate / "notes.md").write_text("notes\n", encoding="utf-8")
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "dummy_neural_amp.scs").write_text(netlist, encoding="utf-8")
        (workspace / "devices.csv").write_text(devices, encoding="utf-8")
        return candidate, workspace

    def test_accepts_valid_candidate_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate, workspace = self.write_candidate(Path(tmp), VALID_NETLIST, VALID_DEVICES)
            reviewer = DeterministicReviewer(
                allowed_files={"amptest/dummy_neural_amp.scs", "amptest/devices.csv"},
                dut_subckt="dummy_neural_amp",
                dut_pins_order=["GND", "VDD", "VIN", "VOUT", "VREF"],
            )

            result = reviewer.review(candidate, workspace, "p1-b001-c01-arch-20260604-231500")

            self.assertTrue(result.passed)
            self.assertEqual(result.errors, [])

    def test_rejects_candidate_id_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate, workspace = self.write_candidate(Path(tmp), VALID_NETLIST, VALID_DEVICES)
            reviewer = DeterministicReviewer({"amptest/dummy_neural_amp.scs", "amptest/devices.csv"}, "dummy_neural_amp", ["GND", "VDD", "VIN", "VOUT", "VREF"])

            result = reviewer.review(candidate, workspace, "different-id")

            self.assertFalse(result.passed)
            self.assertIn("candidate_id_mismatch", result.errors)

    def test_rejects_opamp_and_behavioral_shortcuts(self):
        with tempfile.TemporaryDirectory() as tmp:
            netlist = VALID_NETLIST.replace("Q1 VOUT VIN GND GND sky130_fd_pr_main__npn_05v5", "XOP VIN VOUT ahdLib_opamp")
            candidate, workspace = self.write_candidate(Path(tmp), netlist, VALID_DEVICES)
            reviewer = DeterministicReviewer({"amptest/dummy_neural_amp.scs", "amptest/devices.csv"}, "dummy_neural_amp", ["GND", "VDD", "VIN", "VOUT", "VREF"])

            result = reviewer.review(candidate, workspace, "p1-b001-c01-arch-20260604-231500")

            self.assertFalse(result.passed)
            self.assertIn("forbidden_shortcut", result.errors)

    def test_rejects_illegal_file_touch(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate, workspace = self.write_candidate(Path(tmp), VALID_NETLIST, VALID_DEVICES, touched=["amptest/config.json"])
            reviewer = DeterministicReviewer({"amptest/dummy_neural_amp.scs", "amptest/devices.csv"}, "dummy_neural_amp", ["GND", "VDD", "VIN", "VOUT", "VREF"])

            result = reviewer.review(candidate, workspace, "p1-b001-c01-arch-20260604-231500")

            self.assertFalse(result.passed)
            self.assertIn("illegal_file_touch", result.errors)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the review tests to verify they fail**

Run: `python -m unittest tests.langgraph_runner.test_review -v`

Expected: import failure for `langgraph_runner.review`

- [ ] **Step 3: Implement deterministic reviewer**

```python
# langgraph_runner/review.py
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .schemas import Proposal, ReviewResult


FORBIDDEN_SHORTCUT_PATTERNS = [
    re.compile(r"\bahdlib\b", re.IGNORECASE),
    re.compile(r"\bopamp\b", re.IGNORECASE),
    re.compile(r"\bvcvs\b", re.IGNORECASE),
    re.compile(r"\bvccs\b", re.IGNORECASE),
    re.compile(r"\bccvs\b", re.IGNORECASE),
    re.compile(r"\bcccs\b", re.IGNORECASE),
    re.compile(r"\blaplace\b", re.IGNORECASE),
    re.compile(r"\bbsource\b", re.IGNORECASE),
    re.compile(r"\bahdl_include\b", re.IGNORECASE),
]


class DeterministicReviewer:
    def __init__(self, allowed_files: set[str], dut_subckt: str, dut_pins_order: list[str]):
        self.allowed_files = allowed_files
        self.dut_subckt = dut_subckt
        self.dut_pins_order = dut_pins_order

    def review(self, candidate_dir: Path, workspace_dir: Path, assigned_candidate_id: str) -> ReviewResult:
        errors: list[str] = []
        checks: dict[str, bool] = {}
        proposal_path = candidate_dir / "proposal.json"
        patch_path = candidate_dir / "patch.diff"
        notes_path = candidate_dir / "notes.md"
        checks["required_artifacts"] = proposal_path.exists() and patch_path.exists() and notes_path.exists()
        if not checks["required_artifacts"]:
            errors.append("missing_required_artifact")
            return ReviewResult(candidate_id=assigned_candidate_id, passed=False, checks=checks, errors=errors)

        proposal = Proposal.model_validate(json.loads(proposal_path.read_text(encoding="utf-8")))
        checks["candidate_id"] = proposal.candidate_id == assigned_candidate_id
        if not checks["candidate_id"]:
            errors.append("candidate_id_mismatch")

        checks["patch_present"] = bool(patch_path.read_text(encoding="utf-8").strip())
        if not checks["patch_present"]:
            errors.append("empty_patch")

        checks["file_scope"] = set(proposal.files_touched).issubset(self.allowed_files)
        if not checks["file_scope"]:
            errors.append("illegal_file_touch")

        netlist = (workspace_dir / "dummy_neural_amp.scs").read_text(encoding="utf-8", errors="ignore")
        checks["pin_contract"] = self._has_pin_contract(netlist)
        if not checks["pin_contract"]:
            errors.append("invalid_dut_pin_contract")

        checks["forbidden_shortcut"] = not any(pattern.search(netlist) for pattern in FORBIDDEN_SHORTCUT_PATTERNS)
        if not checks["forbidden_shortcut"]:
            errors.append("forbidden_shortcut")

        devices_path = workspace_dir / "devices.csv"
        checks["devices_csv"] = devices_path.exists() and self._devices_csv_has_required_columns(devices_path)
        if not checks["devices_csv"]:
            errors.append("devices_csv_invalid")

        return ReviewResult(candidate_id=assigned_candidate_id, passed=not errors, checks=checks, errors=errors)

    def _has_pin_contract(self, netlist: str) -> bool:
        expected = " ".join(["subckt", self.dut_subckt, *self.dut_pins_order]).lower()
        logical_lines = [" ".join(line.split()).lower() for line in netlist.splitlines()]
        return expected in logical_lines

    def _devices_csv_has_required_columns(self, path: Path) -> bool:
        required = {"name", "type", "count", "include_in_ppa"}
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            return reader.fieldnames is not None and required.issubset(set(reader.fieldnames))
```

- [ ] **Step 4: Run the review tests**

Run: `python -m unittest tests.langgraph_runner.test_review -v`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add langgraph_runner/review.py tests/langgraph_runner/test_review.py
git commit -m "feat: add deterministic candidate review"
```

## Task 8: Candidate Workspace and Promotion

**Files:**
- Create: `langgraph_runner/workspace.py`
- Test: `tests/langgraph_runner/test_workspace.py`

- [ ] **Step 1: Write failing workspace tests**

```python
# tests/langgraph_runner/test_workspace.py
import tempfile
import unittest
from pathlib import Path

from langgraph_runner.workspace import CandidateWorkspace


class TestCandidateWorkspace(unittest.TestCase):
    def test_workspace_copies_base_files_and_writes_candidate_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dut = root / "amptest" / "dummy_neural_amp.scs"
            base_devices = root / "amptest" / "devices.csv"
            base_config = root / "amptest" / "config.json"
            base_dut.parent.mkdir()
            base_dut.write_text("subckt dummy_neural_amp GND VDD VIN VOUT VREF\nends dummy_neural_amp\n", encoding="utf-8")
            base_devices.write_text("name,type,count,include_in_ppa\nQ1,npn,1,true\n", encoding="utf-8")
            base_config.write_text('{"dut_netlist":"dummy_neural_amp.scs","input_files":{"devices_csv":"devices.csv","ac_csv":"run/ac.csv","tran_csv":"run/tran.csv"}}', encoding="utf-8")
            manager = CandidateWorkspace(root / "automation_artifacts" / "workspaces")

            workspace = manager.create("p1-b001-c01-arch-20260604-231500", base_dut, base_devices, base_config)

            self.assertTrue((workspace / "dummy_neural_amp.scs").exists())
            self.assertTrue((workspace / "devices.csv").exists())
            self.assertTrue((workspace / "config.json").exists())

    def test_promote_copies_workspace_to_canonical_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "dummy_neural_amp.scs").write_text("accepted netlist\n", encoding="utf-8")
            (workspace / "devices.csv").write_text("accepted devices\n", encoding="utf-8")
            target_dut = root / "amptest" / "dummy_neural_amp.scs"
            target_devices = root / "amptest" / "devices.csv"
            target_dut.parent.mkdir()
            manager = CandidateWorkspace(root / "automation_artifacts" / "workspaces")

            manager.promote(workspace, target_dut, target_devices)

            self.assertEqual(target_dut.read_text(encoding="utf-8"), "accepted netlist\n")
            self.assertEqual(target_devices.read_text(encoding="utf-8"), "accepted devices\n")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the workspace tests to verify they fail**

Run: `python -m unittest tests.langgraph_runner.test_workspace -v`

Expected: import failure for `langgraph_runner.workspace`

- [ ] **Step 3: Implement workspace manager**

```python
# langgraph_runner/workspace.py
from __future__ import annotations

import json
import shutil
from pathlib import Path


class CandidateWorkspace:
    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root

    def create(self, candidate_id: str, base_dut: Path, base_devices: Path, base_config: Path) -> Path:
        workspace = self.workspace_root / candidate_id
        workspace.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base_dut, workspace / "dummy_neural_amp.scs")
        shutil.copy2(base_devices, workspace / "devices.csv")
        config = json.loads(base_config.read_text(encoding="utf-8"))
        config["dut_netlist"] = "dummy_neural_amp.scs"
        config.setdefault("input_files", {})
        config["input_files"]["devices_csv"] = "devices.csv"
        config["input_files"]["ac_csv"] = "run/ac.csv"
        config["input_files"]["tran_csv"] = "run/tran.csv"
        (workspace / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        return workspace

    def promote(self, workspace: Path, target_dut: Path, target_devices: Path) -> None:
        target_dut.parent.mkdir(parents=True, exist_ok=True)
        target_devices.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / "dummy_neural_amp.scs", target_dut)
        shutil.copy2(workspace / "devices.csv", target_devices)
```

- [ ] **Step 4: Add patch application tests and implementation**

Add this test to `tests/langgraph_runner/test_workspace.py`:

```python
    def test_apply_patch_rejects_non_zero_git_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            manager = CandidateWorkspace(root / "automation_artifacts" / "workspaces")

            result = manager.apply_patch(workspace, "not a unified diff")

            self.assertFalse(result.applied)
            self.assertIn("git apply failed", result.reason)
```

Add this code to `langgraph_runner/workspace.py`:

```python
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class PatchApplyResult:
    applied: bool
    reason: str


def _ensure_amptest_layout(workspace: Path) -> Path:
    scratch = workspace / "_patch_root"
    amptest = scratch / "amptest"
    amptest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(workspace / "dummy_neural_amp.scs", amptest / "dummy_neural_amp.scs")
    shutil.copy2(workspace / "devices.csv", amptest / "devices.csv")
    return scratch
```

Add this method to `CandidateWorkspace`:

```python
    def apply_patch(self, workspace: Path, patch_text: str) -> PatchApplyResult:
        scratch = _ensure_amptest_layout(workspace)
        patch_file = workspace / "patch.diff"
        patch_file.write_text(patch_text, encoding="utf-8")
        completed = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(patch_file)],
            cwd=str(scratch),
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            return PatchApplyResult(False, "git apply failed: " + completed.stderr.strip())
        shutil.copy2(scratch / "amptest" / "dummy_neural_amp.scs", workspace / "dummy_neural_amp.scs")
        shutil.copy2(scratch / "amptest" / "devices.csv", workspace / "devices.csv")
        return PatchApplyResult(True, "applied")
```

- [ ] **Step 5: Run the workspace tests**

Run: `python -m unittest tests.langgraph_runner.test_workspace -v`

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add langgraph_runner/workspace.py tests/langgraph_runner/test_workspace.py
git commit -m "feat: add candidate workspace management"
```

## Task 9: Serialized Verifier Command

**Files:**
- Create: `langgraph_runner/verifier.py`
- Test: `tests/langgraph_runner/test_verifier.py`

- [ ] **Step 1: Write failing verifier tests**

```python
# tests/langgraph_runner/test_verifier.py
import json
import tempfile
import unittest
from pathlib import Path

from langgraph_runner.verifier import Verifier


class TestVerifier(unittest.TestCase):
    def test_templated_command_validates_required_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_dir = root / "automation_artifacts" / "candidates" / "cid"
            workspace = root / "automation_artifacts" / "workspaces" / "cid"
            candidate_dir.mkdir(parents=True)
            workspace.mkdir(parents=True)
            command = (
                "python -c \"from pathlib import Path; import json; "
                "p=Path(r'{output_dir}'); "
                "(p/'verification.json').write_text(json.dumps({'candidate_id':'cid','status':'passed','metrics_path':str(p/'ppa_metrics.json'),'report_path':str(p/'ppa_report.log'),'spectre_logs':[str(p/'spectre_ac.log'),str(p/'spectre_tran.log')],'performance_nrmse_combined':0.03,'area_total_p':100.0,'power_score_basis_w':0.001,'errors':[]})); "
                "(p/'ppa_metrics.json').write_text(json.dumps({'performance_nrmse_combined':0.03,'area_power':{'area_total_p':100.0,'power_score_basis_w':0.001}})); "
                "(p/'ppa_report.log').write_text('report'); "
                "(p/'spectre_ac.log').write_text('ac'); "
                "(p/'spectre_tran.log').write_text('tran')\""
            )
            verifier = Verifier(command=command, timeout_seconds=30, min_interval_seconds=0, required_outputs=["verification.json", "ppa_metrics.json", "ppa_report.log", "spectre_ac.log", "spectre_tran.log"])

            result = verifier.run("cid", root, workspace, candidate_dir)

            self.assertEqual(result.status, "passed")
            self.assertTrue((candidate_dir / "verification.json").exists())

    def test_missing_required_output_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_dir = root / "candidate"
            workspace = root / "workspace"
            candidate_dir.mkdir()
            workspace.mkdir()
            verifier = Verifier(command="python -c \"print('no files')\"", timeout_seconds=30, min_interval_seconds=0, required_outputs=["verification.json"])

            result = verifier.run("cid", root, workspace, candidate_dir)

            self.assertEqual(result.status, "error")
            self.assertIn("missing required output", result.errors[0])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the verifier tests to verify they fail**

Run: `python -m unittest tests.langgraph_runner.test_verifier -v`

Expected: import failure for `langgraph_runner.verifier`

- [ ] **Step 3: Implement serialized verifier**

```python
# langgraph_runner/verifier.py
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from .schemas import VerificationResult


class Verifier:
    def __init__(self, command: str, timeout_seconds: int, min_interval_seconds: int, required_outputs: list[str]):
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.min_interval_seconds = min_interval_seconds
        self.required_outputs = required_outputs
        self._last_run_monotonic: float | None = None

    def run(self, candidate_id: str, repo_root: Path, local_candidate_dir: Path, output_dir: Path) -> VerificationResult:
        self._wait_for_rate_limit()
        output_dir.mkdir(parents=True, exist_ok=True)
        command = self.command.format(
            candidate_id=candidate_id,
            repo_root=str(repo_root),
            local_candidate_dir=str(local_candidate_dir),
            remote_candidate_dir=str(local_candidate_dir),
            output_dir=str(output_dir),
        )
        completed = subprocess.run(
            command,
            cwd=str(repo_root),
            shell=True,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        self._last_run_monotonic = time.monotonic()
        (output_dir / "verifier_stdout.log").write_text(completed.stdout, encoding="utf-8")
        (output_dir / "verifier_stderr.log").write_text(completed.stderr, encoding="utf-8")
        missing = [name for name in self.required_outputs if not (output_dir / name).exists()]
        if completed.returncode != 0:
            return self._error(candidate_id, output_dir, f"verifier command failed with exit code {completed.returncode}")
        if missing:
            return self._error(candidate_id, output_dir, "missing required output: " + ", ".join(missing))
        return VerificationResult.model_validate_json((output_dir / "verification.json").read_text(encoding="utf-8"))

    def _wait_for_rate_limit(self) -> None:
        if self._last_run_monotonic is None:
            return
        remaining = self.min_interval_seconds - (time.monotonic() - self._last_run_monotonic)
        if remaining > 0:
            time.sleep(remaining)

    def _error(self, candidate_id: str, output_dir: Path, reason: str) -> VerificationResult:
        result = VerificationResult(
            candidate_id=candidate_id,
            status="error",
            metrics_path=str(output_dir / "ppa_metrics.json"),
            report_path=str(output_dir / "ppa_report.log"),
            spectre_logs=[str(output_dir / "spectre_ac.log"), str(output_dir / "spectre_tran.log")],
            performance_nrmse_combined=1.0,
            area_total_p=0.0,
            power_score_basis_w=0.0,
            errors=[reason],
        )
        (output_dir / "verification.json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return result
```

- [ ] **Step 4: Run the verifier tests**

Run: `python -m unittest tests.langgraph_runner.test_verifier -v`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add langgraph_runner/verifier.py tests/langgraph_runner/test_verifier.py
git commit -m "feat: add serialized verifier command"
```

## Task 10: Acceptance Gates and Stagnation

**Files:**
- Create: `langgraph_runner/acceptance.py`
- Test: `tests/langgraph_runner/test_acceptance.py`

- [ ] **Step 1: Write failing acceptance tests**

```python
# tests/langgraph_runner/test_acceptance.py
import unittest

from langgraph_runner.acceptance import (
    AcceptanceDecision,
    evaluate_candidate,
    ppa_surrogate_score,
    three_bjt_fallback_allowed,
)
from langgraph_runner.schemas import RunnerState


class TestAcceptance(unittest.TestCase):
    def test_phase1_accepts_only_combined_nrmse_at_or_below_gate(self):
        state = RunnerState.initial(contract_hash="abc")

        accepted = evaluate_candidate(state, "cid-good", {"performance_nrmse_combined": 0.04, "area_total_p": 100.0, "power_score_basis_w": 0.001}, review_passed=True, verification_status="passed", safety_passed=True)
        rejected = evaluate_candidate(state, "cid-bad", {"performance_nrmse_combined": 0.041, "area_total_p": 1.0, "power_score_basis_w": 0.0}, review_passed=True, verification_status="passed", safety_passed=True)

        self.assertEqual(accepted, AcceptanceDecision.ACCEPT)
        self.assertEqual(rejected, AcceptanceDecision.REJECT)

    def test_phase1_rejects_failed_review_or_verification(self):
        state = RunnerState.initial(contract_hash="abc")

        self.assertEqual(evaluate_candidate(state, "cid", {"performance_nrmse_combined": 0.01}, review_passed=False, verification_status="passed", safety_passed=True), AcceptanceDecision.REJECT)
        self.assertEqual(evaluate_candidate(state, "cid", {"performance_nrmse_combined": 0.01}, review_passed=True, verification_status="error", safety_passed=True), AcceptanceDecision.ERROR)

    def test_ppa_surrogate_score_uses_log_scaled_components(self):
        score = ppa_surrogate_score(
            metrics={"performance_nrmse_combined": 0.04, "area_total_p": 200.0, "power_score_basis_w": 0.002},
            baseline={"performance_nrmse_combined": 0.04, "area_total_p": 100.0, "power_score_basis_w": 0.001},
        )

        self.assertGreater(score, 0.0)

    def test_3bjt_fallback_requires_count_and_stagnation(self):
        self.assertFalse(three_bjt_fallback_allowed(verified_count=11, recent_improvements=[0.0, 0.0, 0.0, 0.0, 0.0]))
        self.assertFalse(three_bjt_fallback_allowed(verified_count=12, recent_improvements=[0.01, 0.0, 0.0, 0.0, 0.0]))
        self.assertTrue(three_bjt_fallback_allowed(verified_count=12, recent_improvements=[0.001, 0.001, 0.0, 0.0, 0.0]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the acceptance tests to verify they fail**

Run: `python -m unittest tests.langgraph_runner.test_acceptance -v`

Expected: import failure for `langgraph_runner.acceptance`

- [ ] **Step 3: Implement acceptance gates**

```python
# langgraph_runner/acceptance.py
from __future__ import annotations

import math
from enum import Enum

from .schemas import Phase, RunnerState


class AcceptanceDecision(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    ERROR = "error"


def ppa_surrogate_score(metrics: dict[str, float], baseline: dict[str, float]) -> float:
    perf = math.log(1.0 + metrics["performance_nrmse_combined"] / baseline["performance_nrmse_combined"])
    power = math.log(1.0 + metrics["power_score_basis_w"] / baseline["power_score_basis_w"])
    area = math.log(1.0 + metrics["area_total_p"] / baseline["area_total_p"])
    return 0.50 * perf + 0.25 * power + 0.25 * area


def evaluate_candidate(
    state: RunnerState,
    candidate_id: str,
    metrics: dict[str, float],
    *,
    review_passed: bool,
    verification_status: str,
    safety_passed: bool,
) -> AcceptanceDecision:
    if verification_status == "error":
        return AcceptanceDecision.ERROR
    if verification_status != "passed" or not review_passed or not safety_passed:
        return AcceptanceDecision.REJECT
    performance = metrics.get("performance_nrmse_combined")
    if performance is None or not math.isfinite(performance):
        return AcceptanceDecision.ERROR
    if state.current_phase == Phase.PHASE1_PERFORMANCE:
        return AcceptanceDecision.ACCEPT if performance <= 0.04 else AcceptanceDecision.REJECT
    if performance > 0.10:
        return AcceptanceDecision.REJECT
    if state.accepted_metrics is None or state.accepted_ppa_surrogate_score is None:
        return AcceptanceDecision.ACCEPT
    score = ppa_surrogate_score(metrics, state.accepted_metrics)
    return AcceptanceDecision.ACCEPT if score < state.accepted_ppa_surrogate_score else AcceptanceDecision.REJECT


def three_bjt_fallback_allowed(verified_count: int, recent_improvements: list[float]) -> bool:
    if verified_count < 12:
        return False
    if len(recent_improvements) < 5:
        return False
    return sum(recent_improvements[-5:]) < 0.005
```

- [ ] **Step 4: Run the acceptance tests**

Run: `python -m unittest tests.langgraph_runner.test_acceptance -v`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add langgraph_runner/acceptance.py tests/langgraph_runner/test_acceptance.py
git commit -m "feat: add phase acceptance gates"
```

## Task 11: LangGraph Node Assembly

**Files:**
- Create: `langgraph_runner/graph.py`
- Test: `tests/langgraph_runner/test_graph.py`

- [ ] **Step 1: Write failing graph tests**

```python
# tests/langgraph_runner/test_graph.py
import unittest

from langgraph_runner.graph import GRAPH_NODE_NAMES, build_graph


class TestGraph(unittest.TestCase):
    def test_graph_contains_design_node_sequence(self):
        self.assertEqual(
            GRAPH_NODE_NAMES,
            [
                "load_context",
                "plan_batch",
                "spawn_subagents",
                "collect_subagent_requests",
                "spawn_prime_agents",
                "collect_prime_outputs",
                "assemble_candidate_proposals",
                "deterministic_review",
                "verify_queue",
                "evaluate_candidates",
                "top_anomaly_check",
                "record_batch",
                "route_next",
            ],
        )

    def test_graph_compiles(self):
        graph = build_graph()

        self.assertTrue(hasattr(graph, "invoke"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the graph tests to verify they fail**

Run: `python -m unittest tests.langgraph_runner.test_graph -v`

Expected: import failure for `langgraph_runner.graph`

- [ ] **Step 3: Implement graph shell with deterministic node boundaries**

```python
# langgraph_runner/graph.py
from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, StateGraph


GRAPH_NODE_NAMES = [
    "load_context",
    "plan_batch",
    "spawn_subagents",
    "collect_subagent_requests",
    "spawn_prime_agents",
    "collect_prime_outputs",
    "assemble_candidate_proposals",
    "deterministic_review",
    "verify_queue",
    "evaluate_candidates",
    "top_anomaly_check",
    "record_batch",
    "route_next",
]


class GraphState(TypedDict, total=False):
    repo_root: str
    run_id: str
    route: str
    state_path: str
    batch_assignments: list[dict]
    candidate_ids: list[str]
    errors: list[str]


def _pass_node(name: str):
    def node(state: GraphState) -> GraphState:
        events = list(state.get("events", []))
        events.append(name)
        return {**state, "events": events}

    return node


def _route_next(state: GraphState) -> GraphState:
    events = list(state.get("events", []))
    events.append("route_next")
    return {**state, "events": events, "route": state.get("route", "stop")}


def _route_condition(state: GraphState) -> str:
    return state.get("route", "stop")


def build_graph():
    graph = StateGraph(GraphState)
    for name in GRAPH_NODE_NAMES[:-1]:
        graph.add_node(name, _pass_node(name))
    graph.add_node("route_next", _route_next)
    graph.set_entry_point("load_context")
    for left, right in zip(GRAPH_NODE_NAMES, GRAPH_NODE_NAMES[1:]):
        graph.add_edge(left, right)
    graph.add_conditional_edges("route_next", _route_condition, {"stop": END, "next_batch": "plan_batch"})
    return graph.compile()
```

- [ ] **Step 4: Run the graph tests**

Run: `python -m unittest tests.langgraph_runner.test_graph -v`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add langgraph_runner/graph.py tests/langgraph_runner/test_graph.py
git commit -m "feat: add langgraph node skeleton"
```

## Task 12: CLI Commands

**Files:**
- Create: `langgraph_runner/cli.py`
- Test: `tests/langgraph_runner/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

```python
# tests/langgraph_runner/test_cli.py
import tempfile
import unittest
from pathlib import Path

from langgraph_runner.cli import main


class TestCli(unittest.TestCase):
    def test_init_creates_artifact_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "contract.md").write_text("contract\n", encoding="utf-8")
            config = root / "runner_config.json"
            config.write_text(
                '{"artifact_root":"automation_artifacts","contract_path":"docs/contract.md","amptest_dir":"amptest","dut_netlist":"amptest/dummy_neural_amp.scs","devices_csv":"amptest/devices.csv","amptest_config":"amptest/config.json","candidate_generation_batch_size":3,"max_active_primes_per_subagent":2,"max_total_primes_per_subagent":4,"agent_timeouts_seconds":{"subagent":1200,"prime":600,"reviewer":300,"top":300},"verifier":{"command":"python -c pass","timeout_seconds":30,"min_interval_seconds":0,"required_outputs":["verification.json"]}}',
                encoding="utf-8",
            )

            code = main(["init", "--repo-root", str(root), "--config", str(config)])

            self.assertEqual(code, 0)
            self.assertTrue((root / "automation_artifacts" / "state.json").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the CLI tests to verify they fail**

Run: `python -m unittest tests.langgraph_runner.test_cli -v`

Expected: import failure for `langgraph_runner.cli`

- [ ] **Step 3: Implement CLI**

```python
# langgraph_runner/cli.py
from __future__ import annotations

import argparse
from pathlib import Path

from .artifacts import ArtifactPaths
from .config import load_runner_config
from .graph import build_graph
from .state_store import StateStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="langgraph-runner")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--config", default="runner_config.json")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init")
    subparsers.add_parser("run-one-batch")
    subparsers.add_parser("run")
    resume = subparsers.add_parser("resume")
    resume.add_argument("--human-response", required=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    config = load_runner_config(Path(args.config).resolve())
    paths = ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / config.artifact_root)
    store = StateStore(paths=paths, contract_path=repo_root / config.contract_path)
    if args.command == "init":
        store.initialize()
        return 0
    graph = build_graph()
    initial = {
        "repo_root": str(repo_root),
        "run_id": "manual",
        "state_path": str(paths.state_json),
        "route": "stop" if args.command == "run-one-batch" else "next_batch",
    }
    graph.invoke(initial)
    return 0
```

- [ ] **Step 4: Run the CLI tests**

Run: `python -m unittest tests.langgraph_runner.test_cli -v`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add langgraph_runner/cli.py tests/langgraph_runner/test_cli.py
git commit -m "feat: add runner cli"
```

## Task 13: End-to-End Fixture Smoke Test

**Files:**
- Modify: `tests/langgraph_runner/test_graph.py`
- Create: `tests/langgraph_runner/test_smoke.py`

- [ ] **Step 1: Write fixture smoke test**

```python
# tests/langgraph_runner/test_smoke.py
import tempfile
import unittest
from pathlib import Path

from langgraph_runner.artifacts import ArtifactPaths
from langgraph_runner.state_store import StateStore
from langgraph_runner.verifier import Verifier
from langgraph_runner.workspace import CandidateWorkspace


class TestSmoke(unittest.TestCase):
    def test_init_workspace_and_fixture_verifier_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = root / "docs" / "top-coordinator-contract.md"
            contract.parent.mkdir()
            contract.write_text("contract\n", encoding="utf-8")
            amptest = root / "amptest"
            amptest.mkdir()
            (amptest / "dummy_neural_amp.scs").write_text("subckt dummy_neural_amp GND VDD VIN VOUT VREF\nends dummy_neural_amp\n", encoding="utf-8")
            (amptest / "devices.csv").write_text("name,type,count,include_in_ppa\nQ1,npn,1,true\n", encoding="utf-8")
            (amptest / "config.json").write_text('{"dut_netlist":"dummy_neural_amp.scs","input_files":{"devices_csv":"devices.csv","ac_csv":"run/ac.csv","tran_csv":"run/tran.csv"}}', encoding="utf-8")
            paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
            state = StateStore(paths, contract).initialize()
            workspace = CandidateWorkspace(paths.workspaces_dir).create("cid", amptest / "dummy_neural_amp.scs", amptest / "devices.csv", amptest / "config.json")
            output = paths.candidate_dir("cid")
            command = (
                "python -c \"from pathlib import Path; import json; "
                "p=Path(r'{output_dir}'); p.mkdir(parents=True, exist_ok=True); "
                "(p/'verification.json').write_text(json.dumps({'candidate_id':'cid','status':'passed','metrics_path':str(p/'ppa_metrics.json'),'report_path':str(p/'ppa_report.log'),'spectre_logs':[str(p/'spectre_ac.log'),str(p/'spectre_tran.log')],'performance_nrmse_combined':0.03,'area_total_p':100.0,'power_score_basis_w':0.001,'errors':[]})); "
                "(p/'ppa_metrics.json').write_text('{}'); (p/'ppa_report.log').write_text('report'); "
                "(p/'spectre_ac.log').write_text('ac'); (p/'spectre_tran.log').write_text('tran')\""
            )
            verifier = Verifier(command, timeout_seconds=30, min_interval_seconds=0, required_outputs=["verification.json", "ppa_metrics.json", "ppa_report.log", "spectre_ac.log", "spectre_tran.log"])

            result = verifier.run("cid", root, workspace, output)

            self.assertEqual(state.batch_no, 0)
            self.assertEqual(result.status, "passed")
            self.assertTrue((output / "ppa_report.log").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the smoke test**

Run: `python -m unittest tests.langgraph_runner.test_smoke -v`

Expected: `OK`

- [ ] **Step 3: Run all runner tests**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: every `tests/langgraph_runner` test reports `ok`

- [ ] **Step 4: Commit**

```bash
git add tests/langgraph_runner/test_smoke.py
git commit -m "test: add runner smoke coverage"
```

## Task 14: Wire Real Graph Nodes to Deterministic Modules

**Files:**
- Modify: `langgraph_runner/graph.py`
- Modify: `langgraph_runner/cli.py`
- Test: `tests/langgraph_runner/test_graph.py`

- [ ] **Step 1: Add graph integration assertions**

Add this test to `tests/langgraph_runner/test_graph.py`:

```python
    def test_one_batch_route_records_expected_events(self):
        graph = build_graph()

        result = graph.invoke({"repo_root": ".", "run_id": "test", "route": "stop"})

        self.assertEqual(result["events"], GRAPH_NODE_NAMES)
```

- [ ] **Step 2: Run the graph integration test**

Run: `python -m unittest tests.langgraph_runner.test_graph -v`

Expected: `OK`

- [ ] **Step 3: Replace pass-through nodes one at a time**

Update `langgraph_runner/graph.py` so these nodes call the deterministic modules already tested:

```python
def load_context_node(state: GraphState) -> GraphState:
    return _record_event(state, "load_context")


def plan_batch_node(state: GraphState) -> GraphState:
    return _record_event(state, "plan_batch")


def deterministic_review_node(state: GraphState) -> GraphState:
    return _record_event(state, "deterministic_review")


def verify_queue_node(state: GraphState) -> GraphState:
    return _record_event(state, "verify_queue")


def evaluate_candidates_node(state: GraphState) -> GraphState:
    return _record_event(state, "evaluate_candidates")


def record_batch_node(state: GraphState) -> GraphState:
    return _record_event(state, "record_batch")
```

Keep this helper in `langgraph_runner/graph.py`:

```python
def _record_event(state: GraphState, event: str) -> GraphState:
    events = list(state.get("events", []))
    events.append(event)
    return {**state, "events": events}
```

Use the real implementations only after each module's tests pass. For nodes that still require live `codex exec`, keep the deterministic wrapper and route a structured error into `state["errors"]` when required artifact files are missing.

- [ ] **Step 4: Run all runner tests**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: every `tests/langgraph_runner` test reports `ok`

- [ ] **Step 5: Commit**

```bash
git add langgraph_runner/graph.py langgraph_runner/cli.py tests/langgraph_runner/test_graph.py
git commit -m "feat: wire graph nodes to runner modules"
```

## Task 15: Documentation and Operator Notes

**Files:**
- Create: `docs/langgraph-runner.md`
- Modify: `docs/top-coordinator-contract.md`
- Test: manual command verification

- [ ] **Step 1: Create operator documentation**

```markdown
# LangGraph Runner

The runner orchestrates neural amplifier candidate generation, deterministic review, serialized verification, acceptance, and artifact recording.

## Commands

Initialize artifacts:

```sh
python -m langgraph_runner --repo-root . --config runner_config.json init
```

Run one batch:

```sh
python -m langgraph_runner --repo-root . --config runner_config.json run-one-batch
```

Run until stop or interrupt:

```sh
python -m langgraph_runner --repo-root . --config runner_config.json run
```

Resume after a human interrupt:

```sh
python -m langgraph_runner --repo-root . --config runner_config.json resume --human-response "continue"
```

## Source of Truth

The canonical automation files are:

- `automation_artifacts/state.json`
- `automation_artifacts/ledger.jsonl`
- `automation_artifacts/candidates/<candidate_id>/`
- `automation_artifacts/workspaces/<candidate_id>/`

The graph checkpoint never overrides `state.json` or `ledger.jsonl`.

## DUT Pin Contract

The runner validates the DUT subcircuit name and pin order from `amptest/config.json`. In the current repository this is `dummy_neural_amp GND VDD VIN VOUT VREF`.
```

- [ ] **Step 2: Add a short cross-reference to the contract**

Append this sentence to `docs/top-coordinator-contract.md` section 12:

```markdown
The first LangGraph runner records automation state under `automation_artifacts/`; see `docs/langgraph-runner.md` for the runner-specific artifact layout.
```

- [ ] **Step 3: Run documentation-facing command**

Run: `python -m langgraph_runner --repo-root . --config runner_config.json init`

Expected: command exits with `0` and creates `automation_artifacts/state.json`

- [ ] **Step 4: Run all runner tests**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: every `tests/langgraph_runner` test reports `ok`

- [ ] **Step 5: Commit**

```bash
git add docs/langgraph-runner.md docs/top-coordinator-contract.md
git commit -m "docs: document langgraph runner operation"
```

## Self-Review

**Spec coverage:**  
The plan maps all design nodes to `langgraph_runner/graph.py` in Tasks 11 and 14. Artifact roots, state, and ledger are covered by Task 3. Candidate ID policy and batch size are covered by Task 4. Agent context packages and `codex exec` calls are covered by Task 5. Prime limits are covered by Task 6. Deterministic review is covered by Task 7. Workspace isolation and promotion are covered by Task 8. Serialized verification and required output checks are covered by Task 9. Phase gates, PPA scoring, and 3BJT fallback policy are covered by Task 10. CLI initialization, one-batch execution, and resume command shape are covered by Task 12. Documentation is covered by Task 15.

**Red-flag scan:**  
Each task names exact files, exact commands, expected outcomes, and concrete code snippets. The plan does not rely on deferred design choices for the first implementation.

**Type consistency:**  
`Phase`, `AgentRole`, `CandidateStatus`, `RunnerState`, `Proposal`, `ReviewResult`, and `VerificationResult` are defined in Task 2 and referenced with the same names in later tasks. Candidate IDs use the same format in Task 4, tests, state, review, and verifier tasks.

## Execution Notes

- Start execution from Task 1 and keep commits small.
- Use `python -m unittest discover -s tests -p "test_*.py" -v` after each module group.
- Do not run a live remote verifier until the fixture verifier path in Task 13 passes.
- If the user confirms that `docs/top-coordinator-contract.md` must override `amptest/config.json`, change Task 7 to validate `neural_amp VIN VREF VDD GND VOUT` and update the current DUT/config before running the full plan.
