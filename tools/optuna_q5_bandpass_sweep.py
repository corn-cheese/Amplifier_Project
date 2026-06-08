from __future__ import annotations

import argparse
import csv
import difflib
import json
import math
import random
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

from langgraph_runner.config import load_runner_config
from langgraph_runner.review import DeterministicReviewer
from langgraph_runner.verifier import Verifier


DUT_REL_PATH = "amptest/dummy_neural_amp.scs"
DEVICES_REL_PATH = "amptest/devices.csv"
Q5_OUTPUT_ACTIVE_SWEEPS_REL_PATH = "automation_artifacts/sweeps/q5-output-active-sink"
NPN_Q5_MODEL = "npn_05v5_W1p00L1p00"
CAP_MODEL = "cap_vpp_11p5x11p7_m1m4_noshield"
RES_MODEL = "res_high_po_5p73"
REQUIRED_Q_DEVICES = {"Q1", "Q2", "Q3", "Q4", "Q5"}
MIN_GAIN_DB = 35.0
MIN_UPPER_3DB_HZ = 20000.0
MIN_VOUT_PEAK_TO_PEAK_V = 0.02


@dataclass(frozen=True)
class ParamSpec:
    device: str
    low: float
    high: float
    log_scale: bool
    kind: str
    support: bool = False


@dataclass(frozen=True)
class FamilySpec:
    slug: str
    candidate_prefix: str
    hypothesis: str
    changed_blocks: tuple[str, ...]
    risk: str
    params: dict[str, ParamSpec]
    allow_q1_input_highpass: bool = False
    q1_input_node: str | None = None
    allow_q5_lf_servo: bool = False


FAMILY_SPECS: dict[str, FamilySpec] = {
    "output-active-sink-expanded": FamilySpec(
        slug="output-active-sink-expanded",
        candidate_prefix="q5-bp-output-sink-expanded",
        hypothesis="Retune the best Q5 output active sink over wider RBUF and distributed capacitor ranges.",
        changed_blocks=("q5_output_active_sink", "expanded_output_load", "distributed_pole_zero_shaping", "device_accounting"),
        risk="Large RBUF or CP3 values can collapse bandwidth or disturb output swing even when raw AC shape improves.",
        params={
            "RBUF_l": ParamSpec("RBUF", 2200.0, 7000.0, False, "resistor"),
            "CP1_m": ParamSpec("CP1", 300.0, 950.0, True, "capacitor"),
            "CP2_m": ParamSpec("CP2", 700.0, 2300.0, True, "capacitor"),
            "CP3_m": ParamSpec("CP3", 4500.0, 13000.0, True, "capacitor"),
            "CE1_m": ParamSpec("CE1", 15000000.0, 32000000.0, False, "capacitor"),
            "CE2_m": ParamSpec("CE2", 20000000.0, 42000000.0, False, "capacitor"),
        },
    ),
    "input-highpass": FamilySpec(
        slug="input-highpass",
        candidate_prefix="q5-bp-input-highpass",
        hypothesis="Add an input coupling high-pass into Q1 base while preserving the Q5 output sink.",
        changed_blocks=("input_coupling_highpass", "q1_base_bias", "emitter_bypass_tuning", "device_accounting"),
        risk="The coupling network can bias Q1 incorrectly or attenuate the 1 kHz transient stimulus if CIN/RBIN are poorly matched.",
        allow_q1_input_highpass=True,
        params={
            "CIN_m": ParamSpec("CIN", 250000.0, 2000000.0, True, "capacitor", True),
            "RBIN_l": ParamSpec("RBIN", 3500.0, 6000.0, True, "resistor", True),
            "CE1_m": ParamSpec("CE1", 45000000.0, 80000000.0, False, "capacitor"),
            "CE2_m": ParamSpec("CE2", 30000000.0, 65000000.0, False, "capacitor"),
        },
    ),
    "feedback-lowpass-n1": FamilySpec(
        slug="feedback-lowpass-n1",
        candidate_prefix="q5-bp-feedback-lowpass-n1",
        hypothesis="Add a Miller-style capacitor between NDRV and N1 while sweeping distributed low-pass capacitors and Q4 feedback.",
        changed_blocks=("miller_lowpass_ndrv_n1", "q4_feedback_strength", "distributed_pole_zero_shaping", "device_accounting"),
        risk="Too much NDRV-to-N1 compensation can overfeed the first-stage collector node and reject bandwidth.",
        params={
            "CM12_m": ParamSpec("CM12", 1.0, 15.0, True, "capacitor", True),
            "CP1_m": ParamSpec("CP1", 450.0, 2200.0, True, "capacitor"),
            "CP2_m": ParamSpec("CP2", 350.0, 1900.0, True, "capacitor"),
            "CP3_m": ParamSpec("CP3", 6000.0, 18000.0, True, "capacitor"),
            "RQ4FB_l": ParamSpec("RQ4FB", 6500.0, 9500.0, True, "resistor"),
        },
    ),
    "feedback-lowpass-output": FamilySpec(
        slug="feedback-lowpass-output",
        candidate_prefix="q5-bp-feedback-lowpass-output",
        hypothesis="Add an output-to-driver compensation capacitor while sweeping distributed low-pass capacitors and Q4 feedback.",
        changed_blocks=("miller_lowpass_vout_ndrv", "q4_feedback_strength", "distributed_pole_zero_shaping", "device_accounting"),
        risk="VOUT-to-NDRV compensation can create excessive local feedback and reduce output swing.",
        params={
            "CMOUT_m": ParamSpec("CMOUT", 5.0, 600.0, True, "capacitor", True),
            "CP1_m": ParamSpec("CP1", 550.0, 1900.0, True, "capacitor"),
            "CP2_m": ParamSpec("CP2", 600.0, 1800.0, True, "capacitor"),
            "CP3_m": ParamSpec("CP3", 3500.0, 8500.0, True, "capacitor"),
            "RQ4FB_l": ParamSpec("RQ4FB", 6500.0, 9000.0, True, "resistor"),
        },
    ),
    "input-highpass-output-sink": FamilySpec(
        slug="input-highpass-output-sink",
        candidate_prefix="q5-bp-input-highpass-output-sink",
        hypothesis="Combine input coupling high-pass with the best Q5 output sink and wider distributed pole tuning.",
        changed_blocks=("input_coupling_highpass", "q5_output_active_sink", "expanded_output_load", "distributed_pole_zero_shaping", "device_accounting"),
        risk="Joint low-side and high-side shaping has the most freedom but can bias Q1 or VOUT out of the useful operating region.",
        allow_q1_input_highpass=True,
        params={
            "CIN_m": ParamSpec("CIN", 150000.0, 600000.0, True, "capacitor", True),
            "RBIN_l": ParamSpec("RBIN", 600.0, 1800.0, True, "resistor", True),
            "RBUF_l": ParamSpec("RBUF", 4500.0, 10000.0, False, "resistor"),
            "CP1_m": ParamSpec("CP1", 2000.0, 4000.0, True, "capacitor"),
            "CP2_m": ParamSpec("CP2", 1500.0, 3800.0, True, "capacitor"),
            "CP3_m": ParamSpec("CP3", 3000.0, 7000.0, True, "capacitor"),
            "CE1_m": ParamSpec("CE1", 52000000.0, 70000000.0, False, "capacitor"),
            "CE2_m": ParamSpec("CE2", 24000000.0, 60000000.0, False, "capacitor"),
            "RQ4FB_l": ParamSpec("RQ4FB", 7500.0, 13500.0, True, "resistor"),
        },
    ),
    "dual-input-highpass-output-sink": FamilySpec(
        slug="dual-input-highpass-output-sink",
        candidate_prefix="q5-bp-dual-input-hp-output-sink",
        hypothesis="Add two cascaded input coupling high-pass sections while preserving the Q5 output sink.",
        changed_blocks=(
            "dual_input_coupling_highpass",
            "q5_output_active_sink",
            "low_cutoff_order_shaping",
            "distributed_pole_zero_shaping",
            "device_accounting",
        ),
        risk="Two input high-pass sections add low-frequency order but can over-attenuate the passband or bias Q1 away from its useful region.",
        q1_input_node="B2",
        params={
            "CIN1_m": ParamSpec("CIN1", 1200000.0, 4200000.0, True, "capacitor", True),
            "RBIN1_l": ParamSpec("RBIN1", 4200.0, 16000.0, True, "resistor", True),
            "CIN2_m": ParamSpec("CIN2", 180000.0, 900000.0, True, "capacitor", True),
            "RBIN2_l": ParamSpec("RBIN2", 900.0, 4200.0, True, "resistor", True),
            "RBUF_l": ParamSpec("RBUF", 4500.0, 8500.0, False, "resistor"),
            "CP1_m": ParamSpec("CP1", 900.0, 2400.0, True, "capacitor"),
            "CP2_m": ParamSpec("CP2", 1600.0, 3200.0, True, "capacitor"),
            "CP3_m": ParamSpec("CP3", 6500.0, 12000.0, True, "capacitor"),
            "CE1_m": ParamSpec("CE1", 52000000.0, 72000000.0, False, "capacitor"),
            "CE2_m": ParamSpec("CE2", 42000000.0, 64000000.0, False, "capacitor"),
            "RQ4FB_l": ParamSpec("RQ4FB", 11000.0, 18000.0, True, "resistor"),
        },
    ),
    "input-highpass-damped-miller": FamilySpec(
        slug="input-highpass-damped-miller",
        candidate_prefix="q5-bp-input-hp-damped-miller",
        hypothesis="Keep the input high-pass and Q5 output sink, then add series-damped Miller compensation for high-side rolloff control.",
        changed_blocks=(
            "input_coupling_highpass",
            "series_damped_miller_ndrv_n1",
            "series_damped_miller_vout_ndrv",
            "q4_feedback_strength",
            "device_accounting",
        ),
        risk="Damped compensation can improve high-frequency attenuation, but excessive feedback can reduce output swing or push the upper cutoff below target.",
        allow_q1_input_highpass=True,
        params={
            "CIN_m": ParamSpec("CIN", 450000.0, 2200000.0, True, "capacitor", True),
            "RBIN_l": ParamSpec("RBIN", 1800.0, 10000.0, True, "resistor", True),
            "RZ12_l": ParamSpec("RZ12", 400.0, 6000.0, True, "resistor", True),
            "CM12_m": ParamSpec("CM12", 5.0, 350.0, True, "capacitor", True),
            "RZOUT_l": ParamSpec("RZOUT", 400.0, 9000.0, True, "resistor", True),
            "CMOUT_m": ParamSpec("CMOUT", 5.0, 450.0, True, "capacitor", True),
            "CP1_m": ParamSpec("CP1", 1700.0, 3200.0, True, "capacitor"),
            "CP2_m": ParamSpec("CP2", 1500.0, 3200.0, True, "capacitor"),
            "CP3_m": ParamSpec("CP3", 4000.0, 8000.0, True, "capacitor"),
            "RQ4FB_l": ParamSpec("RQ4FB", 8000.0, 12500.0, True, "resistor"),
        },
    ),
    "lf-servo-bq4": FamilySpec(
        slug="lf-servo-bq4",
        candidate_prefix="q5-bp-lf-servo-bq4",
        hypothesis="Re-purpose Q5 from output sink into a weak low-frequency servo that pulls the Q4 bias node from VOUT-derived feedback.",
        changed_blocks=(
            "q5_low_frequency_servo",
            "q4_bias_feedback",
            "output_passive_load",
            "emitter_bypass_tuning",
            "device_accounting",
        ),
        risk="The Q5 servo has more authority over Q4 bias than the output sink, so strong feedback can collapse NDRV or shift VOUT DC.",
        allow_q5_lf_servo=True,
        params={
            "RQ5U_l": ParamSpec("RQ5U", 3500.0, 12000.0, True, "resistor"),
            "RQ5B_l": ParamSpec("RQ5B", 700.0, 3000.0, True, "resistor"),
            "RQ5FB_l": ParamSpec("RQ5FB", 40000.0, 180000.0, True, "resistor", True),
            "REQ5_l": ParamSpec("REQ5", 2200.0, 9000.0, True, "resistor"),
            "CQ5_m": ParamSpec("CQ5", 20.0, 900.0, True, "capacitor", True),
            "RBUF_l": ParamSpec("RBUF", 4500.0, 10000.0, False, "resistor"),
            "CP3_m": ParamSpec("CP3", 3500.0, 8500.0, True, "capacitor"),
            "CE1_m": ParamSpec("CE1", 45000000.0, 70000000.0, False, "capacitor"),
            "CE2_m": ParamSpec("CE2", 38000000.0, 65000000.0, False, "capacitor"),
            "RQ4FB_l": ParamSpec("RQ4FB", 7500.0, 13500.0, True, "resistor"),
        },
    ),
}


def build_q5_bandpass_artifacts(
    baseline_netlist: str,
    baseline_devices: str,
    family: str,
    params: dict[str, float],
) -> tuple[str, str]:
    spec = _family_spec(family)
    _validate_params(spec, params)
    baseline_q_lines = _q_device_lines(baseline_netlist)
    if set(baseline_q_lines) != REQUIRED_Q_DEVICES:
        raise ValueError("baseline netlist must contain exactly Q1/Q2/Q3/Q4/Q5")
    if not _is_q5_output_sink_line(baseline_q_lines["Q5"]):
        raise ValueError("baseline Q5 must be the output active sink topology")

    netlist = _build_bandpass_netlist(baseline_netlist, spec, params)
    generated_q_lines = _q_device_lines(netlist)
    if set(generated_q_lines) != REQUIRED_Q_DEVICES:
        raise ValueError("generated netlist must contain exactly Q1/Q2/Q3/Q4/Q5")
    _validate_q_topology_policy(spec, baseline_q_lines, generated_q_lines)
    devices = _build_bandpass_devices(baseline_devices, spec, params)
    return netlist, devices


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

    return {
        "objective": perf,
        "rejected": False,
        "reason": "passed",
        "penalties": {},
        "failure_modes": classify_metrics_failure(metrics),
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


def write_candidate_artifacts(
    output_dir: Path,
    *,
    candidate_id: str,
    family: str,
    baseline_netlist: str,
    baseline_devices: str,
    trial_netlist: str,
    trial_devices: str,
    params: dict[str, float],
    objective: dict[str, Any],
) -> None:
    spec = _family_spec(family)
    output_dir.mkdir(parents=True, exist_ok=True)
    patch_text = _unified_patch(baseline_netlist, trial_netlist, DUT_REL_PATH)
    patch_text += _unified_patch(baseline_devices, trial_devices, DEVICES_REL_PATH)
    proposal = {
        "candidate_id": candidate_id,
        "phase": "phase1_performance",
        "agent": "optimizer",
        "hypothesis": spec.hypothesis,
        "primary_objective": "performance",
        "changed_blocks": list(spec.changed_blocks),
        "files_touched": [DUT_REL_PATH, DEVICES_REL_PATH],
        "expected_effect": {
            "performance_nrmse_combined": "decrease",
            "area_total_p": "increase",
            "power_score_basis_w": "unknown",
        },
        "risk": spec.risk,
        "patch": patch_text,
    }
    notes = [
        "# Q5 Bandpass Targeted Trial",
        "",
        f"candidate_id: {candidate_id}",
        f"family: {family}",
        f"params: {json.dumps(params, sort_keys=True)}",
        f"objective: {json.dumps(_json_safe(objective), sort_keys=True)}",
        "",
        _topology_note(spec),
        "Raw performance_nrmse_combined is the optimization objective; collapse checks remain hard rejects.",
        "",
    ]
    (output_dir / "proposal.json").write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
    (output_dir / "patch.diff").write_text(patch_text, encoding="utf-8")
    (output_dir / "notes.md").write_text("\n".join(notes), encoding="utf-8")


def resolve_baseline_workspace(
    repo_root: Path,
    baseline_workspace: Path | None,
    baseline_summary: Path | None,
) -> tuple[Path, dict[str, Any]]:
    repo = repo_root.resolve()
    if baseline_workspace is not None:
        workspace = _resolve_under_repo(repo, baseline_workspace)
        return workspace, {"source": "explicit", "baseline_workspace": str(workspace)}
    if baseline_summary is not None:
        return _workspace_from_summary_path(repo, _resolve_under_repo(repo, baseline_summary), "explicit_summary")

    latest = _latest_output_active_sink_workspace(repo)
    if latest is not None:
        return latest
    raise FileNotFoundError("no q5 output active sink best_trial_summary.json baseline found")


def run_sweep(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    config = load_runner_config(args.config).model_dump(mode="json")
    baseline_workspace, baseline_source = resolve_baseline_workspace(repo_root, args.baseline_workspace, args.baseline_summary)
    baseline_netlist = (baseline_workspace / "dummy_neural_amp.scs").read_text(encoding="utf-8")
    baseline_devices = (baseline_workspace / "devices.csv").read_text(encoding="utf-8")
    spec = _family_spec(args.family)
    timestamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    sweep_root = repo_root / str(config["artifact_root"]) / "sweeps" / f"q5-bandpass-{spec.slug}" / timestamp
    sweep_root.mkdir(parents=True, exist_ok=True)
    (sweep_root / "baseline_source.json").write_text(
        json.dumps({**baseline_source, "baseline_workspace": str(baseline_workspace)}, indent=2) + "\n",
        encoding="utf-8",
    )

    if args.trials <= 0:
        raise ValueError("--trials must be positive")

    best: dict[str, Any] | None = None
    optuna = _load_optuna()
    if optuna is None:
        rng = random.Random(args.seed)
        for index in range(args.trials):
            result = _run_trial(index, _random_params(spec, rng), args, repo_root, config, sweep_root, baseline_netlist, baseline_devices)
            best = _pick_best(best, result)
    else:
        sampler = optuna.samplers.TPESampler(seed=args.seed)
        study = optuna.create_study(direction="minimize", study_name=args.study_name, sampler=sampler)

        def objective(trial: Any) -> float:
            params = _suggest_params(spec, trial)
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
    spec = _family_spec(args.family)
    trial_no = index + 1
    candidate_id = f"{spec.candidate_prefix}-trial-{trial_no:04d}"
    trial_dir = sweep_root / f"trial_{trial_no:04d}"
    workspace_dir = trial_dir / "workspace"
    trial_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    netlist, devices = build_q5_bandpass_artifacts(baseline_netlist, baseline_devices, spec.slug, params)
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
        family=spec.slug,
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

    objective = evaluate_raw_trial_objective(bool(review.get("passed")), verification_status, metrics)
    (trial_dir / "objective.json").write_text(json.dumps(_json_safe(objective), indent=2) + "\n", encoding="utf-8")
    write_candidate_artifacts(
        trial_dir,
        candidate_id=candidate_id,
        family=spec.slug,
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


def _build_bandpass_netlist(baseline_netlist: str, spec: FamilySpec, params: dict[str, float]) -> str:
    lines = _retune_netlist_lines(baseline_netlist, spec, params)
    q1_input_node = _q1_rewire_node(spec)
    if q1_input_node is not None:
        lines = [_rewrite_q1_input_line(line, q1_input_node) if line.startswith("Q1 ") else line for line in lines]
    if spec.allow_q5_lf_servo:
        lines = [_rewrite_q5_lf_servo_line(line) if line.startswith("Q5 ") else line for line in lines]
    lines = _insert_support_blocks(lines, spec.slug, params)
    return "\n".join(lines) + "\n"


def _retune_netlist_lines(baseline_netlist: str, spec: FamilySpec, params: dict[str, float]) -> list[str]:
    existing_resistors = _existing_param_devices(spec, params, "resistor")
    existing_caps = _existing_param_devices(spec, params, "capacitor")
    support_names = _support_device_names(spec)
    seen: set[str] = set()
    lines: list[str] = []
    for line in baseline_netlist.splitlines():
        name = line.split(maxsplit=1)[0] if line.strip() else ""
        if name in support_names:
            continue
        if name in existing_resistors:
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
    return lines


def _insert_support_blocks(lines: list[str], family: str, params: dict[str, float]) -> list[str]:
    insertions = _support_insertions(family, params)
    if not insertions:
        return lines

    inserted = [False for _ in insertions]
    output: list[str] = []
    for line in lines:
        output.append(line)
        for index, (anchor, block) in enumerate(insertions):
            if not inserted[index] and line.startswith(anchor + " "):
                output.extend(block)
                inserted[index] = True
    if not all(inserted):
        missing = [anchor for was_inserted, (anchor, _block) in zip(inserted, insertions) if not was_inserted]
        raise ValueError("baseline netlist missing support insertion anchor: " + ", ".join(missing))
    return output


def _support_insertions(family: str, params: dict[str, float]) -> list[tuple[str, list[str]]]:
    insertions: list[tuple[str, list[str]]] = []
    if family in {"input-highpass", "input-highpass-output-sink"}:
        insertions.append(
            (
                "RVREF",
                [
                    f"CIN VIN B1 GND {CAP_MODEL} m={_format_multiplier(params['CIN_m'])}",
                    f"RBIN B1 VREF GND {RES_MODEL} l={_format_um(params['RBIN_l'])} w=5.73u m=1",
                ],
            )
        )
    if family == "dual-input-highpass-output-sink":
        insertions.append(
            (
                "RVREF",
                [
                    f"CIN1 VIN B1 GND {CAP_MODEL} m={_format_multiplier(params['CIN1_m'])}",
                    f"RBIN1 B1 VREF GND {RES_MODEL} l={_format_um(params['RBIN1_l'])} w=5.73u m=1",
                    f"CIN2 B1 B2 GND {CAP_MODEL} m={_format_multiplier(params['CIN2_m'])}",
                    f"RBIN2 B2 VREF GND {RES_MODEL} l={_format_um(params['RBIN2_l'])} w=5.73u m=1",
                ],
            )
        )
    if family == "input-highpass-damped-miller":
        insertions.append(
            (
                "RVREF",
                [
                    f"CIN VIN B1 GND {CAP_MODEL} m={_format_multiplier(params['CIN_m'])}",
                    f"RBIN B1 VREF GND {RES_MODEL} l={_format_um(params['RBIN_l'])} w=5.73u m=1",
                ],
            )
        )
        insertions.append(
            (
                "CP2",
                [
                    f"RZ12 NDRV NZ12 GND {RES_MODEL} l={_format_um(params['RZ12_l'])} w=5.73u m=1",
                    f"CM12 NZ12 N1 GND {CAP_MODEL} m={_format_multiplier(params['CM12_m'])}",
                ],
            )
        )
        insertions.append(
            (
                "CP3",
                [
                    f"RZOUT VOUT NZOUT GND {RES_MODEL} l={_format_um(params['RZOUT_l'])} w=5.73u m=1",
                    f"CMOUT NZOUT NDRV GND {CAP_MODEL} m={_format_multiplier(params['CMOUT_m'])}",
                ],
            )
        )
    if family == "lf-servo-bq4":
        insertions.append(
            (
                "RQ5B",
                [
                    f"RQ5FB VOUT BQ5 GND {RES_MODEL} l={_format_um(params['RQ5FB_l'])} w=5.73u m=1",
                    f"CQ5 BQ5 GND GND {CAP_MODEL} m={_format_multiplier(params['CQ5_m'])}",
                ],
            )
        )
    if family == "feedback-lowpass-n1":
        insertions.append(("CP2", [f"CM12 NDRV N1 GND {CAP_MODEL} m={_format_multiplier(params['CM12_m'])}"]))
    if family == "feedback-lowpass-output":
        insertions.append(("CP3", [f"CMOUT VOUT NDRV GND {CAP_MODEL} m={_format_multiplier(params['CMOUT_m'])}"]))
    return insertions


def _topology_note(spec: FamilySpec) -> str:
    if spec.slug == "dual-input-highpass-output-sink":
        return "Q1 input is rewired through B1/B2 using two cascaded CIN/RBIN high-pass sections."
    if _q1_rewire_node(spec) is not None:
        return "Q1 input is rewired through B1/CIN/RBIN for this high-pass family."
    if spec.allow_q5_lf_servo:
        return "Q5 is rewired from the output sink into a weak BQ4 low-frequency servo."
    return "Q1/Q2/Q3/Q4/Q5 topology lines are preserved."


def _q1_rewire_node(spec: FamilySpec) -> str | None:
    if spec.q1_input_node is not None:
        return spec.q1_input_node
    if spec.allow_q1_input_highpass:
        return "B1"
    return None


def _rewrite_q1_input_line(line: str, input_node: str) -> str:
    parts = line.split()
    if len(parts) < 6 or parts[0] != "Q1" or parts[2] != "VIN":
        raise ValueError("input high-pass family requires baseline Q1 base connected to VIN")
    parts[2] = input_node
    return " ".join(parts)


def _rewrite_q5_lf_servo_line(line: str) -> str:
    if not _is_q5_output_sink_line(line):
        raise ValueError("lf-servo-bq4 family requires baseline Q5 output sink topology")
    parts = line.split()
    parts[1:5] = ["BQ4", "BQ5", "EQ5", "GND"]
    return " ".join(parts)


def _build_bandpass_devices(baseline_devices: str, spec: FamilySpec, params: dict[str, float]) -> str:
    input_io = StringIO(baseline_devices)
    reader = csv.DictReader(input_io)
    if reader.fieldnames is None:
        raise ValueError("devices.csv missing header")
    fieldnames = list(reader.fieldnames)
    support_names = _support_device_names(spec)
    resistor_values = _existing_param_devices(spec, params, "resistor")
    cap_values = _existing_param_devices(spec, params, "capacitor")
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for row in reader:
        name = str(row.get("name") or "")
        if name in support_names:
            continue
        if name in resistor_values:
            row["seg_length"] = _format_um(resistor_values[name])
            seen.add(name)
        if name in cap_values:
            row["multiplier"] = _format_multiplier(cap_values[name])
            seen.add(name)
        rows.append(row)
    missing = sorted((set(resistor_values) | set(cap_values)) - seen)
    if missing:
        raise ValueError("devices.csv missing swept devices: " + ", ".join(missing))

    for name, param in spec.params.items():
        if not param.support:
            continue
        if param.kind == "resistor":
            rows.append(_resistor_row(fieldnames, param.device, params[name]))
        elif param.kind == "capacitor":
            rows.append(_capacitor_row(fieldnames, param.device, params[name]))
        else:
            raise ValueError(f"unsupported support device kind: {param.kind}")

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _validate_q_topology_policy(spec: FamilySpec, baseline: dict[str, str], generated: dict[str, str]) -> None:
    for name, line in baseline.items():
        new_line = generated.get(name)
        if new_line == line:
            continue
        q1_input_node = _q1_rewire_node(spec)
        if name == "Q1" and q1_input_node is not None and _is_q1_input_highpass_rewire(line, new_line or "", q1_input_node):
            continue
        if name == "Q5" and spec.allow_q5_lf_servo and _is_q5_lf_servo_line(new_line or ""):
            continue
        raise ValueError(f"{name} topology line changed")


def _is_q1_input_highpass_rewire(before: str, after: str, input_node: str) -> bool:
    old = before.split()
    new = after.split()
    return (
        len(old) == len(new)
        and len(old) >= 6
        and old[0] == new[0] == "Q1"
        and old[2] == "VIN"
        and new[2] == input_node
        and old[:2] == new[:2]
        and old[3:] == new[3:]
    )


def _is_q5_output_sink_line(line: str) -> bool:
    parts = line.split()
    return len(parts) >= 6 and parts[:5] == ["Q5", "VOUT", "BQ5", "EQ5", "GND"] and parts[5] == NPN_Q5_MODEL


def _is_q5_lf_servo_line(line: str) -> bool:
    parts = line.split()
    return len(parts) >= 6 and parts[:5] == ["Q5", "BQ4", "BQ5", "EQ5", "GND"] and parts[5] == NPN_Q5_MODEL


def _existing_param_devices(spec: FamilySpec, params: dict[str, float], kind: str) -> dict[str, float]:
    return {
        param.device: params[name]
        for name, param in spec.params.items()
        if name in params and param.kind == kind and not param.support
    }


def _support_device_names(spec: FamilySpec) -> set[str]:
    return {param.device for param in spec.params.values() if param.support}


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


def _latest_output_active_sink_workspace(repo_root: Path) -> tuple[Path, dict[str, Any]] | None:
    sweeps_root = repo_root / Q5_OUTPUT_ACTIVE_SWEEPS_REL_PATH
    if not sweeps_root.exists():
        return None
    summaries = sorted(
        sweeps_root.glob("*/best_trial_summary.json"),
        key=lambda path: (path.stat().st_mtime, path.parent.name),
        reverse=True,
    )
    for summary_path in summaries:
        result = _workspace_from_summary_path(repo_root, summary_path, "latest_q5_output_active_sink_summary")
        if result is not None:
            return result
    return None


def _workspace_from_summary_path(repo_root: Path, summary_path: Path, source_name: str) -> tuple[Path, dict[str, Any]] | None:
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    workspace = _workspace_from_trial_summary(repo_root, summary)
    if workspace is None:
        best_candidate = summary_path.parent / "best_candidate"
        if _is_workspace(best_candidate):
            workspace = best_candidate.resolve()
    if workspace is None:
        return None
    return workspace, {
        "source": source_name,
        "summary_path": str(summary_path),
        "candidate_id": summary.get("candidate_id"),
    }


def _workspace_from_trial_summary(repo_root: Path, summary: dict[str, Any]) -> Path | None:
    raw = summary.get("trial_dir")
    if not raw:
        return None
    trial_dir = _resolve_under_repo(repo_root, Path(str(raw)))
    candidates = (trial_dir / "workspace", trial_dir)
    for candidate in candidates:
        if _is_workspace(candidate):
            return candidate.resolve()
    return None


def _is_workspace(path: Path) -> bool:
    return (path / "dummy_neural_amp.scs").exists() and (path / "devices.csv").exists()


def _resolve_under_repo(repo_root: Path, value: Path) -> Path:
    repo = repo_root.resolve()
    resolved = value.resolve() if value.is_absolute() else (repo / value).resolve()
    try:
        resolved.relative_to(repo)
    except ValueError as exc:
        raise ValueError(f"path_outside_repo: {value}") from exc
    return resolved


def _q_device_lines(netlist: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in netlist.splitlines():
        parts = line.split(maxsplit=1)
        if not parts:
            continue
        name = parts[0]
        if len(name) > 1 and name.startswith("Q") and name[1:].isdigit():
            result[name] = line
    return result


def _validate_params(spec: FamilySpec, params: dict[str, float]) -> None:
    expected = set(spec.params)
    missing = sorted(expected - set(params))
    if missing:
        raise ValueError("missing sweep params: " + ", ".join(missing))
    unknown = sorted(set(params) - expected)
    if unknown:
        raise ValueError("unknown sweep params: " + ", ".join(unknown))
    for name, value in params.items():
        parsed = _finite_float(value)
        if parsed is None or parsed <= 0.0:
            raise ValueError(f"{name} must be positive finite")


def _family_spec(family: str) -> FamilySpec:
    try:
        return FAMILY_SPECS[family]
    except KeyError as exc:
        raise ValueError(f"unknown Q5 bandpass family: {family}") from exc


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


def _suggest_params(spec: FamilySpec, trial: Any) -> dict[str, float]:
    return {
        name: trial.suggest_float(name, param.low, param.high, log=param.log_scale)
        for name, param in spec.params.items()
    }


def _random_params(spec: FamilySpec, rng: random.Random) -> dict[str, float]:
    params = {}
    for name, param in spec.params.items():
        if param.log_scale:
            params[name] = math.exp(rng.uniform(math.log(param.low), math.log(param.high)))
        else:
            params[name] = rng.uniform(param.low, param.high)
    return params


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


def _rejected(reason: str) -> dict[str, Any]:
    return {"objective": math.inf, "rejected": True, "reason": reason, "penalties": {}, "failure_modes": []}


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


def _format_um(value: float) -> str:
    number = float(value)
    if number.is_integer():
        return f"{int(number)}u"
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Q5 bandpass-targeted Optuna sweep from the best Q5 output active sink baseline.")
    parser.add_argument("--config", type=Path, default=Path("runner_config.json"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--family", choices=sorted(FAMILY_SPECS), required=True)
    parser.add_argument("--baseline-workspace", type=Path)
    parser.add_argument("--baseline-summary", type=Path)
    parser.add_argument("--trials", type=int, default=300)
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--study-name", default="q5-bandpass")
    parser.add_argument("--timestamp")
    parser.add_argument("--seed", type=int, default=23)
    parser.add_argument("--no-verify", action="store_true", help="Generate trial artifacts and review them without running the verifier.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_sweep(args)


if __name__ == "__main__":
    raise SystemExit(main())
