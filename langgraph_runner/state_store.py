from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from .artifacts import ArtifactPaths
from .schemas import AgentRole, CandidateStatus, LedgerEntry, Phase, RunnerState


def contract_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_finite_metrics(metrics: dict[str, float]) -> None:
    for name, value in metrics.items():
        if not math.isfinite(value):
            raise ValueError(f"metric '{name}' must be finite")


def _validate_finite_ppa_surrogate_score(ppa_surrogate_score: float | None) -> None:
    if ppa_surrogate_score is not None and not math.isfinite(ppa_surrogate_score):
        raise ValueError("ppa_surrogate_score must be finite")


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
        _validate_finite_metrics(metrics)
        _validate_finite_ppa_surrogate_score(ppa_surrogate_score)
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
