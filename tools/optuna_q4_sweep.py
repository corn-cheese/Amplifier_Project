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
from langgraph_runner.workspace import resolve_candidate_base_files


DUT_REL_PATH = "amptest/dummy_neural_amp.scs"
DEVICES_REL_PATH = "amptest/devices.csv"
Q4_MODEL = "pnp_05v5_W3p40L3p40"
REQUIRED_PARAMS = ("RC2_l", "RB4T_l", "RB4B_l", "RQ4E_l")
TARGET_GAIN_DB = 40.0
TARGET_UPPER_3DB_HZ = 30000.0
MIN_GAIN_DB = 35.0
MIN_UPPER_3DB_HZ = 20000.0
MIN_VOUT_PEAK_TO_PEAK_V = 0.02
TARGET_VOUT_PEAK_TO_PEAK_V = 0.10


def build_q4_active_load_artifacts(
    baseline_netlist: str,
    baseline_devices: str,
    params: dict[str, float],
) -> tuple[str, str]:
    _require_params(params)
    baseline_q_lines = _q_signal_path_lines(baseline_netlist)
    if set(baseline_q_lines) != {"Q1", "Q2", "Q3"}:
        raise ValueError("baseline must contain Q1/Q2/Q3 signal path devices")

    netlist = _build_q4_netlist(baseline_netlist, params)
    for name, line in baseline_q_lines.items():
        if line not in netlist.splitlines():
            raise ValueError(f"{name} signal path line changed")
    devices = _build_q4_devices(baseline_devices, params)
    return netlist, devices


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


def evaluate_trial_objective(
    review_passed: bool,
    verification_status: str,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    if not review_passed:
        return _rejected("review_failed")
    if verification_status != "passed":
        return _rejected("sim_failed")

    perf = _finite_metric(metrics.get("performance_nrmse_combined"))
    if perf is None:
        return _rejected("non_finite_performance_nrmse_combined")

    gain = _metric(metrics, ("ac", "midband_gain_db"))
    upper = _metric(metrics, ("ac", "upper_3db_hz"))
    swing = _metric(metrics, ("tran", "vout_peak_to_peak_v"))
    tran_nrmse = _first_metric(
        metrics,
        (
            ("tran", "tran_nrmse_vs_target_filter"),
            ("tran", "tran_ac_nrmse_vs_target_filter"),
        ),
    )

    if gain is None or gain < MIN_GAIN_DB:
        return _rejected("gain_collapse")
    if upper is None or upper < MIN_UPPER_3DB_HZ:
        return _rejected("bandwidth_collapse")
    if swing is None or swing < MIN_VOUT_PEAK_TO_PEAK_V:
        return _rejected("output_swing_collapse")

    penalties: dict[str, float] = {}
    gain_penalty = ((gain - TARGET_GAIN_DB) / 5.0) ** 2 * 0.01
    if gain_penalty > 0.0:
        penalties["gain_target_penalty"] = gain_penalty

    cutoff_penalty = abs(math.log(max(upper, 1.0) / TARGET_UPPER_3DB_HZ)) * 0.01
    if cutoff_penalty > 0.0:
        penalties["cutoff_target_penalty"] = cutoff_penalty

    if tran_nrmse is not None:
        penalties["transient_nrmse_penalty"] = tran_nrmse * 0.25

    if swing < TARGET_VOUT_PEAK_TO_PEAK_V:
        penalties["output_swing_penalty"] = ((TARGET_VOUT_PEAK_TO_PEAK_V - swing) / TARGET_VOUT_PEAK_TO_PEAK_V) ** 2 * 0.05

    objective = perf + sum(penalties.values())
    if gain > 45.0:
        objective += (gain - 45.0) * 0.02
        penalties["high_gain_penalty"] = (gain - 45.0) * 0.02
    return {
        "objective": objective,
        "rejected": False,
        "reason": "passed",
        "penalties": penalties,
        "failure_modes": classify_metrics_failure(metrics),
    }


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
        "hypothesis": "Add a fixed-size PNP Q4 high-side active load on NDRV while preserving the baseline Q1/Q2/Q3 signal path.",
        "primary_objective": "performance",
        "changed_blocks": ["q4_active_load", "bias", "device_accounting"],
        "files_touched": [DUT_REL_PATH, DEVICES_REL_PATH],
        "expected_effect": {
            "performance_nrmse_combined": "decrease",
            "area_total_p": "increase",
            "power_score_basis_w": "unknown",
        },
        "risk": "Q4 bias can collapse NDRV or Q3 drive if RB4T/RB4B/RQ4E are badly biased.",
        "patch": patch_text,
    }
    notes = [
        "# Q4 Active Load Trial",
        "",
        f"candidate_id: {candidate_id}",
        f"params: {json.dumps(params, sort_keys=True)}",
        f"objective: {json.dumps(_json_safe(objective), sort_keys=True)}",
        "",
        "Q1/Q2/Q3 signal path lines are preserved from the pinned p1-b028-c03 baseline.",
        f"Q4 is fixed at {Q4_MODEL}; sweep variables are RC2_l, RB4T_l, RB4B_l, and RQ4E_l.",
        "",
    ]
    (output_dir / "proposal.json").write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
    (output_dir / "patch.diff").write_text(patch_text, encoding="utf-8")
    (output_dir / "notes.md").write_text("\n".join(notes), encoding="utf-8")


def run_sweep(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    config = load_runner_config(args.config).model_dump(mode="json")
    base_dut_path, base_devices_path = resolve_candidate_base_files(repo_root, config)
    baseline_netlist = base_dut_path.read_text(encoding="utf-8")
    baseline_devices = base_devices_path.read_text(encoding="utf-8")
    timestamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    sweep_root = repo_root / str(config["artifact_root"]) / "sweeps" / "q4-active-load" / timestamp
    sweep_root.mkdir(parents=True, exist_ok=True)
    (sweep_root / "baseline_source.json").write_text(
        json.dumps(
            {
                "candidate_base_workspace": config.get("candidate_base_workspace"),
                "netlist": str(base_dut_path),
                "devices": str(base_devices_path),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    best: dict[str, Any] | None = None
    if args.trials <= 0:
        raise ValueError("--trials must be positive")

    optuna = _load_optuna()
    if optuna is None:
        rng = random.Random(args.seed)
        for index in range(args.trials):
            params = _random_params(rng)
            result = _run_trial(index, params, args, repo_root, config, sweep_root, baseline_netlist, baseline_devices)
            best = _pick_best(best, result)
    else:
        sampler = optuna.samplers.TPESampler(seed=args.seed)
        study = optuna.create_study(direction="minimize", study_name=args.study_name, sampler=sampler)

        def objective(trial):
            params = _suggest_params(trial)
            result = _run_trial(trial.number, params, args, repo_root, config, sweep_root, baseline_netlist, baseline_devices)
            nonlocal best
            best = _pick_best(best, result)
            return result["objective"]["objective"] if math.isfinite(result["objective"]["objective"]) else 1.0e9

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
    candidate_id = f"q4-active-load-trial-{trial_no:04d}"
    trial_dir = sweep_root / f"trial_{trial_no:04d}"
    workspace_dir = trial_dir / "workspace"
    trial_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    netlist, devices = build_q4_active_load_artifacts(baseline_netlist, baseline_devices, params)
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


def _build_q4_netlist(baseline_netlist: str, params: dict[str, float]) -> str:
    lines: list[str] = []
    inserted = False
    for line in baseline_netlist.splitlines():
        if line.startswith(("Q4 ", "RB4T ", "RB4B ", "RQ4E ")):
            continue
        if line.startswith("RC2 "):
            lines.append(f"RC2 VDD NDRV GND res_high_po_5p73 l={_format_um(params['RC2_l'])} w=5.73u m=1")
            lines.extend(
                [
                    f"RB4T VDD NB4 GND res_high_po_5p73 l={_format_um(params['RB4T_l'])} w=5.73u m=1",
                    f"RB4B NB4 GND GND res_high_po_5p73 l={_format_um(params['RB4B_l'])} w=5.73u m=1",
                    f"RQ4E VDD E4 GND res_high_po_5p73 l={_format_um(params['RQ4E_l'])} w=5.73u m=1",
                    f"Q4 NDRV NB4 E4 VDD {Q4_MODEL}",
                ]
            )
            inserted = True
            continue
        lines.append(line)
    if not inserted:
        raise ValueError("baseline netlist missing RC2 line")
    return "\n".join(lines) + "\n"


def _build_q4_devices(baseline_devices: str, params: dict[str, float]) -> str:
    input_io = StringIO(baseline_devices)
    reader = csv.DictReader(input_io)
    if reader.fieldnames is None:
        raise ValueError("devices.csv missing header")
    fieldnames = list(reader.fieldnames)
    rows = [row for row in reader if row.get("name") not in {"Q4", "RB4T", "RB4B", "RQ4E"}]
    for row in rows:
        if row.get("name") == "RC2":
            row["seg_length"] = _format_um(params["RC2_l"])
    rows.append(_bjt_row(fieldnames, "Q4", "pnp", "3.40u", "3.40u", "11.5600"))
    rows.extend(
        [
            _resistor_row(fieldnames, "RB4T", params["RB4T_l"]),
            _resistor_row(fieldnames, "RB4B", params["RB4B_l"]),
            _resistor_row(fieldnames, "RQ4E", params["RQ4E_l"]),
        ]
    )
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _bjt_row(fieldnames: list[str], name: str, device_type: str, width: str, length: str, area: str) -> dict[str, str]:
    row = {field: "" for field in fieldnames}
    row.update(
        {
            "name": name,
            "type": device_type,
            "count": "1",
            "width": width,
            "length": length,
            "multiplier": "1",
            "ft_hz": "10meg",
            "area_p": area,
            "include_in_ppa": "true",
        }
    )
    return row


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


def _q_signal_path_lines(netlist: str) -> dict[str, str]:
    result = {}
    for line in netlist.splitlines():
        for name in ("Q1", "Q2", "Q3"):
            if line.startswith(name + " "):
                result[name] = line
    return result


def _require_params(params: dict[str, float]) -> None:
    missing = [name for name in REQUIRED_PARAMS if name not in params]
    if missing:
        raise ValueError("missing sweep params: " + ", ".join(missing))
    for name in REQUIRED_PARAMS:
        value = _finite_metric(params[name])
        if value is None or value <= 0.0:
            raise ValueError(f"{name} must be positive finite")


def _format_um(value: float) -> str:
    number = float(value)
    if number.is_integer():
        return f"{int(number)}u"
    return f"{number:.6g}u"


def _metric(metrics: dict[str, Any], path: tuple[str, ...]) -> float | None:
    value: Any = metrics
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return _finite_metric(value)


def _first_metric(metrics: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> float | None:
    for path in paths:
        value = _metric(metrics, path)
        if value is not None:
            return value
    return None


def _finite_metric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _rejected(reason: str) -> dict[str, Any]:
    return {"objective": math.inf, "rejected": True, "reason": reason, "penalties": {}, "failure_modes": []}


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


def _write_workspace_config(repo_root: Path, config: dict[str, Any], workspace_dir: Path) -> None:
    source = repo_root / str(config["amptest_config"])
    data = json.loads(source.read_text(encoding="utf-8"))
    data["dut_netlist"] = "dummy_neural_amp.scs"
    data.setdefault("input_files", {})
    data["input_files"]["devices_csv"] = "devices.csv"
    data["input_files"]["ac_csv"] = "run/ac.csv"
    data["input_files"]["tran_csv"] = "run/tran.csv"
    (workspace_dir / "config.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _load_optuna():
    try:
        import optuna  # type: ignore
    except ImportError:
        return None
    return optuna


def _suggest_params(trial: Any) -> dict[str, float]:
    return {
        "RC2_l": trial.suggest_float("RC2_l", 250.0, 700.0),
        "RB4T_l": trial.suggest_float("RB4T_l", 200.0, 5000.0, log=True),
        "RB4B_l": trial.suggest_float("RB4B_l", 100.0, 3000.0, log=True),
        "RQ4E_l": trial.suggest_float("RQ4E_l", 10.0, 300.0, log=True),
    }


def _random_params(rng: random.Random) -> dict[str, float]:
    return {
        "RC2_l": rng.uniform(250.0, 700.0),
        "RB4T_l": math.exp(rng.uniform(math.log(200.0), math.log(5000.0))),
        "RB4B_l": math.exp(rng.uniform(math.log(100.0), math.log(3000.0))),
        "RQ4E_l": math.exp(rng.uniform(math.log(10.0), math.log(300.0))),
    }


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
    parser = argparse.ArgumentParser(description="Run a Q4 active-load Optuna sweep from the pinned p1-b028-c03 baseline.")
    parser.add_argument("--config", type=Path, default=Path("runner_config.json"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--trials", type=int, default=300)
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--study-name", default="q4-active-load")
    parser.add_argument("--timestamp")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--no-verify", action="store_true", help="Generate trial artifacts and review them without running the verifier.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_sweep(args)


if __name__ == "__main__":
    raise SystemExit(main())
