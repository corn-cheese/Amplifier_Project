from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from inspect import Parameter, signature
from pathlib import Path
from typing import Callable, Mapping

from .agent_errors import AGENT_EXECUTION_FAILED, AGENT_PROCESS_FAILED, AGENT_TIMEOUT
from .artifacts import _safe_fragment
from .batch import CandidateAssignment
from .codex_cli import resolve_codex_command
from .schemas import Phase


@dataclass(frozen=True)
class AgentCall:
    role: str
    context_path: Path
    output_dir: Path
    timeout_seconds: int
    artifact_output_dir: Path | None = None
    agent_call_id: str | None = None


@dataclass(frozen=True)
class AgentRunResult:
    exit_code: int
    stdout_path: Path
    stderr_path: Path
    agent_run_path: Path | None = None
    status: str = "completed"
    error_class: str | None = None
    error: str | None = None
    command: list[str] | None = None


def write_context_package(
    *,
    run_dir: Path,
    agent_call_id: str,
    assignment: CandidateAssignment,
    contract_excerpt: str,
    state_summary: dict,
    recent_ledger: list[dict],
    dut_netlist_path: str,
    devices_csv_path: str,
    base_dut: Path,
    base_devices: Path,
    topology_brief: str | None = None,
) -> Path:
    package = run_dir / "agent_calls" / _safe_fragment(agent_call_id)
    base_files = package / "base_files"
    base_files.mkdir(parents=True, exist_ok=True)
    (package / "output").mkdir(parents=True, exist_ok=True)
    if base_dut.exists():
        shutil.copy2(base_dut, base_files / base_dut.name)
    if base_devices.exists():
        shutil.copy2(base_devices, base_files / base_devices.name)
    phase = assignment.phase.value if isinstance(assignment.phase, Phase) else assignment.phase
    allowed_file_paths = [str(dut_netlist_path), str(devices_csv_path)]
    (package / "state_summary.json").write_text(json.dumps(state_summary, indent=2) + "\n", encoding="utf-8")
    (package / "recent_ledger.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in recent_ledger),
        encoding="utf-8",
    )
    proposal_skeleton = {
        "candidate_id": assignment.candidate_id,
        "phase": phase,
        "agent": assignment.role,
        "hypothesis": "string",
        "primary_objective": assignment.primary_objective,
        "changed_blocks": ["bias"],
        "files_touched": allowed_file_paths,
        "expected_effect": {
            "performance_nrmse_combined": "unknown",
            "area_total_p": "unknown",
            "power_score_basis_w": "unknown",
        },
        "risk": "string",
        "patch": "same unified diff text as output/patch.diff",
    }
    feedback_lines = _recent_verification_feedback_lines(state_summary, recent_ledger)
    macro_topology_lines = _macro_topology_directive_lines(assignment, topology_brief)
    context = "\n".join(
        [
            f"# Agent Context: {assignment.role}",
            "",
            f"candidate_id: {assignment.candidate_id}",
            f"batch_id: {assignment.batch_id}",
            f"phase: {phase}",
            f"primary_objective: {assignment.primary_objective}",
            "",
            "## Contract Excerpt",
            contract_excerpt,
            "",
            "## Required Outputs",
            "- output/proposal.json",
            "- output/patch.diff",
            "- output/notes.md",
            "",
            "Write required files under output/ relative to the current context directory.",
            "",
            *macro_topology_lines,
            *feedback_lines,
            "## Candidate Artifact Contract",
            "You must produce a real candidate by writing all three required files. Prose-only answers are invalid; stdout or chat text is not a candidate artifact.",
            "",
            "### output/proposal.json",
            "Write JSON with these required fields and echo the assigned candidate_id, phase, agent, and primary_objective exactly:",
            "",
            "```json",
            json.dumps(proposal_skeleton, indent=2),
            "```",
            "",
            "Allowed expected_effect values for each metric: decrease, increase, no_major_change, unknown. Use one literal per metric.",
            "",
            "### output/patch.diff",
            "- output/patch.diff must be a unified diff that changes only the DUT netlist and/or device accounting.",
            "- The same unified diff text must appear in the proposal.json patch field.",
            "- Candidate patches apply only in isolated candidate snapshots; do not mutate repository files directly.",
            "",
            "### output/notes.md",
            "- output/notes.md must explain the candidate hypothesis, changed blocks, files touched, expected metric effects, risk, and any reviewer/verifier notes.",
            "- Natural-language claims are not evidence. Do not claim verified success; only verifier artifacts and recorded metrics can prove outcomes.",
            "",
            "### File And Design Constraints",
            f"- Allowed file changes: {dut_netlist_path} and {devices_csv_path} only.",
            "- Do not modify amptest config, analyzer, scoring logic, generated testbenches, AC/transient input conditions, supplies, references, inputs, loads, or metric calculations.",
            "- Forbidden: OPAMP, OPAMP-equivalent macro, Verilog-A behavioral amplifier, ideal gain block, controlled source used as an amplifier.",
            "- Use only the exact installed SKY130 Spectre names from the contract allowlist: npn_05v5_W1p00L1p00, npn_05v5_W1p00L2p00, pnp_05v5_W0p68L0p68, pnp_05v5_W3p40L3p40, res_high_po_5p73, cap_vpp_11p5x11p7_m1m4_noshield, sky130_fd_pr__cap_vpp_11p5x11p7_m1m4_noshield, diode_pd2nw_05v5.",
            "- Do not use sky130_fd_pr_main__... aliases; they are not valid for the installed Spectre include path.",
            "- For PPA accounting, every res_high_po_5p73 instance must include explicit l=, w=, and m= on the netlist line.",
            "- Resistor rows in devices.csv are not enough when resistor_source is netlist.",
            "- An abnormally small area_total_p can indicate invalid resistor area accounting, not a better candidate.",
            "- Keep devices.csv synchronized with every netlist device included in PPA accounting.",
            "",
            "## Codex CLI Execution Environment",
            "- Codex CLI execution inherits the operator's existing CLI environment and authentication.",
            "- Do not ask for or require an OpenAI API key.",
            "- Treat CLI/authentication setup as an operator responsibility; your task is only to write the required output artifacts.",
        ]
    )
    (package / "context.md").write_text(context + "\n", encoding="utf-8")
    return package


def _macro_topology_directive_lines(assignment: CandidateAssignment, topology_brief: str | None) -> list[str]:
    directive = assignment.macro_topology_directive
    if not directive:
        return []

    lines = [
        "## Macro Topology Directive",
        "",
        "This candidate must be a macro-topology change, not a local value retune.",
        "Follow the assigned stage count, signal path class, and feedback class when writing the DUT netlist.",
        "This is prompt-only guidance; the deterministic reviewer will not infer or validate semantic stage count.",
        "",
    ]
    for key in ("stage_count", "signal_path_class", "feedback_class", "topology_intent"):
        if key in directive:
            lines.append(f"- {key}: {directive[key]}")

    avoid_patterns = assignment.avoid_patterns or []
    if avoid_patterns:
        lines.extend(["", "Forbidden recent patterns for this candidate:"])
        lines.extend(f"- {pattern}" for pattern in avoid_patterns)

    brief = (topology_brief or "").strip()
    if brief:
        lines.extend(["", "Brief summary of previous attempts:", brief])

    lines.append("")
    return lines


def _recent_verification_feedback_lines(state_summary: dict, recent_ledger: list[dict]) -> list[str]:
    failures = [
        row
        for row in recent_ledger[-6:]
        if str(row.get("status", "")) in {"rejected", "error"}
    ]
    best_failed_id = state_summary.get("best_failed_candidate_id")
    best_failed_metrics = state_summary.get("best_failed_metrics")
    if not failures and not best_failed_id:
        return []

    lines = [
        "## Feedback From Recent Verifications",
        "",
    ]
    if best_failed_id:
        lines.append(f"- Current best failed candidate: {best_failed_id}")
        if isinstance(best_failed_metrics, dict):
            metrics_text = _format_feedback_metrics(best_failed_metrics)
            if metrics_text:
                lines.append(f"  - Best failed metrics: {metrics_text}")

    for row in failures[-3:]:
        candidate_id = str(row.get("candidate_id", "unknown"))
        status = str(row.get("status", "unknown"))
        reason = str(row.get("reason", "")).strip()
        metrics = row.get("metrics", {})
        metrics_text = _format_feedback_metrics(metrics if isinstance(metrics, dict) else {})
        summary = f"- Recent {status}: {candidate_id}"
        if reason:
            summary += f" ({reason})"
        lines.append(summary)
        if metrics_text:
            lines.append(f"  - Metrics: {metrics_text}")
        failure_modes = _classify_metrics_failure(metrics if isinstance(metrics, dict) else {})
        if failure_modes:
            lines.append(f"  - Failure modes: {', '.join(failure_modes)}")
        if _q4_ndrv_drive_collapse_hint(reason, failure_modes):
            lines.append("  - Q4 addition likely collapsed NDRV/Q3 drive; preserve baseline Q1/Q2/Q3 signal path.")

    lines.extend(
        [
            "",
            "For this candidate:",
            "- create a DC-biased amplifier with nonzero supply current; avoid static_current_a=0 failure modes.",
            "- ensure AC and transient metrics can be produced; performance_nrmse_combined must be non-null.",
            "- do not optimize area until performance metrics are non-null and amplifier-like.",
            "- abnormally small area_total_p can indicate invalid resistor area accounting, not a better candidate.",
            "- res_high_po_5p73 resistor instances must include explicit l=, w=, and m= on the DUT netlist line; devices.csv resistor rows are not enough when resistor_source is netlist.",
            "- avoid corrupt patches; output/patch.diff must be directly applicable as a unified diff.",
            "",
        ]
    )
    return lines


def _format_feedback_metrics(metrics: dict) -> str:
    ordered_names = ["performance_nrmse_combined", "midband_gain_db", "upper_3db_hz", "vout_peak_to_peak_v", "area_total_p", "power_score_basis_w"]
    parts = []
    for name in ordered_names:
        value = metrics.get(name)
        if value is None:
            value = _nested_metric(metrics, name)
        if value is not None:
            parts.append(f"{name}={value}")
    return ", ".join(parts)


def _classify_metrics_failure(metrics: dict) -> list[str]:
    failures = []
    gain = _finite_metric(_nested_metric(metrics, "midband_gain_db"))
    upper = _finite_metric(_nested_metric(metrics, "upper_3db_hz"))
    swing = _finite_metric(_nested_metric(metrics, "vout_peak_to_peak_v"))
    if gain is not None and gain < 35.0:
        failures.append("gain_collapse")
    if upper is not None and upper < 20000.0:
        failures.append("bandwidth_collapse")
    if swing is not None and swing < 0.02:
        failures.append("output_swing_collapse")
    return failures


def _q4_ndrv_drive_collapse_hint(reason: str, failure_modes: list[str]) -> bool:
    reason_lower = reason.lower()
    has_q4_context = "q4" in reason_lower or "ndrv" in reason_lower or "active load" in reason_lower
    drive_failures = {"gain_collapse", "output_swing_collapse"}
    return has_q4_context and bool(drive_failures.intersection(failure_modes))


def _nested_metric(metrics: dict, name: str):
    if name in metrics:
        return metrics[name]
    for parent in ("ac", "tran", "area_power"):
        section = metrics.get(parent)
        if isinstance(section, dict) and name in section:
            return section[name]
    return None


def _finite_metric(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


class AgentRunner:
    def __init__(self, executor=None):
        self.executor = executor or self._subprocess_executor

    def _subprocess_executor(
        self,
        command: list[str],
        cwd: Path,
        timeout: int,
        stdin_text: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=dict(env) if env is not None else None,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
            input=stdin_text,
            capture_output=True,
            check=False,
        )
        return completed.returncode, completed.stdout, completed.stderr

    def run(self, call: AgentCall) -> AgentRunResult:
        context_path = call.context_path.resolve()
        output_dir = call.output_dir.resolve()
        artifact_output_dir = (call.artifact_output_dir or call.output_dir).resolve()
        resolved_call = AgentCall(
            role=call.role,
            context_path=context_path,
            output_dir=output_dir,
            timeout_seconds=call.timeout_seconds,
            artifact_output_dir=artifact_output_dir,
            agent_call_id=call.agent_call_id,
        )
        stdout_path = output_dir / "stdout.log"
        stderr_path = output_dir / "stderr.log"
        agent_run_path = output_dir / "agent_run.json"
        child_env, child_environment_summary = _codex_child_environment(context_path)
        command = [
            *resolve_codex_command(),
            "exec",
            "--sandbox",
            "workspace-write",
            "-C",
            str(context_path),
            "-",
        ]
        exit_code = 1
        stdout = ""
        stderr = ""
        status = "error"
        error_class: str | None = None
        error: str | None = None

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            artifact_output_dir.mkdir(parents=True, exist_ok=True)
            context_text = (context_path / "context.md").read_text(encoding="utf-8")
            runtime_prompt = context_text + "\nRequired artifact output directory: " + str(artifact_output_dir) + "\n"
            exit_code, stdout, stderr = _call_executor(
                self.executor,
                command,
                context_path,
                call.timeout_seconds,
                runtime_prompt,
                child_env,
            )
            if exit_code == 0:
                status = "completed"
            else:
                error_class = AGENT_PROCESS_FAILED
                error = _process_error(exit_code, stdout, stderr)
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            stdout = _text(exc.stdout if exc.stdout is not None else exc.output)
            stderr_text = _text(exc.stderr)
            error = f"agent command timed out after {call.timeout_seconds} seconds"
            stderr = stderr_text + ("\n" if stderr_text else "") + error
            error_class = AGENT_TIMEOUT
        except (PermissionError, OSError) as exc:
            exit_code = 1
            stdout = ""
            stderr = str(exc)
            error = str(exc)
            error_class = AGENT_EXECUTION_FAILED

        return _finalize_agent_run(
            call=resolved_call,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            agent_run_path=agent_run_path,
            artifact_output_dir=artifact_output_dir,
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            status=status,
            error_class=error_class,
            error=error,
            environment=child_environment_summary,
        )


def _finalize_agent_run(
    *,
    call: AgentCall,
    stdout_path: Path,
    stderr_path: Path,
    agent_run_path: Path,
    artifact_output_dir: Path,
    command: list[str],
    exit_code: int,
    stdout: str,
    stderr: str,
    status: str,
    error_class: str | None,
    error: str | None,
    environment: dict[str, str] | None = None,
) -> AgentRunResult:
    log_write_errors = []
    try:
        stdout_path.write_text(stdout, encoding="utf-8")
    except OSError as exc:
        log_write_errors.append(f"stdout.log: {exc}")
    try:
        stderr_path.write_text(stderr, encoding="utf-8")
    except OSError as exc:
        log_write_errors.append(f"stderr.log: {exc}")
    if log_write_errors:
        log_error = "could not write agent logs: " + "; ".join(log_write_errors)
        error = _join_error(error, log_error)
        stderr = stderr + ("\n" if stderr else "") + log_error
        error_class = AGENT_EXECUTION_FAILED
        status = "error"
        if exit_code == 0:
            exit_code = 1

    written_agent_run_path: Path | None = agent_run_path
    while True:
        metadata = {
            "agent_call_id": call.agent_call_id,
            "role": call.role,
            "context_path": str(call.context_path),
            "artifact_output_dir": str(artifact_output_dir),
            "log_dir": str(call.output_dir),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "agent_run_path": str(agent_run_path),
            "paths": {
                "context_path": str(call.context_path),
                "artifact_output_dir": str(artifact_output_dir),
                "log_dir": str(call.output_dir),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "agent_run_path": str(agent_run_path),
            },
            "command": command,
            "environment": environment or {},
            "exit_code": exit_code,
            "status": status,
            "error_class": error_class,
            "error": error,
        }
        try:
            agent_run_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
            break
        except OSError as exc:
            metadata_error = f"could not write agent_run.json: {exc}"
            if metadata_error in (error or ""):
                written_agent_run_path = None
                break
            error = _join_error(error, metadata_error)
            error_class = AGENT_EXECUTION_FAILED
            status = "error"
            if exit_code == 0:
                exit_code = 1
            written_agent_run_path = None
            continue

    return AgentRunResult(
        exit_code=exit_code,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        agent_run_path=written_agent_run_path,
        status=status,
        error_class=error_class,
        error=error,
        command=command,
    )


def _codex_child_environment(context_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    env = _allowed_child_environment(os.environ)
    codex_root, environment_label = _codex_runtime_root(context_path)
    codex_home = codex_root / ".codex_home"
    codex_tmp = codex_root / ".codex_tmp"
    codex_home.mkdir(parents=True, exist_ok=True)
    codex_tmp.mkdir(parents=True, exist_ok=True)
    _seed_codex_home(codex_home, _source_codex_home(env))
    env["CODEX_HOME"] = str(codex_home)
    env["TMP"] = str(codex_tmp)
    env["TEMP"] = str(codex_tmp)
    summary = {
        "codex_cli_environment": environment_label,
        "CODEX_HOME": env.get("CODEX_HOME", "<unset>"),
        "TMP": env.get("TMP", "<unset>"),
        "TEMP": env.get("TEMP", "<unset>"),
    }
    return env, summary


def _codex_runtime_root(context_path: Path) -> tuple[Path, str]:
    if context_path.parent.name == "agent_calls":
        return context_path.parent.parent, "run_local_codex_home"
    return context_path, "context_local_codex_home"


def _source_codex_home(env: Mapping[str, str]) -> Path:
    configured = env.get("CODEX_HOME")
    if configured:
        return Path(configured)
    return Path.home() / ".codex"


def _seed_codex_home(target: Path, source: Path) -> None:
    try:
        source_resolved = source.resolve()
        target_resolved = target.resolve()
    except OSError:
        return
    if source_resolved == target_resolved or not source_resolved.is_dir():
        return
    for name in ("auth.json", "config.toml"):
        source_file = source_resolved / name
        if source_file.is_file():
            shutil.copy2(source_file, target_resolved / name)


def _allowed_child_environment(source: Mapping[str, str]) -> dict[str, str]:
    return dict(source)


def _call_executor(
    executor: Callable,
    command: list[str],
    cwd: Path,
    timeout: int,
    stdin_text: str,
    env: Mapping[str, str],
) -> tuple[int, str, str]:
    if _executor_accepts_env(executor):
        return executor(command, cwd, timeout, stdin_text, env=env)
    return executor(command, cwd, timeout, stdin_text)


def _executor_accepts_env(executor: Callable) -> bool:
    try:
        parameters = signature(executor).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(parameter.kind == Parameter.VAR_KEYWORD or parameter.name == "env" for parameter in parameters)


def _join_error(existing: str | None, addition: str) -> str:
    if existing:
        return existing + "; " + addition
    return addition


def _process_error(exit_code: int, stdout: str, stderr: str) -> str:
    detail = (stderr or stdout).strip()
    if detail:
        return f"process exited with code {exit_code}: {detail}"
    return f"process exited with code {exit_code}"


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
