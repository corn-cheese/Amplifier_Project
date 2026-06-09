from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import difflib
import json
import math
import random
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

REPO_ROOT_FOR_SCRIPT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT_FOR_SCRIPT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT_FOR_SCRIPT))

from langgraph_runner.config import load_runner_config
from langgraph_runner.review import DeterministicReviewer
from langgraph_runner.verifier import Verifier


DUT_REL_PATH = "amptest_v2p3/COREONLY/dummy_neural_amp.scs"
DEVICES_REL_PATH = "amptest_v2p3/COREONLY/devices.csv"
CAP_MODEL = "cap_vpp_11p5x11p7_m1m4_noshield"
RES_MODEL = "res_high_po_5p73"
DIODE_MODEL = "diode_pd2nw_05v5"
NPN_Q5_MODEL = "npn_05v5_W1p00L1p00"
MIN_GAIN_DB = 35.0
MIN_UPPER_3DB_HZ = 20000.0
MIN_VOUT_PEAK_TO_PEAK_V = 0.02
REJECTED_OBJECTIVE_FOR_STUDY = 1.0e99


@dataclass(frozen=True)
class _TrialJob:
    index: int
    params: dict[str, Any]


class _VerifierLaunchThrottle:
    def __init__(self, min_interval_seconds: int):
        self.min_interval_seconds = max(0, int(min_interval_seconds))
        self._last_launch_monotonic: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        with self._lock:
            if self._last_launch_monotonic is not None:
                remaining = self.min_interval_seconds - (time.monotonic() - self._last_launch_monotonic)
                if remaining > 0:
                    time.sleep(remaining)
            self._last_launch_monotonic = time.monotonic()


def run_sweep(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    config = load_runner_config(args.config).model_dump(mode="json")
    spec = _load_topology_spec(args.topology_spec, repo_root)
    baseline_workspace = _resolve_baseline_workspace(repo_root, spec, args.baseline_workspace)
    baseline_netlist = (baseline_workspace / "dummy_neural_amp.scs").read_text(encoding="utf-8")
    baseline_devices = (baseline_workspace / "devices.csv").read_text(encoding="utf-8")

    if args.trials <= 0:
        raise ValueError("--trials must be positive")
    cadence_workers = _cadence_worker_count(args)
    timestamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    sweep_root = repo_root / str(spec["sweep_output_root"]) / str(spec["slug"]) / timestamp
    sweep_root.mkdir(parents=True, exist_ok=True)
    (sweep_root / "baseline_source.json").write_text(
        json.dumps(
            {
                "source": "topology_spec_sweep",
                "baseline_workspace": str(baseline_workspace),
                "topology_spec": str(args.topology_spec),
                "topology_id": spec["id"],
                "slug": spec["slug"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (sweep_root / "topology_spec.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")

    rng = random.Random(args.seed)
    jobs = [_TrialJob(index, _random_params(spec, rng)) for index in range(args.trials)]
    launch_throttle = _parallel_launch_throttle(config, cadence_workers)
    best: dict[str, Any] | None = None

    for _job, result in _run_trial_jobs(
        jobs,
        lambda job: _run_one_trial_job(
            job,
            args,
            repo_root,
            config,
            sweep_root,
            baseline_netlist,
            baseline_devices,
            spec,
            launch_throttle,
        ),
        cadence_workers,
    ):
        best = _pick_best(best, result)

    if best is not None:
        (sweep_root / "best_trial_summary.json").write_text(json.dumps(_json_safe(best), indent=2) + "\n", encoding="utf-8")
        best_candidate = sweep_root / "best_candidate"
        source = Path(best["trial_dir"])
        best_candidate.mkdir(parents=True, exist_ok=True)
        for name in ("proposal.json", "patch.diff", "notes.md", "topology_spec.json"):
            source_path = source / name
            if source_path.exists():
                shutil.copy2(source_path, best_candidate / name)
    return 0


def _load_topology_spec(path: Path, repo_root: Path) -> dict[str, Any]:
    resolved = _resolve_under_repo(repo_root, path)
    spec = json.loads(resolved.read_text(encoding="utf-8"))
    required = {
        "id",
        "slug",
        "title",
        "source_idea",
        "variant",
        "baseline_workspace",
        "sweep_output_root",
        "candidate_prefix",
        "hypothesis",
        "risk",
        "changed_blocks",
        "target",
        "swept_params",
        "support_devices",
        "remove_devices",
        "line_replacements",
        "insertions",
        "notes",
    }
    missing = sorted(required - set(spec))
    if missing:
        raise ValueError("topology spec missing keys: " + ", ".join(missing))
    if not isinstance(spec["swept_params"], dict):
        raise ValueError("topology spec swept_params must be an object")
    return spec


def _resolve_baseline_workspace(repo_root: Path, spec: dict[str, Any], arg_value: Path | None) -> Path:
    raw = arg_value if arg_value is not None else Path(str(spec["baseline_workspace"]))
    resolved = _resolve_under_repo(repo_root, raw)
    if not (resolved / "dummy_neural_amp.scs").exists() or not (resolved / "devices.csv").exists():
        raise FileNotFoundError(f"baseline workspace missing dummy_neural_amp.scs/devices.csv: {resolved}")
    return resolved


def _cadence_worker_count(args: argparse.Namespace) -> int:
    count = int(getattr(args, "cadence_workers", 1))
    if count <= 0:
        raise ValueError("--cadence-workers must be positive")
    return count


def _parallel_launch_throttle(config: dict[str, Any], cadence_workers: int) -> _VerifierLaunchThrottle | None:
    if cadence_workers <= 1:
        return None
    verifier_config = config.get("verifier")
    if not isinstance(verifier_config, dict):
        return None
    return _VerifierLaunchThrottle(int(verifier_config.get("min_interval_seconds", 0)))


def _run_trial_jobs(jobs: list[_TrialJob], runner: Any, cadence_workers: int) -> list[tuple[_TrialJob, dict[str, Any]]]:
    if cadence_workers <= 1 or len(jobs) <= 1:
        return [(job, runner(job)) for job in jobs]

    results: list[tuple[_TrialJob, dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=cadence_workers) as executor:
        futures = {executor.submit(runner, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            results.append((job, future.result()))
    results.sort(key=lambda item: item[0].index)
    return results


def _run_one_trial_job(
    job: _TrialJob,
    args: argparse.Namespace,
    repo_root: Path,
    config: dict[str, Any],
    sweep_root: Path,
    baseline_netlist: str,
    baseline_devices: str,
    spec: dict[str, Any],
    launch_throttle: _VerifierLaunchThrottle | None,
) -> dict[str, Any]:
    return _run_trial(
        job.index,
        job.params,
        args,
        repo_root,
        config,
        sweep_root,
        baseline_netlist,
        baseline_devices,
        spec,
        launch_throttle,
    )


def _run_trial(
    index: int,
    params: dict[str, Any],
    args: argparse.Namespace,
    repo_root: Path,
    config: dict[str, Any],
    sweep_root: Path,
    baseline_netlist: str,
    baseline_devices: str,
    spec: dict[str, Any],
    launch_throttle: _VerifierLaunchThrottle | None = None,
) -> dict[str, Any]:
    trial_no = index + 1
    candidate_id = _candidate_id(str(spec["candidate_prefix"]), sweep_root.name, trial_no)
    trial_dir = sweep_root / f"trial_{trial_no:04d}"
    workspace_dir = trial_dir / "workspace"
    trial_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    netlist, devices = build_topology_artifacts(baseline_netlist, baseline_devices, spec, params)
    (trial_dir / "dummy_neural_amp.scs").write_text(netlist, encoding="utf-8")
    (trial_dir / "devices.csv").write_text(devices, encoding="utf-8")
    (trial_dir / "params.json").write_text(json.dumps(params, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (trial_dir / "topology_spec.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    (workspace_dir / "dummy_neural_amp.scs").write_text(netlist, encoding="utf-8")
    (workspace_dir / "devices.csv").write_text(devices, encoding="utf-8")
    _write_workspace_config(repo_root, config, workspace_dir)

    pending_objective = {"objective": math.inf, "rejected": True, "reason": "not_verified", "penalties": {}, "failure_modes": []}
    write_candidate_artifacts(
        trial_dir,
        spec=spec,
        candidate_id=candidate_id,
        baseline_netlist=baseline_netlist,
        baseline_devices=baseline_devices,
        trial_netlist=netlist,
        trial_devices=devices,
        params=params,
        objective=pending_objective,
        dut_rel_path=str(config["dut_netlist"]),
        devices_rel_path=str(config["devices_csv"]),
    )

    review = _review_trial(repo_root, config, trial_dir, workspace_dir, candidate_id)
    verification_status = "not_run"
    metrics: dict[str, Any] = {}
    if review.get("passed") and not args.no_verify:
        if launch_throttle is not None:
            launch_throttle.wait()
        verification_status, metrics = _verify_trial(config, repo_root, workspace_dir, trial_dir, candidate_id)
    elif args.no_verify:
        verification_status = "skipped"

    objective = evaluate_raw_trial_objective(bool(review.get("passed")), verification_status, metrics)
    (trial_dir / "objective.json").write_text(json.dumps(_json_safe(objective), indent=2) + "\n", encoding="utf-8")
    write_candidate_artifacts(
        trial_dir,
        spec=spec,
        candidate_id=candidate_id,
        baseline_netlist=baseline_netlist,
        baseline_devices=baseline_devices,
        trial_netlist=netlist,
        trial_devices=devices,
        params=params,
        objective=objective,
        dut_rel_path=str(config["dut_netlist"]),
        devices_rel_path=str(config["devices_csv"]),
    )
    result = {
        "trial_no": trial_no,
        "candidate_id": candidate_id,
        "trial_dir": str(trial_dir),
        "topology_id": spec["id"],
        "topology_slug": spec["slug"],
        "params": params,
        "review": review,
        "verification_status": verification_status,
        "metrics": metrics,
        "objective": objective,
    }
    (trial_dir / "trial_summary.json").write_text(json.dumps(_json_safe(result), indent=2) + "\n", encoding="utf-8")
    return result


def build_topology_artifacts(
    baseline_netlist: str,
    baseline_devices: str,
    spec: dict[str, Any],
    params: dict[str, Any],
) -> tuple[str, str]:
    _validate_params(spec, params)
    netlist = _build_netlist(baseline_netlist, spec, params)
    devices = _build_devices(baseline_devices, spec, params)
    return netlist, devices


def _validate_params(spec: dict[str, Any], params: dict[str, Any]) -> None:
    expected = set(spec["swept_params"])
    missing = sorted(expected - set(params))
    if missing:
        raise ValueError("missing sweep params: " + ", ".join(missing))
    unknown = sorted(set(params) - expected)
    if unknown:
        raise ValueError("unknown sweep params: " + ", ".join(unknown))
    for name, value in params.items():
        decl = spec["swept_params"][name]
        parsed = _finite_float(value)
        if parsed is None or parsed <= 0.0:
            raise ValueError(f"{name} must be positive finite")
        low = _finite_float(decl.get("low"))
        high = _finite_float(decl.get("high"))
        if low is not None and high is not None and low > high:
            raise ValueError(f"{name} low must be <= high")


def _build_netlist(baseline_netlist: str, spec: dict[str, Any], params: dict[str, Any]) -> str:
    remove_names = set(str(name) for name in spec.get("remove_devices", []))
    support_names = {str(device["name"]) for device in spec.get("support_devices", [])}
    replacements = {str(item["device"]): str(item["line"]) for item in spec.get("line_replacements", [])}
    existing_resistors = _existing_param_devices(spec, params, "resistor")
    existing_caps = _existing_param_devices(spec, params, "capacitor")
    seen: set[str] = set()
    lines: list[str] = []

    for line in baseline_netlist.splitlines():
        name = line.split(maxsplit=1)[0] if line.strip() else ""
        if name in remove_names or name in support_names:
            continue
        if name in replacements:
            lines.append(_render_template(replacements[name], spec, params))
            if name in existing_resistors or name in existing_caps:
                seen.add(name)
        elif name in existing_resistors:
            lines.append(_replace_assignment(line, "l", _format_um(existing_resistors[name])))
            seen.add(name)
        elif name in existing_caps:
            lines.append(_replace_assignment(line, "m", _format_multiplier(existing_caps[name])))
            seen.add(name)
        else:
            lines.append(line)

    missing = sorted((set(existing_resistors) | set(existing_caps)) - seen)
    if missing:
        raise ValueError("baseline netlist missing swept devices: " + ", ".join(missing))
    lines = _insert_support_blocks(lines, spec, params)
    return "\n".join(lines) + "\n"


def _insert_support_blocks(lines: list[str], spec: dict[str, Any], params: dict[str, Any]) -> list[str]:
    insertions = list(spec.get("insertions", []))
    if not insertions:
        return lines
    inserted = [False for _ in insertions]
    output: list[str] = []
    for line in lines:
        output.append(line)
        for index, insertion in enumerate(insertions):
            anchor = str(insertion["anchor"])
            if not inserted[index] and line.startswith(anchor + " "):
                output.extend(_render_template(str(item), spec, params) for item in insertion.get("lines", []))
                inserted[index] = True
    if not all(inserted):
        missing = [str(insertion["anchor"]) for was_inserted, insertion in zip(inserted, insertions) if not was_inserted]
        raise ValueError("baseline netlist missing support insertion anchor: " + ", ".join(missing))
    return output


def _build_devices(baseline_devices: str, spec: dict[str, Any], params: dict[str, Any]) -> str:
    input_io = StringIO(baseline_devices)
    reader = csv.DictReader(input_io)
    if reader.fieldnames is None:
        raise ValueError("devices.csv missing header")
    fieldnames = list(reader.fieldnames)
    remove_names = set(str(name) for name in spec.get("remove_devices", []))
    support_devices = list(spec.get("support_devices", []))
    support_names = {str(device["name"]) for device in support_devices}
    existing_resistors = _existing_param_devices(spec, params, "resistor")
    existing_caps = _existing_param_devices(spec, params, "capacitor")
    seen: set[str] = set()
    rows: list[dict[str, str]] = []

    for row in reader:
        name = str(row.get("name") or "")
        if name in remove_names or name in support_names:
            continue
        if name in existing_resistors:
            row["seg_length"] = _format_um(existing_resistors[name])
            seen.add(name)
        if name in existing_caps:
            row["multiplier"] = _format_multiplier(existing_caps[name])
            seen.add(name)
        rows.append(row)

    missing = sorted((set(existing_resistors) | set(existing_caps)) - seen)
    if missing:
        raise ValueError("devices.csv missing swept devices: " + ", ".join(missing))

    for device in support_devices:
        kind = str(device["kind"])
        name = str(device["name"])
        param_name = device.get("param")
        raw_value = params[str(param_name)] if param_name else device.get("value", 1.0)
        if kind == "resistor":
            rows.append(_resistor_row(fieldnames, name, float(raw_value)))
        elif kind == "capacitor":
            rows.append(_capacitor_row(fieldnames, name, float(raw_value)))
        elif kind == "diode":
            rows.append(_diode_row(fieldnames, name, float(raw_value)))
        elif kind in {"npn", "pnp"}:
            rows.append(_bjt_row(fieldnames, device))
        else:
            raise ValueError(f"support device {name} has unsupported kind: {kind}")

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _existing_param_devices(spec: dict[str, Any], params: dict[str, Any], kind: str) -> dict[str, float]:
    output: dict[str, float] = {}
    for name, decl in spec["swept_params"].items():
        if not bool(decl.get("existing", False)):
            continue
        if str(decl.get("kind")) != kind:
            continue
        output[str(decl["device"])] = float(params[name])
    return output


def _random_params(spec: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for name, decl in spec["swept_params"].items():
        if "value" in decl:
            params[name] = decl["value"]
            continue
        low = float(decl["low"])
        high = float(decl["high"])
        if bool(decl.get("log_scale", False)):
            params[name] = math.exp(rng.uniform(math.log(low), math.log(high)))
        else:
            params[name] = rng.uniform(low, high)
    return params


def _render_template(template: str, spec: dict[str, Any], params: dict[str, Any]) -> str:
    values: dict[str, str] = {
        "CAP_MODEL": CAP_MODEL,
        "RES_MODEL": RES_MODEL,
        "DIODE_MODEL": DIODE_MODEL,
        "NPN_Q5_MODEL": NPN_Q5_MODEL,
    }
    for name, value in params.items():
        kind = str(spec["swept_params"][name].get("kind"))
        if kind == "resistor":
            values[name] = _format_um(float(value))
        elif kind in {"capacitor", "diode", "scalar"}:
            values[name] = _format_multiplier(float(value))
        else:
            raise ValueError(f"{name} has unsupported kind: {kind}")
    return template.format(**values)


def write_candidate_artifacts(
    output_dir: Path,
    *,
    spec: dict[str, Any],
    candidate_id: str,
    baseline_netlist: str,
    baseline_devices: str,
    trial_netlist: str,
    trial_devices: str,
    params: dict[str, Any],
    objective: dict[str, Any],
    dut_rel_path: str = DUT_REL_PATH,
    devices_rel_path: str = DEVICES_REL_PATH,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    patch_text = _unified_patch(baseline_netlist, trial_netlist, dut_rel_path)
    patch_text += _unified_patch(baseline_devices, trial_devices, devices_rel_path)
    proposal = {
        "candidate_id": candidate_id,
        "phase": "phase2a_area",
        "agent": "optimizer",
        "hypothesis": spec["hypothesis"],
        "primary_objective": "performance",
        "changed_blocks": list(spec["changed_blocks"]),
        "files_touched": [dut_rel_path, devices_rel_path],
        "expected_effect": {
            "performance_nrmse_combined": "decrease",
            "area_total_p": "increase",
            "power_score_basis_w": "unknown",
        },
        "risk": spec["risk"],
        "patch": patch_text,
    }
    notes = [
        "# Q5 Topology Spec Sweep Trial",
        "",
        f"candidate_id: {candidate_id}",
        f"topology_id: {spec['id']}",
        f"topology_slug: {spec['slug']}",
        f"title: {spec['title']}",
        f"params: {json.dumps(params, sort_keys=True)}",
        f"objective: {json.dumps(_json_safe(objective), sort_keys=True)}",
        "",
        str(spec["hypothesis"]),
        str(spec["risk"]),
        "",
        "Topology notes:",
        *[f"- {item}" for item in spec.get("notes", [])],
        "",
    ]
    (output_dir / "proposal.json").write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
    (output_dir / "patch.diff").write_text(patch_text, encoding="utf-8")
    (output_dir / "notes.md").write_text("\n".join(notes), encoding="utf-8")


def _review_trial(repo_root: Path, config: dict[str, Any], trial_dir: Path, workspace_dir: Path, candidate_id: str) -> dict[str, Any]:
    amptest_config = json.loads((repo_root / str(config["amptest_config"])).read_text(encoding="utf-8"))
    reviewer = DeterministicReviewer(
        allowed_files={str(config["dut_netlist"]), str(config["devices_csv"])},
        dut_subckt=str(amptest_config["dut_subckt"]),
        dut_pins_order=[str(pin) for pin in amptest_config["dut_pins_order"]],
    )
    result = reviewer.review(trial_dir, workspace_dir, candidate_id)
    (trial_dir / "review.json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return result.model_dump(mode="json")


def _verify_trial(
    config: dict[str, Any],
    repo_root: Path,
    workspace_dir: Path,
    trial_dir: Path,
    candidate_id: str,
) -> tuple[str, dict[str, Any]]:
    verifier_config = config["verifier"]
    verifier = Verifier(
        command=str(verifier_config["command"]),
        timeout_seconds=int(verifier_config["timeout_seconds"]),
        min_interval_seconds=int(verifier_config["min_interval_seconds"]),
        required_outputs=list(verifier_config["required_outputs"]),
    )
    result = verifier.run(candidate_id, repo_root, workspace_dir, trial_dir)
    metrics_path = trial_dir / "ppa_metrics.json"
    metrics: dict[str, Any] = {}
    if metrics_path.exists():
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            metrics = {}
    return result.status, metrics


def evaluate_raw_trial_objective(
    review_passed: bool,
    verification_status: str,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    if not review_passed:
        return _rejected("review_failed")
    if verification_status != "passed":
        return _rejected("sim_failed")

    perf = _finite_float(metrics.get("performance_nrmse_combined"))
    if perf is None:
        return _rejected("non_finite_performance_nrmse_combined")
    gain = _metric(metrics, ("ac", "midband_gain_db"))
    upper = _metric(metrics, ("ac", "upper_3db_hz"))
    swing = _metric(metrics, ("tran", "vout_peak_to_peak_v"))
    if gain is None or gain < MIN_GAIN_DB:
        return _rejected("gain_collapse")
    if upper is None or upper < MIN_UPPER_3DB_HZ:
        return _rejected("bandwidth_collapse")
    if swing is None or swing < MIN_VOUT_PEAK_TO_PEAK_V:
        return _rejected("output_swing_collapse")

    area = _metric(metrics, ("area_power", "area_total_p"))
    if area is None:
        return _rejected("non_finite_area_total_p")

    return {
        "objective": perf,
        "rejected": False,
        "reason": "passed",
        "penalties": {},
        "failure_modes": classify_metrics_failure(metrics),
        "performance_nrmse_combined": perf,
        "area_total_p": area,
    }


def classify_metrics_failure(metrics: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    gain = _metric(metrics, ("ac", "midband_gain_db"))
    upper = _metric(metrics, ("ac", "upper_3db_hz"))
    swing = _metric(metrics, ("tran", "vout_peak_to_peak_v"))
    if gain is None or gain < MIN_GAIN_DB:
        failures.append("gain_collapse")
    if upper is None or upper < MIN_UPPER_3DB_HZ:
        failures.append("bandwidth_collapse")
    if swing is None or swing < MIN_VOUT_PEAK_TO_PEAK_V:
        failures.append("output_swing_collapse")
    return failures


def _rejected(reason: str) -> dict[str, Any]:
    return {"objective": math.inf, "rejected": True, "reason": reason, "penalties": {}, "failure_modes": []}


def _pick_best(best: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    if best is None:
        return candidate
    return candidate if _best_rank(candidate) < _best_rank(best) else best


def _best_rank(candidate: dict[str, Any]) -> tuple[int, float, float, int]:
    objective = candidate.get("objective", {})
    rejected = 1 if objective.get("rejected", True) else 0
    objective_value = _finite_float(objective.get("objective"))
    if objective_value is None:
        objective_value = REJECTED_OBJECTIVE_FOR_STUDY
    area = _finite_float(objective.get("area_total_p"))
    if area is None:
        area = REJECTED_OBJECTIVE_FOR_STUDY
    return (rejected, objective_value, area, int(candidate.get("trial_no", 0)))


def _metric(metrics: dict[str, Any], path: tuple[str, ...]) -> float | None:
    value: Any = metrics
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return _finite_float(value)


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _candidate_id(prefix: str, timestamp: str, trial_no: int) -> str:
    safe_prefix = _candidate_id_part(prefix)
    safe_timestamp = _candidate_id_part(timestamp)
    if safe_timestamp:
        return f"{safe_prefix}-{safe_timestamp}-trial-{trial_no:04d}"
    return f"{safe_prefix}-trial-{trial_no:04d}"


def _candidate_id_part(value: str) -> str:
    output = []
    previous_dash = False
    for char in value.strip():
        if char.isalnum() or char in {"_", "-"}:
            output.append(char)
            previous_dash = False
        elif not previous_dash:
            output.append("-")
            previous_dash = True
    return "".join(output).strip("-")


def _replace_assignment(line: str, key: str, value: str) -> str:
    prefix = f"{key}="
    parts = line.split()
    for index, part in enumerate(parts):
        if part.startswith(prefix):
            parts[index] = prefix + value
            return " ".join(parts)
    return line + f" {prefix}{value}"


def _write_workspace_config(repo_root: Path, config: dict[str, Any], workspace_dir: Path) -> None:
    source = repo_root / str(config["amptest_config"])
    data = json.loads(source.read_text(encoding="utf-8"))
    data["dut_netlist"] = "dummy_neural_amp.scs"
    data.setdefault("input_files", {})
    data["input_files"]["devices_csv"] = "devices.csv"
    data["input_files"]["ac_csv"] = "run/ac.csv"
    data["input_files"]["tran_csv"] = "run/tran.csv"
    (workspace_dir / "config.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _unified_patch(before: str, after: str, rel_path: str) -> str:
    body = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
        )
    )
    if not body:
        return ""
    return f"diff --git a/{rel_path} b/{rel_path}\n" + body


def _resistor_row(fieldnames: list[str], name: str, length_um: float) -> dict[str, str]:
    row = {field: "" for field in fieldnames}
    row.update(
        {
            "name": name,
            "type": "resistor",
            "count": "1",
            "segments": "1",
            "seg_length": _format_um(length_um),
            "seg_width": "5.73u",
            "include_in_ppa": "true",
        }
    )
    return row


def _capacitor_row(fieldnames: list[str], name: str, multiplier: float) -> dict[str, str]:
    row = {field: "" for field in fieldnames}
    row.update(
        {
            "name": name,
            "type": "capacitor",
            "count": "1",
            "width": "11.5u",
            "length": "11.7u",
            "multiplier": _format_multiplier(multiplier),
            "include_in_ppa": "true",
        }
    )
    return row


def _diode_row(fieldnames: list[str], name: str, multiplier: float) -> dict[str, str]:
    row = {field: "" for field in fieldnames}
    row.update(
        {
            "name": name,
            "type": "diode",
            "count": "1",
            "width": "1.00u",
            "length": "1.00u",
            "multiplier": _format_multiplier(multiplier),
            "include_in_ppa": "true",
        }
    )
    return row


def _bjt_row(fieldnames: list[str], device: dict[str, Any]) -> dict[str, str]:
    kind = str(device["kind"])
    width = str(device.get("width", "1.00u"))
    length = str(device.get("length", "1.00u"))
    multiplier = str(device.get("multiplier", "1"))
    area = _finite_float(device.get("area_p"))
    if area is None:
        try:
            width_um = float(width.removesuffix("u"))
            length_um = float(length.removesuffix("u"))
            area = width_um * length_um
        except ValueError:
            area = 1.0
    row = {field: "" for field in fieldnames}
    row.update(
        {
            "name": str(device["name"]),
            "type": kind,
            "count": str(device.get("count", "1")),
            "width": width,
            "length": length,
            "multiplier": multiplier,
            "ft_hz": str(device.get("ft_hz", "10meg")),
            "area_p": f"{area:.4f}",
            "include_in_ppa": str(device.get("include_in_ppa", "true")).lower(),
        }
    )
    return row


def _format_um(value: float) -> str:
    number = float(value)
    if abs(number - round(number)) < 1.0e-9:
        return f"{int(round(number))}u"
    return f"{number:.6g}u"


def _format_multiplier(value: float) -> str:
    number = float(value)
    rounded = int(round(number))
    return str(max(1, rounded))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _resolve_under_repo(repo_root: Path, value: Path) -> Path:
    repo = repo_root.resolve()
    resolved = value.resolve() if value.is_absolute() else (repo / value).resolve()
    try:
        resolved.relative_to(repo)
    except ValueError as exc:
        raise ValueError(f"path_outside_repo: {value}") from exc
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Q5 bandpass sweep from a generated topology JSON spec.")
    parser.add_argument("--config", type=Path, default=Path("runner_config.json"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--topology-spec", type=Path, required=True)
    parser.add_argument("--baseline-workspace", type=Path)
    parser.add_argument("--trials", type=int, default=300)
    parser.add_argument("--timestamp")
    parser.add_argument("--seed", type=int, default=23)
    parser.add_argument("--cadence-workers", type=int, default=1, help="Number of Cadence verifier trials to run concurrently.")
    parser.add_argument("--no-verify", action="store_true", help="Generate trial artifacts and review them without running the verifier.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_sweep(args)


if __name__ == "__main__":
    raise SystemExit(main())
