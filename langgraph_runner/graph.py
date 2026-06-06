from __future__ import annotations

import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from .acceptance import AcceptanceDecision, evaluate_candidate, ppa_surrogate_score
from .agent_errors import AGENT_EXECUTION_ERROR_CLASSES, AGENT_EXECUTION_FAILED, AGENT_TIMEOUT
from .agent_io import AgentCall, AgentRunResult, AgentRunner, _finalize_agent_run, resolve_codex_command, write_context_package
from .agent_outputs import copy_prime_output, parse_prime_output, parse_subagent_output
from .artifacts import ArtifactPaths
from .batch import CandidateAssignment, plan_batch
from .candidate_assembly import CandidateAssembler
from .config import load_runner_config
from .prime_limits import PrimeLimitTracker
from .review import DeterministicReviewer
from .schemas import AgentRole, CandidateStatus, LedgerEntry, Phase, ReviewResult, RunnerState, TopDecision, VerificationResult
from .state_store import StateStore
from .verifier import STDERR_LOG, STDOUT_LOG, Verifier
from .workspace import CandidateWorkspace


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

BATCH_LOCAL_STATE_DEFAULTS = {
    "batch_assignments": [],
    "candidate_ids": [],
    "agent_calls": [],
    "subagent_outputs": [],
    "prime_requests": [],
    "prime_calls": [],
    "prime_outputs": [],
    "candidate_artifacts": [],
    "review_results": [],
    "verification_queue": [],
    "verification_results": [],
    "candidate_evaluations": [],
    "top_decision": {},
    "top_decision_path": "",
    "ledger_entries": [],
    "promoted_candidate_id": None,
    "human_interrupt": None,
}


class GraphState(TypedDict, total=False):
    repo_root: str
    run_id: str
    route: str
    config_path: str
    state_path: str
    artifact_root: str
    contract_path: str
    human_response: str
    resume_pending_interrupt: bool
    stop_after_current_pass: bool
    counted_run_total: int
    counted_run_remaining: int
    runner_config: dict[str, Any]
    runner_state: dict[str, Any]
    run_dir: str
    agent_calls: list[dict[str, Any]]
    subagent_outputs: list[dict[str, Any]]
    prime_requests: list[dict[str, Any]]
    prime_calls: list[dict[str, Any]]
    prime_outputs: list[dict[str, Any]]
    candidate_artifacts: list[dict[str, Any]]
    batch_assignments: list[dict]
    candidate_ids: list[str]
    review_results: list[dict[str, Any]]
    verification_queue: list[str]
    verification_results: list[dict[str, Any]]
    candidate_evaluations: list[dict[str, Any]]
    top_decision: dict[str, Any]
    top_decision_path: str
    ledger_entries: list[dict[str, Any]]
    promoted_candidate_id: str | None
    human_interrupt: dict[str, Any]
    errors: list[str]
    events: list[str]


def _record_event(state: GraphState, event: str) -> GraphState:
    events = list(state.get("events", []))
    events.append(event)
    return {**state, "events": events}


def _record_error(state: GraphState, node_name: str, message: str) -> GraphState:
    errors = list(state.get("errors", []))
    errors.append(f"{node_name}: {message}")
    return {**state, "errors": errors}


def _clear_batch_local_state(state: GraphState) -> GraphState:
    defaults = {
        key: list(value) if isinstance(value, list) else dict(value) if isinstance(value, dict) else value
        for key, value in BATCH_LOCAL_STATE_DEFAULTS.items()
    }
    return {**state, **defaults}


def _repo_root(state: GraphState) -> Path:
    return Path(state.get("repo_root") or ".").resolve()


def _resolve_repo_path(repo_root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    return (repo_root / path).resolve()


def _resolve_repo_config_path(repo_root: Path, value: str) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (repo_root.resolve() / path).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ValueError(f"path_outside_repo: {value}") from exc
    return resolved


def _config_path(state: GraphState, repo_root: Path) -> Path:
    return _resolve_repo_config_path(repo_root, state.get("config_path") or "runner_config.json")


def _artifact_paths(state: GraphState) -> ArtifactPaths | None:
    repo_root = _repo_root(state)
    artifact_root = state.get("artifact_root")
    if artifact_root is None and state.get("state_path"):
        artifact_root = str(_resolve_repo_path(repo_root, state["state_path"]).parent)
    if artifact_root is None and state.get("runner_config"):
        artifact_root = str(repo_root / str(state["runner_config"]["artifact_root"]))
    if artifact_root is None:
        return None
    return ArtifactPaths(repo_root=repo_root, artifact_root=_resolve_repo_path(repo_root, artifact_root))


def _candidate_id_list(state: GraphState) -> list[str]:
    if state.get("candidate_ids"):
        return list(state["candidate_ids"])
    return [str(assignment["candidate_id"]) for assignment in state.get("batch_assignments", [])]


def _candidate_result_map(
    results: Any,
    *,
    result_name: str,
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    mapped = {}
    if not isinstance(results, list):
        errors.append(f"evaluate_candidates: malformed {result_name} is not a list")
        return mapped
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            errors.append(f"evaluate_candidates: malformed {result_name}[{index}] is not an object")
            continue
        candidate_id = result.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            errors.append(f"evaluate_candidates: malformed {result_name}[{index}] missing candidate_id")
            continue
        mapped[candidate_id] = result
    return mapped


def _artifact_result_for_candidate(
    *,
    paths: ArtifactPaths | None,
    candidate_id: str,
    filename: str,
    model: type[ReviewResult] | type[VerificationResult],
    errors: list[str],
    node_name: str = "evaluate_candidates",
) -> dict[str, Any] | None:
    if paths is None:
        return None
    artifact_path = paths.candidate_dir(candidate_id) / filename
    if not artifact_path.exists():
        return None
    try:
        raw = json.loads(artifact_path.read_text(encoding="utf-8"))
        result = model.model_validate(raw)
        if result.candidate_id != candidate_id:
            raise ValueError(f"candidate_id mismatch: {result.candidate_id!r}")
        return result.model_dump(mode="json")
    except (OSError, json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
        message = f"invalid_{artifact_path.stem}_artifact: {exc}"
        errors.append(f"{node_name}: {candidate_id} {message}")
        if model is ReviewResult:
            return {
                "candidate_id": candidate_id,
                "passed": False,
                "checks": {"artifact_valid": False},
                "errors": [message],
                "warnings": [],
            }
        return _verification_error_payload(candidate_id, paths.candidate_dir(candidate_id), message)


def _assignment_to_dict(assignment: CandidateAssignment) -> dict[str, Any]:
    return {
        "candidate_id": assignment.candidate_id,
        "batch_id": assignment.batch_id,
        "role": assignment.role,
        "phase": assignment.phase.value,
        "primary_objective": assignment.primary_objective,
    }


def _load_dut_contract(repo_root: Path, config: dict[str, Any]) -> tuple[str, list[str]]:
    amptest_config_value = config.get("amptest_config")
    if not isinstance(amptest_config_value, str) or not amptest_config_value.strip():
        raise ValueError("runner_config amptest_config must be a non-empty string")

    amptest_config_path = _resolve_repo_config_path(repo_root, amptest_config_value)
    amptest_config = json.loads(amptest_config_path.read_text(encoding="utf-8"))
    if not isinstance(amptest_config, dict):
        raise ValueError(f"{amptest_config_path} must contain a JSON object")

    dut_subckt = amptest_config.get("dut_subckt")
    if not isinstance(dut_subckt, str) or not dut_subckt.strip():
        raise ValueError(f"{amptest_config_path} dut_subckt must be a non-empty string")

    dut_pins_order = amptest_config.get("dut_pins_order")
    if not isinstance(dut_pins_order, list) or not dut_pins_order:
        raise ValueError(f"{amptest_config_path} dut_pins_order must be a non-empty list of strings")
    if not all(isinstance(pin, str) and pin.strip() for pin in dut_pins_order):
        raise ValueError(f"{amptest_config_path} dut_pins_order must be a non-empty list of strings")

    return dut_subckt.strip(), [pin.strip() for pin in dut_pins_order]


def _load_config_dict(state: GraphState) -> dict[str, Any] | None:
    if state.get("runner_config"):
        return dict(state["runner_config"])
    repo_root = _repo_root(state)
    config_path = _config_path(state, repo_root)
    if not config_path.exists():
        return None
    return load_runner_config(config_path).model_dump(mode="json")


def _store_for_state(state: GraphState, paths: ArtifactPaths, config: dict[str, Any]) -> StateStore:
    repo_root = _repo_root(state)
    contract_path_value = state.get("contract_path") or config.get("contract_path")
    if not isinstance(contract_path_value, str):
        raise ValueError("missing contract_path")
    return StateStore(paths=paths, contract_path=_resolve_repo_path(repo_root, contract_path_value))


def _assignment_from_dict(raw: dict[str, Any]) -> CandidateAssignment:
    return CandidateAssignment(
        candidate_id=str(raw["candidate_id"]),
        batch_id=str(raw["batch_id"]),
        role=str(raw["role"]),
        phase=Phase(str(raw["phase"])),
        primary_objective=str(raw["primary_objective"]),
    )


def _timeout_for_role(config: dict[str, Any], role: str, default_key: str) -> int:
    timeouts = config.get("agent_timeouts_seconds") or {}
    if isinstance(timeouts, dict):
        value = timeouts.get(default_key, timeouts.get(role, 1200))
        try:
            return int(value)
        except (TypeError, ValueError):
            return 1200
    return 1200


def _agent_runner_for_config(config: dict[str, Any]):
    backend = config.get("agent_backend") or {}
    mode = backend.get("mode", "codex_exec") if isinstance(backend, dict) else "codex_exec"
    if mode == "local_deterministic":
        from .local_agent import LocalDeterministicAgentRunner

        return LocalDeterministicAgentRunner()
    return AgentRunner()


def _agent_exception_command_for_config(config: dict[str, Any], call: AgentCall) -> list[str]:
    backend = config.get("agent_backend") or {}
    mode = backend.get("mode", "codex_exec") if isinstance(backend, dict) else "codex_exec"
    if mode == "local_deterministic":
        return ["local_deterministic_agent", call.role]
    return [*resolve_codex_command(), "exec", "-C", str(call.context_path), "-"]


def _run_agent_for_assignment(
    *,
    state: GraphState,
    assignment_dict: dict[str, Any],
    attempt: int,
    validation_errors: list[str] | None = None,
) -> tuple[dict[str, Any], AgentRunResult]:
    repo_root = _repo_root(state)
    config = state["runner_config"]
    paths = _artifact_paths(state)
    if paths is None:
        raise ValueError("missing artifact paths")
    run_dir = paths.run_dir(str(state.get("run_id") or "manual"))
    run_dir.mkdir(parents=True, exist_ok=True)
    assignment = _assignment_from_dict(assignment_dict)
    store = _store_for_state(state, paths, config)
    recent_ledger = [entry.model_dump(mode="json") for entry in store.read_ledger()[-20:]]
    contract_path = _resolve_repo_path(repo_root, str(state["contract_path"]))
    contract_excerpt = contract_path.read_text(encoding="utf-8", errors="replace")
    agent_call_id = f"{assignment.candidate_id}-subagent-a{attempt}"
    context_path = write_context_package(
        run_dir=run_dir,
        agent_call_id=agent_call_id,
        assignment=assignment,
        contract_excerpt=contract_excerpt,
        state_summary=state.get("runner_state", {}),
        recent_ledger=recent_ledger,
        dut_netlist_path=str(config["dut_netlist"]),
        devices_csv_path=str(config["devices_csv"]),
        base_dut=_resolve_repo_config_path(repo_root, str(config["dut_netlist"])),
        base_devices=_resolve_repo_config_path(repo_root, str(config["devices_csv"])),
    )
    if validation_errors:
        (context_path / "validation_errors.json").write_text(
            json.dumps(validation_errors, indent=2) + "\n",
            encoding="utf-8",
        )
    artifact_output_dir = context_path / "output"
    output_dir = run_dir / "agent_outputs" / agent_call_id
    call = AgentCall(
        role=assignment.role,
        context_path=context_path,
        output_dir=output_dir,
        timeout_seconds=_timeout_for_role(config, assignment.role, "subagent"),
        artifact_output_dir=artifact_output_dir,
        agent_call_id=agent_call_id,
    )
    try:
        result = _agent_runner_for_config(config).run(call)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result = _write_agent_exception_result(call, exc, command=_agent_exception_command_for_config(config, call))
    call_state = {
        "agent_call_id": agent_call_id,
        "candidate_id": assignment.candidate_id,
        "batch_id": assignment.batch_id,
        "role": assignment.role,
        "attempt": attempt,
        "context_path": str(context_path),
        "output_dir": str(artifact_output_dir),
        "log_dir": str(output_dir),
        "exit_code": result.exit_code,
        "stdout_path": str(result.stdout_path),
        "stderr_path": str(result.stderr_path),
        "status": result.status,
    }
    if result.agent_run_path is not None:
        call_state["agent_run_path"] = str(result.agent_run_path)
    if result.error_class is not None:
        call_state["error_class"] = result.error_class
    if result.error is not None:
        call_state["error"] = result.error
    if result.command is not None:
        call_state["command"] = list(result.command)
    return call_state, result


def _write_agent_exception_result(
    call: AgentCall,
    exc: OSError | subprocess.TimeoutExpired,
    *,
    command: list[str] | None = None,
) -> AgentRunResult:
    artifact_output_dir = call.artifact_output_dir or call.output_dir
    stdout_path = call.output_dir / "stdout.log"
    stderr_path = call.output_dir / "stderr.log"
    agent_run_path = call.output_dir / "agent_run.json"
    command = command or [*resolve_codex_command(), "exec", "-C", str(call.context_path), "-"]
    if isinstance(exc, subprocess.TimeoutExpired):
        exit_code = 124
        stdout = _text(exc.stdout if exc.stdout is not None else exc.output)
        stderr_text = _text(exc.stderr)
        error = f"agent command timed out after {call.timeout_seconds} seconds"
        stderr = stderr_text + ("\n" if stderr_text else "") + error
        error_class = AGENT_TIMEOUT
    else:
        exit_code = 1
        stdout = ""
        stderr = str(exc)
        error = str(exc)
        error_class = AGENT_EXECUTION_FAILED
    try:
        call.output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as setup_exc:
        error = error + f"; could not create log directory: {setup_exc}"
    try:
        artifact_output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as setup_exc:
        error = error + f"; could not create artifact output directory: {setup_exc}"
    return _finalize_agent_run(
        call=call,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        agent_run_path=agent_run_path,
        artifact_output_dir=artifact_output_dir,
        command=command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        status="error",
        error_class=error_class,
        error=error,
    )


def _subagent_output_dir_for_call(call: dict[str, Any], *, repo_root: Path | None = None) -> Path | None:
    output_dir = str(call.get("output_dir") or "")
    if output_dir:
        path = Path(output_dir)
        if path != Path(".") and _valid_fallback_output_dir(path, repo_root=repo_root):
            return path
    context_path = str(call.get("context_path") or "")
    if context_path:
        path = Path(context_path)
        if path != Path(".") and path.exists():
            return path / "output"
    if not output_dir:
        return None
    return None


def _valid_fallback_output_dir(path: Path, *, repo_root: Path | None) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if resolved == Path.cwd().resolve():
        return False
    if repo_root is not None and resolved == repo_root.resolve():
        return False
    if not resolved.is_dir():
        return False
    candidate_artifact_names = {"proposal.json", "patch.diff", "notes.md"}
    if any((resolved / name).exists() for name in candidate_artifact_names):
        return True
    try:
        next(resolved.iterdir())
    except StopIteration:
        return False
    except OSError:
        return False
    return False


def _agent_execution_error_class(call: dict[str, Any]) -> str | None:
    error_class = str(call.get("error_class") or "")
    if error_class in AGENT_EXECUTION_ERROR_CLASSES:
        return error_class
    errors = call.get("errors", [])
    if isinstance(errors, list):
        for error in errors:
            value = str(error)
            if value in AGENT_EXECUTION_ERROR_CLASSES:
                return value
    return None


def _subagent_error_output_state(
    *,
    call: dict[str, Any],
    candidate_id: str,
    agent_call_id: str,
    output_dir: Path | None,
    errors: list[str],
    error_class: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    state = {
        "candidate_id": candidate_id,
        "agent_call_id": agent_call_id,
        "output_dir": str(output_dir) if output_dir is not None else "",
        "valid": False,
        "status": "error",
        "errors": errors,
        "prime_requests": [],
    }
    if error_class is not None:
        state["error_class"] = error_class
    if error:
        state["error"] = error
    if call.get("agent_run_path"):
        state["agent_run_path"] = str(call["agent_run_path"])
    if call.get("stdout_path"):
        state["stdout_path"] = str(call["stdout_path"])
    if call.get("stderr_path"):
        state["stderr_path"] = str(call["stderr_path"])
    return state


def _should_retry_subagent_output(call: dict[str, Any], parse_errors: list[str]) -> bool:
    if _agent_execution_error_class(call):
        return False
    if call.get("status") == "error":
        return False
    if "agent_output_path_missing" in parse_errors:
        return False
    return True


def _agent_run_result_fields(result: AgentRunResult) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "exit_code": result.exit_code,
        "stdout_path": str(result.stdout_path),
        "stderr_path": str(result.stderr_path),
        "status": result.status,
    }
    if result.agent_run_path is not None:
        fields["agent_run_path"] = str(result.agent_run_path)
    if result.error_class is not None:
        fields["error_class"] = result.error_class
    if result.error is not None:
        fields["error"] = result.error
    if result.command is not None:
        fields["command"] = list(result.command)
    return fields


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _load_runner_state(state: GraphState, paths: ArtifactPaths, contract_path: Path) -> GraphState:
    store = StateStore(paths=paths, contract_path=contract_path)
    runner_state = store.load_state()
    return {
        **state,
        "state_path": str(paths.state_json),
        "artifact_root": str(paths.artifact_root),
        "runner_state": runner_state.model_dump(mode="json"),
    }


def _attach_resume_pending_interrupt(state: GraphState, paths: ArtifactPaths) -> GraphState:
    if state.get("human_response") is None:
        return state
    run_dir = paths.run_dir(str(state.get("run_id") or "manual"))
    pending = run_dir / "human_interrupt.json"
    if not pending.exists():
        return state
    return {**state, "run_dir": str(run_dir), "resume_pending_interrupt": True}


def load_context_node(state: GraphState) -> GraphState:
    next_state = _record_event(state, "load_context")
    repo_root = _repo_root(next_state)
    try:
        config_path = _config_path(next_state, repo_root)
    except ValueError as exc:
        return _record_error(next_state, "load_context", f"invalid runner config path: {exc}")
    if not config_path.exists():
        return _record_error(next_state, "load_context", f"missing runner config at {config_path}")

    try:
        config = load_runner_config(config_path)
    except (OSError, ValueError, ValidationError) as exc:
        return _record_error(next_state, "load_context", f"invalid runner config at {config_path}: {exc}")

    contract_path = _resolve_repo_path(repo_root, config.contract_path)
    artifact_root = (
        _resolve_repo_path(repo_root, next_state["state_path"]).parent
        if next_state.get("state_path")
        else _resolve_repo_path(repo_root, config.artifact_root)
    )
    next_state = {
        **next_state,
        "config_path": str(config_path),
        "runner_config": config.model_dump(mode="json"),
        "artifact_root": str(artifact_root),
        "contract_path": str(contract_path),
    }
    if not contract_path.exists():
        return _record_error(next_state, "load_context", f"missing contract at {contract_path}")

    if not next_state.get("state_path"):
        return _attach_resume_pending_interrupt(
            next_state,
            ArtifactPaths(repo_root=repo_root, artifact_root=artifact_root),
        )

    try:
        loaded = _load_runner_state(
            next_state,
            ArtifactPaths(repo_root=repo_root, artifact_root=artifact_root),
            contract_path,
        )
        return _attach_resume_pending_interrupt(loaded, ArtifactPaths(repo_root=repo_root, artifact_root=artifact_root))
    except (OSError, ValueError, ValidationError) as exc:
        return _record_error(next_state, "load_context", f"could not load runner state: {exc}")


def plan_batch_node(state: GraphState) -> GraphState:
    next_state = _record_event(state, "plan_batch")
    if next_state.get("resume_pending_interrupt"):
        return next_state
    next_state = _clear_batch_local_state(next_state)
    if not next_state.get("runner_config"):
        return _record_error(next_state, "plan_batch", "missing runner_config; skipped batch planning")
    if not next_state.get("runner_state"):
        return _record_error(next_state, "plan_batch", "missing runner_state; skipped batch planning")

    try:
        runner_state = RunnerState.model_validate(next_state["runner_state"])
        batch_size = int(next_state["runner_config"]["candidate_generation_batch_size"])
        assignments = plan_batch(runner_state, batch_size, datetime.now(timezone.utc))
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        return _record_error(next_state, "plan_batch", f"could not plan batch: {exc}")

    assignment_dicts = [_assignment_to_dict(assignment) for assignment in assignments]
    return {
        **next_state,
        "batch_assignments": assignment_dicts,
        "candidate_ids": [assignment["candidate_id"] for assignment in assignment_dicts],
    }


def spawn_subagents_node(state: GraphState) -> GraphState:
    next_state = _record_event(state, "spawn_subagents")
    if next_state.get("resume_pending_interrupt"):
        return next_state
    paths = _artifact_paths(next_state)
    required = ["runner_config", "runner_state", "batch_assignments", "artifact_root", "contract_path"]
    missing = [key for key in required if not next_state.get(key)]
    if paths is None:
        missing.append("artifact_paths")
    if missing:
        return _record_error(next_state, "spawn_subagents", "missing required state: " + ", ".join(missing))

    assert paths is not None
    paths.ensure_root()
    run_dir = paths.run_dir(str(next_state.get("run_id") or "manual"))
    run_dir.mkdir(parents=True, exist_ok=True)
    agent_calls = list(next_state.get("agent_calls", []))

    for assignment in next_state.get("batch_assignments", []):
        try:
            call_state, _result = _run_agent_for_assignment(
                state={**next_state, "run_dir": str(run_dir)},
                assignment_dict=assignment,
                attempt=1,
            )
        except (OSError, ValueError, ValidationError, subprocess.TimeoutExpired) as exc:
            call_state = {
                "agent_call_id": f"{assignment.get('candidate_id', 'unknown')}-subagent-a1",
                "candidate_id": str(assignment.get("candidate_id", "")),
                "role": str(assignment.get("role", "")),
                "attempt": 1,
                "context_path": "",
                "output_dir": "",
                "exit_code": 1,
                "stdout_path": "",
                "stderr_path": "",
                "status": "error",
                "errors": [str(exc)],
            }
        agent_calls.append(call_state)

    return {**next_state, "run_dir": str(run_dir), "agent_calls": agent_calls}


def collect_subagent_requests_node(state: GraphState) -> GraphState:
    next_state = _record_event(state, "collect_subagent_requests")
    if next_state.get("resume_pending_interrupt"):
        return next_state
    if not next_state.get("agent_calls"):
        return _record_error(next_state, "collect_subagent_requests", "missing agent_calls")

    assignments = {str(item["candidate_id"]): item for item in next_state.get("batch_assignments", [])}
    agent_calls = list(next_state.get("agent_calls", []))
    final_outputs: dict[str, dict[str, Any]] = {}
    prime_requests: list[dict[str, Any]] = []
    errors = list(next_state.get("errors", []))
    repo_root = _repo_root(next_state)

    for call in list(agent_calls):
        candidate_id = str(call.get("candidate_id") or "")
        agent_call_id = str(call.get("agent_call_id") or "")
        execution_error_class = _agent_execution_error_class(call)
        if execution_error_class:
            final_outputs[candidate_id] = _subagent_error_output_state(
                call=call,
                candidate_id=candidate_id,
                agent_call_id=agent_call_id,
                output_dir=_subagent_output_dir_for_call(call, repo_root=repo_root),
                errors=[execution_error_class, *([str(call["error"])] if call.get("error") else [])],
                error_class=execution_error_class,
                error=str(call.get("error") or ""),
            )
            continue

        output_dir = _subagent_output_dir_for_call(call, repo_root=repo_root)
        if not candidate_id or not agent_call_id:
            parsed = None
            parse_errors = ["missing_agent_output"]
        elif output_dir is None:
            parsed = _subagent_error_output_state(
                call=call,
                candidate_id=candidate_id,
                agent_call_id=agent_call_id,
                output_dir=None,
                errors=["agent_output_path_missing"],
            )
            parse_errors = ["agent_output_path_missing"]
        elif not output_dir.exists():
            parsed = _subagent_error_output_state(
                call=call,
                candidate_id=candidate_id,
                agent_call_id=agent_call_id,
                output_dir=output_dir,
                errors=["missing_agent_output"],
            )
            parse_errors = ["missing_agent_output"]
        else:
            parsed_output = parse_subagent_output(output_dir, candidate_id, agent_call_id=agent_call_id)
            parsed = parsed_output.to_state()
            parse_errors = parsed_output.errors

        if parsed is not None and parsed.get("valid"):
            final_outputs[candidate_id] = parsed
            prime_requests.extend(parsed.get("prime_requests", []))
            continue

        if candidate_id in assignments and _should_retry_subagent_output(call, parse_errors):
            try:
                retry_call, _result = _run_agent_for_assignment(
                    state=next_state,
                    assignment_dict=assignments[candidate_id],
                    attempt=2,
                    validation_errors=parse_errors,
                )
                agent_calls.append(retry_call)
                retry_error_class = _agent_execution_error_class(retry_call)
                if retry_error_class:
                    final_outputs[candidate_id] = _subagent_error_output_state(
                        call=retry_call,
                        candidate_id=candidate_id,
                        agent_call_id=str(retry_call["agent_call_id"]),
                        output_dir=_subagent_output_dir_for_call(retry_call, repo_root=repo_root),
                        errors=[retry_error_class, *([str(retry_call["error"])] if retry_call.get("error") else [])],
                        error_class=retry_error_class,
                        error=str(retry_call.get("error") or ""),
                    )
                    continue
                retry_output_dir = _subagent_output_dir_for_call(retry_call, repo_root=repo_root)
                if retry_output_dir is None:
                    final_outputs[candidate_id] = _subagent_error_output_state(
                        call=retry_call,
                        candidate_id=candidate_id,
                        agent_call_id=str(retry_call["agent_call_id"]),
                        output_dir=None,
                        errors=["agent_output_path_missing"],
                    )
                    continue
                retry_parsed = parse_subagent_output(
                    retry_output_dir,
                    candidate_id,
                    agent_call_id=str(retry_call["agent_call_id"]),
                ).to_state()
                if retry_parsed.get("valid"):
                    final_outputs[candidate_id] = retry_parsed
                    prime_requests.extend(retry_parsed.get("prime_requests", []))
                else:
                    final_outputs[candidate_id] = retry_parsed
            except (OSError, ValueError, ValidationError, subprocess.TimeoutExpired) as exc:
                errors.append(f"collect_subagent_requests: retry failed for {candidate_id}: {exc}")
                final_outputs[candidate_id] = {
                    "candidate_id": candidate_id,
                    "agent_call_id": agent_call_id,
                    "output_dir": str(output_dir),
                    "valid": False,
                    "status": "error",
                    "errors": [*parse_errors, f"retry_failed: {exc}"],
                    "prime_requests": [],
                }
        elif parsed is not None:
            final_outputs[candidate_id] = parsed

    return {
        **next_state,
        "agent_calls": agent_calls,
        "subagent_outputs": list(final_outputs.values()),
        "prime_requests": prime_requests,
        "errors": errors,
    }


def spawn_prime_agents_node(state: GraphState) -> GraphState:
    next_state = _record_event(state, "spawn_prime_agents")
    if next_state.get("resume_pending_interrupt"):
        return next_state
    requests = list(next_state.get("prime_requests", []))
    if not requests:
        return {**next_state, "prime_calls": list(next_state.get("prime_calls", []))}
    paths = _artifact_paths(next_state)
    if paths is None or not next_state.get("runner_config"):
        return _record_error(next_state, "spawn_prime_agents", "missing artifact paths or runner_config")

    config = next_state["runner_config"]
    tracker = PrimeLimitTracker(
        max_active=int(config["max_active_primes_per_subagent"]),
        max_total=int(config["max_total_primes_per_subagent"]),
    )
    run_dir = paths.run_dir(str(next_state.get("run_id") or "manual"))
    prime_calls = list(next_state.get("prime_calls", []))
    for request in requests:
        parent_id = str(request["parent_agent_call_id"])
        prime_role = str(request["prime_role"])
        decision = tracker.request(parent_id, prime_role)
        prime_call_id = f"{parent_id}-prime-{request.get('request_index', len(prime_calls))}"
        output_dir = run_dir / "prime_outputs" / prime_call_id
        if not decision.approved:
            prime_calls.append(
                {
                    **request,
                    "prime_call_id": prime_call_id,
                    "output_dir": str(output_dir),
                    "status": "rejected",
                    "errors": [decision.reason],
                }
            )
            continue
        context_path = run_dir / "prime_contexts" / prime_call_id
        call = AgentCall(
            role=prime_role,
            context_path=context_path,
            output_dir=output_dir,
            timeout_seconds=_timeout_for_role(config, prime_role, "prime"),
            agent_call_id=prime_call_id,
        )
        try:
            context_path.mkdir(parents=True, exist_ok=True)
            (context_path / "context.md").write_text(
                "# Prime Agent Context\n\n"
                f"candidate_id: {request['candidate_id']}\n"
                f"prime_role: {prime_role}\n\n"
                f"{request['prompt']}\n",
                encoding="utf-8",
            )
            result = _agent_runner_for_config(config).run(call)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result = _write_agent_exception_result(call, exc, command=_agent_exception_command_for_config(config, call))
        except ValueError as exc:
            prime_calls.append(
                {
                    **request,
                    "prime_call_id": prime_call_id,
                    "output_dir": str(output_dir),
                    "context_path": str(context_path),
                    "log_dir": str(output_dir),
                    "exit_code": 1,
                    "stdout_path": "",
                    "stderr_path": "",
                    "status": "error",
                    "errors": [f"prime_agent_error: {exc}"],
                }
            )
            tracker.finish(parent_id, prime_role)
            continue
        try:
            result_fields = _agent_run_result_fields(result)
            status = "completed" if result.exit_code == 0 and result.status == "completed" else "error"
            result_fields["status"] = status
            if status == "completed":
                errors = []
            elif result.error_class in AGENT_EXECUTION_ERROR_CLASSES:
                errors = [result.error_class]
            elif result.error_class:
                errors = [result.error_class]
            else:
                errors = ["prime_agent_exit_nonzero"]
            prime_calls.append(
                {
                    **request,
                    "prime_call_id": prime_call_id,
                    "output_dir": str(output_dir),
                    "context_path": str(context_path),
                    "log_dir": str(output_dir),
                    **result_fields,
                    "errors": errors,
                }
            )
        finally:
            tracker.finish(parent_id, prime_role)
    return {**next_state, "prime_calls": prime_calls}


def collect_prime_outputs_node(state: GraphState) -> GraphState:
    next_state = _record_event(state, "collect_prime_outputs")
    if next_state.get("resume_pending_interrupt"):
        return next_state
    paths = _artifact_paths(next_state)
    if paths is None:
        if next_state.get("prime_calls"):
            return _record_error(next_state, "collect_prime_outputs", "missing artifact paths")
        return {**next_state, "prime_outputs": []}

    outputs = []
    for call in next_state.get("prime_calls", []):
        if call.get("status") != "completed":
            outputs.append({**call, "valid": False})
            continue
        prime_output = parse_prime_output(
            Path(str(call["output_dir"])),
            str(call["candidate_id"]),
            prime_call_id=str(call["prime_call_id"]),
        )
        copy_prime_output(
            prime_output,
            paths.candidate_dir(str(call["candidate_id"])) / "primes" / str(call["prime_call_id"]),
        )
        outputs.append(prime_output.to_state())
    return {**next_state, "prime_outputs": outputs}


def assemble_candidate_proposals_node(state: GraphState) -> GraphState:
    next_state = _record_event(state, "assemble_candidate_proposals")
    if next_state.get("resume_pending_interrupt"):
        return next_state
    paths = _artifact_paths(next_state)
    config = _load_config_dict(next_state)
    if paths is None or config is None:
        return _record_error(next_state, "assemble_candidate_proposals", "missing artifact paths or runner_config")
    if not next_state.get("batch_assignments"):
        return _record_error(next_state, "assemble_candidate_proposals", "missing batch_assignments")

    assembler = CandidateAssembler(paths=paths, repo_root=_repo_root(next_state), config=config)
    subagent_outputs = {
        str(item["candidate_id"]): item
        for item in next_state.get("subagent_outputs", [])
        if item.get("candidate_id")
    }
    artifacts = []
    for assignment in next_state["batch_assignments"]:
        result = assembler.assemble(assignment, subagent_outputs.get(str(assignment["candidate_id"])))
        artifacts.append(result.to_state())
    return {**next_state, "candidate_artifacts": artifacts}


def deterministic_review_node(state: GraphState) -> GraphState:
    next_state = _record_event(state, "deterministic_review")
    if next_state.get("resume_pending_interrupt"):
        return next_state
    repo_root = _repo_root(next_state)
    config = next_state.get("runner_config")
    paths = _artifact_paths(next_state)
    candidate_ids = _candidate_id_list(next_state)
    if not config:
        return _record_error(next_state, "deterministic_review", "missing runner_config; skipped review")
    if paths is None:
        return _record_error(next_state, "deterministic_review", "missing artifact paths; skipped review")
    if not candidate_ids:
        return _record_error(next_state, "deterministic_review", "missing candidate_ids; skipped review")

    try:
        dut_netlist = str(config["dut_netlist"])
        devices_csv = str(config["devices_csv"])
        dut_subckt, dut_pins_order = _load_dut_contract(repo_root, config)
    except (KeyError, OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return _record_error(next_state, "deterministic_review", f"invalid DUT config; skipped review: {exc}")

    reviewer = DeterministicReviewer(
        allowed_files={dut_netlist, devices_csv},
        dut_subckt=dut_subckt,
        dut_pins_order=dut_pins_order,
    )
    assembly_results = {
        str(item["candidate_id"]): item
        for item in next_state.get("candidate_artifacts", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }
    results = []
    for candidate_id in candidate_ids:
        assembly = assembly_results.get(candidate_id)
        if assembly is not None and assembly.get("status") != "assembled":
            result = ReviewResult(
                candidate_id=candidate_id,
                passed=False,
                checks={"assembly": False},
                errors=["assembly_failed", *list(assembly.get("errors", []))],
            )
            candidate_dir = paths.candidate_dir(candidate_id)
            candidate_dir.mkdir(parents=True, exist_ok=True)
            (candidate_dir / "review.json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
            results.append(result.model_dump(mode="json"))
            continue
        try:
            result = reviewer.review(paths.candidate_dir(candidate_id), paths.workspace_dir(candidate_id), candidate_id)
        except (OSError, ValueError, ValidationError) as exc:
            result = ReviewResult(
                candidate_id=candidate_id,
                passed=False,
                checks={"review_exception": False},
                errors=[f"deterministic_review_error: {exc}"],
            )
        try:
            candidate_dir = paths.candidate_dir(candidate_id)
            candidate_dir.mkdir(parents=True, exist_ok=True)
            (candidate_dir / "review.json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            next_state = _record_error(next_state, "deterministic_review", f"could not write review.json for {candidate_id}: {exc}")
        results.append(result.model_dump(mode="json"))
    return {**next_state, "review_results": results}


def verify_queue_node(state: GraphState) -> GraphState:
    next_state = _record_event(state, "verify_queue")
    if next_state.get("resume_pending_interrupt"):
        return next_state
    paths = _artifact_paths(next_state)
    config = _load_config_dict(next_state)
    if paths is None or config is None:
        return _record_error(next_state, "verify_queue", "missing artifact paths or runner_config")
    errors = list(next_state.get("errors", []))
    review_results = _candidate_result_map(
        next_state.get("review_results", []),
        result_name="review_results",
        errors=errors,
    )
    all_candidate_ids = _candidate_id_list(next_state)
    candidate_ids = []
    for candidate_id in all_candidate_ids:
        review = _artifact_result_for_candidate(
            paths=paths,
            candidate_id=candidate_id,
            filename="review.json",
            model=ReviewResult,
            errors=errors,
            node_name="verify_queue",
        )
        if review is None:
            review = review_results.get(candidate_id)
        if review is not None and review.get("passed"):
            candidate_ids.append(candidate_id)
    consumed_rerun_decision = False
    top_decision = next_state.get("top_decision") or {}
    if isinstance(top_decision, dict) and top_decision.get("decision") == "rerun_verification":
        selected_ids = [candidate_id for candidate_id in top_decision.get("candidate_ids", []) if isinstance(candidate_id, str)]
        selected_set = set(selected_ids)
        candidate_ids = [candidate_id for candidate_id in candidate_ids if candidate_id in selected_set]
        consumed_rerun_decision = True
    if not all_candidate_ids:
        return _record_error(next_state, "verify_queue", "missing candidate_ids; skipped verifier queue")
    verifier_config = config.get("verifier")
    if not isinstance(verifier_config, dict):
        return _record_error(next_state, "verify_queue", "missing verifier config")

    verifier = Verifier(
        command=str(verifier_config["command"]),
        timeout_seconds=int(verifier_config["timeout_seconds"]),
        min_interval_seconds=int(verifier_config["min_interval_seconds"]),
        required_outputs=list(verifier_config["required_outputs"]),
    )
    verification_results = []
    for candidate_id in candidate_ids:
        try:
            result = verifier.run(
                candidate_id,
                _repo_root(next_state),
                paths.workspace_dir(candidate_id),
                paths.candidate_dir(candidate_id),
            )
            verification_results.append(result.model_dump(mode="json"))
        except (OSError, ValueError, ValidationError) as exc:
            message = f"verifier_exception: {exc}"
            errors.append(f"verify_queue: verifier failed for {candidate_id}: {exc}")
            result = _write_verification_error_result(candidate_id, paths.candidate_dir(candidate_id), message)
            verification_results.append(result.model_dump(mode="json"))
    result_state = {
        **next_state,
        "verification_queue": candidate_ids,
        "verification_results": verification_results,
        "errors": errors,
    }
    if consumed_rerun_decision:
        result_state["top_decision"] = {}
        result_state["route"] = "next_batch"
    return result_state


def _verification_error_payload(candidate_id: str, candidate_dir: Path, message: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "status": "error",
        "metrics_path": str(candidate_dir / "ppa_metrics.json"),
        "report_path": str(candidate_dir / "ppa_report.log"),
        "spectre_logs": [],
        "performance_nrmse_combined": 1.0,
        "area_total_p": 0.0,
        "power_score_basis_w": 0.0,
        "errors": [message],
    }


def _write_verification_error_result(candidate_id: str, candidate_dir: Path, message: str) -> VerificationResult:
    result = VerificationResult.model_validate(_verification_error_payload(candidate_id, candidate_dir, message))
    candidate_dir.mkdir(parents=True, exist_ok=True)
    (candidate_dir / "verification.json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return result


def evaluate_candidates_node(state: GraphState) -> GraphState:
    next_state = _record_event(state, "evaluate_candidates")
    if next_state.get("resume_pending_interrupt"):
        return next_state
    if not next_state.get("runner_state"):
        return _record_error(next_state, "evaluate_candidates", "missing runner_state; skipped evaluation")

    errors = list(next_state.get("errors", []))
    review_results = _candidate_result_map(
        next_state.get("review_results", []),
        result_name="review_results",
        errors=errors,
    )
    verification_results = _candidate_result_map(
        next_state.get("verification_results", []),
        result_name="verification_results",
        errors=errors,
    )
    candidate_ids = _candidate_id_list(next_state)
    if not candidate_ids:
        errors.append("evaluate_candidates: missing candidate_ids; skipped evaluation")
        return {**next_state, "errors": errors}

    try:
        runner_state = RunnerState.model_validate(next_state["runner_state"])
    except (TypeError, ValueError, ValidationError) as exc:
        errors.append(f"evaluate_candidates: invalid runner_state: {exc}")
        return {**next_state, "errors": errors}

    paths = _artifact_paths(next_state)
    evaluations = []
    for candidate_id in candidate_ids:
        review = _artifact_result_for_candidate(
            paths=paths,
            candidate_id=candidate_id,
            filename="review.json",
            model=ReviewResult,
            errors=errors,
        )
        if review is None:
            review = review_results.get(candidate_id)
        verification = _artifact_result_for_candidate(
            paths=paths,
            candidate_id=candidate_id,
            filename="verification.json",
            model=VerificationResult,
            errors=errors,
        )
        if verification is None:
            verification = verification_results.get(candidate_id)
        if review is None:
            evaluation = {
                "candidate_id": candidate_id,
                "status": "error",
                "reason": "missing_review_result",
                "metrics": {},
                "ppa_surrogate_score": None,
            }
            evaluations.append(evaluation)
            _write_verdict(paths, candidate_id, evaluation, errors)
            continue
        if not bool(review.get("passed")):
            status = _status_from_review_errors(review.get("errors", []))
            evaluation = {
                "candidate_id": candidate_id,
                "status": status,
                "reason": "; ".join(review.get("errors", [])) or "review_failed",
                "metrics": {},
                "ppa_surrogate_score": None,
            }
            evaluations.append(evaluation)
            _write_verdict(paths, candidate_id, evaluation, errors)
            continue
        if verification is None:
            evaluation = {
                "candidate_id": candidate_id,
                "status": "error",
                "reason": "missing_verification_result",
                "metrics": {},
                "ppa_surrogate_score": None,
            }
            evaluations.append(evaluation)
            _write_verdict(paths, candidate_id, evaluation, errors)
            continue
        try:
            verification_result = VerificationResult.model_validate(verification)
            metrics = {
                "performance_nrmse_combined": verification_result.performance_nrmse_combined,
                "area_total_p": verification_result.area_total_p,
                "power_score_basis_w": verification_result.power_score_basis_w,
            }
            decision = evaluate_candidate(
                runner_state,
                candidate_id,
                metrics,
                review_passed=bool(review.get("passed")),
                verification_status=verification_result.status,
                safety_passed=True,
            )
        except (TypeError, ValueError, ValidationError) as exc:
            errors.append(f"evaluate_candidates: could not evaluate {candidate_id}: {exc}")
            evaluation = {
                "candidate_id": candidate_id,
                "status": "error",
                "reason": f"evaluation_error: {exc}",
                "metrics": {},
                "ppa_surrogate_score": None,
            }
            evaluations.append(evaluation)
            _write_verdict(paths, candidate_id, evaluation, errors)
            continue
        evaluation = {
            "candidate_id": candidate_id,
            "status": _status_from_acceptance(decision),
            "reason": _reason_from_acceptance(decision, verification_result),
            "metrics": metrics,
            "ppa_surrogate_score": _candidate_ppa_score(runner_state, metrics),
        }
        evaluations.append(evaluation)
        _write_verdict(paths, candidate_id, evaluation, errors)

    return {**next_state, "candidate_evaluations": evaluations, "errors": errors}


def record_batch_node(state: GraphState) -> GraphState:
    next_state = _record_event(state, "record_batch")
    if next_state.get("resume_pending_interrupt"):
        return {**next_state, "ledger_entries": [], "promoted_candidate_id": None}
    if not next_state.get("candidate_evaluations"):
        return _record_error(next_state, "record_batch", "missing candidate_evaluations; skipped batch record")
    paths = _artifact_paths(next_state)
    config = _load_config_dict(next_state)
    if paths is None or config is None or not next_state.get("runner_state"):
        return _record_error(next_state, "record_batch", "missing artifact paths, runner_config, or runner_state")

    try:
        runner_state = RunnerState.model_validate(next_state["runner_state"])
        store = _store_for_state(next_state, paths, config)
    except (TypeError, ValueError, ValidationError) as exc:
        return _record_error(next_state, "record_batch", f"invalid canonical state: {exc}")

    evaluations = [dict(item) for item in next_state["candidate_evaluations"]]
    top_decision = next_state.get("top_decision") or {}
    if top_decision.get("decision") in {"human_interrupt", "rerun_verification"}:
        return {
            **next_state,
            "candidate_evaluations": evaluations,
            "ledger_entries": [],
            "promoted_candidate_id": None,
        }

    try:
        ledger_models = _precompute_ledger_entries(next_state, paths, runner_state, evaluations)
    except (TypeError, ValueError, ValidationError) as exc:
        return _record_error(next_state, "record_batch", f"precompute ledger failed: {exc}")

    winner = _select_winner(evaluations, runner_state)
    if winner is not None:
        try:
            CandidateWorkspace(paths.workspaces_dir).promote(
                paths.workspace_dir(str(winner["candidate_id"])),
                _resolve_repo_config_path(_repo_root(next_state), str(config["dut_netlist"])),
                _resolve_repo_config_path(_repo_root(next_state), str(config["devices_csv"])),
            )
        except (OSError, ValueError) as exc:
            _write_batch_error(paths, str(next_state.get("run_id") or "manual"), f"promotion_failed: {exc}")
            return _record_error(next_state, "record_batch", f"promotion failed: {exc}")

    for evaluation in evaluations:
        candidate_id = str(evaluation["candidate_id"])
        _write_verdict(paths, candidate_id, evaluation, list(next_state.get("errors", [])))

    try:
        _append_ledger_entries(paths, ledger_models)
    except OSError as exc:
        return _record_error(next_state, "record_batch", f"could not append ledger: {exc}")

    _update_runner_state_after_batch(runner_state, evaluations, winner, next_state)
    try:
        store.write_state(runner_state)
    except OSError as exc:
        return _record_error(next_state, "record_batch", f"could not write state: {exc}")

    return {
        **next_state,
        "candidate_evaluations": evaluations,
        "ledger_entries": [entry.model_dump(mode="json") for entry in ledger_models],
        "promoted_candidate_id": str(winner["candidate_id"]) if winner is not None else None,
        "runner_state": runner_state.model_dump(mode="json"),
    }


def top_anomaly_check_node(state: GraphState) -> GraphState:
    next_state = _record_event(state, "top_anomaly_check")
    paths = _artifact_paths(next_state)
    if paths is None:
        return _record_error(next_state, "top_anomaly_check", "missing artifact paths")
    run_id = str(next_state.get("run_id") or "manual")
    run_dir = paths.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    errors = list(next_state.get("errors", []))
    execution_failure_candidate_ids = _all_agent_execution_failure_candidate_ids(next_state)
    missing_output_candidate_ids = _all_missing_subagent_output_candidate_ids(next_state)
    verifier_failure_candidate_ids = _all_verifier_infrastructure_failure_candidate_ids(next_state)

    pending_interrupt = run_dir / "human_interrupt.json"
    if execution_failure_candidate_ids:
        reason = "all candidates failed agent execution"
        _write_agent_execution_batch_error(paths, run_id, next_state, execution_failure_candidate_ids, reason)
        decision = TopDecision.model_validate(
            {
                "decision": "stop",
                "reason": reason,
                "anomaly_level": "critical",
                "candidate_ids": execution_failure_candidate_ids,
                "next_batch_strategy": "Stop until Codex CLI launch is fixed.",
                "human_interrupt": {
                    "required": False,
                    "question": None,
                    "recommended_action": None,
                    "evidence_paths": [],
                },
            }
        )
    elif missing_output_candidate_ids:
        reason = "all candidates failed assembly: missing_valid_subagent_output"
        _write_missing_output_batch_error(paths, run_id, next_state, missing_output_candidate_ids, reason)
        decision = TopDecision.model_validate(
            {
                "decision": "stop",
                "reason": reason,
                "anomaly_level": "critical",
                "candidate_ids": missing_output_candidate_ids,
                "next_batch_strategy": "Stop until subagent output generation is fixed.",
                "human_interrupt": {
                    "required": False,
                    "question": None,
                    "recommended_action": None,
                    "evidence_paths": [],
                },
            }
        )
    elif verifier_failure_candidate_ids:
        reason = "all candidates failed verifier infrastructure"
        _write_verifier_infrastructure_batch_error(paths, run_id, next_state, verifier_failure_candidate_ids, reason)
        decision = TopDecision.model_validate(
            {
                "decision": "stop",
                "reason": reason,
                "anomaly_level": "critical",
                "candidate_ids": verifier_failure_candidate_ids,
                "next_batch_strategy": "Stop until verifier infrastructure is fixed.",
                "human_interrupt": {
                    "required": False,
                    "question": None,
                    "recommended_action": None,
                    "evidence_paths": [],
                },
            }
        )
    elif next_state.get("human_response") is not None:
        if pending_interrupt.exists():
            payload = json.loads(pending_interrupt.read_text(encoding="utf-8"))
            payload["human_response"] = next_state["human_response"]
            pending_interrupt.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            decision = _default_top_decision("continue", "Human response received; continuing counted workflow.")
        else:
            errors.append("top_anomaly_check: human_response_without_pending_interrupt")
            decision = _default_top_decision("continue", "No pending interrupt exists; continuing counted workflow.")
    elif next_state.get("top_decision"):
        try:
            decision = TopDecision.model_validate(next_state["top_decision"])
        except ValidationError as exc:
            errors.append(f"top_anomaly_check: invalid top_decision: {exc}")
            decision = _default_top_decision("continue", "Invalid top decision ignored; continuing.")
    else:
        decision = _default_top_decision("continue", "No batch anomaly detected by deterministic runner.")

    decision_path = run_dir / "top_decision.json"
    decision_path.write_text(decision.model_dump_json(indent=2) + "\n", encoding="utf-8")
    human_interrupt = None
    if decision.decision == "human_interrupt":
        human_interrupt = decision.human_interrupt.model_dump(mode="json")
        pending_interrupt.write_text(decision.model_dump_json(indent=2) + "\n", encoding="utf-8")

    return {
        **next_state,
        "top_decision": decision.model_dump(mode="json"),
        "top_decision_path": str(decision_path),
        "human_interrupt": human_interrupt,
        "errors": errors,
    }


def _default_top_decision(decision: str, reason: str) -> TopDecision:
    return TopDecision.model_validate(
        {
            "decision": decision,
            "reason": reason,
            "anomaly_level": "none",
            "candidate_ids": [],
            "next_batch_strategy": "Continue counted workflow.",
            "human_interrupt": {
                "required": decision == "human_interrupt",
                "question": None,
                "recommended_action": None,
                "evidence_paths": [],
            },
        }
    )


def _all_missing_subagent_output_candidate_ids(state: GraphState) -> list[str]:
    candidate_ids = _candidate_id_list(state)
    if not candidate_ids:
        return []
    assembly_results = {
        str(item.get("candidate_id")): item
        for item in state.get("candidate_artifacts", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }
    if all(candidate_id in assembly_results for candidate_id in candidate_ids):
        missing = []
        for candidate_id in candidate_ids:
            if not _assembly_result_is_agent_output_missing(assembly_results[candidate_id]):
                return []
            missing.append(candidate_id)
        return missing

    return []


def _all_verifier_infrastructure_failure_candidate_ids(state: GraphState) -> list[str]:
    candidate_ids = _candidate_id_list(state)
    if not candidate_ids:
        return []
    evaluations = {
        str(item.get("candidate_id")): item
        for item in state.get("candidate_evaluations", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }
    if not all(candidate_id in evaluations for candidate_id in candidate_ids):
        return []
    failed = []
    for candidate_id in candidate_ids:
        evaluation = evaluations[candidate_id]
        if evaluation.get("status") != "error":
            return []
        if not _is_verifier_infrastructure_failure_reason(str(evaluation.get("reason") or "")):
            return []
        failed.append(candidate_id)
    return failed


def _is_verifier_infrastructure_failure_reason(reason: str) -> bool:
    normalized = reason.strip().lower()
    if normalized == "missing verification.json":
        return True
    return normalized.startswith(
        (
            "verifier command exited with status ",
            "verifier command timed out ",
            "missing required output: ",
            "required output not updated by current run: ",
            "verifier_exception: ",
        )
    )


def _all_agent_execution_failure_candidate_ids(state: GraphState) -> list[str]:
    candidate_ids = _candidate_id_list(state)
    if not candidate_ids:
        return []
    assembly_results = {
        str(item.get("candidate_id")): item
        for item in state.get("candidate_artifacts", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }
    if not all(candidate_id in assembly_results for candidate_id in candidate_ids):
        return []
    failed = []
    for candidate_id in candidate_ids:
        if _assembly_result_agent_execution_error_class(assembly_results[candidate_id]) is None:
            return []
        failed.append(candidate_id)
    return failed


def _assembly_result_agent_execution_error_class(assembly: dict[str, Any]) -> str | None:
    error_class = str(assembly.get("error_class") or "")
    if error_class in AGENT_EXECUTION_ERROR_CLASSES:
        return error_class
    reason = str(assembly.get("reason") or "")
    if reason in AGENT_EXECUTION_ERROR_CLASSES:
        return reason
    errors = assembly.get("errors", [])
    if isinstance(errors, list):
        for error in errors:
            value = str(error)
            if value in AGENT_EXECUTION_ERROR_CLASSES:
                return value
    return None


def _assembly_result_is_agent_output_missing(assembly: dict[str, Any]) -> bool:
    if assembly.get("error_class") == "agent_output_missing":
        return True
    if assembly.get("reason") == "agent_output_missing":
        return True
    errors = assembly.get("errors", [])
    return isinstance(errors, list) and "missing_valid_subagent_output" in errors


def _write_agent_execution_batch_error(
    paths: ArtifactPaths,
    run_id: str,
    state: GraphState,
    candidate_ids: list[str],
    reason: str,
) -> None:
    run_dir = paths.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    calls_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for call in state.get("agent_calls", []):
        if isinstance(call, dict) and call.get("candidate_id"):
            calls_by_candidate.setdefault(str(call["candidate_id"]), []).append(call)
    assembly_by_candidate = {
        str(item.get("candidate_id")): item
        for item in state.get("candidate_artifacts", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }

    candidates = []
    for candidate_id in candidate_ids:
        calls = calls_by_candidate.get(candidate_id, [])
        context_paths = [str(call.get("context_path") or "") for call in calls if call.get("context_path")]
        output_paths = []
        for call in calls:
            output_path = _subagent_output_dir_for_call(call, repo_root=_repo_root(state))
            if output_path is not None:
                output_paths.append(str(output_path))
        log_paths = []
        for call in calls:
            for key in ("stdout_path", "stderr_path", "agent_run_path"):
                if call.get(key):
                    log_paths.append(str(call[key]))
        assembly = assembly_by_candidate.get(candidate_id, {})
        candidates.append(
            {
                "candidate_id": candidate_id,
                "error_class": _assembly_result_agent_execution_error_class(assembly) or "agent_execution_failed",
                "context_paths": context_paths,
                "output_paths": output_paths,
                "log_paths": log_paths,
            }
        )

    payload = {
        "error_class": "batch_agent_execution_failure",
        "reason": reason,
        "candidate_ids": candidate_ids,
        "candidates": candidates,
    }
    (run_dir / "batch_error.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_missing_output_batch_error(
    paths: ArtifactPaths,
    run_id: str,
    state: GraphState,
    candidate_ids: list[str],
    reason: str,
) -> None:
    run_dir = paths.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    calls_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for call in state.get("agent_calls", []):
        if isinstance(call, dict) and call.get("candidate_id"):
            calls_by_candidate.setdefault(str(call["candidate_id"]), []).append(call)
    outputs_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for output in state.get("subagent_outputs", []):
        if isinstance(output, dict) and output.get("candidate_id"):
            outputs_by_candidate.setdefault(str(output["candidate_id"]), []).append(output)
    assembly_by_candidate = {
        str(item.get("candidate_id")): item
        for item in state.get("candidate_artifacts", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }
    review_by_candidate = {
        str(item.get("candidate_id")): item
        for item in state.get("review_results", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }

    candidates = []
    for candidate_id in candidate_ids:
        calls = calls_by_candidate.get(candidate_id, [])
        context_paths = [str(call.get("context_path") or "") for call in calls if call.get("context_path")]
        output_paths = []
        for call in calls:
            output_path = _subagent_output_dir_for_call(call, repo_root=_repo_root(state))
            if output_path is not None:
                output_paths.append(str(output_path))
        validation_errors: list[str] = []
        for context_path in context_paths:
            validation_errors.extend(_read_validation_errors(Path(context_path) / "validation_errors.json"))
        for output in outputs_by_candidate.get(candidate_id, []):
            validation_errors.extend(str(error) for error in output.get("errors", []))
        assembly = assembly_by_candidate.get(candidate_id, {})
        review = review_by_candidate.get(candidate_id, {})
        candidates.append(
            {
                "candidate_id": candidate_id,
                "agent_call_ids": [str(call.get("agent_call_id") or "") for call in calls if call.get("agent_call_id")],
                "context_paths": context_paths,
                "output_paths": output_paths,
                "output_path": output_paths[-1] if output_paths else str(paths.run_dir(run_id) / "agent_calls" / candidate_id / "output"),
                "validation_errors": sorted(set(validation_errors)),
                "assembly_path": str(paths.candidate_dir(candidate_id) / "assembly.json"),
                "assembly_errors": list(assembly.get("errors", [])) if isinstance(assembly, dict) else [],
                "review_path": str(paths.candidate_dir(candidate_id) / "review.json"),
                "review_errors": list(review.get("errors", [])) if isinstance(review, dict) else [],
            }
        )

    payload = {
        "error_class": "batch_agent_output_failure",
        "reason": reason,
        "candidate_ids": candidate_ids,
        "candidates": candidates,
    }
    (run_dir / "batch_error.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_verifier_infrastructure_batch_error(
    paths: ArtifactPaths,
    run_id: str,
    state: GraphState,
    candidate_ids: list[str],
    reason: str,
) -> None:
    run_dir = paths.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    evaluations_by_candidate = {
        str(item.get("candidate_id")): item
        for item in state.get("candidate_evaluations", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }
    verification_by_candidate = {
        str(item.get("candidate_id")): item
        for item in state.get("verification_results", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }

    candidates = []
    for candidate_id in candidate_ids:
        candidate_dir = paths.candidate_dir(candidate_id)
        verification = verification_by_candidate.get(candidate_id, {})
        verifier_errors = [str(error) for error in verification.get("errors", [])] if isinstance(verification, dict) else []
        artifact_paths = [
            str(candidate_dir / "verification.json"),
            str(candidate_dir / "verdict.json"),
        ]
        if isinstance(verification, dict):
            for key in ("metrics_path", "report_path"):
                if verification.get(key):
                    artifact_paths.append(str(verification[key]))
        log_paths = [
            str(candidate_dir / STDOUT_LOG),
            str(candidate_dir / STDERR_LOG),
        ]
        if isinstance(verification, dict) and isinstance(verification.get("spectre_logs"), list):
            log_paths.extend(str(path) for path in verification["spectre_logs"])
        evaluation = evaluations_by_candidate.get(candidate_id, {})
        verifier_reason = str(evaluation.get("reason") or "; ".join(verifier_errors) or "verifier_infrastructure_failure")
        candidates.append(
            {
                "candidate_id": candidate_id,
                "error_class": "verifier_infrastructure_failure",
                "verifier_reason": verifier_reason,
                "artifact_dir": str(candidate_dir),
                "workspace_dir": str(paths.workspace_dir(candidate_id)),
                "artifact_paths": list(dict.fromkeys(path for path in artifact_paths if path)),
                "log_paths": list(dict.fromkeys(path for path in log_paths if path)),
                "verifier_errors": verifier_errors,
            }
        )

    payload = {
        "error_class": "batch_verifier_infrastructure_failure",
        "reason": reason,
        "candidate_ids": candidate_ids,
        "candidates": candidates,
    }
    (run_dir / "batch_error.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _read_validation_errors(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]


def _write_verdict(paths: ArtifactPaths | None, candidate_id: str, evaluation: dict[str, Any], errors: list[str]) -> None:
    if paths is None:
        return
    try:
        candidate_dir = paths.candidate_dir(candidate_id)
        candidate_dir.mkdir(parents=True, exist_ok=True)
        (candidate_dir / "verdict.json").write_text(json.dumps(evaluation, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        errors.append(f"evaluate_candidates: could not write verdict for {candidate_id}: {exc}")


def _status_from_review_errors(review_errors: Any) -> str:
    errors = set(review_errors if isinstance(review_errors, list) else [])
    error_markers = {
        "missing_required_artifact",
        "proposal_schema_invalid",
        "workspace_netlist_missing",
        "assembly_failed",
        "missing_valid_subagent_output",
    }
    if any(str(error).startswith("deterministic_review_error") for error in errors):
        return "error"
    if errors & error_markers:
        return "error"
    return "rejected"


def _status_from_acceptance(decision: AcceptanceDecision) -> str:
    if decision == AcceptanceDecision.ACCEPT:
        return "accepted"
    if decision == AcceptanceDecision.ERROR:
        return "error"
    return "rejected"


def _reason_from_acceptance(decision: AcceptanceDecision, verification: VerificationResult) -> str:
    if verification.errors:
        return "; ".join(verification.errors)
    if decision == AcceptanceDecision.ACCEPT:
        return "accepted"
    if decision == AcceptanceDecision.ERROR:
        return "acceptance_error"
    return "acceptance_gate_failed"


def _candidate_ppa_score(runner_state: RunnerState, metrics: dict[str, float]) -> float | None:
    baseline = runner_state.ppa_baseline_metrics or runner_state.accepted_metrics
    if baseline is None:
        return None
    try:
        return ppa_surrogate_score(metrics, baseline)
    except ValueError:
        return None


def _select_winner(evaluations: list[dict[str, Any]], runner_state: RunnerState) -> dict[str, Any] | None:
    accepted = [item for item in evaluations if item.get("status") == "accepted"]
    if not accepted:
        return None
    if runner_state.current_phase == Phase.PHASE1_PERFORMANCE:
        return min(accepted, key=lambda item: float(item.get("metrics", {}).get("performance_nrmse_combined", float("inf"))))
    return min(
        accepted,
        key=lambda item: (
            float("inf") if item.get("ppa_surrogate_score") is None else float(item["ppa_surrogate_score"]),
            float(item.get("metrics", {}).get("performance_nrmse_combined", float("inf"))),
        ),
    )


def _write_batch_error(paths: ArtifactPaths, run_id: str, message: str) -> None:
    run_dir = paths.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "batch_error.json").write_text(json.dumps({"error": message}, indent=2) + "\n", encoding="utf-8")


def _precompute_ledger_entries(
    state: GraphState,
    paths: ArtifactPaths,
    runner_state: RunnerState,
    evaluations: list[dict[str, Any]],
) -> list[LedgerEntry]:
    assignment_map = {str(item["candidate_id"]): item for item in state.get("batch_assignments", [])}
    entries = []
    for evaluation in evaluations:
        candidate_id = str(evaluation["candidate_id"])
        assignment = assignment_map.get(candidate_id, {})
        metrics = dict(evaluation.get("metrics", {}))
        _validate_finite_mapping(metrics)
        ppa_surrogate_score = evaluation.get("ppa_surrogate_score")
        if ppa_surrogate_score is not None and not _finite_number(ppa_surrogate_score):
            raise ValueError("ppa_surrogate_score must be finite")
        entries.append(
            LedgerEntry(
                candidate_id=candidate_id,
                batch_id=str(assignment.get("batch_id", _batch_id_from_candidate_id(candidate_id))),
                phase=Phase(str(assignment.get("phase", runner_state.current_phase.value))),
                agent=AgentRole(str(assignment.get("role", "architecture"))),
                status=CandidateStatus(str(evaluation["status"])),
                reason=str(evaluation.get("reason", "")),
                metrics=metrics,
                ppa_surrogate_score=ppa_surrogate_score,
                artifact_dir=str(paths.candidate_dir(candidate_id)),
                workspace_dir=str(paths.workspace_dir(candidate_id)),
                created_at=datetime.now(timezone.utc),
                contract_hash=runner_state.contract_hash,
            )
        )
    return entries


def _append_ledger_entries(paths: ArtifactPaths, entries: list[LedgerEntry]) -> None:
    if not entries:
        return
    paths.ledger_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with paths.ledger_jsonl.open("a", encoding="utf-8") as handle:
        handle.write("".join(entry.model_dump_json() + "\n" for entry in entries))


def _validate_finite_mapping(metrics: dict[str, Any]) -> None:
    for name, value in metrics.items():
        if not _finite_number(value):
            raise ValueError(f"metric '{name}' must be finite")


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _batch_id_from_candidate_id(candidate_id: str) -> str:
    parts = candidate_id.split("-")
    if len(parts) >= 2:
        return "-".join(parts[:2])
    return candidate_id


def _update_runner_state_after_batch(
    runner_state: RunnerState,
    evaluations: list[dict[str, Any]],
    winner: dict[str, Any] | None,
    state: GraphState,
) -> None:
    runner_state.batch_no += 1
    verified_count = sum(1 for item in evaluations if item.get("status") != "error" and item.get("metrics"))
    if runner_state.current_phase == Phase.PHASE1_PERFORMANCE:
        runner_state.three_bjt_verified_count += verified_count
    elif runner_state.current_phase == Phase.PHASE2A_AREA:
        runner_state.phase2a_verified_count += verified_count
    if verified_count:
        runner_state.last_verification_at = datetime.now(timezone.utc).isoformat()
    if state.get("top_decision_path"):
        runner_state.last_top_decision_path = str(state["top_decision_path"])

    rejected_with_metrics = [item for item in evaluations if item.get("status") == "rejected" and item.get("metrics")]
    if rejected_with_metrics:
        best_failed = min(
            rejected_with_metrics,
            key=lambda item: float(item["metrics"].get("performance_nrmse_combined", float("inf"))),
        )
        runner_state.best_failed_candidate_id = str(best_failed["candidate_id"])
        runner_state.best_failed_metrics = dict(best_failed["metrics"])

    if winner is None:
        return

    metrics = dict(winner.get("metrics", {}))
    runner_state.accepted_candidate_id = str(winner["candidate_id"])
    runner_state.accepted_metrics = metrics
    if runner_state.ppa_baseline_metrics is None:
        runner_state.ppa_baseline_metrics = metrics
    try:
        runner_state.accepted_ppa_surrogate_score = ppa_surrogate_score(metrics, runner_state.ppa_baseline_metrics)
    except ValueError:
        runner_state.accepted_ppa_surrogate_score = None
    if runner_state.current_phase == Phase.PHASE1_PERFORMANCE:
        runner_state.baseline_candidate_id = str(winner["candidate_id"])
        runner_state.current_phase = Phase.PHASE2A_AREA


def _pass_node(name: str):
    def node(state: GraphState) -> GraphState:
        return _record_event(state, name)

    return node


def _route_next(state: GraphState) -> GraphState:
    next_state = _record_event(state, "route_next")
    route = next_state["route"] if "route" in next_state else None
    top_decision = next_state.get("top_decision") or {}
    if isinstance(top_decision, dict):
        decision = top_decision.get("decision")
        if decision == "human_interrupt":
            route = "human_interrupt"
        elif decision == "rerun_verification":
            route = "rerun_verification"
        elif decision == "stop":
            route = "stop"
    if route is None:
        route = "stop"
    if not isinstance(route, str):
        next_state = _record_error(next_state, "route_next", f"invalid route {route!r}; stopping")
        route = "stop"
    elif route not in {"stop", "next_batch", "human_interrupt", "rerun_verification"}:
        next_state = _record_error(next_state, "route_next", f"invalid route {route!r}; stopping")
        route = "stop"
    elif route == "next_batch" and _has_counted_run(next_state) and _has_record_batch_error(next_state):
        route = "stop"
    elif route == "next_batch":
        if "counted_run_remaining" in next_state or "counted_run_total" in next_state:
            try:
                remaining = int(next_state.get("counted_run_remaining", next_state.get("counted_run_total", 0)))
            except (TypeError, ValueError):
                next_state = _record_error(next_state, "route_next", "invalid counted_run_remaining; stopping")
                route = "stop"
            else:
                if remaining <= 1:
                    next_state = {**next_state, "counted_run_remaining": 0}
                    route = "stop"
                else:
                    next_state = {**next_state, "counted_run_remaining": remaining - 1}
        elif next_state.get("stop_after_current_pass"):
            route = "stop"
    return {**next_state, "route": route}


def _has_record_batch_error(state: GraphState) -> bool:
    return any(str(error).startswith("record_batch:") for error in state.get("errors", []))


def _has_counted_run(state: GraphState) -> bool:
    return "counted_run_remaining" in state or "counted_run_total" in state


def _route_condition(state: GraphState) -> str:
    return state.get("route") or "stop"


def build_graph():
    graph = StateGraph(GraphState)
    nodes = {
        "load_context": load_context_node,
        "plan_batch": plan_batch_node,
        "spawn_subagents": spawn_subagents_node,
        "collect_subagent_requests": collect_subagent_requests_node,
        "spawn_prime_agents": spawn_prime_agents_node,
        "collect_prime_outputs": collect_prime_outputs_node,
        "assemble_candidate_proposals": assemble_candidate_proposals_node,
        "deterministic_review": deterministic_review_node,
        "verify_queue": verify_queue_node,
        "evaluate_candidates": evaluate_candidates_node,
        "top_anomaly_check": top_anomaly_check_node,
        "record_batch": record_batch_node,
    }
    for name in GRAPH_NODE_NAMES[:-1]:
        graph.add_node(name, nodes.get(name, _pass_node(name)))
    graph.add_node("route_next", _route_next)

    graph.set_entry_point("load_context")
    for source, target in zip(GRAPH_NODE_NAMES, GRAPH_NODE_NAMES[1:]):
        graph.add_edge(source, target)
    graph.add_conditional_edges(
        "route_next",
        _route_condition,
        {
            "stop": END,
            "next_batch": "plan_batch",
            "human_interrupt": END,
            "rerun_verification": "verify_queue",
        },
    )

    return graph.compile()
