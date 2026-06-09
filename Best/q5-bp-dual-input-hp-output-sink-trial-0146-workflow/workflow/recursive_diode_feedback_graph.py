from __future__ import annotations

import argparse
import csv
import io
import json
import math
import random
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

WORKFLOW_DIR = Path(__file__).resolve().parent
REPO_ROOT_FOR_CLONED_WORKFLOW = WORKFLOW_DIR.parents[2]
if str(REPO_ROOT_FOR_CLONED_WORKFLOW) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT_FOR_CLONED_WORKFLOW))

from langgraph_runner.agent_io import AgentCall, AgentRunner
from langgraph_runner.config import load_runner_config

from optuna_q5_bandpass_sweep import (
    CAP_MODEL,
    DIODE_MODEL,
    MAX_AREA_OBJECTIVE_PERFORMANCE_NRMSE,
    MIN_GAIN_DB,
    MIN_UPPER_3DB_HZ,
    MIN_VOUT_PEAK_TO_PEAK_V,
    _finite_float,
    _format_multiplier,
    _json_safe,
    _review_trial,
    _unified_patch,
    _verify_trial,
    _write_workspace_config,
    evaluate_raw_trial_objective,
)


ALLOWED_TARGETS = ("RBIN1", "RBIN2")
ALLOWED_ORIENTATIONS = ("node_to_vref", "vref_to_node")
TARGET_NODE = {"RBIN1": "B1", "RBIN2": "B2"}
TARGET_CAP = {"RBIN1": "CIN1", "RBIN2": "CIN2"}
CAP_PARAM = {"RBIN1": "CIN1_m", "RBIN2": "CIN2_m"}
PROPOSAL_BACKENDS = ("codex", "heuristic")
MAX_CONSECUTIVE_REJECTS = 3
LOW_CUTOFF_MAX_DEGRADATION = 1.2
PERFORMANCE_NRMSE_TOLERANCE = 0.01
MIN_CAP_REDUCTION_RATIO = 0.8
DUT_REL_PATH = "amptest_v2p3/COREONLY/dummy_neural_amp.scs"
DEVICES_REL_PATH = "amptest_v2p3/COREONLY/devices.csv"


@dataclass(frozen=True)
class SingleDiodeProposal:
    target_resistor: str
    orientation: str
    rationale: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SingleDiodeProposal":
        target = str(data.get("target_resistor") or "")
        orientation = str(data.get("orientation") or "")
        diode_count = data.get("diode_count", 1)
        rationale = str(data.get("rationale") or "").strip()
        if target not in ALLOWED_TARGETS:
            raise ValueError("target_resistor must be RBIN1 or RBIN2")
        if orientation not in ALLOWED_ORIENTATIONS:
            raise ValueError("orientation must be node_to_vref or vref_to_node")
        try:
            parsed_count = int(diode_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("diode_count must be 1") from exc
        if parsed_count != 1:
            raise ValueError("proposal must add exactly one diode")
        if not rationale:
            raise ValueError("rationale is required")
        return cls(target, orientation, rationale)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_resistor": self.target_resistor,
            "orientation": self.orientation,
            "diode_count": 1,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class BaselineSnapshot:
    workspace_dir: str
    netlist: str
    devices: str
    metrics: dict[str, Any]

    def to_state(self) -> dict[str, Any]:
        return {
            "workspace_dir": self.workspace_dir,
            "netlist": self.netlist,
            "devices": self.devices,
            "metrics": self.metrics,
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "BaselineSnapshot":
        return cls(
            workspace_dir=str(state.get("workspace_dir") or ""),
            netlist=str(state.get("netlist") or ""),
            devices=str(state.get("devices") or ""),
            metrics=dict(state.get("metrics") or {}),
        )


@dataclass(frozen=True)
class AcceptanceDecision:
    accepted: bool
    reason: str
    cap_ratio: float | None = None
    area_delta: float | None = None
    performance_delta: float | None = None
    lower_cutoff_ratio: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "cap_ratio": self.cap_ratio,
            "area_delta": self.area_delta,
            "performance_delta": self.performance_delta,
            "lower_cutoff_ratio": self.lower_cutoff_ratio,
        }


class RecursiveGraphState(TypedDict, total=False):
    repo_root: str
    config_path: str
    runner_config: dict[str, Any]
    timestamp: str
    sweep_root: str
    baseline_workspace: str
    baseline: dict[str, Any]
    available_targets: list[str]
    round_index: int
    max_rounds: int
    sweep_trials: int
    seed: int
    no_verify: bool
    proposal_backend: str
    consecutive_rejects: int
    accepted_chain: list[dict[str, Any]]
    rejected_chain: list[dict[str, Any]]
    proposal: dict[str, Any]
    proposal_valid: bool
    proposal_error: str
    round_dir: str
    trial_results: list[dict[str, Any]]
    best_trial: dict[str, Any]
    round_decision: dict[str, Any]
    round_summary: dict[str, Any]
    route: str
    events: list[str]
    errors: list[str]


def apply_single_diode_candidate(
    baseline_netlist: str,
    baseline_devices: str,
    *,
    proposal: SingleDiodeProposal,
    diode_name: str,
    cap_multiplier: float,
    diode_multiplier: float,
) -> tuple[str, str]:
    target = proposal.target_resistor
    cap_name = TARGET_CAP[target]
    node = TARGET_NODE[target]
    cap_seen = False
    target_removed = False
    output_lines: list[str] = []
    diode_line = _single_diode_line(diode_name, node, proposal.orientation, diode_multiplier)

    for line in baseline_netlist.splitlines():
        name = _line_name(line)
        if name == target:
            output_lines.append(diode_line)
            target_removed = True
            continue
        if name == cap_name:
            output_lines.append(_replace_assignment(line, "m", _format_multiplier(cap_multiplier)))
            cap_seen = True
            continue
        output_lines.append(line)

    if not target_removed:
        raise ValueError(f"baseline netlist missing target resistor {target}")
    if not cap_seen:
        raise ValueError(f"baseline netlist missing target capacitor {cap_name}")

    devices = _retune_devices_for_single_diode(
        baseline_devices,
        removed_resistor=target,
        cap_name=cap_name,
        cap_multiplier=cap_multiplier,
        diode_name=diode_name,
    )
    return "\n".join(output_lines).rstrip() + "\n", devices


def evaluate_candidate_for_acceptance(
    baseline: BaselineSnapshot,
    trial: dict[str, Any],
) -> AcceptanceDecision:
    review = trial.get("review") or {}
    if not bool(review.get("passed")):
        return AcceptanceDecision(False, "review_failed")
    if str(trial.get("verification_status") or "") != "passed":
        return AcceptanceDecision(False, "verification_not_passed")
    objective = trial.get("objective") or {}
    if bool(objective.get("rejected")):
        return AcceptanceDecision(False, str(objective.get("reason") or "objective_rejected"))

    proposal = SingleDiodeProposal.from_dict(dict(trial.get("proposal") or {}))
    cap_name = TARGET_CAP[proposal.target_resistor]
    cap_param = CAP_PARAM[proposal.target_resistor]
    baseline_cap = _netlist_cap_multiplier(baseline.netlist, cap_name)
    candidate_cap = _finite_float((trial.get("params") or {}).get(cap_param))
    if baseline_cap is None or candidate_cap is None or baseline_cap <= 0.0:
        return AcceptanceDecision(False, "missing_cap_multiplier")
    cap_ratio = candidate_cap / baseline_cap
    if cap_ratio > MIN_CAP_REDUCTION_RATIO:
        return AcceptanceDecision(False, "insufficient_cap_reduction", cap_ratio=cap_ratio)

    baseline_perf = _metric(baseline.metrics, ("performance_nrmse_combined",))
    candidate_perf = _metric(trial.get("metrics") or {}, ("performance_nrmse_combined",))
    if baseline_perf is None or candidate_perf is None:
        return AcceptanceDecision(False, "missing_performance", cap_ratio=cap_ratio)
    performance_delta = candidate_perf - baseline_perf
    if performance_delta > PERFORMANCE_NRMSE_TOLERANCE:
        return AcceptanceDecision(False, "performance_regression", cap_ratio=cap_ratio, performance_delta=performance_delta)

    baseline_lower = _metric(baseline.metrics, ("ac", "lower_3db_hz"))
    candidate_lower = _metric(trial.get("metrics") or {}, ("ac", "lower_3db_hz"))
    if baseline_lower is None or candidate_lower is None or baseline_lower <= 0.0:
        return AcceptanceDecision(False, "missing_lower_cutoff", cap_ratio=cap_ratio, performance_delta=performance_delta)
    lower_ratio = candidate_lower / baseline_lower
    if lower_ratio > LOW_CUTOFF_MAX_DEGRADATION:
        return AcceptanceDecision(
            False,
            "low_cutoff_regression",
            cap_ratio=cap_ratio,
            performance_delta=performance_delta,
            lower_cutoff_ratio=lower_ratio,
        )

    baseline_area = _metric(baseline.metrics, ("area_power", "area_total_p"))
    candidate_area = _metric(trial.get("metrics") or {}, ("area_power", "area_total_p"))
    if baseline_area is None or candidate_area is None:
        return AcceptanceDecision(False, "missing_area", cap_ratio=cap_ratio, performance_delta=performance_delta, lower_cutoff_ratio=lower_ratio)
    area_delta = candidate_area - baseline_area
    if area_delta >= 0.0:
        return AcceptanceDecision(
            False,
            "area_not_reduced",
            cap_ratio=cap_ratio,
            performance_delta=performance_delta,
            lower_cutoff_ratio=lower_ratio,
            area_delta=area_delta,
        )

    return AcceptanceDecision(
        True,
        "accepted",
        cap_ratio=cap_ratio,
        area_delta=area_delta,
        performance_delta=performance_delta,
        lower_cutoff_ratio=lower_ratio,
    )


def select_best_acceptable_trial(
    baseline: BaselineSnapshot,
    trials: list[dict[str, Any]],
) -> dict[str, Any] | None:
    accepted: list[tuple[tuple[float, float, float], dict[str, Any], AcceptanceDecision]] = []
    for trial in trials:
        decision = evaluate_candidate_for_acceptance(baseline, trial)
        if not decision.accepted:
            continue
        metrics = trial.get("metrics") or {}
        area = _metric(metrics, ("area_power", "area_total_p"))
        perf = _metric(metrics, ("performance_nrmse_combined",))
        accepted.append(
            (
                (
                    decision.cap_ratio if decision.cap_ratio is not None else math.inf,
                    area if area is not None else math.inf,
                    perf if perf is not None else math.inf,
                ),
                trial,
                decision,
            )
        )
    if not accepted:
        return None
    accepted.sort(key=lambda item: item[0])
    selected = dict(accepted[0][1])
    selected["acceptance"] = accepted[0][2].to_dict()
    return selected


def load_baseline_node(state: RecursiveGraphState) -> RecursiveGraphState:
    next_state = _record_event(state, "load_baseline")
    repo_root = Path(next_state.get("repo_root") or ".").resolve()
    config_path = _resolve_under_repo(repo_root, Path(str(next_state.get("config_path") or WORKFLOW_DIR / "runner_config.json")))
    config = load_runner_config(config_path).model_dump(mode="json")
    baseline_workspace = _resolve_under_repo(repo_root, Path(str(next_state["baseline_workspace"])))
    netlist = (baseline_workspace / "dummy_neural_amp.scs").read_text(encoding="utf-8")
    devices = (baseline_workspace / "devices.csv").read_text(encoding="utf-8")
    metrics = _load_baseline_metrics(baseline_workspace)
    timestamp = str(next_state.get("timestamp") or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    sweep_root = repo_root / str(config["artifact_root"]) / "sweeps" / "q5-recursive-single-diode-feedback" / timestamp
    sweep_root.mkdir(parents=True, exist_ok=True)
    metrics_source = _baseline_metrics_source(baseline_workspace)
    (sweep_root / "baseline_source.json").write_text(
        json.dumps(
            {
                "baseline_workspace": str(baseline_workspace),
                "metrics_source": str(metrics_source) if metrics_source is not None else None,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    baseline = BaselineSnapshot(str(baseline_workspace), netlist, devices, metrics).to_state()
    return {
        **next_state,
        "repo_root": str(repo_root),
        "config_path": str(config_path),
        "runner_config": config,
        "timestamp": timestamp,
        "sweep_root": str(sweep_root),
        "baseline": baseline,
        "available_targets": _available_targets(netlist),
        "round_index": int(next_state.get("round_index") or 0),
        "max_rounds": int(next_state.get("max_rounds") or 10),
        "sweep_trials": int(next_state.get("sweep_trials") or 20),
        "seed": int(next_state.get("seed") or 23),
        "proposal_backend": str(next_state.get("proposal_backend") or "codex"),
        "consecutive_rejects": int(next_state.get("consecutive_rejects") or 0),
        "accepted_chain": list(next_state.get("accepted_chain", [])),
        "rejected_chain": list(next_state.get("rejected_chain", [])),
    }


def propose_single_diode_node(state: RecursiveGraphState) -> RecursiveGraphState:
    next_state = _record_event(state, "propose_single_diode")
    round_index = int(next_state.get("round_index") or 0) + 1
    sweep_root = Path(str(next_state["sweep_root"]))
    round_dir = sweep_root / f"round_{round_index:04d}"
    round_dir.mkdir(parents=True, exist_ok=True)
    context = _proposal_context(next_state, round_index)
    (round_dir / "proposal_context.md").write_text(context, encoding="utf-8")
    backend = str(next_state.get("proposal_backend") or "codex")
    try:
        if backend == "heuristic":
            proposal = _heuristic_proposal(next_state, round_index)
        elif backend == "codex":
            proposal = _codex_proposal(round_dir, context)
        else:
            raise ValueError(f"unknown proposal backend: {backend}")
        (round_dir / "proposal.json").write_text(json.dumps(proposal.to_dict(), indent=2) + "\n", encoding="utf-8")
        return {**next_state, "round_index": round_index, "round_dir": str(round_dir), "proposal": proposal.to_dict(), "proposal_valid": True}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        error = str(exc)
        (round_dir / "proposal_error.json").write_text(json.dumps({"error": error}, indent=2) + "\n", encoding="utf-8")
        return {
            **next_state,
            "round_index": round_index,
            "round_dir": str(round_dir),
            "proposal": {},
            "proposal_valid": False,
            "proposal_error": error,
        }


def validate_proposal_node(state: RecursiveGraphState) -> RecursiveGraphState:
    next_state = _record_event(state, "validate_proposal")
    if not next_state.get("proposal_valid", False):
        return next_state
    try:
        proposal = SingleDiodeProposal.from_dict(dict(next_state.get("proposal") or {}))
        baseline = BaselineSnapshot.from_state(dict(next_state["baseline"]))
        if proposal.target_resistor not in _available_targets(baseline.netlist):
            raise ValueError(f"target no longer available: {proposal.target_resistor}")
    except ValueError as exc:
        return {**next_state, "proposal_valid": False, "proposal_error": str(exc)}
    return next_state


def run_20_sweep_node(state: RecursiveGraphState) -> RecursiveGraphState:
    next_state = _record_event(state, "run_20_sweep")
    if not next_state.get("proposal_valid", False):
        return {**next_state, "trial_results": []}
    repo_root = Path(str(next_state["repo_root"]))
    config = dict(next_state["runner_config"])
    baseline = BaselineSnapshot.from_state(dict(next_state["baseline"]))
    proposal = SingleDiodeProposal.from_dict(dict(next_state["proposal"]))
    round_dir = Path(str(next_state["round_dir"]))
    sweep_trials = int(next_state.get("sweep_trials") or 20)
    rng = random.Random(int(next_state.get("seed") or 23) + int(next_state.get("round_index") or 0))
    results = []
    for index in range(sweep_trials):
        params = _single_diode_trial_params(baseline, proposal, rng)
        result = _run_single_diode_trial(
            index=index,
            params=params,
            proposal=proposal,
            baseline=baseline,
            repo_root=repo_root,
            config=config,
            round_dir=round_dir,
            timestamp=str(next_state["timestamp"]),
            round_index=int(next_state.get("round_index") or 0),
            no_verify=bool(next_state.get("no_verify", False)),
        )
        results.append(result)
    return {**next_state, "trial_results": results}


def evaluate_keep_drop_node(state: RecursiveGraphState) -> RecursiveGraphState:
    next_state = _record_event(state, "evaluate_keep_drop")
    baseline = BaselineSnapshot.from_state(dict(next_state["baseline"]))
    trials = list(next_state.get("trial_results", []))
    best = select_best_acceptable_trial(baseline, trials)
    if best is None:
        decision = AcceptanceDecision(False, "no_acceptable_trial").to_dict()
    else:
        decision = dict(best.get("acceptance") or AcceptanceDecision(True, "accepted").to_dict())
    round_summary = {
        "round": next_state.get("round_index"),
        "proposal": next_state.get("proposal"),
        "proposal_valid": bool(next_state.get("proposal_valid", False)),
        "proposal_error": next_state.get("proposal_error"),
        "trial_count": len(trials),
        "accepted": bool(decision.get("accepted")),
        "decision": decision,
        "best_trial": _summary_for_trial(best) if best else None,
    }
    if next_state.get("round_dir"):
        (Path(str(next_state["round_dir"])) / "round_summary.json").write_text(
            json.dumps(_json_safe(round_summary), indent=2) + "\n",
            encoding="utf-8",
        )
    output: RecursiveGraphState = {**next_state, "round_decision": decision, "round_summary": round_summary}
    if best is not None:
        output["best_trial"] = best
    else:
        output.pop("best_trial", None)
    return output


def update_baseline_or_reject_node(state: RecursiveGraphState) -> RecursiveGraphState:
    next_state = _record_event(state, "update_baseline_or_reject")
    decision = dict(next_state.get("round_decision") or {})
    round_summary = dict(next_state.get("round_summary") or {})
    accepted_chain = list(next_state.get("accepted_chain", []))
    rejected_chain = list(next_state.get("rejected_chain", []))
    if bool(decision.get("accepted")):
        best_trial = dict(next_state.get("best_trial") or {})
        baseline = {
            "workspace_dir": str(best_trial.get("workspace_dir") or Path(str(best_trial.get("trial_dir") or "")) / "workspace"),
            "netlist": str(best_trial.get("netlist") or ""),
            "devices": str(best_trial.get("devices") or ""),
            "metrics": dict(best_trial.get("metrics") or {}),
        }
        if not baseline["netlist"] and baseline["workspace_dir"]:
            workspace = Path(baseline["workspace_dir"])
            if (workspace / "dummy_neural_amp.scs").exists():
                baseline["netlist"] = (workspace / "dummy_neural_amp.scs").read_text(encoding="utf-8")
            if (workspace / "devices.csv").exists():
                baseline["devices"] = (workspace / "devices.csv").read_text(encoding="utf-8")
        accepted_entry = {**round_summary, "promoted_workspace": baseline["workspace_dir"]}
        accepted_chain.append(accepted_entry)
        _append_chain(next_state, "accepted_chain.jsonl", accepted_entry)
        return {
            **next_state,
            "baseline": baseline,
            "available_targets": _available_targets(baseline.get("netlist", "")),
            "consecutive_rejects": 0,
            "accepted_chain": accepted_chain,
            "rejected_chain": rejected_chain,
        }

    rejected_chain.append(round_summary)
    _append_chain(next_state, "rejected_chain.jsonl", round_summary)
    return {
        **next_state,
        "consecutive_rejects": int(next_state.get("consecutive_rejects") or 0) + 1,
        "accepted_chain": accepted_chain,
        "rejected_chain": rejected_chain,
    }


def route_next_node(state: RecursiveGraphState) -> RecursiveGraphState:
    next_state = _record_event(state, "route_next")
    round_index = int(next_state.get("round_index") or 0)
    max_rounds = int(next_state.get("max_rounds") or 10)
    consecutive_rejects = int(next_state.get("consecutive_rejects") or 0)
    available_targets = list(next_state.get("available_targets", []))
    if round_index >= max_rounds:
        route = "finalize"
    elif consecutive_rejects >= MAX_CONSECUTIVE_REJECTS:
        route = "finalize"
    elif not available_targets:
        route = "finalize"
    else:
        route = "continue"
    return {**next_state, "route": route}


def finalize_node(state: RecursiveGraphState) -> RecursiveGraphState:
    next_state = _record_event(state, "finalize")
    sweep_root = Path(str(next_state["sweep_root"]))
    summary = {
        "timestamp": next_state.get("timestamp"),
        "rounds_completed": int(next_state.get("round_index") or 0),
        "accepted_count": len(next_state.get("accepted_chain", [])),
        "rejected_count": len(next_state.get("rejected_chain", [])),
        "consecutive_rejects": int(next_state.get("consecutive_rejects") or 0),
        "available_targets": list(next_state.get("available_targets", [])),
        "final_baseline_workspace": (next_state.get("baseline") or {}).get("workspace_dir"),
        "accepted_chain": list(next_state.get("accepted_chain", [])),
        "rejected_chain": list(next_state.get("rejected_chain", [])),
        "events": list(next_state.get("events", [])),
        "errors": list(next_state.get("errors", [])),
    }
    sweep_root.mkdir(parents=True, exist_ok=True)
    (sweep_root / "recursive_summary.json").write_text(json.dumps(_json_safe(summary), indent=2) + "\n", encoding="utf-8")
    _copy_best_candidate(next_state, sweep_root)
    return next_state


def build_graph():
    graph = StateGraph(RecursiveGraphState)
    graph.add_node("load_baseline", load_baseline_node)
    graph.add_node("propose_single_diode", propose_single_diode_node)
    graph.add_node("validate_proposal", validate_proposal_node)
    graph.add_node("run_20_sweep", run_20_sweep_node)
    graph.add_node("evaluate_keep_drop", evaluate_keep_drop_node)
    graph.add_node("update_baseline_or_reject", update_baseline_or_reject_node)
    graph.add_node("route_next", route_next_node)
    graph.add_node("finalize", finalize_node)
    graph.set_entry_point("load_baseline")
    graph.add_edge("load_baseline", "propose_single_diode")
    graph.add_edge("propose_single_diode", "validate_proposal")
    graph.add_edge("validate_proposal", "run_20_sweep")
    graph.add_edge("run_20_sweep", "evaluate_keep_drop")
    graph.add_edge("evaluate_keep_drop", "update_baseline_or_reject")
    graph.add_edge("update_baseline_or_reject", "route_next")
    graph.add_conditional_edges(
        "route_next",
        lambda state: state.get("route") or "finalize",
        {
            "continue": "propose_single_diode",
            "finalize": "finalize",
        },
    )
    graph.add_edge("finalize", END)
    return graph.compile()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="recursive-diode-feedback")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=WORKFLOW_DIR / "runner_config.json")
    parser.add_argument("--baseline-workspace", type=Path, default=WORKFLOW_DIR.parent / "best_run" / "trial_0146" / "workspace")
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--sweep-trials", type=int, default=20)
    parser.add_argument("--timestamp")
    parser.add_argument("--seed", type=int, default=23)
    parser.add_argument("--proposal-backend", choices=PROPOSAL_BACKENDS, default="codex")
    parser.add_argument("--no-verify", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.rounds <= 0:
        raise ValueError("--rounds must be positive")
    if args.sweep_trials <= 0:
        raise ValueError("--sweep-trials must be positive")
    repo_root = args.repo_root.resolve()
    graph = build_graph()
    final_state = graph.invoke(
        {
            "repo_root": str(repo_root),
            "config_path": str(args.config),
            "baseline_workspace": str(args.baseline_workspace),
            "timestamp": args.timestamp,
            "max_rounds": args.rounds,
            "sweep_trials": args.sweep_trials,
            "seed": args.seed,
            "proposal_backend": args.proposal_backend,
            "no_verify": args.no_verify,
        },
        config={"recursion_limit": max(25, args.rounds * 10 + 10)},
    )
    if final_state.get("errors"):
        return 1
    return 0


def _run_single_diode_trial(
    *,
    index: int,
    params: dict[str, float],
    proposal: SingleDiodeProposal,
    baseline: BaselineSnapshot,
    repo_root: Path,
    config: dict[str, Any],
    round_dir: Path,
    timestamp: str,
    round_index: int,
    no_verify: bool,
) -> dict[str, Any]:
    trial_no = index + 1
    diode_name = f"DLG{round_index:02d}{trial_no:04d}"
    candidate_id = _candidate_id(timestamp, round_index, trial_no)
    trial_dir = round_dir / f"trial_{trial_no:04d}"
    workspace_dir = trial_dir / "workspace"
    trial_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    cap_param = CAP_PARAM[proposal.target_resistor]
    netlist, devices = apply_single_diode_candidate(
        baseline.netlist,
        baseline.devices,
        proposal=proposal,
        diode_name=diode_name,
        cap_multiplier=params[cap_param],
        diode_multiplier=params["DNEW_m"],
    )
    for target_dir in (trial_dir, workspace_dir):
        (target_dir / "dummy_neural_amp.scs").write_text(netlist, encoding="utf-8")
        (target_dir / "devices.csv").write_text(devices, encoding="utf-8")
    (trial_dir / "params.json").write_text(json.dumps(params, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_workspace_config(repo_root, config, workspace_dir)
    pending = {"objective": math.inf, "rejected": True, "reason": "not_verified", "penalties": {}}
    _write_candidate_artifacts(
        trial_dir,
        candidate_id=candidate_id,
        baseline_netlist=baseline.netlist,
        baseline_devices=baseline.devices,
        trial_netlist=netlist,
        trial_devices=devices,
        proposal=proposal,
        params=params,
        objective=pending,
        dut_rel_path=str(config.get("dut_netlist", DUT_REL_PATH)),
        devices_rel_path=str(config.get("devices_csv", DEVICES_REL_PATH)),
    )
    review = _review_trial(repo_root, config, trial_dir, workspace_dir, candidate_id)
    verification_status = "skipped" if no_verify else "not_run"
    metrics: dict[str, Any] = {}
    if review.get("passed") and not no_verify:
        verification_status, metrics = _verify_trial(config, repo_root, workspace_dir, trial_dir, candidate_id)
    objective = evaluate_raw_trial_objective(bool(review.get("passed")), verification_status, metrics)
    (trial_dir / "objective.json").write_text(json.dumps(_json_safe(objective), indent=2) + "\n", encoding="utf-8")
    _write_candidate_artifacts(
        trial_dir,
        candidate_id=candidate_id,
        baseline_netlist=baseline.netlist,
        baseline_devices=baseline.devices,
        trial_netlist=netlist,
        trial_devices=devices,
        proposal=proposal,
        params=params,
        objective=objective,
        dut_rel_path=str(config.get("dut_netlist", DUT_REL_PATH)),
        devices_rel_path=str(config.get("devices_csv", DEVICES_REL_PATH)),
    )
    result = {
        "trial_no": trial_no,
        "candidate_id": candidate_id,
        "trial_dir": str(trial_dir),
        "workspace_dir": str(workspace_dir),
        "proposal": proposal.to_dict(),
        "params": params,
        "review": review,
        "verification_status": verification_status,
        "metrics": metrics,
        "objective": objective,
        "netlist": netlist,
        "devices": devices,
    }
    (trial_dir / "trial_summary.json").write_text(json.dumps(_json_safe(_summary_for_trial(result)), indent=2) + "\n", encoding="utf-8")
    return result


def _write_candidate_artifacts(
    output_dir: Path,
    *,
    candidate_id: str,
    baseline_netlist: str,
    baseline_devices: str,
    trial_netlist: str,
    trial_devices: str,
    proposal: SingleDiodeProposal,
    params: dict[str, float],
    objective: dict[str, Any],
    dut_rel_path: str,
    devices_rel_path: str,
) -> None:
    patch_text = _unified_patch(baseline_netlist, trial_netlist, dut_rel_path)
    patch_text += _unified_patch(baseline_devices, trial_devices, devices_rel_path)
    proposal_json = {
        "candidate_id": candidate_id,
        "phase": "phase2a_area",
        "agent": "optimizer",
        "hypothesis": "Replace one input bias resistor with one weak diode junction and retune only the affected input capacitor.",
        "primary_objective": "area",
        "changed_blocks": ["single_diode_pseudoresistor", "input_highpass_cap_reduction", "device_accounting"],
        "files_touched": [dut_rel_path, devices_rel_path],
        "expected_effect": {
            "performance_nrmse_combined": "unknown",
            "area_total_p": "decrease",
            "power_score_basis_w": "unknown",
        },
        "risk": "A single diode junction may conduct too strongly or with the wrong polarity, shifting input bias or the lower cutoff.",
        "patch": patch_text,
    }
    notes = [
        "# Recursive Single-Diode Feedback Trial",
        "",
        f"candidate_id: {candidate_id}",
        f"proposal: {json.dumps(proposal.to_dict(), sort_keys=True)}",
        f"params: {json.dumps(params, sort_keys=True)}",
        f"objective: {json.dumps(_json_safe(objective), sort_keys=True)}",
        "",
        "The graph accepts this trial only if it preserves performance guards, keeps the low cutoff within tolerance, and reduces area plus the affected capacitor.",
        "",
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "proposal.json").write_text(json.dumps(proposal_json, indent=2) + "\n", encoding="utf-8")
    (output_dir / "patch.diff").write_text(patch_text, encoding="utf-8")
    (output_dir / "notes.md").write_text("\n".join(notes), encoding="utf-8")


def _single_diode_trial_params(
    baseline: BaselineSnapshot,
    proposal: SingleDiodeProposal,
    rng: random.Random,
) -> dict[str, float]:
    cap_name = TARGET_CAP[proposal.target_resistor]
    cap_param = CAP_PARAM[proposal.target_resistor]
    baseline_cap = _netlist_cap_multiplier(baseline.netlist, cap_name)
    if baseline_cap is None or baseline_cap <= 0.0:
        raise ValueError(f"missing positive baseline multiplier for {cap_name}")
    cap_low = max(1.0, baseline_cap * 0.05)
    cap_high = max(cap_low, baseline_cap * MIN_CAP_REDUCTION_RATIO)
    cap_value = math.exp(rng.uniform(math.log(cap_low), math.log(cap_high)))
    return {
        cap_param: cap_value,
        "DNEW_m": math.exp(rng.uniform(math.log(1.0), math.log(16.0))),
    }


def _single_diode_line(name: str, node: str, orientation: str, diode_multiplier: float) -> str:
    multiplier = _format_multiplier(diode_multiplier)
    if orientation == "node_to_vref":
        return f"{name} {node} VREF {DIODE_MODEL} m={multiplier}"
    if orientation == "vref_to_node":
        return f"{name} VREF {node} {DIODE_MODEL} m={multiplier}"
    raise ValueError(f"unknown diode orientation: {orientation}")


def _retune_devices_for_single_diode(
    baseline_devices: str,
    *,
    removed_resistor: str,
    cap_name: str,
    cap_multiplier: float,
    diode_name: str,
) -> str:
    reader = csv.DictReader(io.StringIO(baseline_devices))
    fieldnames = list(reader.fieldnames or [])
    if not fieldnames:
        raise ValueError("devices.csv missing header")
    rows = []
    removed = False
    cap_seen = False
    for row in reader:
        name = str(row.get("name") or "")
        if name == removed_resistor:
            removed = True
            continue
        if name == cap_name:
            row = dict(row)
            row["multiplier"] = _format_multiplier(cap_multiplier)
            cap_seen = True
        rows.append(row)
    if not removed:
        raise ValueError(f"devices.csv missing target resistor {removed_resistor}")
    if not cap_seen:
        raise ValueError(f"devices.csv missing target capacitor {cap_name}")
    rows.append(_diode_device_row(fieldnames, diode_name))
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _diode_device_row(fieldnames: list[str], name: str) -> dict[str, str]:
    row = {field: "" for field in fieldnames}
    row.update(
        {
            "name": name,
            "type": "diode",
            "count": "1",
            "width": "1.00u",
            "length": "1.00u",
            "multiplier": "1",
            "include_in_ppa": "true",
        }
    )
    return row


def _proposal_context(state: RecursiveGraphState, round_index: int) -> str:
    baseline = BaselineSnapshot.from_state(dict(state["baseline"]))
    recent_rejects = list(state.get("rejected_chain", []))[-3:]
    return "\n".join(
        [
            "# Recursive Single-Diode Proposal",
            "",
            f"round: {round_index}",
            "Goal: add exactly one diode junction as a weak pseudo-resistor, remove exactly one matching input bias resistor, and let the graph retune only the affected input capacitor.",
            "",
            "Required output: write output/proposal.json with:",
            '{"target_resistor":"RBIN1|RBIN2","orientation":"node_to_vref|vref_to_node","diode_count":1,"rationale":"..."}',
            "",
            "Constraints:",
            "- target_resistor must still exist in the active baseline.",
            f"- available_targets: {', '.join(_available_targets(baseline.netlist)) or 'none'}",
            "- add no more than one diode.",
            "- do not propose a back-to-back or anti-parallel cell.",
            "- BJT junction behavior is represented by diode_pd2nw_05v5.",
            "",
            "Recent rejected rounds:",
            json.dumps(_json_safe(recent_rejects), indent=2),
            "",
        ]
    )


def _heuristic_proposal(state: RecursiveGraphState, round_index: int) -> SingleDiodeProposal:
    targets = list(state.get("available_targets", []))
    if not targets:
        raise ValueError("no available RBIN targets")
    rejected_pairs = {
        (
            ((row.get("proposal") or {}).get("target_resistor")),
            ((row.get("proposal") or {}).get("orientation")),
        )
        for row in state.get("rejected_chain", [])
        if isinstance(row, dict)
    }
    for target in targets:
        for orientation in ALLOWED_ORIENTATIONS:
            if (target, orientation) not in rejected_pairs:
                return SingleDiodeProposal(target, orientation, "heuristic fallback proposal")
    target = targets[(round_index - 1) % len(targets)]
    orientation = ALLOWED_ORIENTATIONS[(round_index - 1) % len(ALLOWED_ORIENTATIONS)]
    return SingleDiodeProposal(target, orientation, "heuristic fallback proposal")


def _codex_proposal(round_dir: Path, context: str) -> SingleDiodeProposal:
    context_dir = round_dir / "proposer_context"
    output_dir = context_dir / "output"
    context_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    (context_dir / "context.md").write_text(context, encoding="utf-8")
    result = AgentRunner().run(AgentCall("optimizer", context_dir, context_dir / "logs", timeout_seconds=300, artifact_output_dir=output_dir))
    if result.exit_code != 0:
        raise ValueError(f"codex proposer failed: {result.error or result.exit_code}")
    proposal_path = output_dir / "proposal.json"
    if not proposal_path.exists():
        raise ValueError("codex proposer did not write output/proposal.json")
    return SingleDiodeProposal.from_dict(json.loads(proposal_path.read_text(encoding="utf-8")))


def _available_targets(netlist: str) -> list[str]:
    names = {_line_name(line) for line in netlist.splitlines()}
    return [target for target in ALLOWED_TARGETS if target in names]


def _metric(metrics: dict[str, Any], path: tuple[str, ...]) -> float | None:
    value: Any = metrics
    for part in path:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return _finite_float(value)


def _netlist_cap_multiplier(netlist: str, cap_name: str) -> float | None:
    for line in netlist.splitlines():
        if _line_name(line) == cap_name:
            return _assignment_float(line, "m")
    return None


def _assignment_float(line: str, key: str) -> float | None:
    prefix = key + "="
    for part in line.split():
        if part.startswith(prefix):
            return _finite_float(part[len(prefix) :])
    return None


def _replace_assignment(line: str, key: str, value: str) -> str:
    prefix = f"{key}="
    parts = line.split()
    for index, part in enumerate(parts):
        if part.startswith(prefix):
            parts[index] = prefix + value
            return " ".join(parts)
    return line + f" {prefix}{value}"


def _line_name(line: str) -> str:
    parts = line.split(maxsplit=1)
    return parts[0] if parts else ""


def _load_baseline_metrics(workspace: Path) -> dict[str, Any]:
    source = _baseline_metrics_source(workspace)
    if source is None:
        raise FileNotFoundError(f"could not find ppa_metrics.json near {workspace}")
    return json.loads(source.read_text(encoding="utf-8"))


def _baseline_metrics_source(workspace: Path) -> Path | None:
    candidates = [
        workspace / "run" / "ppa_metrics.json",
        workspace / "ppa_metrics.json",
        workspace.parent / "ppa_metrics.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _resolve_under_repo(repo_root: Path, value: Path) -> Path:
    resolved = value.resolve() if value.is_absolute() else (repo_root / value).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ValueError(f"path_outside_repo: {value}") from exc
    return resolved


def _candidate_id(timestamp: str, round_index: int, trial_no: int) -> str:
    safe = "".join(char if char.isalnum() or char in {"_", "-"} else "-" for char in timestamp).strip("-")
    return f"q5-recursive-single-diode-{safe}-r{round_index:04d}-trial-{trial_no:04d}"


def _summary_for_trial(trial: dict[str, Any] | None) -> dict[str, Any] | None:
    if trial is None:
        return None
    return {
        key: value
        for key, value in trial.items()
        if key not in {"netlist", "devices"}
    }


def _append_chain(state: RecursiveGraphState, name: str, entry: dict[str, Any]) -> None:
    sweep_root = state.get("sweep_root")
    if not sweep_root:
        return
    with (Path(str(sweep_root)) / name).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_json_safe(entry), sort_keys=True) + "\n")


def _copy_best_candidate(state: RecursiveGraphState, sweep_root: Path) -> None:
    accepted = list(state.get("accepted_chain", []))
    if not accepted:
        return
    baseline = dict(state.get("baseline") or {})
    workspace = Path(str(baseline.get("workspace_dir") or ""))
    if not workspace.exists():
        return
    best_dir = sweep_root / "best_candidate"
    best_dir.mkdir(parents=True, exist_ok=True)
    for name in ("dummy_neural_amp.scs", "devices.csv", "config.json"):
        source = workspace / name
        if source.exists():
            shutil.copy2(source, best_dir / name)


def _record_event(state: RecursiveGraphState, event: str) -> RecursiveGraphState:
    return {**state, "events": [*list(state.get("events", [])), event]}


if __name__ == "__main__":
    raise SystemExit(main())
