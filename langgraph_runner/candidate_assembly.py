from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_outputs import ParsedSubagentOutput
from .artifacts import ArtifactPaths
from .workspace import CandidateWorkspace


@dataclass(frozen=True)
class CandidateAssemblyResult:
    candidate_id: str
    status: str
    errors: list[str]
    candidate_dir: Path
    workspace_dir: Path

    def to_state(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "status": self.status,
            "errors": list(self.errors),
            "candidate_dir": str(self.candidate_dir),
            "workspace_dir": str(self.workspace_dir),
        }


class CandidateAssembler:
    def __init__(self, *, paths: ArtifactPaths, repo_root: Path, config: dict[str, Any]):
        self.paths = paths
        self.repo_root = repo_root
        self.config = config
        self.workspace = CandidateWorkspace(paths.workspaces_dir)

    def assemble(self, assignment: dict[str, Any], parsed: ParsedSubagentOutput | dict[str, Any] | None) -> CandidateAssemblyResult:
        candidate_id = str(assignment["candidate_id"])
        candidate_dir = self.paths.candidate_dir(candidate_id)
        workspace_dir = self.paths.workspace_dir(candidate_id)
        candidate_dir.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []

        if parsed is None:
            return self._write_error(candidate_id, candidate_dir, workspace_dir, ["missing_valid_subagent_output"])
        parsed_output = _coerce_parsed_output(parsed)
        if not parsed_output.valid or parsed_output.proposal is None:
            errors.extend(parsed_output.errors or ["invalid_subagent_output"])
            return self._write_error(candidate_id, candidate_dir, workspace_dir, errors)

        errors.extend(_assignment_echo_errors(assignment, parsed_output.proposal.model_dump(mode="json")))
        if errors:
            return self._write_error(candidate_id, candidate_dir, workspace_dir, errors)

        try:
            shutil.copy2(parsed_output.proposal_path, candidate_dir / "proposal.json")
            shutil.copy2(parsed_output.patch_path, candidate_dir / "patch.diff")
            shutil.copy2(parsed_output.notes_path, candidate_dir / "notes.md")
            workspace_dir = self.workspace.create(
                candidate_id,
                _repo_path(self.repo_root, self.config["dut_netlist"]),
                _repo_path(self.repo_root, self.config["devices_csv"]),
                _repo_path(self.repo_root, self.config["amptest_config"]),
            )
            patch_text = parsed_output.patch_path.read_text(encoding="utf-8")
            patch_result = self.workspace.apply_patch(workspace_dir, patch_text)
            if not patch_result.applied:
                errors.append(f"patch_apply_failed: {patch_result.reason}")
        except (OSError, KeyError, ValueError) as exc:
            errors.append(f"assembly_error: {exc}")

        if errors:
            return self._write_error(candidate_id, candidate_dir, workspace_dir, errors)

        result = CandidateAssemblyResult(candidate_id, "assembled", [], candidate_dir, workspace_dir)
        (candidate_dir / "assembly.json").write_text(json.dumps(result.to_state(), indent=2) + "\n", encoding="utf-8")
        return result

    def _write_error(
        self,
        candidate_id: str,
        candidate_dir: Path,
        workspace_dir: Path,
        errors: list[str],
    ) -> CandidateAssemblyResult:
        result = CandidateAssemblyResult(candidate_id, "error", errors, candidate_dir, workspace_dir)
        candidate_dir.mkdir(parents=True, exist_ok=True)
        (candidate_dir / "assembly.json").write_text(json.dumps(result.to_state(), indent=2) + "\n", encoding="utf-8")
        return result


def _repo_path(repo_root: Path, value: str) -> Path:
    repo = repo_root.resolve()
    path = Path(value)
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (repo / path).resolve()
    try:
        resolved.relative_to(repo)
    except ValueError as exc:
        raise ValueError(f"path_outside_repo: {value}") from exc
    return resolved


def _assignment_echo_errors(assignment: dict[str, Any], proposal: dict[str, Any]) -> list[str]:
    checks = {
        "candidate_id": proposal.get("candidate_id") == assignment.get("candidate_id"),
        "phase": proposal.get("phase") == assignment.get("phase"),
        "agent": proposal.get("agent") == assignment.get("role"),
        "primary_objective": proposal.get("primary_objective") == assignment.get("primary_objective"),
    }
    if all(checks.values()):
        return []
    return ["assignment_echo_mismatch"]


def _coerce_parsed_output(value: ParsedSubagentOutput | dict[str, Any]) -> ParsedSubagentOutput:
    if isinstance(value, ParsedSubagentOutput):
        return value
    from .agent_outputs import parse_subagent_output

    return parse_subagent_output(
        Path(str(value["output_dir"])),
        str(value["candidate_id"]),
        agent_call_id=str(value["agent_call_id"]),
    )
