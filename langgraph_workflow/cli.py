from __future__ import annotations

import argparse
import json
from pathlib import Path

from .backend import EdaSshBackend
from .config import load_workflow_config
from .validation import validate_seed
from .workflow import build_state_graph, run_mock_workflow_once


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="langgraph_workflow")
    sub = parser.add_subparsers(dest="command", required=True)

    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--config", required=True, type=Path)

    run_parser = sub.add_parser("run")
    run_parser.add_argument("--config", required=True, type=Path)

    ssh_check_parser = sub.add_parser("ssh-check")
    ssh_check_parser.add_argument("--config", required=True, type=Path)

    report_parser = sub.add_parser("report")
    report_parser.add_argument("--run-root", default=Path("runs"), type=Path)

    args = parser.parse_args(argv)
    if args.command == "validate":
        return _validate(args.config)
    if args.command == "run":
        return _run(args.config)
    if args.command == "ssh-check":
        return _ssh_check(args.config)
    if args.command == "report":
        return _report(args.run_root)
    raise AssertionError(args.command)


def _validate(path: Path) -> int:
    cfg = load_workflow_config(path)
    if cfg.backend == "eda_ssh":
        EdaSshBackend(cfg.remote, amptest_local_dir=cfg.amptest_local_dir).validate()
    if cfg.seed_file:
        for seed in _load_seeds(cfg.seed_file):
            result = validate_seed(seed)
            if not result.valid:
                print(json.dumps({"valid": False, "errors": result.errors}, indent=2))
                return 1
    print(json.dumps({"valid": True}, indent=2))
    return 0


def _run(path: Path) -> int:
    cfg = load_workflow_config(path)
    if cfg.backend == "mock":
        if not cfg.seed_file or not cfg.mock_fixture_dir:
            raise RuntimeError("mock runs require seed_file and mock_fixture_dir")
        seeds = _load_seeds(cfg.seed_file)
        if not seeds:
            raise RuntimeError("seed_file has no seeds")
        state = run_mock_workflow_once(
            seed=seeds[0],
            params=seeds[0]["initial_params"],
            run_root=cfg.run_root,
            amptest_config_path=cfg.amptest_local_dir / "config.json",
            fixture_dir=cfg.mock_fixture_dir,
        )
        print(json.dumps({"best_result": state.get("best_result")}, indent=2, default=str))
        return 0

    graph = build_state_graph(cfg)
    compiled = graph.compile()
    result = compiled.invoke({})
    print(json.dumps(result, indent=2, default=str))
    return 0


def _ssh_check(path: Path) -> int:
    cfg = load_workflow_config(path)
    if cfg.backend != "eda_ssh":
        raise RuntimeError("ssh-check requires backend: eda_ssh")
    backend = EdaSshBackend(
        cfg.remote,
        amptest_local_dir=cfg.amptest_local_dir,
        timeout_s=cfg.remote_timeout_s,
        min_interval_s=cfg.min_interval_s,
        daily_max_trials=cfg.daily_max_trials,
    )
    result = backend.check()
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


def _report(run_root: Path) -> int:
    report = run_root / "final_report.md"
    if not report.exists():
        raise FileNotFoundError(report)
    print(report.read_text())
    return 0


def _load_seeds(path: Path) -> list[dict]:
    with path.open() as f:
        data = json.load(f)
    seeds = data.get("seeds", data)
    if not isinstance(seeds, list):
        raise ValueError("seed file must contain a list or {'seeds': [...]}")
    return seeds


if __name__ == "__main__":
    raise SystemExit(main())
