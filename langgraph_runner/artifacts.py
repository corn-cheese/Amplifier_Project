from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def _safe_fragment(value: str) -> str:
    path = Path(value)
    if not value:
        raise ValueError("path fragment must not be empty")
    if value == ".":
        raise ValueError("path fragment must not be current directory")
    if path.drive:
        raise ValueError("path fragment must not contain a drive")
    if path.is_absolute():
        raise ValueError("path fragment must be relative")
    if "/" in value or "\\" in value:
        raise ValueError("path fragment must not contain path separators")
    if any(".." in part for part in path.parts):
        raise ValueError("path fragment must not contain '..'")
    return value


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
        return self.candidates_dir / _safe_fragment(candidate_id)

    def workspace_dir(self, candidate_id: str) -> Path:
        return self.workspaces_dir / _safe_fragment(candidate_id)

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / _safe_fragment(run_id)

    def ensure_root(self) -> None:
        for path in (self.artifact_root, self.runs_dir, self.candidates_dir, self.workspaces_dir):
            path.mkdir(parents=True, exist_ok=True)
        if not self.ledger_jsonl.exists():
            self.ledger_jsonl.write_text("", encoding="utf-8")
