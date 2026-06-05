import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


def scratch_root(case: str) -> Path:
    root = Path(__file__).resolve().parents[2] / ".test_tmp_langgraph_runner" / "cli" / case
    path = root / uuid.uuid4().hex
    path.mkdir(mode=0o777, parents=True, exist_ok=True)
    return path


def write_runner_config(root: Path) -> Path:
    config = {
        "artifact_root": "automation_artifacts",
        "contract_path": "docs/contract.md",
        "amptest_dir": "amptest",
        "dut_netlist": "dut.sp",
        "devices_csv": "devices.csv",
        "amptest_config": "amptest.yaml",
        "candidate_generation_batch_size": 2,
        "max_active_primes_per_subagent": 1,
        "max_total_primes_per_subagent": 2,
        "agent_timeouts_seconds": {"architecture": 60, "implementation": 60},
        "verifier": {
            "command": "python -m unittest",
            "timeout_seconds": 60,
            "min_interval_seconds": 0,
            "required_outputs": ["report.json"],
        },
    }
    path = root / "runner_config.json"
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def update_runner_config(path: Path, **updates) -> None:
    config = json.loads(path.read_text(encoding="utf-8"))
    config.update(updates)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def write_named_runner_config(root: Path, filename: str) -> Path:
    config = write_runner_config(root)
    named_config = root / filename
    named_config.write_text(config.read_text(encoding="utf-8"), encoding="utf-8")
    return named_config


def configured_root(case: str) -> tuple[Path, Path]:
    root = scratch_root(case)
    docs = root / "docs"
    docs.mkdir(mode=0o777, exist_ok=True)
    (docs / "contract.md").write_text("contract\n", encoding="utf-8")
    return root, write_runner_config(root)


class FakeGraph:
    def __init__(self):
        self.invocations = []

    def invoke(self, state):
        self.invocations.append(state)
        return state


class TestCli(unittest.TestCase):
    def test_init_creates_artifact_root(self):
        from langgraph_runner.cli import main

        root, config = configured_root("init_creates_artifact_root")

        result = main(["--repo-root", str(root), "--config", str(config), "init"])

        self.assertEqual(result, 0)
        self.assertTrue((root / "automation_artifacts" / "state.json").exists())

    def test_run_one_batch_invokes_stop_route(self):
        from langgraph_runner.cli import main

        root, config = configured_root("run_one_batch_stop")
        graph = FakeGraph()

        with patch("langgraph_runner.cli.build_graph", return_value=graph):
            result = main(["--repo-root", str(root), "--config", str(config), "run-one-batch"])

        self.assertEqual(result, 0)
        self.assertEqual(len(graph.invocations), 1)
        self.assertEqual(graph.invocations[0]["route"], "stop")
        self.assertEqual(graph.invocations[0]["repo_root"], str(root.resolve()))
        self.assertEqual(graph.invocations[0]["run_id"], "manual")

    def test_run_one_batch_passes_resolved_alternate_config_path(self):
        from langgraph_runner.cli import main

        root, _config = configured_root("run_one_batch_alt_config")
        config = write_named_runner_config(root, "alt_runner_config.json")
        graph = FakeGraph()

        with patch("langgraph_runner.cli.build_graph", return_value=graph):
            result = main(["--repo-root", str(root), "--config", "alt_runner_config.json", "run-one-batch"])

        self.assertEqual(result, 0)
        self.assertEqual(len(graph.invocations), 1)
        self.assertEqual(graph.invocations[0]["config_path"], str(config.resolve()))

    def test_run_invokes_next_batch_with_state_path(self):
        from langgraph_runner.cli import main

        root, config = configured_root("run_next_batch")
        graph = FakeGraph()

        with patch("langgraph_runner.cli.build_graph", return_value=graph):
            result = main(["--repo-root", str(root), "--config", str(config), "run"])

        self.assertEqual(result, 0)
        self.assertEqual(len(graph.invocations), 1)
        self.assertEqual(graph.invocations[0]["route"], "next_batch")
        self.assertEqual(
            graph.invocations[0]["state_path"],
            str(root.resolve() / "automation_artifacts" / "state.json"),
        )

    def test_run_with_real_shell_graph_returns_without_recursion(self):
        from langgraph_runner.cli import main
        from langgraph_runner.graph import build_graph

        root, config = configured_root("run_real_shell_graph")
        graph = build_graph()

        with patch("langgraph_runner.cli.build_graph", return_value=graph) as build_graph_mock:
            result = main(["--repo-root", str(root), "--config", str(config), "run"])

        self.assertEqual(result, 0)
        build_graph_mock.assert_called_once_with()

    def test_default_config_resolves_relative_to_repo_root(self):
        from langgraph_runner.cli import main

        root, _config = configured_root("default_config_repo_root")
        cwd = scratch_root("default_config_process_cwd")
        original_cwd = Path.cwd()

        try:
            os.chdir(cwd)
            result = main(["--repo-root", str(root), "init"])
        finally:
            os.chdir(original_cwd)

        self.assertEqual(result, 0)
        self.assertTrue((root / "automation_artifacts" / "state.json").exists())

    def test_config_path_must_stay_under_repo_root(self):
        from langgraph_runner.cli import main

        root, _config = configured_root("config_path_repo_root")
        outside = scratch_root("config_path_outside") / "runner_config.json"
        outside.write_text((root / "runner_config.json").read_text(encoding="utf-8"), encoding="utf-8")

        with self.assertRaises(ValueError):
            main(["--repo-root", str(root), "--config", str(outside), "init"])

    def test_artifact_root_must_stay_under_repo_root(self):
        from langgraph_runner.cli import main

        root, config = configured_root("artifact_root_repo_root")
        outside_name = f"outside_artifacts_{root.name}"
        update_runner_config(config, artifact_root=f"../{outside_name}")

        with self.assertRaises(ValueError):
            main(["--repo-root", str(root), "--config", str(config), "init"])

        self.assertFalse((root.parent / outside_name).exists())

    def test_contract_path_must_stay_under_repo_root(self):
        from langgraph_runner.cli import main

        root, config = configured_root("contract_path_repo_root")
        outside_contract = scratch_root("outside_contract") / "contract.md"
        outside_contract.write_text("outside\n", encoding="utf-8")
        update_runner_config(config, contract_path=str(outside_contract))

        with self.assertRaises(ValueError):
            main(["--repo-root", str(root), "--config", str(config), "init"])

    def test_resume_passes_human_response_to_graph(self):
        from langgraph_runner.cli import main

        root, config = configured_root("resume_human_response")
        graph = FakeGraph()

        with patch("langgraph_runner.cli.build_graph", return_value=graph):
            result = main(
                [
                    "--repo-root",
                    str(root),
                    "--config",
                    str(config),
                    "resume",
                    "--human-response",
                    "approved",
                ]
            )

        self.assertEqual(result, 0)
        self.assertEqual(len(graph.invocations), 1)
        self.assertEqual(graph.invocations[0]["route"], "next_batch")
        self.assertEqual(graph.invocations[0]["human_response"], "approved")

    def test_parser_accepts_resume_human_response(self):
        from langgraph_runner.cli import build_parser

        args = build_parser().parse_args(["resume", "--human-response", "approved"])

        self.assertEqual(args.command, "resume")
        self.assertEqual(args.human_response, "approved")


if __name__ == "__main__":
    unittest.main()
