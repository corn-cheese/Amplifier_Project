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
