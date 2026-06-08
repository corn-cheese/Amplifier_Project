from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_errors import AGENT_EXECUTION_ERROR_CLASSES
from .agent_outputs import ParsedSubagentOutput
from .artifacts import ArtifactPaths, _safe_fragment
from .workspace import CandidateWorkspace, resolve_candidate_base_files


@dataclass(frozen=True)
class CandidateAssemblyResult:
    candidate_id: str
    status: str
    errors: list[str]
    candidate_dir: Path
    workspace_dir: Path
    error_class: str | None = None
    reason: str | None = None

    def to_state(self) -> dict[str, Any]:
        state = {
            "candidate_id": self.candidate_id,
            "status": self.status,
            "errors": list(self.errors),
            "candidate_dir": str(self.candidate_dir),
            "workspace_dir": str(self.workspace_dir),
        }
        if self.error_class is not None:
            state["error_class"] = self.error_class
        if self.reason is not None:
            state["reason"] = self.reason
        return state


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
            if _has_missing_required_output(errors):
                errors.append("missing_valid_subagent_output")
            return self._write_error(candidate_id, candidate_dir, workspace_dir, errors)

        errors.extend(_assignment_echo_errors(assignment, parsed_output.proposal.model_dump(mode="json")))
        if errors:
            return self._write_error(candidate_id, candidate_dir, workspace_dir, errors)

        try:
            shutil.copy2(parsed_output.proposal_path, candidate_dir / "proposal.json")
            shutil.copy2(parsed_output.patch_path, candidate_dir / "patch.diff")
            shutil.copy2(parsed_output.notes_path, candidate_dir / "notes.md")
            base_dut, base_devices = resolve_candidate_base_files(self.repo_root, self.config)
            workspace_dir = self.workspace.create(
                candidate_id,
                base_dut,
                base_devices,
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
        error_class, reason = _classify_assembly_error(errors)
        result = CandidateAssemblyResult(candidate_id, "error", errors, candidate_dir, workspace_dir, error_class, reason)
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


def _has_missing_required_output(errors: list[str]) -> bool:
    return any(error in {"agent_output_path_missing", "missing_agent_output", "missing_proposal", "missing_patch", "missing_notes"} for error in errors)


def _classify_assembly_error(errors: list[str]) -> tuple[str | None, str | None]:
    for error in errors:
        if error in AGENT_EXECUTION_ERROR_CLASSES:
            return error, error
    if "missing_valid_subagent_output" in errors:
        return "agent_output_missing", "agent_output_missing"
    if any(error == "agent_output_path_missing" for error in errors):
        return "agent_output_missing", "agent_output_missing"
    if any(error.startswith("patch_apply_failed") for error in errors):
        return "candidate_patch_failed", "candidate_patch_failed"
    if any(_is_invalid_output_error(error) for error in errors):
        return "agent_output_invalid", "agent_output_invalid"
    return None, None


def _is_invalid_output_error(error: str) -> bool:
    return (
        error.startswith("invalid_proposal")
        or error.startswith("invalid_prime_request")
        or error.startswith("invalid_prime_requests")
        or error in {"empty_patch", "empty_notes"}
    )


def _coerce_parsed_output(value: ParsedSubagentOutput | dict[str, Any]) -> ParsedSubagentOutput:
    if isinstance(value, ParsedSubagentOutput):
        return value
    from .agent_outputs import parse_subagent_output

    state_errors = [str(error) for error in value.get("errors", [])]
    if _state_has_non_parseable_error(value, state_errors):
        error_class = str(value.get("error_class") or "")
        if error_class and error_class not in state_errors:
            state_errors = [error_class, *state_errors]
        output_dir = _non_artifact_error_output_dir(value)
        return ParsedSubagentOutput(
            candidate_id=str(value["candidate_id"]),
            agent_call_id=str(value["agent_call_id"]),
            output_dir=output_dir,
            valid=False,
            errors=state_errors or [str(value.get("error_class") or "invalid_subagent_output")],
            proposal=None,
            proposal_path=output_dir / "proposal.json",
            patch_path=output_dir / "patch.diff",
            notes_path=output_dir / "notes.md",
            prime_requests=[],
        )

    parsed = parse_subagent_output(
        Path(str(value["output_dir"])),
        str(value["candidate_id"]),
        agent_call_id=str(value["agent_call_id"]),
    )
    merged_errors = [*parsed.errors]
    for error in state_errors:
        if error not in merged_errors:
            merged_errors.append(error)
    if merged_errors == parsed.errors:
        return parsed
    return ParsedSubagentOutput(
        candidate_id=parsed.candidate_id,
        agent_call_id=parsed.agent_call_id,
        output_dir=parsed.output_dir,
        valid=False,
        errors=merged_errors,
        proposal=parsed.proposal,
        proposal_path=parsed.proposal_path,
        patch_path=parsed.patch_path,
        notes_path=parsed.notes_path,
        prime_requests=[],
    )


def _state_has_non_parseable_error(value: dict[str, Any], state_errors: list[str]) -> bool:
    error_class = str(value.get("error_class") or "")
    if error_class in AGENT_EXECUTION_ERROR_CLASSES:
        return True
    if any(error in AGENT_EXECUTION_ERROR_CLASSES for error in state_errors):
        return True
    return "agent_output_path_missing" in state_errors


def _non_artifact_error_output_dir(value: dict[str, Any]) -> Path:
    output_dir = str(value.get("output_dir") or "")
    if output_dir and Path(output_dir) != Path("."):
        return Path(output_dir)
    context_path = str(value.get("context_path") or "")
    if context_path and Path(context_path) != Path("."):
        return Path(context_path) / "__missing_agent_output_dir__"
    candidate_id = _safe_fragment_or_default(str(value.get("candidate_id") or ""), "unknown-candidate")
    agent_call_id = _safe_fragment_or_default(str(value.get("agent_call_id") or ""), "unknown-agent-call")
    return Path("__missing_agent_output_dir__") / candidate_id / agent_call_id


def _safe_fragment_or_default(value: str, default: str) -> str:
    try:
        return _safe_fragment(value)
    except ValueError:
        return default
