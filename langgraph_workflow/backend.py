from __future__ import annotations

import shutil
import shlex
import subprocess
import time
import os
import hashlib
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from .state import AMPT_EST_COMMAND
from .rendering import mark_artifact_complete


class BackendError(RuntimeError):
    pass


class BackendConfigError(BackendError):
    pass


@dataclass(frozen=True)
class RunOutcome:
    exit_code: int
    stdout: str
    stderr: str
    downloaded_files: list[Path]


class MockBackend:
    name = "mock"

    def __init__(self, fixture_dir: str | Path):
        self.fixture_dir = Path(fixture_dir)

    def run(self, artifact_dir: str | Path) -> RunOutcome:
        artifact_path = Path(artifact_dir)
        artifact_path.mkdir(parents=True, exist_ok=True)
        copied: list[Path] = []
        for fixture in self.fixture_dir.iterdir():
            if fixture.is_file():
                target = artifact_path / fixture.name
                shutil.copy2(fixture, target)
                copied.append(target)
        if (artifact_path / "ppa_metrics.json").exists():
            mark_artifact_complete(artifact_path)
        return RunOutcome(exit_code=0, stdout="mock backend completed\n", stderr="", downloaded_files=copied)


class EdaSshBackend:
    name = "eda_ssh"

    def __init__(
        self,
        remote: dict[str, Any],
        *,
        amptest_local_dir: str | Path = "amptest",
        timeout_s: int = 1200,
        min_interval_s: int = 60,
        daily_max_trials: int = 20,
    ):
        self.remote = remote
        self.amptest_local_dir = Path(amptest_local_dir)
        self.timeout_s = timeout_s
        self.min_interval_s = min_interval_s
        self.daily_max_trials = daily_max_trials

    def validate(self) -> None:
        missing = [key for key in ("host", "user", "base_dir") if not str(self.remote.get(key, "")).strip()]
        if missing:
            raise BackendConfigError(f"remote config missing required keys: {', '.join(missing)}")
        for filename in ("ppa_wrapper.py", "ppa_wrapper_core.py"):
            if not (self.amptest_local_dir / filename).exists():
                raise BackendConfigError(f"missing evaluator file: {self.amptest_local_dir / filename}")
        identity_file = self._remote_path_setting("identity_file", "key_file")
        if identity_file and not Path(identity_file).expanduser().exists():
            raise BackendConfigError(f"missing SSH identity file: {identity_file}")
        ssh_config = self._remote_path_setting("ssh_config", "config_file")
        if ssh_config and not Path(ssh_config).expanduser().exists():
            raise BackendConfigError(f"missing SSH config file: {ssh_config}")

    def run(self, artifact_dir: str | Path) -> RunOutcome:
        self.validate()
        artifact_path = Path(artifact_dir)
        with self._cadence_execution_lock(artifact_path):
            self._enforce_rate_limit(artifact_path)
            remote_dir = self._remote_trial_dir(artifact_path)
            remote_target = f"{self.remote['user']}@{self.remote['host']}:{remote_dir}"

            stdout_chunks: list[str] = []
            stderr_chunks: list[str] = []
            downloaded: list[Path] = []
            exit_code = 0
            try:
                self._run_cmd(
                    self._ssh_command(f"mkdir -p {shlex.quote(remote_dir)}"),
                    stdout_chunks,
                    stderr_chunks,
                )
                upload_files = [
                    self.amptest_local_dir / "ppa_wrapper.py",
                    self.amptest_local_dir / "ppa_wrapper_core.py",
                    artifact_path / "candidate.scs",
                    artifact_path / "devices.csv",
                    artifact_path / "config.json",
                    artifact_path / "trial_metadata.json",
                ]
                self._run_cmd(
                    self._scp_command([str(p) for p in upload_files], f"{remote_target}/"),
                    stdout_chunks,
                    stderr_chunks,
                )
                self._run_cmd(
                    self._ssh_command(self._remote_bash_command(self._remote_run_script(remote_dir))),
                    stdout_chunks,
                    stderr_chunks,
                )
            except subprocess.CalledProcessError as exc:
                exit_code = exc.returncode
            finally:
                for filename in (
                    "ppa_metrics.json",
                    "ppa_report.log",
                    "ppa_summary.log",
                    "spectre_ac.log",
                    "spectre_tran.log",
                    "ac.csv",
                    "tran.csv",
                    "ac_response.png",
                    "transient_response.png",
                    "remote_stdout.log",
                    "remote_stderr.log",
                ):
                    try:
                        self._download_file(remote_dir, filename, artifact_path)
                        downloaded.append(artifact_path / filename)
                    except (subprocess.SubprocessError, OSError):
                        continue
                self._append_downloaded_remote_logs(artifact_path, stdout_chunks, stderr_chunks)
                if exit_code == 0 and (artifact_path / "ppa_metrics.json").exists():
                    mark_artifact_complete(artifact_path)
            return RunOutcome(exit_code, "".join(stdout_chunks), "".join(stderr_chunks), downloaded)

    def check(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        for tool in ("ssh", "scp"):
            path = shutil.which(tool)
            checks.append({"name": f"local_{tool}", "ok": path is not None, "path": path})
        try:
            self.validate()
            checks.append({"name": "config", "ok": True})
        except BackendConfigError as exc:
            checks.append({"name": "config", "ok": False, "error": str(exc)})

        if not all(check["ok"] for check in checks):
            return {"ok": False, "checks": checks}

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        try:
            self._run_cmd(
                self._ssh_command(self._remote_bash_command(self._remote_check_script())),
                stdout_chunks,
                stderr_chunks,
            )
            checks.append({"name": "remote", "ok": True})
        except subprocess.CalledProcessError as exc:
            checks.append(
                {
                    "name": "remote",
                    "ok": False,
                    "exit_code": exc.returncode,
                    "stdout": exc.output or "",
                    "stderr": exc.stderr or "",
                }
            )
        return {
            "ok": all(check["ok"] for check in checks),
            "checks": checks,
            "stdout": "".join(stdout_chunks),
            "stderr": "".join(stderr_chunks),
        }

    def _ssh_host(self) -> str:
        return f"{self.remote['user']}@{self.remote['host']}"

    def _ssh_command(self, remote_command: str) -> list[str]:
        return ["ssh", *self._ssh_options(for_scp=False), self._ssh_host(), remote_command]

    def _scp_command(self, sources: list[str], target: str) -> list[str]:
        return ["scp", *self._ssh_options(for_scp=True), *sources, target]

    def _ssh_options(self, *, for_scp: bool) -> list[str]:
        options: list[str] = []
        ssh_config = self._remote_path_setting("ssh_config", "config_file")
        if ssh_config:
            options.extend(["-F", str(Path(ssh_config).expanduser())])
        port = self.remote.get("port")
        if port:
            options.extend(["-P" if for_scp else "-p", str(port)])
        identity_file = self._remote_path_setting("identity_file", "key_file")
        if identity_file:
            options.extend(["-i", str(Path(identity_file).expanduser())])

        batch_mode = self.remote.get("batch_mode", True)
        if batch_mode is not None:
            options.extend(["-o", f"BatchMode={self._ssh_bool_value(batch_mode)}"])
        self._append_ssh_option(options, "connect_timeout_s", "ConnectTimeout")
        strict = self.remote.get("strict_host_key_checking")
        if strict is not None:
            options.extend(["-o", f"StrictHostKeyChecking={self._ssh_bool_value(strict)}"])
        known_hosts = self._remote_path_setting("known_hosts_file", "user_known_hosts_file")
        if known_hosts:
            options.extend(["-o", f"UserKnownHostsFile={Path(known_hosts).expanduser()}"])
        self._append_ssh_option(options, "server_alive_interval_s", "ServerAliveInterval")
        self._append_ssh_option(options, "server_alive_count_max", "ServerAliveCountMax")
        return options

    def _append_ssh_option(self, options: list[str], remote_key: str, ssh_key: str) -> None:
        value = self.remote.get(remote_key)
        if value is not None:
            options.extend(["-o", f"{ssh_key}={value}"])

    def _remote_path_setting(self, *keys: str) -> str | None:
        for key in keys:
            value = self.remote.get(key)
            if value:
                return str(value)
        return None

    def _ssh_bool_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return "yes" if value else "no"
        return str(value)

    def _remote_trial_dir(self, artifact_path: Path) -> str:
        seed_id = artifact_path.parent.name
        trial_id = artifact_path.name
        return f"{str(self.remote['base_dir']).rstrip('/')}/{seed_id}/{trial_id}"

    def _remote_bash_command(self, script: str) -> str:
        return f"bash -lc {shlex.quote(script)}"

    def _remote_run_script(self, remote_dir: str) -> str:
        lines = [
            "set -euo pipefail",
            f"cd {shlex.quote(remote_dir)}",
        ]
        pre_command = str(self.remote.get("pre_command", "")).strip()
        if pre_command:
            lines.append(pre_command)
        lines.append(f"{self._remote_eval_command()} > remote_stdout.log 2> remote_stderr.log")
        return "\n".join(lines)

    def _remote_check_script(self) -> str:
        lines = ["set -euo pipefail"]
        pre_command = str(self.remote.get("pre_command", "")).strip()
        if pre_command:
            lines.append(pre_command)
        lines.extend(
            [
                f"mkdir -p {shlex.quote(str(self.remote['base_dir']).rstrip('/'))}",
                f"{self._remote_python_command()} --version",
                "command -v spectre >/dev/null 2>&1 || true",
                "command -v ocean >/dev/null 2>&1 || true",
            ]
        )
        return "\n".join(lines)

    def _remote_eval_command(self) -> str:
        command = str(self.remote.get("command", AMPT_EST_COMMAND))
        python = self.remote.get("python")
        if python and command.startswith("python3 "):
            return command.replace("python3", str(python), 1)
        return command

    def _remote_python_command(self) -> str:
        return str(self.remote.get("python", "python3"))

    def _download_file(self, remote_dir: str, filename: str, artifact_path: Path) -> None:
        remote_source = f"{self._ssh_host()}:{remote_dir}/{filename}"
        subprocess.run(
            self._scp_command([remote_source], str(artifact_path / filename)),
            check=True,
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
        )

    def _run_cmd(
        self,
        cmd: list[str],
        stdout_chunks: list[str],
        stderr_chunks: list[str],
    ) -> None:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=self.timeout_s)
        stdout_chunks.append(result.stdout)
        stderr_chunks.append(result.stderr)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, cmd, output=result.stdout, stderr=result.stderr)

    def _append_downloaded_remote_logs(
        self,
        artifact_path: Path,
        stdout_chunks: list[str],
        stderr_chunks: list[str],
    ) -> None:
        stdout_log = artifact_path / "remote_stdout.log"
        stderr_log = artifact_path / "remote_stderr.log"
        if stdout_log.exists():
            stdout_chunks.append(stdout_log.read_text(errors="replace"))
        if stderr_log.exists():
            stderr_chunks.append(stderr_log.read_text(errors="replace"))

    @contextmanager
    def _cadence_execution_lock(self, artifact_path: Path):
        lock_path = self._cadence_lock_path(artifact_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd: int | None = None
        try:
            while fd is None:
                try:
                    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, f"pid={os.getpid()}\nstarted={datetime.now(UTC).isoformat()}\n".encode("utf-8"))
                except FileExistsError:
                    time.sleep(1.0)
            yield
        finally:
            if fd is not None:
                os.close(fd)
                try:
                    lock_path.unlink()
                except (FileNotFoundError, PermissionError):
                    pass

    def _cadence_lock_path(self, artifact_path: Path) -> Path:
        run_root = artifact_path.parents[1].resolve()
        digest = hashlib.sha256(str(run_root).encode("utf-8")).hexdigest()[:16]
        return Path(tempfile.gettempdir()) / "langgraph_workflow_locks" / f"cadence_{digest}.lock"

    def _enforce_rate_limit(self, artifact_path: Path) -> None:
        """Throttle Cadence/Spectre starts to avoid abnormal burst access."""

        state_path = artifact_path.parents[1] / "cadence_rate_limit.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        today = datetime.now(UTC).date().isoformat()
        state: dict[str, Any] = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
            except json.JSONDecodeError:
                state = {}
        if state.get("date") != today:
            state = {"date": today, "count": 0, "last_start_epoch": None}
        if int(state.get("count", 0)) >= self.daily_max_trials:
            raise BackendError(f"daily Cadence trial limit reached: {self.daily_max_trials}")
        last_start = state.get("last_start_epoch")
        if last_start is not None:
            wait_s = self.min_interval_s - (now - float(last_start))
            if wait_s > 0:
                time.sleep(wait_s)
                now = time.time()
        state["last_start_epoch"] = now
        state["count"] = int(state.get("count", 0)) + 1
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
