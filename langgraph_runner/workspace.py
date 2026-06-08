from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .artifacts import _safe_fragment


DUT_NETLIST = "dummy_neural_amp.scs"
DEVICES_CSV = "devices.csv"
DEFAULT_DUT_NETLIST_PATH = f"amptest/{DUT_NETLIST}"
DEFAULT_DEVICES_CSV_PATH = f"amptest/{DEVICES_CSV}"
ALLOWED_PATCH_PATHS = {DEFAULT_DUT_NETLIST_PATH, DEFAULT_DEVICES_CSV_PATH}
HUNK_HEADER_RE = re.compile(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")
INCLUDE_RE = re.compile(r'^\s*(?:include|ahdl_include)\s+"([^"]+)"', re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class PatchApplyResult:
    applied: bool
    reason: str


def _required_workspace_files(workspace: Path) -> tuple[Path, Path]:
    return workspace / DUT_NETLIST, workspace / DEVICES_CSV


def resolve_candidate_base_files(repo_root: Path, config: dict) -> tuple[Path, Path]:
    base_workspace = str(config.get("candidate_base_workspace") or "").strip()
    if not base_workspace:
        return (
            _repo_path(repo_root, str(config["dut_netlist"])),
            _repo_path(repo_root, str(config["devices_csv"])),
        )

    workspace = _repo_path(repo_root, base_workspace)
    return (
        workspace / Path(str(config["dut_netlist"])).name,
        workspace / Path(str(config["devices_csv"])).name,
    )


def _repo_path(repo_root: Path, value: str) -> Path:
    repo = repo_root.resolve()
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (repo / path).resolve()
    try:
        resolved.relative_to(repo)
    except ValueError as exc:
        raise ValueError(f"path_outside_repo: {value}") from exc
    return resolved


def _best_effort_unlink(path: Path) -> None:
    try:
        path.unlink()
    except (FileNotFoundError, OSError):
        pass


def _copy_pair_staged(sources: tuple[Path, Path], targets: tuple[Path, Path], stage_name: str) -> None:
    for source in sources:
        if not source.exists():
            raise FileNotFoundError(f"required source file missing: {source}")
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)

    temps = tuple(target.with_name(f"{target.name}.{stage_name}.tmp") for target in targets)
    backups = tuple(target.with_name(f"{target.name}.{stage_name}.bak") for target in targets)
    try:
        for source, temp in zip(sources, temps, strict=True):
            shutil.copy2(source, temp)
    except Exception:
        for temp in temps:
            _best_effort_unlink(temp)
        raise

    backup_exists: list[bool] = []
    try:
        for target, backup in zip(targets, backups, strict=True):
            if target.exists():
                shutil.copy2(target, backup)
                backup_exists.append(True)
            else:
                _best_effort_unlink(backup)
                backup_exists.append(False)
    except Exception:
        for temp in temps:
            _best_effort_unlink(temp)
        for backup in backups:
            _best_effort_unlink(backup)
        raise

    touched: list[Path] = []
    try:
        for temp, target in zip(temps, targets, strict=True):
            touched.append(target)
            _replace_staged_file(temp, target)
    except Exception:
        _restore_backups(targets, backups, backup_exists, touched)
        _cleanup_staged_files(temps, backups)
        raise
    _cleanup_staged_files(temps, backups)


def _replace_staged_file(temp: Path, target: Path) -> None:
    try:
        temp.replace(target)
    except PermissionError:
        shutil.copy2(temp, target)
        _best_effort_unlink(temp)


def _restore_backups(targets: tuple[Path, Path], backups: tuple[Path, Path], backup_exists: list[bool], touched: list[Path]) -> None:
    touched_set = set(touched)
    for target, backup, had_backup in zip(targets, backups, backup_exists, strict=True):
        if target not in touched_set:
            continue
        if had_backup:
            try:
                shutil.copy2(backup, target)
            except OSError:
                pass
        else:
            _best_effort_unlink(target)


def _cleanup_staged_files(temps: tuple[Path, Path], backups: tuple[Path, Path]) -> None:
    for path in (*temps, *backups):
        _best_effort_unlink(path)


def _sandbox_unlink_failure(stderr: str) -> bool:
    return "unable to unlink" in stderr or "unable to write file" in stderr


def _strip_patch_prefix(value: str) -> str:
    value = value.strip()
    if value == "/dev/null":
        return value
    if value.startswith("a/") or value.startswith("b/"):
        return value[2:]
    return value


def _apply_unified_diff_fallback(scratch: Path, patch_text: str, allowed_patch_paths: set[str] | None = None) -> PatchApplyResult | None:
    allowed_patch_paths = allowed_patch_paths or ALLOWED_PATCH_PATHS
    lines = patch_text.splitlines(keepends=True)
    index = 0
    patched_any = False
    patched_contents: dict[str, list[str]] = {}

    while index < len(lines):
        if not lines[index].startswith("diff --git "):
            if lines[index].strip():
                raise ValueError("patch must contain git diff sections")
            index += 1
            continue

        diff_parts = lines[index].strip().split()
        if len(diff_parts) < 4:
            raise ValueError("invalid diff header")
        index += 1
        old_path = None
        new_path = None
        while index < len(lines) and not lines[index].startswith("--- "):
            if lines[index].startswith("diff --git "):
                raise ValueError("diff section missing file headers")
            index += 1
        if index < len(lines) and lines[index].startswith("--- "):
            old_path = _strip_patch_prefix(lines[index][4:].strip())
            index += 1
        if index < len(lines) and lines[index].startswith("+++ "):
            new_path = _strip_patch_prefix(lines[index][4:].strip())
            index += 1
        if old_path is None or new_path is None:
            raise ValueError("diff section missing file headers")

        rel_path = old_path if new_path == "/dev/null" else new_path
        if rel_path not in allowed_patch_paths:
            raise ValueError(f"unsupported patch path: {rel_path}")
        if new_path == "/dev/null":
            return PatchApplyResult(False, "missing patched output: " + rel_path)
        if old_path == "/dev/null":
            raise ValueError("new files are not supported")

        hunk_lines: list[str] = []
        while index < len(lines) and not lines[index].startswith("diff --git "):
            hunk_lines.append(lines[index])
            index += 1
        if not hunk_lines:
            raise ValueError("diff section missing hunks")

        file_path = scratch / rel_path
        patched_contents[rel_path] = _apply_hunks(file_path.read_text(encoding="utf-8").splitlines(keepends=True), hunk_lines)
        patched_any = True

    if not patched_any:
        raise ValueError("patch contained no supported hunks")

    for rel_path, content in patched_contents.items():
        (scratch / rel_path).write_text("".join(content), encoding="utf-8")
    return PatchApplyResult(True, "applied")


def _apply_hunks(original: list[str], hunk_lines: list[str]) -> list[str]:
    output: list[str] = []
    cursor = 0
    index = 0
    while index < len(hunk_lines):
        header = hunk_lines[index]
        if not header.startswith("@@ "):
            raise ValueError("expected hunk header")
        match = HUNK_HEADER_RE.match(header)
        if match is None:
            raise ValueError("invalid hunk header")
        hunk_start = int(match.group(1)) - 1
        if hunk_start < cursor:
            raise ValueError("overlapping hunks")
        output.extend(original[cursor:hunk_start])
        cursor = hunk_start
        index += 1

        while index < len(hunk_lines) and not hunk_lines[index].startswith("@@ "):
            line = hunk_lines[index]
            marker = line[:1]
            payload = line[1:]
            if marker == " ":
                _require_matching_source_line(original, cursor, payload)
                output.append(original[cursor])
                cursor += 1
            elif marker == "-":
                _require_matching_source_line(original, cursor, payload)
                cursor += 1
            elif marker == "+":
                output.append(payload)
            elif marker == "\\":
                pass
            else:
                raise ValueError("invalid hunk line")
            index += 1

    output.extend(original[cursor:])
    return output


def _require_matching_source_line(original: list[str], cursor: int, expected: str) -> None:
    if cursor >= len(original) or original[cursor] != expected:
        raise ValueError("hunk context does not match source")


def _ensure_amptest_layout(workspace: Path) -> Path:
    scratch = workspace / "_patch_root"
    amptest = scratch / "amptest"
    amptest.mkdir(parents=True, exist_ok=True)
    dut, devices = _required_workspace_files(workspace)
    shutil.copy2(dut, amptest / DUT_NETLIST)
    shutil.copy2(devices, amptest / DEVICES_CSV)
    return scratch


def _ensure_patch_layout(workspace: Path, dut_netlist_path: str, devices_csv_path: str) -> Path:
    if dut_netlist_path == DEFAULT_DUT_NETLIST_PATH and devices_csv_path == DEFAULT_DEVICES_CSV_PATH:
        return _ensure_amptest_layout(workspace)

    scratch = workspace / "_patch_root"
    dut_target = scratch / dut_netlist_path
    devices_target = scratch / devices_csv_path
    dut_target.parent.mkdir(parents=True, exist_ok=True)
    devices_target.parent.mkdir(parents=True, exist_ok=True)
    dut, devices = _required_workspace_files(workspace)
    shutil.copy2(dut, dut_target)
    shutil.copy2(devices, devices_target)
    return scratch


def _safe_patch_path(value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"patch path must be repo-relative: {value}")
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"unsafe patch path: {value}")
    return path.as_posix()


def _base_support_files(base_dut: Path, config: dict) -> list[Path]:
    refs = set(INCLUDE_RE.findall(base_dut.read_text(encoding="utf-8")))
    for key in ("include_files", "ahdl_include_files"):
        for value in config.get(key, []):
            refs.add(str(value))

    sources: list[Path] = []
    for ref in sorted(refs):
        rel_path = _direct_relative_include_path(ref)
        if rel_path is None:
            continue
        source = base_dut.parent / rel_path
        if source.is_file():
            sources.append(source)
    return sources


def _direct_relative_include_path(value: str) -> Path | None:
    path = Path(value)
    if path.is_absolute():
        return None
    parts = path.parts
    if len(parts) != 1 or parts[0] in {"", ".", ".."}:
        return None
    return path


class CandidateWorkspace:
    def __init__(
        self,
        workspace_root: Path,
        dut_netlist_path: str = DEFAULT_DUT_NETLIST_PATH,
        devices_csv_path: str = DEFAULT_DEVICES_CSV_PATH,
    ):
        self.workspace_root = workspace_root
        self.dut_netlist_path = _safe_patch_path(dut_netlist_path)
        self.devices_csv_path = _safe_patch_path(devices_csv_path)
        self.allowed_patch_paths = {self.dut_netlist_path, self.devices_csv_path}

    def create(self, candidate_id: str, base_dut: Path, base_devices: Path, base_config: Path) -> Path:
        workspace = self.workspace_root / _safe_fragment(candidate_id)
        workspace.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base_dut, workspace / DUT_NETLIST)
        shutil.copy2(base_devices, workspace / DEVICES_CSV)
        config = json.loads(base_config.read_text(encoding="utf-8"))
        config["dut_netlist"] = DUT_NETLIST
        config.setdefault("input_files", {})
        config["input_files"]["devices_csv"] = DEVICES_CSV
        config["input_files"]["ac_csv"] = "run/ac.csv"
        config["input_files"]["tran_csv"] = "run/tran.csv"
        (workspace / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        for source in _base_support_files(base_dut, config):
            shutil.copy2(source, workspace / source.name)
        return workspace

    def promote(self, workspace: Path, target_dut: Path, target_devices: Path) -> None:
        source_dut, source_devices = _required_workspace_files(workspace)
        _copy_pair_staged((source_dut, source_devices), (target_dut, target_devices), "promote")

    def apply_patch(self, workspace: Path, patch_text: str) -> PatchApplyResult:
        scratch = _ensure_patch_layout(workspace, self.dut_netlist_path, self.devices_csv_path)
        patch_file = workspace / "patch.diff"
        patch_file.write_bytes(patch_text.encode("utf-8"))
        env = os.environ.copy()
        existing_ceiling = env.get("GIT_CEILING_DIRECTORIES")
        workspace_ceiling = str(workspace.resolve())
        env["GIT_CEILING_DIRECTORIES"] = (
            workspace_ceiling if not existing_ceiling else existing_ceiling + os.pathsep + workspace_ceiling
        )
        completed = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(patch_file.resolve())],
            cwd=str(scratch),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            if _sandbox_unlink_failure(completed.stderr):
                try:
                    fallback_result = _apply_unified_diff_fallback(scratch, patch_text, self.allowed_patch_paths)
                except ValueError:
                    return PatchApplyResult(False, "git apply failed: " + completed.stderr.strip())
                if not fallback_result.applied:
                    return fallback_result
            else:
                return PatchApplyResult(False, "git apply failed: " + completed.stderr.strip())
        patched_dut = scratch / self.dut_netlist_path
        patched_devices = scratch / self.devices_csv_path
        missing = [path for path in (patched_dut, patched_devices) if not path.exists()]
        if missing:
            missing_names = ", ".join(str(path.relative_to(scratch)).replace("\\", "/") for path in missing)
            return PatchApplyResult(False, "missing patched output: " + missing_names)
        _copy_pair_staged((patched_dut, patched_devices), (workspace / DUT_NETLIST, workspace / DEVICES_CSV), "patch")
        return PatchApplyResult(True, "applied")
