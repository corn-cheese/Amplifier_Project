from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

from .artifacts import ArtifactPaths
from .config import load_runner_config
from .graph import build_graph
from .production import DEFAULT_PRODUCTION_ARTIFACT_ROOT, PRODUCTION_SPEC_PATH, run_production_canary
from .state_store import StateStore


RUN_RECURSION_LIMIT_PER_COUNT = 20
SUBCOMMANDS = {"init", "run-one-batch", "run", "resume", "production-run"}
OPTIONS_WITH_VALUES = {
    "--repo-root",
    "--config",
    "--count",
    "--human-response",
    "--artifact-root",
    "--config-output",
    "--run-id",
    "--eda-smoke-command",
    "--eda-signoff",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="langgraph-runner")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--config", default="runner_config.json")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init")
    subparsers.add_parser("run-one-batch")
    run = subparsers.add_parser("run")
    run.add_argument("--count", type=_positive_int, default=1)
    resume = subparsers.add_parser("resume")
    resume.add_argument("--human-response")
    production = subparsers.add_parser("production-run")
    production.add_argument("--artifact-root", default=DEFAULT_PRODUCTION_ARTIFACT_ROOT)
    production.add_argument("--config-output")
    production.add_argument("--run-id", default="manual")
    eda = production.add_mutually_exclusive_group()
    eda.add_argument("--eda-smoke-command")
    eda.add_argument("--eda-signoff")

    return parser


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    parser = build_parser()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            args, unparsed = parser.parse_known_args(raw_argv)
    except SystemExit:
        parser_stderr = stderr.getvalue()
        _write_production_parser_failure_from_argv(raw_argv, parser_stderr.strip())
        if parser_stderr:
            sys.stderr.write(parser_stderr)
        raise
    if unparsed:
        message = "unrecognized arguments: " + " ".join(unparsed)
        if args.command == "production-run":
            _try_write_production_parser_failure(
                repo_root=Path(args.repo_root).resolve(),
                config=args.config,
                artifact_root=args.artifact_root,
                run_id=args.run_id,
                message=message,
                details={
                    "stderr": message,
                    "unparsed_argv": unparsed,
                    "argv": raw_argv,
                    "eda_smoke_command": args.eda_smoke_command,
                },
            )
        parser.error(message)

    repo_root = Path(args.repo_root).resolve()
    config_path = _resolve_under_repo(repo_root, args.config)
    if args.command == "production-run":
        run_production_canary(
            repo_root=repo_root,
            base_config_path=config_path,
            artifact_root=args.artifact_root,
            config_output=args.config_output,
            run_id=args.run_id,
            eda_smoke_command=args.eda_smoke_command,
            eda_signoff=args.eda_signoff,
        )
        return 0

    config = load_runner_config(config_path)
    artifact_root = _resolve_under_repo(repo_root, config.artifact_root)
    contract_path = _resolve_under_repo(repo_root, config.contract_path)
    paths = ArtifactPaths(repo_root=repo_root, artifact_root=artifact_root)
    store = StateStore(paths=paths, contract_path=contract_path)

    if args.command == "init":
        store.initialize()
        return 0

    route = "stop" if args.command == "run-one-batch" else "next_batch"
    graph = build_graph()
    initial_state = {
        "repo_root": str(repo_root),
        "run_id": "manual",
        "config_path": str(config_path),
        "state_path": str(paths.state_json),
        "route": route,
    }
    if args.command == "run":
        initial_state["counted_run_total"] = args.count
        initial_state["counted_run_remaining"] = args.count
    elif args.command == "resume":
        initial_state["counted_run_total"] = 1
        initial_state["counted_run_remaining"] = 1
    if args.command == "resume" and args.human_response is not None:
        initial_state["human_response"] = args.human_response

    if args.command == "run":
        final_state = graph.invoke(initial_state, config={"recursion_limit": RUN_RECURSION_LIMIT_PER_COUNT * args.count})
    else:
        final_state = graph.invoke(initial_state)
    if args.command in {"run", "resume"} and _counted_run_failed(final_state):
        _write_run_failure_diagnostic(final_state, paths)
        return 1
    return 0


def _counted_run_failed(state: object) -> bool:
    if not isinstance(state, dict):
        return False
    if "counted_run_total" not in state and "counted_run_remaining" not in state:
        return False
    top_decision = state.get("top_decision") or {}
    if isinstance(top_decision, dict):
        if top_decision.get("decision") == "stop" and top_decision.get("anomaly_level") == "critical":
            return True
        reason = str(top_decision.get("reason") or "")
        if "all candidates failed agent execution" in reason:
            return True
    return any("all candidates failed agent execution" in str(error) for error in state.get("errors", []))


def _write_run_failure_diagnostic(state: dict, paths: ArtifactPaths) -> None:
    top_decision = state.get("top_decision") or {}
    reason = "counted run failed"
    if isinstance(top_decision, dict) and top_decision.get("reason"):
        reason = str(top_decision["reason"])
    artifact_paths = []
    top_decision_path = state.get("top_decision_path")
    if top_decision_path:
        artifact_paths.append(Path(str(top_decision_path)))
    artifact_paths.append(paths.run_dir(str(state.get("run_id") or "manual")) / "batch_error.json")
    sys.stderr.write(f"langgraph-runner: {reason}\n")
    for path in dict.fromkeys(str(path) for path in artifact_paths):
        sys.stderr.write(f"artifact: {path}\n")


def _write_production_parser_failure_from_argv(argv: list[str], stderr: str) -> None:
    if _argv_subcommand(argv) != "production-run":
        return
    repo_root = Path(_argv_option_value(argv, "--repo-root", ".")).resolve()
    artifact_root = _argv_option_value(argv, "--artifact-root", DEFAULT_PRODUCTION_ARTIFACT_ROOT)
    run_id = _argv_option_value(argv, "--run-id", "manual")
    config = _argv_option_value(argv, "--config", "runner_config.json")
    _try_write_production_parser_failure(
        repo_root=repo_root,
        config=config,
        artifact_root=artifact_root,
        run_id=run_id,
        message=stderr or "argument parser failed",
        details={
            "stderr": stderr,
            "argv": argv,
        },
    )


def _try_write_production_parser_failure(
    *,
    repo_root: Path,
    config: str,
    artifact_root: str,
    run_id: str,
    message: str,
    details: dict,
) -> None:
    try:
        _write_production_parser_failure(
            repo_root=repo_root,
            config=config,
            artifact_root=artifact_root,
            run_id=run_id,
            message=message,
            details=details,
        )
    except (OSError, ValueError):
        return


def _write_production_parser_failure(
    *,
    repo_root: Path,
    config: str,
    artifact_root: str,
    run_id: str,
    message: str,
    details: dict,
) -> None:
    artifact_root_path = _resolve_under_repo(repo_root, artifact_root)
    run_dir = ArtifactPaths(repo_root=repo_root, artifact_root=artifact_root_path).run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        config_path = _resolve_under_repo(repo_root, config)
        config_path_value = str(config_path)
    except ValueError:
        config_path_value = str(config)
    payload = {
        "production_spec": PRODUCTION_SPEC_PATH,
        "config_path": config_path_value,
        "artifact_root": str(artifact_root_path),
        "run_id": run_id,
        "error": message,
        "error_class": "operator_command_error",
        "checks": {
            "argument_parser": {
                "status": "failed",
                "class": "operator_command_error",
                "details": json.dumps(details, sort_keys=True),
            }
        },
    }
    (run_dir / "production_run_failure.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _argv_subcommand(argv: list[str]) -> str | None:
    skip_next = False
    for value in argv:
        if skip_next:
            skip_next = False
            continue
        if value.startswith("--"):
            option = value.split("=", 1)[0]
            if "=" not in value and option in OPTIONS_WITH_VALUES:
                skip_next = True
            continue
        if value in SUBCOMMANDS:
            return value
    return None


def _argv_option_value(argv: list[str], option: str, default: str) -> str:
    prefix = option + "="
    for value in argv:
        if value.startswith(prefix):
            return value[len(prefix) :] or default
    try:
        index = argv.index(option)
    except ValueError:
        return default
    value_index = index + 1
    if value_index >= len(argv):
        return default
    value = argv[value_index]
    if value.startswith("--"):
        return default
    return value


def _resolve_under_repo(repo_root: Path, value: str) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (repo_root / path).resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"config path must stay under repo root: {value}") from exc
    return resolved
