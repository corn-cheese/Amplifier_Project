from __future__ import annotations

import json
import shutil
import subprocess
import sys
import csv
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .artifacts import ArtifactPaths
from .config import RunnerConfig, load_runner_config
from .graph import build_graph
from .review import ALLOWED_DEVICE_TYPES, FORBIDDEN_SHORTCUT_PATTERNS
from .state_store import StateStore


DEFAULT_PRODUCTION_ARTIFACT_ROOT = "automation_artifacts/prod"
PRODUCTION_SPEC_PATH = "docs/superpowers/specs/2026-06-05-langgraph-runner-production-run-design.md"
REQUIRED_PRODUCTION_CONTRACT_PATH = "docs/top-coordinator-contract.md"
MIN_PRODUCTION_VERIFIER_TIMEOUT_SECONDS = 1800


@dataclass(frozen=True)
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ProductionRunResult:
    config_path: Path
    artifact_root: Path
    backup_dir: Path
    summary_path: Path
    checks: dict[str, dict[str, str]]
    graph_state: dict[str, Any]


class ProductionRunError(RuntimeError):
    def __init__(self, message: str, checks: dict[str, dict[str, str]] | None = None):
        super().__init__(message)
        self.checks = checks or {}


CommandRunner = Callable[..., CommandResult]
GraphFactory = Callable[[], Any]


def prepare_production_config(
    *,
    repo_root: Path,
    base_config_path: Path,
    artifact_root: str = DEFAULT_PRODUCTION_ARTIFACT_ROOT,
    timestamp: str | None = None,
    config_output: str | Path | None = None,
) -> Path:
    repo_root = repo_root.resolve()
    base_config_path = _resolve_under_repo(repo_root, base_config_path)
    base_config = load_runner_config(base_config_path)
    contract_path = _repo_relative_path(repo_root, base_config.contract_path)
    if contract_path != REQUIRED_PRODUCTION_CONTRACT_PATH:
        raise ValueError(f"production contract_path must remain {REQUIRED_PRODUCTION_CONTRACT_PATH}")
    artifact_root_value = _repo_relative_path(repo_root, artifact_root)
    base_artifact_root = _repo_relative_path(repo_root, base_config.artifact_root)
    if artifact_root_value == base_artifact_root:
        raise ValueError("production run requires an isolated artifact root")

    for value in (
        base_config.contract_path,
        base_config.amptest_dir,
        base_config.dut_netlist,
        base_config.devices_csv,
        base_config.amptest_config,
    ):
        _resolve_under_repo(repo_root, value)

    data = base_config.model_dump(mode="json")
    data["artifact_root"] = artifact_root_value
    data["candidate_generation_batch_size"] = 1
    data["verifier"] = {
        **data["verifier"],
        "min_interval_seconds": max(int(data["verifier"]["min_interval_seconds"]), 30),
        "timeout_seconds": max(
            int(data["verifier"]["timeout_seconds"]),
            MIN_PRODUCTION_VERIFIER_TIMEOUT_SECONDS,
        ),
    }

    output_path = _production_config_path(repo_root, timestamp, config_output)
    if output_path == base_config_path or output_path == repo_root / "runner_config.json":
        raise ValueError("production config output must not overwrite the repository default or base config")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    load_runner_config(output_path)
    return output_path


def create_production_backup(
    *,
    repo_root: Path,
    config_path: Path,
    config: RunnerConfig,
    timestamp: str | None = None,
) -> Path:
    repo_root = repo_root.resolve()
    config_path = _resolve_under_repo(repo_root, config_path)
    artifact_root = _resolve_under_repo(repo_root, config.artifact_root)
    backup_dir = _unique_dir(artifact_root / "backups" / (timestamp or _timestamp()))
    backup_dir.mkdir(parents=True, exist_ok=False)

    manifest: dict[str, Any] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "config_path": str(config_path),
        "artifact_root": str(artifact_root),
        "copied": [],
        "missing": [],
    }

    _copy_repo_file(repo_root, backup_dir, config.dut_netlist, manifest)
    _copy_repo_file(repo_root, backup_dir, config.devices_csv, manifest)
    _copy_artifact_file(artifact_root, backup_dir, "state.json", manifest)
    _copy_artifact_file(artifact_root, backup_dir, "ledger.jsonl", manifest)
    _copy_artifact_dir(artifact_root, backup_dir, "candidates", manifest)
    _copy_artifact_dir(artifact_root, backup_dir, "workspaces", manifest)
    _copy_artifact_dir(artifact_root, backup_dir, "runs", manifest)

    config_backup = backup_dir / "operator_config" / config_path.name
    config_backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config_path, config_backup)
    manifest["copied"].append(f"operator_config/{config_path.name}")

    (backup_dir / "backup_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return backup_dir


def run_production_canary(
    *,
    repo_root: Path,
    base_config_path: Path,
    artifact_root: str = DEFAULT_PRODUCTION_ARTIFACT_ROOT,
    config_output: str | Path | None = None,
    timestamp: str | None = None,
    run_id: str = "manual",
    eda_smoke_command: str | None = None,
    eda_signoff: str | None = None,
    command_runner: CommandRunner | None = None,
    graph_factory: GraphFactory = build_graph,
) -> ProductionRunResult:
    repo_root = repo_root.resolve()
    timestamp_value = timestamp or _timestamp()
    runner = command_runner or _run_command
    checks: dict[str, dict[str, str]] = {}

    try:
        config_path = prepare_production_config(
            repo_root=repo_root,
            base_config_path=base_config_path,
            artifact_root=artifact_root,
            timestamp=timestamp_value,
            config_output=config_output,
        )
    except (OSError, ValueError) as exc:
        _write_early_failure(repo_root, artifact_root, run_id, checks, str(exc))
        raise ProductionRunError(str(exc), checks) from exc
    config = load_runner_config(config_path)
    paths = ArtifactPaths(repo_root=repo_root, artifact_root=_resolve_under_repo(repo_root, config.artifact_root))

    try:
        git_status = runner(["git", "status", "--short"], cwd=repo_root)
        _record_command_check(checks, "git_status", git_status, accepted_status="reviewed")
        if git_status.returncode != 0:
            raise ProductionRunError("git status preflight failed", checks)
        _check_contract(repo_root, config, checks)
        _initialize_production_state(repo_root, paths, config, checks)
        _require_command_success(
            checks,
            "unit_tests",
            runner(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests/langgraph_runner",
                    "-p",
                    "test_*.py",
                    "-v",
                ],
                cwd=repo_root,
            ),
        )
        _require_command_success(checks, "codex_exec_help", runner(["codex", "exec", "--help"], cwd=repo_root))
        _check_eda_smoke(
            repo_root=repo_root,
            config=config,
            eda_smoke_command=eda_smoke_command,
            eda_signoff=eda_signoff,
            runner=runner,
            checks=checks,
        )

        backup_dir = create_production_backup(
            repo_root=repo_root,
            config_path=config_path,
            config=config,
            timestamp=timestamp_value,
        )
        checks["backup"] = {"status": "passed", "details": str(backup_dir)}

        run_dir = paths.run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        start_note = {
            "production_spec": PRODUCTION_SPEC_PATH,
            "config_path": str(config_path),
            "artifact_root": str(paths.artifact_root),
            "backup_dir": str(backup_dir),
            "run_id": run_id,
            "checks": checks,
        }
        (run_dir / "production_run_start.json").write_text(json.dumps(start_note, indent=2) + "\n", encoding="utf-8")

        initial_state = {
            "repo_root": str(repo_root),
            "run_id": run_id,
            "config_path": str(config_path),
            "state_path": str(paths.state_json),
            "route": "stop",
        }
        graph_state = graph_factory().invoke(initial_state, config={"recursion_limit": 20})

        summary_path = run_dir / "production_run_summary.json"
        summary = {
            **start_note,
            "summary_path": str(summary_path),
            "graph": _graph_summary(graph_state),
        }
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return ProductionRunResult(
            config_path=config_path,
            artifact_root=paths.artifact_root,
            backup_dir=backup_dir,
            summary_path=summary_path,
            checks=checks,
            graph_state=graph_state,
        )
    except ProductionRunError as exc:
        _write_failure(paths, run_id, config_path, checks, str(exc))
        raise
    except (OSError, ValueError) as exc:
        _write_failure(paths, run_id, config_path, checks, str(exc))
        raise ProductionRunError(str(exc), checks) from exc


def _initialize_production_state(
    repo_root: Path,
    paths: ArtifactPaths,
    config: RunnerConfig,
    checks: dict[str, dict[str, str]],
) -> None:
    contract_path = _resolve_under_repo(repo_root, config.contract_path)
    StateStore(paths=paths, contract_path=contract_path).initialize()
    if not paths.state_json.exists() or not paths.ledger_jsonl.exists():
        raise ProductionRunError("production init did not create canonical state and ledger", checks)
    checks["init"] = {"status": "passed", "details": str(paths.state_json)}


def _check_contract(repo_root: Path, config: RunnerConfig, checks: dict[str, dict[str, str]]) -> None:
    contract = _resolve_under_repo(repo_root, config.contract_path)
    amptest_config_path = _resolve_under_repo(repo_root, config.amptest_config)
    dut_netlist = _resolve_under_repo(repo_root, config.dut_netlist)
    devices_csv = _resolve_under_repo(repo_root, config.devices_csv)
    amptest_dir = _resolve_under_repo(repo_root, config.amptest_dir)

    missing = [str(path) for path in (contract, amptest_config_path, dut_netlist, devices_csv, amptest_dir) if not path.exists()]
    if missing:
        checks["contract"] = {"status": "failed", "details": "missing: " + ", ".join(missing)}
        raise ProductionRunError("contract preflight failed", checks)

    try:
        amptest_config = json.loads(amptest_config_path.read_text(encoding="utf-8"))
        dut_subckt = str(amptest_config["dut_subckt"]).strip()
        dut_pins = list(amptest_config["dut_pins_order"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        checks["contract"] = {"status": "failed", "details": f"invalid amptest config: {exc}"}
        raise ProductionRunError("contract preflight failed", checks) from exc
    if not dut_subckt or not dut_pins or not all(isinstance(pin, str) and pin.strip() for pin in dut_pins):
        checks["contract"] = {"status": "failed", "details": "invalid DUT subckt or pin order"}
        raise ProductionRunError("contract preflight failed", checks)

    actual_pins = _find_subckt_pins(dut_netlist, dut_subckt)
    if actual_pins != dut_pins:
        checks["contract"] = {
            "status": "failed",
            "details": f"DUT pin order mismatch for {dut_subckt}: expected {dut_pins}, got {actual_pins}",
        }
        raise ProductionRunError("contract preflight failed", checks)
    if not _devices_csv_is_valid(devices_csv):
        checks["contract"] = {
            "status": "failed",
            "details": f"invalid devices.csv at {devices_csv}",
        }
        raise ProductionRunError("contract preflight failed", checks)

    checks["contract"] = {
        "status": "passed",
        "details": f"{dut_subckt} {' '.join(dut_pins)}; devices.csv valid",
    }


def _check_eda_smoke(
    *,
    repo_root: Path,
    config: RunnerConfig,
    eda_smoke_command: str | None,
    eda_signoff: str | None,
    runner: CommandRunner,
    checks: dict[str, dict[str, str]],
) -> None:
    if eda_smoke_command:
        _require_command_success(
            checks,
            "eda_smoke",
            runner(
                eda_smoke_command,
                cwd=repo_root,
                shell=True,
                timeout=config.verifier.timeout_seconds,
            ),
        )
        return
    if eda_signoff:
        checks["eda_smoke"] = {"status": "signed_off", "details": eda_signoff}
        return
    checks["eda_smoke"] = {
        "status": "failed",
        "details": "provide --eda-smoke-command or --eda-signoff before production execution",
    }
    raise ProductionRunError("EDA smoke command or signoff is required before production execution", checks)


def _record_command_check(
    checks: dict[str, dict[str, str]],
    name: str,
    result: CommandResult,
    *,
    accepted_status: str = "passed",
) -> None:
    status = accepted_status if result.returncode == 0 else "failed"
    checks[name] = {"status": status, "details": _command_details(result)}


def _require_command_success(checks: dict[str, dict[str, str]], name: str, result: CommandResult) -> None:
    _record_command_check(checks, name, result)
    if result.returncode != 0:
        raise ProductionRunError(f"{name} failed", checks)


def _run_command(command, *, cwd: Path, shell: bool = False, timeout: int | None = None) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            shell=shell,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        return CommandResult(
            command=_command_text(command),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        stdout = _text(getattr(exc, "stdout", None))
        stderr = _text(getattr(exc, "stderr", None) or str(exc))
        return CommandResult(command=_command_text(command), returncode=1, stdout=stdout, stderr=stderr)


def _copy_repo_file(repo_root: Path, backup_dir: Path, value: str, manifest: dict[str, Any]) -> None:
    source = _unresolved_under_repo(repo_root, value)
    label = source.relative_to(repo_root).as_posix()
    if not source.exists():
        manifest["missing"].append(label)
        return
    _ensure_no_links(source)
    target = backup_dir / "repo_files" / source.relative_to(repo_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    manifest["copied"].append(label)


def _copy_artifact_file(artifact_root: Path, backup_dir: Path, name: str, manifest: dict[str, Any]) -> None:
    source = artifact_root / name
    label = f"artifact_root/{name}"
    if not source.exists():
        manifest["missing"].append(label)
        return
    _ensure_no_links(source)
    target = backup_dir / "artifact_root" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    manifest["copied"].append(label)


def _copy_artifact_dir(artifact_root: Path, backup_dir: Path, name: str, manifest: dict[str, Any]) -> None:
    source = artifact_root / name
    label = f"artifact_root/{name}/"
    if not source.exists():
        manifest["missing"].append(label)
        return
    _ensure_no_links(source)
    shutil.copytree(source, backup_dir / "artifact_root" / name)
    manifest["copied"].append(label)


def _write_failure(
    paths: ArtifactPaths,
    run_id: str,
    config_path: Path | None,
    checks: dict[str, dict[str, str]],
    error: str,
) -> Path:
    run_dir = paths.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    failure_path = run_dir / "production_run_failure.json"
    payload = {
        "production_spec": PRODUCTION_SPEC_PATH,
        "config_path": str(config_path) if config_path is not None else None,
        "artifact_root": str(paths.artifact_root),
        "run_id": run_id,
        "error": error,
        "checks": checks,
    }
    failure_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return failure_path


def _write_early_failure(
    repo_root: Path,
    artifact_root: str,
    run_id: str,
    checks: dict[str, dict[str, str]],
    error: str,
) -> Path | None:
    try:
        artifact_root_value = _repo_relative_path(repo_root, artifact_root)
    except ValueError:
        return None
    if artifact_root_value == "automation_artifacts":
        return None
    paths = ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / artifact_root_value)
    return _write_failure(paths, run_id, None, checks, error)


def _production_config_path(repo_root: Path, timestamp: str | None, config_output: str | Path | None) -> Path:
    if config_output is not None:
        return _resolve_under_repo(repo_root, config_output)
    return repo_root / "automation_artifacts" / "operator_configs" / f"prod-run-{timestamp or _timestamp()}.json"


def _resolve_under_repo(repo_root: Path, value: str | Path) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (repo_root / path).resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"path must stay under repo root: {value}") from exc
    return resolved


def _unresolved_under_repo(repo_root: Path, value: str | Path) -> Path:
    path = Path(value)
    absolute = path if path.is_absolute() else repo_root / path
    normalized = Path(os.path.abspath(absolute))
    try:
        normalized.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"path must stay under repo root: {value}") from exc
    return normalized


def _repo_relative_path(repo_root: Path, value: str | Path) -> str:
    resolved = _resolve_under_repo(repo_root, value)
    relative = resolved.relative_to(repo_root).as_posix()
    if not relative or relative == ".":
        raise ValueError(f"path must not resolve to repo root: {value}")
    return relative


def _find_subckt_pins(dut_netlist: Path, dut_subckt: str) -> list[str] | None:
    for line in dut_netlist.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0].lower() == "subckt" and parts[1] == dut_subckt:
            return parts[2:]
    return None


def _devices_csv_is_valid(path: Path) -> bool:
    required = {"name", "type", "count", "include_in_ppa"}
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
                return False
            for row in reader:
                row_text = " ".join(str(value) for value in row.values() if value is not None)
                if any(pattern.search(row_text) for pattern in FORBIDDEN_SHORTCUT_PATTERNS):
                    return False
                if str(row.get("type") or "").strip().lower() not in ALLOWED_DEVICE_TYPES:
                    return False
    except (OSError, csv.Error):
        return False
    return True


def _ensure_no_links(path: Path) -> None:
    if _is_link_or_junction(path):
        raise ValueError(f"backup source must not be a link or junction: {path}")
    if not path.is_dir():
        return
    for child in path.rglob("*"):
        if _is_link_or_junction(child):
            raise ValueError(f"backup source must not contain links or junctions: {child}")


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(is_junction) and is_junction())


def _graph_summary(graph_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "route": graph_state.get("route"),
        "events": graph_state.get("events", []),
        "candidate_ids": graph_state.get("candidate_ids", []),
        "candidate_evaluations": graph_state.get("candidate_evaluations", []),
        "promoted_candidate_id": graph_state.get("promoted_candidate_id"),
        "errors": graph_state.get("errors", []),
    }


def _command_details(result: CommandResult) -> str:
    details = {
        "command": result.command,
        "returncode": result.returncode,
        "stdout": _truncate(result.stdout),
        "stderr": _truncate(result.stderr),
    }
    return json.dumps(details, sort_keys=True)


def _command_text(command) -> str:
    if isinstance(command, (list, tuple)):
        return subprocess.list2cmdline([str(part) for part in command])
    return str(command)


def _truncate(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _unique_dir(path: Path) -> Path:
    if not path.exists():
        return path
    index = 1
    while True:
        candidate = path.with_name(f"{path.name}-{index}")
        if not candidate.exists():
            return candidate
        index += 1
