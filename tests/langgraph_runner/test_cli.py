import json
import contextlib
import io
import os
import sys
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
        self.configs = []

    def invoke(self, state, config=None):
        self.invocations.append(state)
        self.configs.append(config)
        return state


class CapturingGraph:
    def __init__(self, graph, default_config=None):
        self.graph = graph
        self.default_config = default_config
        self.final_state = None
        self.configs = []

    def invoke(self, state, config=None):
        effective_config = config if config is not None else self.default_config
        self.configs.append(effective_config)
        self.final_state = self.graph.invoke(state, config=effective_config)
        return self.final_state


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
        self.assertEqual(graph.invocations[0]["counted_run_total"], 1)
        self.assertEqual(graph.invocations[0]["counted_run_remaining"], 1)
        self.assertNotIn("stop_after_current_pass", graph.invocations[0])

    def test_run_count_sets_counted_run_state(self):
        from langgraph_runner.cli import main

        root, config = configured_root("run_count")
        graph = FakeGraph()

        with patch("langgraph_runner.cli.build_graph", return_value=graph):
            result = main(["--repo-root", str(root), "--config", str(config), "run", "--count", "3"])

        self.assertEqual(result, 0)
        self.assertEqual(graph.invocations[0]["route"], "next_batch")
        self.assertEqual(graph.invocations[0]["counted_run_total"], 3)
        self.assertEqual(graph.invocations[0]["counted_run_remaining"], 3)
        self.assertEqual(graph.configs[0], {"recursion_limit": 60})

    def test_run_count_one_sets_recursion_limit(self):
        from langgraph_runner.cli import main

        root, config = configured_root("run_count_one_recursion_limit")
        graph = FakeGraph()

        with patch("langgraph_runner.cli.build_graph", return_value=graph):
            result = main(["--repo-root", str(root), "--config", str(config), "run", "--count", "1"])

        self.assertEqual(result, 0)
        self.assertEqual(graph.configs[0], {"recursion_limit": 20})

    def test_run_returns_nonzero_and_reports_artifacts_for_critical_batch_stop(self):
        from langgraph_runner.cli import main

        root, config = configured_root("run_critical_batch_stop")
        run_dir = root / "automation_artifacts" / "runs" / "manual"
        batch_error = run_dir / "batch_error.json"
        top_decision = run_dir / "top_decision.json"
        graph = FakeGraph()

        def invoke(state, config=None):
            graph.invocations.append(state)
            graph.configs.append(config)
            return {
                **state,
                "route": "stop",
                "top_decision": {
                    "decision": "stop",
                    "reason": "all candidates failed agent execution",
                    "anomaly_level": "critical",
                },
                "top_decision_path": str(top_decision),
                "errors": ["record_batch: all candidates failed agent execution"],
            }

        graph.invoke = invoke
        stderr = io.StringIO()

        with patch("langgraph_runner.cli.build_graph", return_value=graph):
            with contextlib.redirect_stderr(stderr):
                result = main(["--repo-root", str(root), "--config", str(config), "run", "--count", "1"])

        self.assertEqual(result, 1)
        diagnostic = stderr.getvalue()
        self.assertIn("all candidates failed agent execution", diagnostic)
        self.assertIn(str(top_decision), diagnostic)
        self.assertIn(str(batch_error), diagnostic)

    def test_resume_returns_nonzero_and_reports_artifacts_for_critical_batch_stop(self):
        from langgraph_runner.cli import main

        root, config = configured_root("resume_critical_batch_stop")
        run_dir = root / "automation_artifacts" / "runs" / "manual"
        batch_error = run_dir / "batch_error.json"
        top_decision = run_dir / "top_decision.json"
        graph = FakeGraph()

        def invoke(state, config=None):
            graph.invocations.append(state)
            graph.configs.append(config)
            return {
                **state,
                "route": "stop",
                "top_decision": {
                    "decision": "stop",
                    "reason": "all candidates failed agent execution",
                    "anomaly_level": "critical",
                },
                "top_decision_path": str(top_decision),
                "errors": ["record_batch: all candidates failed agent execution"],
            }

        graph.invoke = invoke
        stderr = io.StringIO()

        with patch("langgraph_runner.cli.build_graph", return_value=graph):
            with contextlib.redirect_stderr(stderr):
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

        self.assertEqual(result, 1)
        self.assertEqual(graph.invocations[0]["counted_run_total"], 1)
        diagnostic = stderr.getvalue()
        self.assertIn("all candidates failed agent execution", diagnostic)
        self.assertIn(str(top_decision), diagnostic)
        self.assertIn(str(batch_error), diagnostic)

    def test_parser_rejects_non_positive_run_count(self):
        from langgraph_runner.cli import build_parser

        with self.assertRaises(SystemExit):
            build_parser().parse_args(["run", "--count", "0"])

    def test_run_with_real_shell_graph_reports_agent_failure_without_recursion(self):
        from langgraph_runner.artifacts import ArtifactPaths
        from langgraph_runner.cli import main
        from langgraph_runner.graph import build_graph
        from langgraph_runner.state_store import StateStore
        from tests.langgraph_runner.test_graph import ExecutionFailureAgentRunner, write_workflow_fixture

        root, config = configured_root("run_real_shell_graph")
        config = write_workflow_fixture(root, batch_size=1)
        paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
        StateStore(paths, root / "docs" / "contract.md").initialize()
        graph = CapturingGraph(build_graph())
        fake_runner = ExecutionFailureAgentRunner(error_class="agent_process_failed", exit_code=2)
        stderr = io.StringIO()

        with patch("langgraph_runner.cli.build_graph", return_value=graph) as build_graph_mock:
            with patch("langgraph_runner.graph.AgentRunner", return_value=fake_runner):
                with contextlib.redirect_stderr(stderr):
                    result = main(["--repo-root", str(root), "--config", str(config), "run"])

        self.assertEqual(result, 1)
        build_graph_mock.assert_called_once_with()
        self.assertEqual(graph.configs[0], {"recursion_limit": 20})
        self.assertIn("all candidates failed agent execution", stderr.getvalue())

    def test_run_count_two_with_real_shell_graph_returns_without_recursion(self):
        from langgraph_runner.artifacts import ArtifactPaths
        from langgraph_runner.cli import main
        from langgraph_runner.graph import build_graph
        from langgraph_runner.state_store import StateStore
        from tests.langgraph_runner.test_graph import FakeAgentRunner, write_workflow_fixture

        root, config = configured_root("run_count_two_real_shell_graph")
        config = write_workflow_fixture(root, batch_size=1)
        paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
        StateStore(paths, root / "docs" / "contract.md").initialize()
        graph = CapturingGraph(build_graph(), default_config={"recursion_limit": 20})
        fake_runner = FakeAgentRunner()

        with patch("langgraph_runner.cli.build_graph", return_value=graph) as build_graph_mock:
            with patch("langgraph_runner.graph.AgentRunner", return_value=fake_runner):
                result = main(["--repo-root", str(root), "--config", str(config), "run", "--count", "2"])

        self.assertEqual(result, 0)
        build_graph_mock.assert_called_once_with()
        self.assertIsNotNone(graph.final_state)
        self.assertGreater(graph.configs[0]["recursion_limit"], 20)
        self.assertEqual(graph.final_state["route"], "stop")
        self.assertEqual(graph.final_state["counted_run_remaining"], 0)

    def test_run_count_one_local_backend_reaches_verification_without_codex_runner(self):
        from langgraph_runner.artifacts import ArtifactPaths
        from langgraph_runner.cli import main
        from langgraph_runner.graph import build_graph
        from langgraph_runner.state_store import StateStore
        from tests.langgraph_runner.test_graph import write_workflow_fixture

        root, _config = configured_root("run_count_one_local_backend")
        config = write_workflow_fixture(root, batch_size=1)
        config_data = json.loads(config.read_text(encoding="utf-8"))
        config_data["agent_backend"] = {"mode": "local_deterministic"}
        config.write_text(json.dumps(config_data, indent=2) + "\n", encoding="utf-8")
        paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
        StateStore(paths, root / "docs" / "contract.md").initialize()
        graph = CapturingGraph(build_graph())

        with patch("langgraph_runner.cli.build_graph", return_value=graph):
            with patch("langgraph_runner.graph.AgentRunner", side_effect=AssertionError("codex backend should not be used")):
                result = main(["--repo-root", str(root), "--config", str(config), "run", "--count", "1"])

        self.assertEqual(result, 0)
        self.assertIsNotNone(graph.final_state)
        candidate_id = graph.final_state["candidate_ids"][0]
        candidate_dir = paths.candidate_dir(candidate_id)
        self.assertTrue((candidate_dir / "verification.json").exists())
        self.assertFalse(list(paths.run_dir("manual").glob("agent_outputs/*/codex_home")))

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
        self.assertEqual(graph.invocations[0]["counted_run_total"], 1)
        self.assertEqual(graph.invocations[0]["counted_run_remaining"], 1)

    def test_parser_accepts_resume_human_response(self):
        from langgraph_runner.cli import build_parser

        args = build_parser().parse_args(["resume", "--human-response", "approved"])

        self.assertEqual(args.command, "resume")
        self.assertEqual(args.human_response, "approved")

    def test_parser_accepts_powershell_safe_smoke_command_as_single_value(self):
        from langgraph_runner.cli import build_parser

        smoke_command = f'& "{sys.executable}" -c "print(\'smoke\')"'

        args = build_parser().parse_args(
            [
                "production-run",
                "--artifact-root",
                "automation_artifacts/prod",
                "--eda-smoke-command",
                smoke_command,
            ]
        )

        self.assertEqual(args.command, "production-run")
        self.assertEqual(args.eda_smoke_command, smoke_command)

    def test_production_run_misplit_smoke_command_records_operator_command_error(self):
        from langgraph_runner.cli import main

        root, config = configured_root("production_misplit_smoke_command")

        with patch("langgraph_runner.cli.run_production_canary") as run_mock:
            with self.assertRaises(SystemExit) as context:
                main(
                    [
                        "--repo-root",
                        str(root),
                        "--config",
                        str(config),
                        "production-run",
                        "--artifact-root",
                        "automation_artifacts/prod",
                        "--eda-smoke-command",
                        "&",
                        str(Path(sys.executable)),
                        "-c",
                        "print('smoke')",
                    ]
                )

        self.assertEqual(context.exception.code, 2)
        run_mock.assert_not_called()
        failure_path = root / "automation_artifacts" / "prod" / "runs" / "manual" / "production_run_failure.json"
        self.assertTrue(failure_path.exists())
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        self.assertEqual(failure["error_class"], "operator_command_error")
        self.assertEqual(failure["checks"]["argument_parser"]["class"], "operator_command_error")
        self.assertIn("unrecognized arguments", failure["checks"]["argument_parser"]["details"])
        details = json.loads(failure["checks"]["argument_parser"]["details"])
        self.assertEqual(details["eda_smoke_command"], "&")
        self.assertEqual(details["unparsed_argv"], [str(Path(sys.executable)), "-c", "print('smoke')"])
        self.assertIn("unrecognized arguments", details["stderr"])

    def test_production_run_missing_smoke_command_value_records_operator_command_error(self):
        from langgraph_runner.cli import main

        root, config = configured_root("production_missing_smoke_value")

        with patch("langgraph_runner.cli.run_production_canary") as run_mock:
            with self.assertRaises(SystemExit) as context:
                main(
                    [
                        "--repo-root",
                        str(root),
                        "--config",
                        str(config),
                        "production-run",
                        "--artifact-root",
                        "automation_artifacts/prod",
                        "--eda-smoke-command",
                    ]
                )

        self.assertEqual(context.exception.code, 2)
        run_mock.assert_not_called()
        failure_path = root / "automation_artifacts" / "prod" / "runs" / "manual" / "production_run_failure.json"
        self.assertTrue(failure_path.exists())
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        self.assertEqual(failure["error_class"], "operator_command_error")
        details = json.loads(failure["checks"]["argument_parser"]["details"])
        self.assertIn("expected one argument", details["stderr"])
        self.assertIn("--eda-smoke-command", details["stderr"])

    def test_run_invalid_count_named_production_run_does_not_write_production_failure(self):
        from langgraph_runner.cli import main

        root, config = configured_root("run_invalid_count_named_production_run")

        with self.assertRaises(SystemExit) as context:
            main(["--repo-root", str(root), "--config", str(config), "run", "--count", "production-run"])

        self.assertEqual(context.exception.code, 2)
        self.assertFalse(
            (root / "automation_artifacts" / "prod" / "runs" / "manual" / "production_run_failure.json").exists()
        )

    def test_production_parser_failure_outside_artifact_root_does_not_mask_argparse_exit(self):
        from langgraph_runner.cli import main

        root, config = configured_root("production_parser_failure_outside_artifact_root")

        with self.assertRaises(SystemExit) as context:
            main(
                [
                    "--repo-root",
                    str(root),
                    "--config",
                    str(config),
                    "production-run",
                    "--artifact-root",
                    "../outside-prod",
                    "--eda-smoke-command",
                ]
            )

        self.assertEqual(context.exception.code, 2)
        self.assertFalse((root.parent / "outside-prod").exists())

    def test_production_parser_failure_honors_equals_style_root_and_artifact_options(self):
        from langgraph_runner.cli import main

        root, config = configured_root("production_equals_style_parser_failure")
        cwd = scratch_root("production_equals_style_parser_failure_cwd")
        original_cwd = Path.cwd()

        try:
            os.chdir(cwd)
            with self.assertRaises(SystemExit) as context:
                main(
                    [
                        f"--repo-root={root}",
                        f"--config={config}",
                        "production-run",
                        "--artifact-root=automation_artifacts/prod2",
                        "--eda-smoke-command",
                    ]
                )
        finally:
            os.chdir(original_cwd)

        self.assertEqual(context.exception.code, 2)
        intended_failure = root / "automation_artifacts" / "prod2" / "runs" / "manual" / "production_run_failure.json"
        self.assertTrue(intended_failure.exists())
        failure = json.loads(intended_failure.read_text(encoding="utf-8"))
        self.assertEqual(failure["artifact_root"], str(root / "automation_artifacts" / "prod2"))
        self.assertEqual(failure["config_path"], str(config))
        self.assertEqual(failure["error_class"], "operator_command_error")
        self.assertFalse((cwd / "automation_artifacts" / "prod2" / "runs" / "manual" / "production_run_failure.json").exists())


if __name__ == "__main__":
    unittest.main()
