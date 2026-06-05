from __future__ import annotations

import argparse
from pathlib import Path

from .artifacts import ArtifactPaths
from .config import load_runner_config
from .graph import build_graph
from .production import DEFAULT_PRODUCTION_ARTIFACT_ROOT, run_production_canary
from .state_store import StateStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="langgraph-runner")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--config", default="runner_config.json")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init")
    subparsers.add_parser("run-one-batch")
    subparsers.add_parser("run")
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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
    if route == "next_batch":
        initial_state["stop_after_current_pass"] = True
    if args.command == "resume" and args.human_response is not None:
        initial_state["human_response"] = args.human_response

    graph.invoke(initial_state)
    return 0


def _resolve_under_repo(repo_root: Path, value: str) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (repo_root / path).resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"config path must stay under repo root: {value}") from exc
    return resolved
