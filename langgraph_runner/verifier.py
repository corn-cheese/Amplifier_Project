from __future__ import annotations

import json
import math
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .schemas import VerificationResult


STDOUT_LOG = "verifier_stdout.log"
STDERR_LOG = "verifier_stderr.log"
VERIFICATION_JSON = "verification.json"
PPA_RUN_OUTPUTS = ("ppa_metrics.json", "ppa_report.log", "spectre_ac.log", "spectre_tran_static.log", "spectre_tran.log")


@dataclass(frozen=True)
class _OutputSnapshot:
    mtime_ns: int
    size: int


@dataclass(frozen=True)
class _VerifierProcessResult:
    returncode: int | None
    stdout: str | bytes | None
    stderr: str | bytes | None
    timed_out: bool


class Verifier:
    def __init__(self, command: str, timeout_seconds: int, min_interval_seconds: int, required_outputs: list[str]):
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.min_interval_seconds = min_interval_seconds
        self.required_outputs = list(required_outputs)
        self._last_run_monotonic: float | None = None
        self._lock = threading.Lock()

    def run(self, candidate_id: str, repo_root: Path, local_candidate_dir: Path, output_dir: Path) -> VerificationResult:
        with self._lock:
            output_dir.mkdir(parents=True, exist_ok=True)
            self._wait_for_min_interval()
            output_snapshots = self._snapshot_outputs(output_dir)
            ppa_run_snapshots = self._snapshot_ppa_run_outputs(local_candidate_dir)
            try:
                command = self.command.format(
                    candidate_id=candidate_id,
                    repo_root=str(repo_root),
                    local_candidate_dir=str(local_candidate_dir),
                    remote_candidate_dir=str(local_candidate_dir),
                    output_dir=str(output_dir),
                )
            except (IndexError, KeyError, ValueError) as exc:
                return self._error(candidate_id, output_dir, f"invalid verifier command template: {exc}")

            completed = self._run_command(command, repo_root)
            if completed.timed_out:
                self._last_run_monotonic = time.monotonic()
                self._write_logs(output_dir, completed.stdout, completed.stderr)
                return self._error(
                    candidate_id,
                    output_dir,
                    f"verifier command timed out after {self.timeout_seconds} seconds",
                )

            self._last_run_monotonic = time.monotonic()
            self._write_logs(output_dir, completed.stdout, completed.stderr)

            if completed.returncode != 0:
                return self._error(
                    candidate_id,
                    output_dir,
                    f"verifier command exited with status {completed.returncode}",
                )

            ppa_result = self._normalize_ppa_outputs(candidate_id, local_candidate_dir, output_dir, ppa_run_snapshots)
            if ppa_result is not None:
                return ppa_result

            missing_outputs = self._missing_required_outputs(output_dir)
            if missing_outputs:
                return self._error(
                    candidate_id,
                    output_dir,
                    "missing required output: " + ", ".join(missing_outputs),
                )

            stale_outputs = self._stale_required_outputs(output_dir, output_snapshots)
            if stale_outputs:
                return self._error(
                    candidate_id,
                    output_dir,
                    "required output not updated by current run: " + ", ".join(stale_outputs),
                )

            verification_path = output_dir / VERIFICATION_JSON
            try:
                return VerificationResult.model_validate_json(verification_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return self._error(candidate_id, output_dir, "missing verification.json")
            except (ValidationError, ValueError) as exc:
                return self._error(candidate_id, output_dir, f"invalid verification.json: {exc}")

    def _wait_for_min_interval(self) -> None:
        if self._last_run_monotonic is None or self.min_interval_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_run_monotonic
        remaining = self.min_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _run_command(self, command: str, repo_root: Path) -> _VerifierProcessResult:
        popen_kwargs = {
            "cwd": str(repo_root),
            "shell": True,
            "text": True,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(command, **popen_kwargs)
        try:
            stdout, stderr = process.communicate(timeout=self.timeout_seconds)
            return _VerifierProcessResult(process.returncode, stdout, stderr, timed_out=False)
        except subprocess.TimeoutExpired as exc:
            _terminate_process_tree(process)
            stdout = exc.stdout
            stderr = exc.stderr
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            return _VerifierProcessResult(getattr(process, "returncode", None), stdout, stderr, timed_out=True)

    def _missing_required_outputs(self, output_dir: Path) -> list[str]:
        missing = []
        for output in self.required_outputs:
            path = self._resolve_output_path(output_dir, output)
            if not path.exists():
                missing.append(output)
        return missing

    def _snapshot_outputs(self, output_dir: Path) -> dict[str, _OutputSnapshot | None]:
        return {output: _file_snapshot(self._resolve_output_path(output_dir, output)) for output in self._freshness_outputs()}

    def _stale_required_outputs(
        self,
        output_dir: Path,
        snapshots: dict[str, _OutputSnapshot | None],
    ) -> list[str]:
        stale = []
        for output in self._freshness_outputs():
            current_snapshot = _file_snapshot(self._resolve_output_path(output_dir, output))
            if current_snapshot is not None and snapshots.get(output) == current_snapshot:
                stale.append(output)
        return stale

    def _freshness_outputs(self) -> list[str]:
        return list(dict.fromkeys([*self.required_outputs, VERIFICATION_JSON]))

    def _resolve_output_path(self, output_dir: Path, output: str) -> Path:
        path = Path(output)
        if not path.is_absolute():
            return output_dir / path
        return path

    def _snapshot_ppa_run_outputs(self, local_candidate_dir: Path) -> dict[str, _OutputSnapshot | None]:
        run_dir = local_candidate_dir / "run"
        return {name: _file_snapshot(run_dir / name) for name in PPA_RUN_OUTPUTS}

    def _normalize_ppa_outputs(
        self,
        candidate_id: str,
        local_candidate_dir: Path,
        output_dir: Path,
        ppa_run_snapshots: dict[str, _OutputSnapshot | None],
    ) -> VerificationResult | None:
        run_dir = local_candidate_dir / "run"
        if not run_dir.is_dir():
            return None
        output_dir.mkdir(parents=True, exist_ok=True)
        copied_logs = []
        for name in PPA_RUN_OUTPUTS:
            source = run_dir / name
            if not source.exists():
                continue
            if _file_snapshot(source) == ppa_run_snapshots.get(name):
                continue
            target = output_dir / name
            shutil.copy2(source, target)
            if name.startswith("spectre_"):
                copied_logs.append(str(target))
        verification_path = output_dir / VERIFICATION_JSON
        metrics_path = output_dir / "ppa_metrics.json"
        report_path = output_dir / "ppa_report.log"
        if not metrics_path.exists() or not report_path.exists():
            return None
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except OSError:
            return None
        except json.JSONDecodeError as exc:
            return self._ppa_metrics_error(candidate_id, output_dir, copied_logs, f"invalid ppa_metrics.json: {exc}")
        area_power = metrics.get("area_power") if isinstance(metrics, dict) else {}
        if not isinstance(area_power, dict):
            area_power = {}
        metric_errors = []
        performance_nrmse_combined = _required_finite_metric(
            metrics.get("performance_nrmse_combined") if isinstance(metrics, dict) else None,
            "performance_nrmse_combined",
            metric_errors,
        )
        area_total_p = _required_finite_metric(
            area_power.get("area_total_p"),
            "area_power.area_total_p",
            metric_errors,
        )
        power_score_basis_w = _required_finite_metric(
            area_power.get("power_score_basis_w"),
            "area_power.power_score_basis_w",
            metric_errors,
        )
        if metric_errors:
            return self._ppa_metrics_error(
                candidate_id,
                output_dir,
                copied_logs,
                "invalid ppa_metrics.json: " + ", ".join(metric_errors),
            )
        result = VerificationResult(
            candidate_id=candidate_id,
            status="passed",
            metrics_path=str(metrics_path),
            report_path=str(report_path),
            spectre_logs=copied_logs,
            performance_nrmse_combined=performance_nrmse_combined,
            area_total_p=area_total_p,
            power_score_basis_w=power_score_basis_w,
            errors=[],
        )
        verification_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return None

    def _ppa_metrics_error(
        self,
        candidate_id: str,
        output_dir: Path,
        spectre_logs: list[str],
        message: str,
    ) -> VerificationResult:
        result = VerificationResult(
            candidate_id=candidate_id,
            status="error",
            metrics_path=str(output_dir / "ppa_metrics.json"),
            report_path=str(output_dir / "ppa_report.log"),
            spectre_logs=spectre_logs,
            performance_nrmse_combined=0.0,
            area_total_p=0.0,
            power_score_basis_w=0.0,
            errors=[message],
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / VERIFICATION_JSON).write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return result

    def _error(self, candidate_id: str, output_dir: Path, message: str) -> VerificationResult:
        result = VerificationResult(
            candidate_id=candidate_id,
            status="error",
            metrics_path=str(output_dir / "ppa_metrics.json"),
            report_path=str(output_dir / "ppa_report.log"),
            spectre_logs=[],
            performance_nrmse_combined=1.0,
            area_total_p=0.0,
            power_score_basis_w=0.0,
            errors=[message],
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / VERIFICATION_JSON).write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return result

    def _write_logs(self, output_dir: Path, stdout: str | bytes | None, stderr: str | bytes | None) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / STDOUT_LOG).write_text(_text(stdout), encoding="utf-8", errors="replace")
        (output_dir / STDERR_LOG).write_text(_text(stderr), encoding="utf-8", errors="replace")


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _file_snapshot(path: Path) -> _OutputSnapshot | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return _OutputSnapshot(mtime_ns=stat.st_mtime_ns, size=stat.st_size)


def _required_finite_metric(value: object, name: str, errors: list[str]) -> float:
    if value is None:
        errors.append(f"{name} is missing or null")
        return 0.0
    if isinstance(value, bool):
        errors.append(f"{name} is not a finite number")
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        errors.append(f"{name} is not a finite number")
        return 0.0
    if not math.isfinite(parsed):
        errors.append(f"{name} is not a finite number")
        return 0.0
    return parsed


def _terminate_process_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode == 0:
                return
        except OSError:
            pass
        _kill_process(process)
        return

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        _kill_process(process)


def _kill_process(process: subprocess.Popen) -> None:
    try:
        process.kill()
    except OSError:
        pass
