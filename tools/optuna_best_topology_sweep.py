from __future__ import annotations

import argparse
import csv
import difflib
import json
import math
import random
import shutil
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

from langgraph_runner.config import load_runner_config
from langgraph_runner.review import DeterministicReviewer
from langgraph_runner.verifier import Verifier
from tools.optuna_q4_sweep import evaluate_trial_objective


DUT_REL_PATH = "amptest/dummy_neural_amp.scs"
DEVICES_REL_PATH = "amptest/devices.csv"
DEFAULT_BEST_CANDIDATE_ID = "p1-b066-c03-arch-20260607-082159"
STRATEGY_STATE_REL_PATH = "automation_artifacts/strategy_rotation.json"

RESISTOR_PARAMS = {
    "RVREF_l": ("RVREF", 10000.0, 40000.0, False),
    "RC1_l": ("RC1", 350.0, 900.0, False),
    "RE1U_l": ("RE1U", 10.0, 80.0, True),
    "RE1B_l": ("RE1B", 150.0, 900.0, False),
    "RC2_l": ("RC2", 250.0, 700.0, False),
    "RQ4U_l": ("RQ4U", 100.0, 1000.0, True),
    "RQ4R_l": ("RQ4R", 250.0, 2000.0, True),
    "RQ4FB_l": ("RQ4FB", 5000.0, 50000.0, True),
    "REQ4_l": ("REQ4", 10.0, 150.0, True),
    "RE2U_l": ("RE2U", 10.0, 80.0, True),
    "RE2B_l": ("RE2B", 150.0, 900.0, False),
    "RBUF_l": ("RBUF", 400.0, 1600.0, False),
}
CAP_PARAMS = {
    "CE1_m": ("CE1", 20000000.0, 90000000.0, False),
    "CE2_m": ("CE2", 20000000.0, 90000000.0, False),
    "CP1_m": ("CP1", 100.0, 3000.0, True),
    "CP2_m": ("CP2", 100.0, 4000.0, True),
    "CP3_m": ("CP3", 100.0, 3000.0, True),
}
ALL_PARAMS = {**RESISTOR_PARAMS, **CAP_PARAMS}


def build_best_topology_artifacts(
    baseline_netlist: str,
    baseline_devices: str,
    params: dict[str, float],
) -> tuple[str, str]:
    _validate_params(params)
    baseline_q_lines = _q_device_lines(baseline_netlist)
    if set(baseline_q_lines) != {"Q1", "Q2", "Q3", "Q4"}:
        raise ValueError("baseline must contain fixed Q1/Q2/Q3/Q4 topology devices")

    netlist = _retune_netlist(baseline_netlist, params)
    devices = _retune_devices(baseline_devices, params)
    for name, line in baseline_q_lines.items():
        if line not in netlist.splitlines():
            raise ValueError(f"{name} topology line changed")
    return netlist, devices


def write_candidate_artifacts(
    output_dir: Path,
    *,
    candidate_id: str,
    baseline_netlist: str,
    baseline_devices: str,
    trial_netlist: str,
    trial_devices: str,
    params: dict[str, float],
    objective: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    patch_text = _unified_patch(baseline_netlist, trial_netlist, DUT_REL_PATH)
    patch_text += _unified_patch(baseline_devices, trial_devices, DEVICES_REL_PATH)
    proposal = {
        "candidate_id": candidate_id,
        "phase": "phase1_performance",
        "agent": "optimizer",
        "hypothesis": "Retune passive values in the current best 4BJT topology while preserving all BJT device connections.",
        "primary_objective": "performance",
        "changed_blocks": ["fixed_topology_passive_tuning", "pole_zero_shaping", "device_accounting"],
        "files_touched": [DUT_REL_PATH, DEVICES_REL_PATH],
        "expected_effect": {
            "performance_nrmse_combined": "decrease",
            "area_total_p": "unknown",
            "power_score_basis_w": "unknown",
        },
        "risk": "Retuning capacitors and bias resistors can shift cutoff, ripple, output centering, or Q4 bias out of the useful region.",
        "patch": patch_text,
    }
    notes = [
        "# Best Topology Fixed-Structure Trial",
        "",
        f"candidate_id: {candidate_id}",
        f"params: {json.dumps(params, sort_keys=True)}",
        f"objective: {json.dumps(_json_safe(objective), sort_keys=True)}",
        "",
        "Q1/Q2/Q3/Q4 topology lines are preserved from the selected best workspace.",
        "Only resistor `l=` values and capacitor `m=` multipliers are swept.",
        "",
    ]
    (output_dir / "proposal.json").write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
    (output_dir / "patch.diff").write_text(patch_text, encoding="utf-8")
    (output_dir / "notes.md").write_text("\n".join(notes), encoding="utf-8")


def run_sweep(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    config = load_runner_config(args.config).model_dump(mode="json")
    baseline_workspace = _baseline_workspace(repo_root, args.baseline_workspace)
    baseline_netlist = (baseline_workspace / "dummy_neural_amp.scs").read_text(encoding="utf-8")
    baseline_devices = (baseline_workspace / "devices.csv").read_text(encoding="utf-8")
    timestamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    sweep_root = repo_root / str(config["artifact_root"]) / "sweeps" / "best-topology-fixed" / timestamp
    sweep_root.mkdir(parents=True, exist_ok=True)
    (sweep_root / "baseline_source.json").write_text(
        json.dumps({"baseline_workspace": str(baseline_workspace)}, indent=2) + "\n",
        encoding="utf-8",
    )

    if args.trials <= 0:
        raise ValueError("--trials must be positive")

    best: dict[str, Any] | None = None
    optuna = _load_optuna()
    if optuna is None:
        rng = random.Random(args.seed)
        for index in range(args.trials):
            result = _run_trial(index, _random_params(rng), args, repo_root, config, sweep_root, baseline_netlist, baseline_devices)
            best = _pick_best(best, result)
    else:
        sampler = optuna.samplers.TPESampler(seed=args.seed)
        study = optuna.create_study(direction="minimize", study_name=args.study_name, sampler=sampler)

        def objective(trial):
            params = _suggest_params(trial)
            result = _run_trial(trial.number, params, args, repo_root, config, sweep_root, baseline_netlist, baseline_devices)
            nonlocal best
            best = _pick_best(best, result)
            value = result["objective"]["objective"]
            return value if math.isfinite(value) else 1.0e9

        study.optimize(objective, n_trials=args.trials, timeout=args.timeout_seconds)

    if best is not None:
        (sweep_root / "best_trial_summary.json").write_text(json.dumps(_json_safe(best), indent=2) + "\n", encoding="utf-8")
        best_candidate = sweep_root / "best_candidate"
        source = Path(best["trial_dir"])
        best_candidate.mkdir(parents=True, exist_ok=True)
        for name in ("proposal.json", "patch.diff", "notes.md"):
            shutil.copy2(source / name, best_candidate / name)
    return 0


def _run_trial(
    index: int,
    params: dict[str, float],
    args: argparse.Namespace,
    repo_root: Path,
    config: dict[str, Any],
    sweep_root: Path,
    baseline_netlist: str,
    baseline_devices: str,
) -> dict[str, Any]:
    trial_no = index + 1
    candidate_id = f"best-topology-trial-{trial_no:04d}"
    trial_dir = sweep_root / f"trial_{trial_no:04d}"
    workspace_dir = trial_dir / "workspace"
    trial_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    netlist, devices = build_best_topology_artifacts(baseline_netlist, baseline_devices, params)
    (trial_dir / "dummy_neural_amp.scs").write_text(netlist, encoding="utf-8")
    (trial_dir / "devices.csv").write_text(devices, encoding="utf-8")
    (trial_dir / "params.json").write_text(json.dumps(params, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (workspace_dir / "dummy_neural_amp.scs").write_text(netlist, encoding="utf-8")
    (workspace_dir / "devices.csv").write_text(devices, encoding="utf-8")
    _write_workspace_config(repo_root, config, workspace_dir)

    pending_objective = {"objective": math.inf, "rejected": True, "reason": "not_verified", "penalties": {}}
    write_candidate_artifacts(
        trial_dir,
        candidate_id=candidate_id,
        baseline_netlist=baseline_netlist,
        baseline_devices=baseline_devices,
        trial_netlist=netlist,
        trial_devices=devices,
        params=params,
        objective=pending_objective,
    )

    review = _review_trial(repo_root, config, trial_dir, workspace_dir, candidate_id)
    verification_status = "not_run"
    metrics: dict[str, Any] = {}
    if review.get("passed") and not args.no_verify:
        verification_status, metrics = _verify_trial(config, repo_root, workspace_dir, trial_dir, candidate_id)
    elif args.no_verify:
        verification_status = "skipped"

    objective = evaluate_trial_objective(bool(review.get("passed")), verification_status, metrics)
    (trial_dir / "objective.json").write_text(json.dumps(_json_safe(objective), indent=2) + "\n", encoding="utf-8")
    write_candidate_artifacts(
        trial_dir,
        candidate_id=candidate_id,
        baseline_netlist=baseline_netlist,
        baseline_devices=baseline_devices,
        trial_netlist=netlist,
        trial_devices=devices,
        params=params,
        objective=objective,
    )
    result = {
        "trial_no": trial_no,
        "candidate_id": candidate_id,
        "trial_dir": str(trial_dir),
        "params": params,
        "review": review,
        "verification_status": verification_status,
        "metrics": metrics,
        "objective": objective,
    }
    (trial_dir / "trial_summary.json").write_text(json.dumps(_json_safe(result), indent=2) + "\n", encoding="utf-8")
    return result


def _retune_netlist(baseline_netlist: str, params: dict[str, float]) -> str:
    resistor_values = {spec[0]: params[name] for name, spec in RESISTOR_PARAMS.items() if name in params}
    cap_values = {spec[0]: params[name] for name, spec in CAP_PARAMS.items() if name in params}
    lines = []
    seen = set()
    for line in baseline_netlist.splitlines():
        name = line.split(maxsplit=1)[0] if line.strip() else ""
        if name in resistor_values:
            lines.append(_replace_assignment(line, "l", _format_um(resistor_values[name])))
            seen.add(name)
        elif name in cap_values:
            lines.append(_replace_assignment(line, "m", _format_multiplier(cap_values[name])))
            seen.add(name)
        else:
            lines.append(line)
    expected = set(resistor_values) | set(cap_values)
    missing = sorted(expected - seen)
    if missing:
        raise ValueError("baseline netlist missing swept devices: " + ", ".join(missing))
    return "\n".join(lines) + "\n"


def _retune_devices(baseline_devices: str, params: dict[str, float]) -> str:
    resistor_values = {spec[0]: params[name] for name, spec in RESISTOR_PARAMS.items() if name in params}
    cap_values = {spec[0]: params[name] for name, spec in CAP_PARAMS.items() if name in params}
    input_io = StringIO(baseline_devices)
    reader = csv.DictReader(input_io)
    if reader.fieldnames is None:
        raise ValueError("devices.csv missing header")
    fieldnames = list(reader.fieldnames)
    rows = []
    seen = set()
    for row in reader:
        name = str(row.get("name") or "")
        if name in resistor_values:
            row["seg_length"] = _format_um(resistor_values[name])
            seen.add(name)
        if name in cap_values:
            row["multiplier"] = _format_multiplier(cap_values[name])
            seen.add(name)
        rows.append(row)
    expected = set(resistor_values) | set(cap_values)
    missing = sorted(expected - seen)
    if missing:
        raise ValueError("devices.csv missing swept devices: " + ", ".join(missing))
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


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


def _suggest_params(trial: Any) -> dict[str, float]:
    params = {}
    for name, (_device, low, high, log_scale) in ALL_PARAMS.items():
        params[name] = trial.suggest_float(name, low, high, log=log_scale)
    return params


def _random_params(rng: random.Random) -> dict[str, float]:
    params = {}
    for name, (_device, low, high, log_scale) in ALL_PARAMS.items():
        if log_scale:
            params[name] = math.exp(rng.uniform(math.log(low), math.log(high)))
        else:
            params[name] = rng.uniform(low, high)
    return params


def _validate_params(params: dict[str, float]) -> None:
    unknown = sorted(set(params) - set(ALL_PARAMS))
    if unknown:
        raise ValueError("unknown sweep params: " + ", ".join(unknown))
    for name, value in params.items():
        parsed = _finite_float(value)
        if parsed is None or parsed <= 0.0:
            raise ValueError(f"{name} must be positive finite")


def _baseline_workspace(repo_root: Path, value: Path | None) -> Path:
    if value is not None:
        return _resolve_under_repo(repo_root, value)
    state_path = repo_root / STRATEGY_STATE_REL_PATH
    candidate_id = DEFAULT_BEST_CANDIDATE_ID
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
        if isinstance(state, dict) and state.get("best_candidate_id"):
            candidate_id = str(state["best_candidate_id"])
    return repo_root / "automation_artifacts" / "workspaces" / candidate_id


def _resolve_under_repo(repo_root: Path, value: Path) -> Path:
    repo = repo_root.resolve()
    resolved = value.resolve() if value.is_absolute() else (repo / value).resolve()
    try:
        resolved.relative_to(repo)
    except ValueError as exc:
        raise ValueError(f"path_outside_repo: {value}") from exc
    return resolved


def _replace_assignment(line: str, key: str, value: str) -> str:
    prefix = f"{key}="
    parts = line.split()
    for index, part in enumerate(parts):
        if part.startswith(prefix):
            parts[index] = prefix + value
            return " ".join(parts)
    return line + f" {prefix}{value}"


def _q_device_lines(netlist: str) -> dict[str, str]:
    result = {}
    for line in netlist.splitlines():
        for name in ("Q1", "Q2", "Q3", "Q4"):
            if line.startswith(name + " "):
                result[name] = line
    return result


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


def _format_um(value: float) -> str:
    number = float(value)
    if number.is_integer():
        return f"{int(number)}u"
    return f"{number:.6g}u"


def _format_multiplier(value: float) -> str:
    number = float(value)
    rounded = int(round(number))
    return str(max(1, rounded))


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


def _load_optuna():
    try:
        import optuna  # type: ignore
    except ImportError:
        return None
    return optuna


def _pick_best(best: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    if best is None:
        return candidate
    best_obj = float(best["objective"]["objective"])
    candidate_obj = float(candidate["objective"]["objective"])
    if candidate_obj < best_obj:
        return candidate
    return best


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an Optuna sweep on the current best fixed 4BJT topology.")
    parser.add_argument("--config", type=Path, default=Path("runner_config.json"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--baseline-workspace", type=Path)
    parser.add_argument("--trials", type=int, default=50)
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--study-name", default="best-topology-fixed")
    parser.add_argument("--timestamp")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--no-verify", action="store_true", help="Generate trial artifacts and review them without running the verifier.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_sweep(args)


if __name__ == "__main__":
    raise SystemExit(main())
