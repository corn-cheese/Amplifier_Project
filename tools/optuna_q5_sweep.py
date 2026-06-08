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
from tools.optuna_q4_sweep import evaluate_trial_objective


DUT_REL_PATH = "amptest/dummy_neural_amp.scs"
DEVICES_REL_PATH = "amptest/devices.csv"
STRATEGY_STATE_REL_PATH = "automation_artifacts/strategy_rotation.json"
BEST_TOPOLOGY_SWEEPS_REL_PATH = "automation_artifacts/sweeps/best-topology-fixed"
NPN_Q5_MODEL = "npn_05v5_W1p00L1p00"
PNP_Q5_MODEL = "pnp_05v5_W3p40L3p40"
CAP_MODEL = "cap_vpp_11p5x11p7_m1m4_noshield"
RES_MODEL = "res_high_po_5p73"


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
    q5_device_type: str
    q5_width: str
    q5_length: str
    q5_area: str
    q5_model: str
    params: dict[str, ParamSpec]


FAMILY_SPECS: dict[str, FamilySpec] = {
    "lf-servo": FamilySpec(
        slug="lf-servo",
        candidate_prefix="q5-lf-servo",
        hypothesis="Add a weak NPN Q5 low-frequency servo into BQ4 while preserving the 4BJT signal path.",
        changed_blocks=("q5_low_frequency_servo", "q4_bias_feedback", "pole_zero_shaping", "device_accounting"),
        risk="Q5 servo feedback can move BQ4/NDRV bias enough to collapse gain or transient swing if too strong.",
        q5_device_type="npn",
        q5_width="1.00u",
        q5_length="1.00u",
        q5_area="1.0000",
        q5_model=NPN_Q5_MODEL,
        params={
            "RQ5FB_l": ParamSpec("RQ5FB", 5000.0, 80000.0, True, "resistor", True),
            "RQ5REF_l": ParamSpec("RQ5REF", 5000.0, 80000.0, True, "resistor", True),
            "REQ5_l": ParamSpec("REQ5", 100.0, 5000.0, True, "resistor", True),
            "CQ5_m": ParamSpec("CQ5", 1.0, 500.0, True, "capacitor", True),
            "CE1_m": ParamSpec("CE1", 20000000.0, 70000000.0, False, "capacitor"),
            "CE2_m": ParamSpec("CE2", 20000000.0, 70000000.0, False, "capacitor"),
            "CP1_m": ParamSpec("CP1", 100.0, 3000.0, True, "capacitor"),
            "CP2_m": ParamSpec("CP2", 100.0, 3000.0, True, "capacitor"),
            "CP3_m": ParamSpec("CP3", 100.0, 3000.0, True, "capacitor"),
        },
    ),
    "output-active-sink": FamilySpec(
        slug="output-active-sink",
        candidate_prefix="q5-output-sink",
        hypothesis="Add a weak NPN Q5 active sink at VOUT while retaining the existing RBUF output load.",
        changed_blocks=("q5_output_active_sink", "output_bias", "pole_zero_shaping", "device_accounting"),
        risk="Q5 output sink can pull VOUT too low, reduce swing, or add too much output loading.",
        q5_device_type="npn",
        q5_width="1.00u",
        q5_length="1.00u",
        q5_area="1.0000",
        q5_model=NPN_Q5_MODEL,
        params={
            "RQ5U_l": ParamSpec("RQ5U", 1000.0, 50000.0, True, "resistor", True),
            "RQ5B_l": ParamSpec("RQ5B", 1000.0, 50000.0, True, "resistor", True),
            "REQ5_l": ParamSpec("REQ5", 100.0, 5000.0, True, "resistor", True),
            "RBUF_l": ParamSpec("RBUF", 600.0, 3000.0, False, "resistor"),
            "CP3_m": ParamSpec("CP3", 100.0, 4000.0, True, "capacitor"),
            "RQ4FB_l": ParamSpec("RQ4FB", 5000.0, 50000.0, True, "resistor"),
        },
    ),
    "q4-reference": FamilySpec(
        slug="q4-reference",
        candidate_prefix="q5-q4-reference",
        hypothesis="Add a diode-connected PNP Q5 reference branch that weakly stabilizes the Q4 active-load bias.",
        changed_blocks=("q5_q4_reference", "active_load_bias", "pole_zero_shaping", "device_accounting"),
        risk="Q5 reference bias may not add enough transfer-function freedom, or it may overconstrain BQ4.",
        q5_device_type="pnp",
        q5_width="3.40u",
        q5_length="3.40u",
        q5_area="11.5600",
        q5_model=PNP_Q5_MODEL,
        params={
            "RQ5E_l": ParamSpec("RQ5E", 10.0, 300.0, True, "resistor", True),
            "RQ5R_l": ParamSpec("RQ5R", 500.0, 10000.0, True, "resistor", True),
            "RQ5C_l": ParamSpec("RQ5C", 1000.0, 80000.0, True, "resistor", True),
            "REQ4_l": ParamSpec("REQ4", 10.0, 150.0, True, "resistor"),
            "RQ4U_l": ParamSpec("RQ4U", 100.0, 1500.0, True, "resistor"),
            "RQ4R_l": ParamSpec("RQ4R", 250.0, 3000.0, True, "resistor"),
            "RQ4FB_l": ParamSpec("RQ4FB", 5000.0, 50000.0, True, "resistor"),
            "RC2_l": ParamSpec("RC2", 250.0, 700.0, False, "resistor"),
            "CP2_m": ParamSpec("CP2", 100.0, 4000.0, True, "capacitor"),
        },
    ),
    "q2-emitter-helper": FamilySpec(
        slug="q2-emitter-helper",
        candidate_prefix="q5-q2-emitter-helper",
        hypothesis="Add a weak NPN Q5 current helper at the Q2 emitter to tune second-stage degeneration.",
        changed_blocks=("q5_q2_emitter_helper", "q2_degeneration", "pole_zero_shaping", "device_accounting"),
        risk="Q5 action can reduce Q2 headroom, collapse gain, or leave the AC shape plateau unchanged.",
        q5_device_type="npn",
        q5_width="1.00u",
        q5_length="1.00u",
        q5_area="1.0000",
        q5_model=NPN_Q5_MODEL,
        params={
            "RQ5U_l": ParamSpec("RQ5U", 1000.0, 50000.0, True, "resistor", True),
            "RQ5B_l": ParamSpec("RQ5B", 1000.0, 50000.0, True, "resistor", True),
            "REQ5_l": ParamSpec("REQ5", 50.0, 5000.0, True, "resistor", True),
            "RE2U_l": ParamSpec("RE2U", 10.0, 80.0, True, "resistor"),
            "RE2B_l": ParamSpec("RE2B", 150.0, 900.0, False, "resistor"),
            "CE2_m": ParamSpec("CE2", 20000000.0, 90000000.0, False, "capacitor"),
            "RC2_l": ParamSpec("RC2", 250.0, 700.0, False, "resistor"),
            "CP2_m": ParamSpec("CP2", 100.0, 4000.0, True, "capacitor"),
        },
    ),
}


def build_q5_artifacts(
    baseline_netlist: str,
    baseline_devices: str,
    family: str,
    params: dict[str, float],
) -> tuple[str, str]:
    spec = _family_spec(family)
    _validate_params(spec, params)
    baseline_q_lines = _q_device_lines(baseline_netlist)
    if set(baseline_q_lines) != {"Q1", "Q2", "Q3", "Q4"}:
        raise ValueError("baseline netlist must contain exactly Q1/Q2/Q3/Q4 before adding Q5")

    netlist = _build_q5_netlist(baseline_netlist, spec, params)
    generated_q_lines = _q_device_lines(netlist)
    if set(generated_q_lines) != {"Q1", "Q2", "Q3", "Q4", "Q5"}:
        raise ValueError("generated netlist must contain exactly Q1/Q2/Q3/Q4/Q5")
    for name, line in baseline_q_lines.items():
        if generated_q_lines.get(name) != line:
            raise ValueError(f"{name} topology line changed")
    devices = _build_q5_devices(baseline_devices, spec, params)
    return netlist, devices


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
        "# Q5 5BJT Trial",
        "",
        f"candidate_id: {candidate_id}",
        f"family: {family}",
        f"params: {json.dumps(params, sort_keys=True)}",
        f"objective: {json.dumps(_json_safe(objective), sort_keys=True)}",
        "",
        "Q1/Q2/Q3/Q4 topology lines are preserved from the selected 4BJT baseline.",
        "Exactly one new BJT named Q5 is added with family-specific support passives.",
        "",
    ]
    (output_dir / "proposal.json").write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
    (output_dir / "patch.diff").write_text(patch_text, encoding="utf-8")
    (output_dir / "notes.md").write_text("\n".join(notes), encoding="utf-8")


def resolve_baseline_workspace(repo_root: Path, value: Path | None) -> tuple[Path, dict[str, Any]]:
    repo = repo_root.resolve()
    if value is not None:
        workspace = _resolve_under_repo(repo, value)
        return workspace, {"source": "explicit", "baseline_workspace": str(workspace)}

    completed = _latest_completed_best_topology_workspace(repo)
    running = _running_best_topology_workspace(repo)
    if completed is not None and running is not None:
        if _baseline_source_order(running[1], "sweep_dir") > _baseline_source_order(completed[1], "summary_path"):
            return running
        return completed
    if completed is not None:
        return completed
    if running is not None:
        return running

    strategy = _strategy_rotation_workspace(repo)
    if strategy is not None:
        return strategy

    raise FileNotFoundError("no 4BJT baseline workspace found")


def run_sweep(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    config = load_runner_config(args.config).model_dump(mode="json")
    baseline_workspace, baseline_source = resolve_baseline_workspace(repo_root, args.baseline_workspace)
    baseline_netlist = (baseline_workspace / "dummy_neural_amp.scs").read_text(encoding="utf-8")
    baseline_devices = (baseline_workspace / "devices.csv").read_text(encoding="utf-8")
    spec = _family_spec(args.family)
    timestamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    sweep_root = repo_root / str(config["artifact_root"]) / "sweeps" / f"q5-{spec.slug}" / timestamp
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

    netlist, devices = build_q5_artifacts(baseline_netlist, baseline_devices, spec.slug, params)
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

    objective = evaluate_trial_objective(bool(review.get("passed")), verification_status, metrics)
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


def _build_q5_netlist(baseline_netlist: str, spec: FamilySpec, params: dict[str, float]) -> str:
    lines = _retune_netlist_lines(baseline_netlist, spec, params)
    block = _q5_netlist_block(spec.slug, params)
    anchor = _insertion_anchor(spec.slug)
    output: list[str] = []
    inserted = False
    for line in lines:
        output.append(line)
        if line.startswith(anchor + " "):
            output.extend(block)
            inserted = True
    if not inserted:
        raise ValueError(f"baseline netlist missing insertion anchor: {anchor}")
    return "\n".join(output) + "\n"


def _retune_netlist_lines(baseline_netlist: str, spec: FamilySpec, params: dict[str, float]) -> list[str]:
    existing_resistors = _existing_param_devices(spec, params, "resistor")
    existing_caps = _existing_param_devices(spec, params, "capacitor")
    seen: set[str] = set()
    lines: list[str] = []
    for line in baseline_netlist.splitlines():
        name = line.split(maxsplit=1)[0] if line.strip() else ""
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


def _q5_netlist_block(family: str, params: dict[str, float]) -> list[str]:
    if family == "lf-servo":
        return [
            f"RQ5FB VOUT BQ5 GND {RES_MODEL} l={_format_um(params['RQ5FB_l'])} w=5.73u m=1",
            f"RQ5REF BQ5 VREF GND {RES_MODEL} l={_format_um(params['RQ5REF_l'])} w=5.73u m=1",
            f"REQ5 EQ5 GND GND {RES_MODEL} l={_format_um(params['REQ5_l'])} w=5.73u m=1",
            f"CQ5 BQ5 GND GND {CAP_MODEL} m={_format_multiplier(params['CQ5_m'])}",
            f"Q5 BQ4 BQ5 EQ5 GND {NPN_Q5_MODEL}",
        ]
    if family == "output-active-sink":
        return [
            f"RQ5U VREF BQ5 GND {RES_MODEL} l={_format_um(params['RQ5U_l'])} w=5.73u m=1",
            f"RQ5B BQ5 GND GND {RES_MODEL} l={_format_um(params['RQ5B_l'])} w=5.73u m=1",
            f"REQ5 EQ5 GND GND {RES_MODEL} l={_format_um(params['REQ5_l'])} w=5.73u m=1",
            f"Q5 VOUT BQ5 EQ5 GND {NPN_Q5_MODEL}",
        ]
    if family == "q4-reference":
        return [
            f"RQ5E VDD EQ5 GND {RES_MODEL} l={_format_um(params['RQ5E_l'])} w=5.73u m=1",
            f"RQ5R BQ5 VREF GND {RES_MODEL} l={_format_um(params['RQ5R_l'])} w=5.73u m=1",
            f"RQ5C BQ5 BQ4 GND {RES_MODEL} l={_format_um(params['RQ5C_l'])} w=5.73u m=1",
            f"Q5 BQ5 BQ5 EQ5 VDD {PNP_Q5_MODEL}",
        ]
    if family == "q2-emitter-helper":
        return [
            f"RQ5U VREF BQ5 GND {RES_MODEL} l={_format_um(params['RQ5U_l'])} w=5.73u m=1",
            f"RQ5B BQ5 GND GND {RES_MODEL} l={_format_um(params['RQ5B_l'])} w=5.73u m=1",
            f"REQ5 EQ5 GND GND {RES_MODEL} l={_format_um(params['REQ5_l'])} w=5.73u m=1",
            f"Q5 E2 BQ5 EQ5 GND {NPN_Q5_MODEL}",
        ]
    raise ValueError(f"unknown Q5 family: {family}")


def _insertion_anchor(family: str) -> str:
    if family in {"lf-servo", "q4-reference"}:
        return "RQ4FB"
    if family == "output-active-sink":
        return "RBUF"
    if family == "q2-emitter-helper":
        return "CE2"
    raise ValueError(f"unknown Q5 family: {family}")


def _build_q5_devices(baseline_devices: str, spec: FamilySpec, params: dict[str, float]) -> str:
    input_io = StringIO(baseline_devices)
    reader = csv.DictReader(input_io)
    if reader.fieldnames is None:
        raise ValueError("devices.csv missing header")
    fieldnames = list(reader.fieldnames)
    support_names = {param.device for param in spec.params.values() if param.support} | {"Q5"}
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

    rows.append(_bjt_row(fieldnames, "Q5", spec.q5_device_type, spec.q5_width, spec.q5_length, spec.q5_area))
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


def _existing_param_devices(spec: FamilySpec, params: dict[str, float], kind: str) -> dict[str, float]:
    return {
        param.device: params[name]
        for name, param in spec.params.items()
        if name in params and param.kind == kind and not param.support
    }


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


def _latest_completed_best_topology_workspace(repo_root: Path) -> tuple[Path, dict[str, Any]] | None:
    sweeps_root = repo_root / BEST_TOPOLOGY_SWEEPS_REL_PATH
    if not sweeps_root.exists():
        return None
    summaries = sorted(sweeps_root.glob("*/best_trial_summary.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for summary_path in summaries:
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        workspace = _workspace_from_trial_summary(repo_root, summary)
        if workspace is not None:
            return workspace, {
                "source": "completed_best_topology_summary",
                "summary_path": str(summary_path),
                "candidate_id": summary.get("candidate_id"),
            }
        best_candidate = summary_path.parent / "best_candidate"
        if _is_workspace(best_candidate):
            return best_candidate.resolve(), {
                "source": "completed_best_topology_best_candidate",
                "summary_path": str(summary_path),
                "candidate_id": summary.get("candidate_id"),
            }
    return None


def _running_best_topology_workspace(repo_root: Path) -> tuple[Path, dict[str, Any]] | None:
    sweeps_root = repo_root / BEST_TOPOLOGY_SWEEPS_REL_PATH
    if not sweeps_root.exists():
        return None
    sweep_dirs = [path for path in sweeps_root.iterdir() if path.is_dir() and not (path / "best_trial_summary.json").exists()]
    sweep_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for sweep_dir in sweep_dirs:
        best: tuple[float, Path, dict[str, Any]] | None = None
        for summary_path in sweep_dir.glob("trial_*/trial_summary.json"):
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not _is_accepted_running_trial(summary):
                continue
            workspace = _workspace_from_trial_summary(repo_root, summary)
            if workspace is None:
                continue
            objective = _finite_float(summary.get("objective", {}).get("objective"))
            if objective is None:
                continue
            if best is None or objective < best[0]:
                best = (objective, workspace, summary)
        if best is not None:
            objective, workspace, summary = best
            return workspace, {
                "source": "running_best_topology_best_so_far",
                "sweep_dir": str(sweep_dir),
                "candidate_id": summary.get("candidate_id"),
                "objective": objective,
            }
    return None


def _strategy_rotation_workspace(repo_root: Path) -> tuple[Path, dict[str, Any]] | None:
    state_path = repo_root / STRATEGY_STATE_REL_PATH
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    candidate_id = state.get("best_candidate_id") if isinstance(state, dict) else None
    if not candidate_id:
        return None
    workspace = repo_root / "automation_artifacts" / "workspaces" / str(candidate_id)
    if not _is_workspace(workspace):
        return None
    return workspace.resolve(), {"source": "strategy_rotation", "candidate_id": str(candidate_id)}


def _baseline_source_order(source: dict[str, Any], key: str) -> tuple[str, float]:
    raw = source.get(key)
    if not raw:
        return "", -1.0
    path = Path(str(raw))
    if key == "summary_path":
        path = path.parent
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    return path.name, mtime


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


def _is_accepted_running_trial(summary: dict[str, Any]) -> bool:
    if not isinstance(summary.get("review"), dict) or not summary["review"].get("passed"):
        return False
    if summary.get("verification_status") != "passed":
        return False
    objective = summary.get("objective")
    if not isinstance(objective, dict) or objective.get("rejected"):
        return False
    return _finite_float(objective.get("objective")) is not None


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
        raise ValueError(f"unknown Q5 family: {family}") from exc


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


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Q5-oriented 5BJT Optuna sweep from the best 4BJT baseline.")
    parser.add_argument("--config", type=Path, default=Path("runner_config.json"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--family", choices=sorted(FAMILY_SPECS), required=True)
    parser.add_argument("--baseline-workspace", type=Path)
    parser.add_argument("--trials", type=int, default=50)
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--study-name", default="q5-sweep")
    parser.add_argument("--timestamp")
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--no-verify", action="store_true", help="Generate trial artifacts and review them without running the verifier.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_sweep(args)


if __name__ == "__main__":
    raise SystemExit(main())
