from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .backend import EdaSshBackend, MockBackend
from .objective import compute_objective
from .optimizer import create_study, sample_trial_params, serialize_study, tell_trial
from .rendering import artifact_complete, render_candidate
from .seed_generation import CodexExecSeedProvider, CodexExecSeedRepairProvider
from .state import CircuitSeed, TrialResult, WorkflowConfig, WorkflowState
from .validation import validate_seed

try:
    from langgraph.graph import END, START, StateGraph  # type: ignore
except ImportError:  # pragma: no cover - exercised only when dependency is installed
    END = "__end__"
    START = "__start__"
    StateGraph = None


def build_state_graph(
    config: WorkflowConfig,
    *,
    seed_provider: Callable[[WorkflowState], list[CircuitSeed]] | None = None,
    repair_provider: Callable[[WorkflowState], CircuitSeed] | None = None,
    backend: Any | None = None,
) -> Any:
    """Build a LangGraph StateGraph when langgraph is installed.

    The local test environment does not include langgraph, so this function
    raises a clear dependency error instead of hiding that production runtime
    requirement.
    """

    if StateGraph is None:
        raise RuntimeError("langgraph is not installed; install runtime dependencies to build StateGraph")

    provider = seed_provider or _seed_provider_from_config(config)
    repair = repair_provider or _repair_provider_from_config(config)
    run_backend = backend or _backend_from_config(config)
    graph = StateGraph(WorkflowState)

    graph.add_node("load_project_spec", lambda state: load_project_spec(state, config))
    graph.add_node("llm_seed_topologies", lambda state: llm_seed_topologies(state, provider, config))
    graph.add_node("validate_seed", validate_seed_node)
    graph.add_node("select_next_seed", select_next_seed)
    graph.add_node("repair_seed_with_codex", lambda state: repair_seed_with_codex(state, repair, config))
    graph.add_node("init_optuna_study", lambda state: init_optuna_study(state, config))
    graph.add_node("sample_trial", lambda state: sample_trial(state, config))
    graph.add_node("render_candidate", lambda state: render_candidate_node(state, config))
    graph.add_node("run_amptest_ssh", lambda state: run_amptest_node(state, run_backend))
    graph.add_node("score_trial", lambda state: score_trial(state, config))
    graph.add_node("route_next", lambda state: route_next(state, config))
    graph.add_node("final_report", final_report)

    graph.add_edge(START, "load_project_spec")
    graph.add_edge("load_project_spec", "llm_seed_topologies")
    graph.add_conditional_edges(
        "llm_seed_topologies",
        lambda state: "validate" if state.get("active_seed") else "stop",
        {"validate": "validate_seed", "stop": "final_report"},
    )
    graph.add_conditional_edges(
        "validate_seed",
        lambda state: route_after_seed_validation(state, config),
        {
            "valid": "init_optuna_study",
            "next_seed": "select_next_seed",
            "reseed": "llm_seed_topologies",
            "stop": "final_report",
        },
    )
    graph.add_edge("select_next_seed", "validate_seed")
    graph.add_conditional_edges(
        "repair_seed_with_codex",
        lambda state: "validate" if state.get("active_seed") else state.get("next_route", "stop"),
        {
            "validate": "validate_seed",
            "next_seed": "select_next_seed",
            "reseed": "llm_seed_topologies",
            "stop": "final_report",
        },
    )
    graph.add_edge("init_optuna_study", "sample_trial")
    graph.add_edge("sample_trial", "render_candidate")
    graph.add_edge("render_candidate", "run_amptest_ssh")
    graph.add_edge("run_amptest_ssh", "score_trial")
    graph.add_edge("score_trial", "route_next")
    graph.add_conditional_edges(
        "route_next",
        lambda state: state.get("next_route", "stop"),
        {
            "continue": "sample_trial",
            "next_seed": "select_next_seed",
            "repair_seed": "repair_seed_with_codex",
            "reseed": "llm_seed_topologies",
            "stop": "final_report",
        },
    )
    graph.add_edge("final_report", END)
    return graph


def load_project_spec(state: WorkflowState, config: WorkflowConfig) -> WorkflowState:
    amptest_config_path = config.amptest_local_dir / "config.json"
    amptest_readme = config.amptest_local_dir / "README.md"
    spec_path = Path("neural_signal_amplifier_project.md")
    state = dict(state)
    spec = {
        "project_spec_path": str(spec_path),
        "amptest_config_path": str(amptest_config_path),
        "amptest_readme_path": str(amptest_readme),
    }
    if amptest_config_path.exists():
        with amptest_config_path.open() as f:
            spec["amptest_config"] = json.load(f)
    state["spec"] = spec
    state.setdefault("failure_reasons", [])
    state.setdefault("trial_results", [])
    state.setdefault("seed_smoke_passed", {})
    state.setdefault("seed_repair_attempts", {})
    state.setdefault("consecutive_smoke_failures", 0)
    state.setdefault("abandoned_seed_ids", [])
    return state


def llm_seed_topologies(
    state: WorkflowState,
    seed_provider: Callable[[WorkflowState], list[CircuitSeed]],
    config: WorkflowConfig | None = None,
) -> WorkflowState:
    state = dict(state)
    try:
        generated_seeds = seed_provider(state)
    except Exception as exc:  # noqa: BLE001 - interrupt payload should preserve any provider failure
        state["interrupt"] = {"reason": "seed_generation_failed", "error": str(exc)}
        state["seeds"] = list(state.get("seeds", []))
        state["active_seed"] = None
        return state

    existing_seeds = list(state.get("seeds", []))
    remaining = None if config is None else max(0, config.max_seeds - len(existing_seeds))
    new_seeds = _unique_new_seeds(existing_seeds, generated_seeds)
    if remaining is not None:
        new_seeds = new_seeds[:remaining]
    seeds = existing_seeds + new_seeds
    state["seeds"] = seeds
    if new_seeds:
        state["active_seed_index"] = len(existing_seeds)
        state["active_seed"] = new_seeds[0]
        state["study_name"] = None
        state["optimizer_state"] = None
        state["seed_valid"] = False
        state["interrupt"] = None
    elif not seeds:
        state["active_seed"] = None
        state["interrupt"] = {"reason": "no_seed_available"}
    else:
        state["active_seed"] = None
        state["interrupt"] = {"reason": "no_new_seed_available"}
    return state


def validate_seed_node(state: WorkflowState) -> WorkflowState:
    state = dict(state)
    seed = state.get("active_seed")
    if not seed:
        state["seed_valid"] = False
        state["interrupt"] = {"reason": "missing_active_seed"}
        return state
    result = validate_seed(seed)
    if not result.valid:
        state["seed_valid"] = False
        _abandon_seed_id(state, seed["seed_id"])
        failures = list(state.get("failure_reasons", []))
        failures.extend(result.errors)
        state["failure_reasons"] = failures
        state["interrupt"] = {"reason": "invalid_seed", "errors": result.errors}
    else:
        state["seed_valid"] = True
        state["interrupt"] = None
    return state


def route_after_seed_validation(state: WorkflowState, config: WorkflowConfig) -> str:
    if state.get("seed_valid"):
        return "valid"
    if _has_next_seed(state):
        return "next_seed"
    if config.seed_file is None and len(state.get("seeds", [])) < config.max_seeds:
        return "reseed"
    return "stop"


def select_next_seed(state: WorkflowState) -> WorkflowState:
    state = dict(state)
    seeds = list(state.get("seeds", []))
    next_index = int(state.get("active_seed_index", -1)) + 1
    if next_index >= len(seeds):
        state["active_seed"] = None
        state["seed_valid"] = False
        state["interrupt"] = {"reason": "no_next_seed_available"}
        return state
    state["active_seed_index"] = next_index
    state["active_seed"] = seeds[next_index]
    state["study_name"] = None
    state["optimizer_state"] = None
    state["seed_valid"] = False
    state["interrupt"] = None
    return state


def repair_seed_with_codex(
    state: WorkflowState,
    repair_provider: Callable[[WorkflowState], CircuitSeed],
    config: WorkflowConfig,
) -> WorkflowState:
    state = dict(state)
    failed_seed = state.get("active_seed")
    if not failed_seed:
        state["active_seed"] = None
        state["next_route"] = "stop"
        state["interrupt"] = {"reason": "missing_failed_seed_for_repair"}
        return state

    attempts = dict(state.get("seed_repair_attempts", {}))
    failed_seed_id = failed_seed["seed_id"]
    attempt = int(attempts.get(failed_seed_id, 0)) + 1
    attempts[failed_seed_id] = attempt
    state["seed_repair_attempts"] = attempts
    _abandon_seed_id(state, failed_seed_id)

    if attempt > config.max_seed_repair_attempts or len(state.get("seeds", [])) >= config.max_seeds:
        state["active_seed"] = None
        state["next_route"] = _route_after_seed_exhaustion(state, config)
        return state

    try:
        repaired_seed = dict(repair_provider(state))
    except Exception as exc:  # noqa: BLE001 - preserve repair failure for report/interrupt state
        failures = list(state.get("failure_reasons", []))
        failures.append(f"seed repair failed for {failed_seed_id}: {exc}")
        state["failure_reasons"] = failures
        state["active_seed"] = None
        state["next_route"] = _route_after_seed_exhaustion(state, config)
        state["interrupt"] = {"reason": "seed_repair_failed", "error": str(exc), "seed_id": failed_seed_id}
        return state

    existing_ids = {seed["seed_id"] for seed in state.get("seeds", [])}
    if repaired_seed.get("seed_id") in existing_ids:
        repaired_seed["seed_id"] = f"{failed_seed_id}_repair{attempt}"
    seeds = list(state.get("seeds", []))
    seeds.append(repaired_seed)
    state["seeds"] = seeds
    state["active_seed_index"] = len(seeds) - 1
    state["active_seed"] = repaired_seed
    state["study_name"] = None
    state["optimizer_state"] = None
    state["seed_valid"] = False
    state["next_route"] = "validate"
    state["interrupt"] = None
    return state


def init_optuna_study(state: WorkflowState, config: WorkflowConfig) -> WorkflowState:
    state = dict(state)
    seed = state.get("active_seed")
    if seed and not state.get("study_name"):
        state["study_name"] = f"bjt_amp_{seed['seed_id']}"
    if seed:
        study = create_study(seed, storage=config.optuna_storage, study_name=state.get("study_name"))
        state["optimizer_state"] = serialize_study(study)
    return state


def sample_trial(state: WorkflowState, config: WorkflowConfig) -> WorkflowState:
    state = dict(state)
    seed = state.get("active_seed")
    if not seed:
        state["interrupt"] = {"reason": "missing_active_seed"}
        return state
    study = create_study(
        seed,
        storage=config.optuna_storage,
        study_name=state.get("study_name"),
        state=state.get("optimizer_state"),
    )
    trial_number, params = sample_trial_params(study, seed)
    state["optimizer_state"] = serialize_study(study)
    trial_results = list(state.get("trial_results", []))
    trial_id = f"trial_{trial_number}"
    trial_results.append(
        {
            "trial_id": trial_id,
            "seed_id": seed["seed_id"],
            "params": params,
            "status": "queued",
            "metrics": None,
            "objective": None,
            "artifact_dir": "",
            "error": None,
            "optuna_trial_number": trial_number,
        }
    )
    state["trial_results"] = trial_results
    return state


def render_candidate_node(state: WorkflowState, config: WorkflowConfig) -> WorkflowState:
    state = dict(state)
    seed = state["active_seed"]
    trial = dict(state["trial_results"][-1])
    base_config = state.get("spec", {}).get("amptest_config")
    if base_config is None:
        with (config.amptest_local_dir / "config.json").open() as f:
            base_config = json.load(f)
    artifact_dir = config.run_root / seed["seed_id"] / trial["trial_id"]
    render_candidate(
        seed=seed,
        params=trial["params"],
        base_amptest_config=base_config,
        artifact_dir=artifact_dir,
        trial_id=trial["trial_id"],
        backend_name=config.backend,
    )
    trial["artifact_dir"] = str(artifact_dir)
    trial["status"] = "rendered"
    state["trial_results"][-1] = trial
    return state


def run_amptest_node(state: WorkflowState, backend: Any) -> WorkflowState:
    state = dict(state)
    trial = dict(state["trial_results"][-1])
    artifact_dir = Path(trial["artifact_dir"])
    if artifact_complete(artifact_dir, trial["params"]):
        trial["status"] = "simulated"
        state["trial_results"][-1] = trial
        return state
    try:
        outcome = backend.run(artifact_dir)
    except Exception as exc:  # noqa: BLE001
        trial["status"] = "failed"
        trial["error"] = str(exc)
        state["trial_results"][-1] = trial
        return state
    state["remote_run"] = {
        "exit_code": outcome.exit_code,
        "stdout": outcome.stdout,
        "stderr": outcome.stderr,
        "downloaded_files": [str(path) for path in outcome.downloaded_files],
    }
    trial["status"] = "simulated" if outcome.exit_code == 0 else "failed"
    trial["error"] = None if outcome.exit_code == 0 else outcome.stderr
    state["trial_results"][-1] = trial
    return state


def score_trial(state: WorkflowState, config: WorkflowConfig | None = None) -> WorkflowState:
    state = dict(state)
    trial = dict(state["trial_results"][-1])
    metrics_path = Path(trial["artifact_dir"]) / "ppa_metrics.json"
    metrics = None
    if metrics_path.exists():
        with metrics_path.open() as f:
            metrics = json.load(f)
    objective = compute_objective(metrics, simulation_failed=trial["status"] == "failed")
    seed = state.get("active_seed")
    if seed:
        study = create_study(
            seed,
            storage=config.optuna_storage if config else None,
            study_name=state.get("study_name"),
            state=state.get("optimizer_state"),
        )
        tell_trial(
            study,
            int(trial.get("optuna_trial_number", trial["trial_id"].split("_")[-1])),
            objective.objective if trial["status"] != "failed" else None,
            failed=trial["status"] == "failed",
        )
        state["optimizer_state"] = serialize_study(study)
    trial["metrics"] = metrics
    trial["objective"] = objective.objective
    trial["status"] = "scored"
    if objective.penalties:
        trial["error"] = "; ".join(objective.penalties)
    state["trial_results"][-1] = trial
    best = state.get("best_result")
    if best is None or (trial["objective"] is not None and trial["objective"] < best["objective"]):
        state["best_result"] = trial
    return state


def route_next(state: WorkflowState, config: WorkflowConfig) -> WorkflowState:
    state = dict(state)
    if len(state.get("trial_results", [])) >= config.daily_max_trials:
        state["next_route"] = "stop"
        state["interrupt"] = {"reason": "daily_trial_limit_reached", "limit": config.daily_max_trials}
        return state

    seed = state.get("active_seed")
    if seed and not _seed_smoke_passed(state, seed["seed_id"]):
        trial = state["trial_results"][-1]
        if _trial_smoke_passed(trial):
            smoke = dict(state.get("seed_smoke_passed", {}))
            smoke[seed["seed_id"]] = True
            state["seed_smoke_passed"] = smoke
            state["consecutive_smoke_failures"] = 0
        else:
            state["consecutive_smoke_failures"] = int(state.get("consecutive_smoke_failures", 0)) + 1
            failures = list(state.get("failure_reasons", []))
            failures.append(f"smoke failed for {seed['seed_id']}: {trial.get('error') or 'unknown error'}")
            state["failure_reasons"] = failures
            if int(state["consecutive_smoke_failures"]) >= config.max_consecutive_smoke_failures:
                _abandon_seed_id(state, seed["seed_id"])
                state["next_route"] = "stop"
                state["interrupt"] = {
                    "reason": "max_consecutive_smoke_failures_reached",
                    "limit": config.max_consecutive_smoke_failures,
                }
                return state
            if _can_repair_active_seed(state, config):
                state["next_route"] = "repair_seed"
                return state
            _abandon_seed_id(state, seed["seed_id"])
            state["next_route"] = _route_after_seed_exhaustion(state, config)
            return state

    best = state.get("best_result")
    if best and best.get("objective") is not None and best["objective"] <= config.objective_target:
        state["next_route"] = "stop"
    elif _active_seed_trial_count(state) < config.max_trials_per_seed:
        state["next_route"] = "continue"
    elif _has_next_seed(state):
        state["next_route"] = "next_seed"
    elif config.seed_file is None and len(state.get("seeds", [])) < config.max_seeds:
        state["next_route"] = "reseed"
    else:
        state["next_route"] = "stop"
    return state


def final_report(state: WorkflowState) -> WorkflowState:
    state = dict(state)
    state["next_route"] = "stop"
    best = state.get("best_result")
    if not best:
        return state
    artifact_dir = Path(best["artifact_dir"])
    report = _format_report(state)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "final_report.md").write_text(report)
    run_root = artifact_dir.parent.parent
    (run_root / "final_report.md").write_text(report)
    return state


def run_mock_workflow_once(
    *,
    seed: CircuitSeed,
    params: dict[str, Any],
    run_root: str | Path,
    amptest_config_path: str | Path,
    fixture_dir: str | Path,
) -> WorkflowState:
    validation = validate_seed(seed)
    if not validation.valid:
        raise ValueError("; ".join(validation.errors))
    with Path(amptest_config_path).open() as f:
        base_config = json.load(f)
    artifact_dir = Path(run_root) / seed["seed_id"] / "trial_0"
    render_candidate(
        seed=seed,
        params=params,
        base_amptest_config=base_config,
        artifact_dir=artifact_dir,
        trial_id="trial_0",
        backend_name="mock",
    )
    backend = MockBackend(fixture_dir)
    backend.run(artifact_dir)
    with (artifact_dir / "ppa_metrics.json").open() as f:
        metrics = json.load(f)
    objective = compute_objective(metrics)
    trial: TrialResult = {
        "trial_id": "trial_0",
        "seed_id": seed["seed_id"],
        "params": params,
        "status": "scored",
        "metrics": metrics,
        "objective": objective.objective,
        "artifact_dir": str(artifact_dir),
        "error": "; ".join(objective.penalties) if objective.penalties else None,
    }
    state: WorkflowState = {
        "spec": {"amptest_config_path": str(amptest_config_path)},
        "seeds": [seed],
        "active_seed": seed,
        "study_name": f"bjt_amp_{seed['seed_id']}",
        "trial_results": [trial],
        "best_result": trial,
        "remote_run": {"backend": "mock"},
        "failure_reasons": [],
    }
    return final_report(state)


def _seed_provider_from_config(config: WorkflowConfig) -> Callable[[WorkflowState], list[CircuitSeed]]:
    if config.seed_file:
        return _file_seed_provider(config.seed_file)
    return CodexExecSeedProvider(
        max_seeds=config.llm_seed_batch_size,
        attempts=config.llm_seed_attempts,
        run_root=config.run_root,
        model=config.codex_exec_model,
        profile=config.codex_exec_profile,
        timeout_s=config.codex_exec_timeout_s,
        sandbox=config.codex_exec_sandbox,
    )


def _repair_provider_from_config(config: WorkflowConfig) -> Callable[[WorkflowState], CircuitSeed]:
    return CodexExecSeedRepairProvider(
        attempts=1,
        run_root=config.run_root,
        model=config.codex_exec_model,
        profile=config.codex_exec_profile,
        timeout_s=config.codex_exec_timeout_s,
        sandbox=config.codex_exec_sandbox,
        log_excerpt_chars=config.smoke_log_excerpt_chars,
    )


def _file_seed_provider(path: Path) -> Callable[[WorkflowState], list[CircuitSeed]]:
    def provider(_: WorkflowState) -> list[CircuitSeed]:
        with path.open() as f:
            data = json.load(f)
        seeds = data.get("seeds", data)
        if not isinstance(seeds, list):
            raise ValueError("seed_file must contain a list or {'seeds': [...]}")
        return seeds

    return provider


def _unique_new_seeds(existing: list[CircuitSeed], generated: list[CircuitSeed]) -> list[CircuitSeed]:
    existing_ids = {seed["seed_id"] for seed in existing}
    new_seeds: list[CircuitSeed] = []
    for seed in generated:
        seed_id = seed["seed_id"]
        if seed_id in existing_ids:
            continue
        existing_ids.add(seed_id)
        new_seeds.append(seed)
    return new_seeds


def _has_next_seed(state: WorkflowState) -> bool:
    return int(state.get("active_seed_index", 0)) + 1 < len(state.get("seeds", []))


def _seed_smoke_passed(state: WorkflowState, seed_id: str) -> bool:
    return bool(dict(state.get("seed_smoke_passed", {})).get(seed_id))


def _trial_smoke_passed(trial: TrialResult | dict[str, Any]) -> bool:
    if trial.get("status") != "scored":
        return False
    metrics = trial.get("metrics")
    if not isinstance(metrics, dict):
        return False
    if metrics.get("performance_nrmse_combined") is None:
        return False
    ac = metrics.get("ac")
    tran = metrics.get("tran")
    return isinstance(ac, dict) and bool(ac) and isinstance(tran, dict) and bool(tran)


def _can_repair_active_seed(state: WorkflowState, config: WorkflowConfig) -> bool:
    seed = state.get("active_seed")
    if not seed:
        return False
    attempts = dict(state.get("seed_repair_attempts", {}))
    return (
        int(attempts.get(seed["seed_id"], 0)) < config.max_seed_repair_attempts
        and len(state.get("seeds", [])) < config.max_seeds
    )


def _abandon_seed_id(state: WorkflowState, seed_id: str) -> None:
    abandoned = list(state.get("abandoned_seed_ids", []))
    if seed_id not in abandoned:
        abandoned.append(seed_id)
    state["abandoned_seed_ids"] = abandoned


def _route_after_seed_exhaustion(state: WorkflowState, config: WorkflowConfig) -> str:
    if _has_next_seed(state):
        return "next_seed"
    if config.seed_file is None and len(state.get("seeds", [])) < config.max_seeds:
        return "reseed"
    return "stop"


def _active_seed_trial_count(state: WorkflowState) -> int:
    seed = state.get("active_seed")
    if not seed:
        return 0
    seed_id = seed["seed_id"]
    return sum(1 for trial in state.get("trial_results", []) if trial.get("seed_id") == seed_id)


def _backend_from_config(config: WorkflowConfig) -> Any:
    if config.backend == "mock":
        if not config.mock_fixture_dir:
            raise RuntimeError("mock_fixture_dir is required for mock backend")
        return MockBackend(config.mock_fixture_dir)
    return EdaSshBackend(
        config.remote,
        amptest_local_dir=config.amptest_local_dir,
        timeout_s=config.remote_timeout_s,
        min_interval_s=config.min_interval_s,
        daily_max_trials=config.daily_max_trials,
    )


def _format_report(state: WorkflowState) -> str:
    best = state.get("best_result")
    lines = ["# LangGraph Workflow Final Report", ""]
    if not best:
        lines.append("No scored trial results.")
        return "\n".join(lines) + "\n"
    lines.extend(
        [
            f"- best_seed: {best['seed_id']}",
            f"- best_trial: {best['trial_id']}",
            f"- objective: {best['objective']}",
            f"- artifact_dir: {best['artifact_dir']}",
            "",
            "## Best Parameters",
            "",
            json.dumps(best["params"], indent=2, sort_keys=True),
            "",
            "## Failed Reasons",
            "",
        ]
    )
    failures = state.get("failure_reasons", [])
    if failures:
        lines.extend(f"- {reason}" for reason in failures)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"
