import json
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


def scratch_root(case: str) -> Path:
    root = Path(__file__).resolve().parents[2] / ".test_tmp_langgraph_runner" / "production" / case
    path = root / uuid.uuid4().hex
    path.mkdir(mode=0o777, parents=True, exist_ok=True)
    return path


def write_repo_fixture(
    root: Path,
    *,
    min_interval_seconds: int = 0,
    timeout_seconds: int = 1800,
    write_contract: bool = True,
) -> Path:
    docs = root / "docs"
    docs.mkdir(mode=0o777, parents=True, exist_ok=True)
    if write_contract:
        (docs / "top-coordinator-contract.md").write_text("# Contract\n", encoding="utf-8")

    amptest = root / "amptest"
    amptest.mkdir(mode=0o777, parents=True, exist_ok=True)
    (amptest / "dummy_neural_amp.scs").write_text(
        "simulator lang=spectre\n"
        "subckt dummy_neural_amp GND VDD VIN VOUT VREF\n"
        "R1 VDD VOUT 10k\n"
        "ends dummy_neural_amp\n",
        encoding="utf-8",
    )
    (amptest / "devices.csv").write_text("name,type,count,include_in_ppa\nR1,resistor,1,true\n", encoding="utf-8")
    (amptest / "config.json").write_text(
        json.dumps(
            {
                "dut_subckt": "dummy_neural_amp",
                "dut_pins_order": ["GND", "VDD", "VIN", "VOUT", "VREF"],
                "input_files": {"devices_csv": "devices.csv"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    config = {
        "artifact_root": "automation_artifacts",
        "contract_path": "docs/top-coordinator-contract.md",
        "amptest_dir": "amptest",
        "dut_netlist": "amptest/dummy_neural_amp.scs",
        "devices_csv": "amptest/devices.csv",
        "amptest_config": "amptest/config.json",
        "candidate_generation_batch_size": 3,
        "max_active_primes_per_subagent": 2,
        "max_total_primes_per_subagent": 4,
        "agent_timeouts_seconds": {"subagent": 1200, "prime": 600, "reviewer": 300, "top": 300},
        "verifier": {
            "command": "python fake_verifier.py",
            "timeout_seconds": timeout_seconds,
            "min_interval_seconds": min_interval_seconds,
            "required_outputs": [
                "verification.json",
                "ppa_metrics.json",
                "ppa_report.log",
                "spectre_ac.log",
                "spectre_tran.log",
            ],
        },
    }
    config_path = root / "runner_config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config_path


def update_config(path: Path, **updates) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(updates)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class CommandRecorder:
    def __init__(self):
        self.commands = []

    def __call__(self, command, *, cwd: Path, shell: bool = False, timeout: int | None = None):
        from langgraph_runner.production import CommandResult

        self.commands.append((command, cwd, shell, timeout))
        return CommandResult(command=str(command), returncode=0, stdout="ok\n", stderr="")


class FailingGitStatusRecorder(CommandRecorder):
    def __call__(self, command, *, cwd: Path, shell: bool = False, timeout: int | None = None):
        from langgraph_runner.production import CommandResult

        self.commands.append((command, cwd, shell, timeout))
        if command == ["git", "status", "--short"]:
            return CommandResult(command=str(command), returncode=1, stdout="", stderr="not a git repo\n")
        return CommandResult(command=str(command), returncode=0, stdout="ok\n", stderr="")


class FakeGraph:
    def __init__(self):
        self.invocations = []

    def invoke(self, state, config=None):
        self.invocations.append((state, config))
        artifact_root = Path(state["state_path"]).parent
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        candidate_dir = artifact_root / "candidates" / candidate_id
        candidate_dir.mkdir(parents=True, exist_ok=True)
        (candidate_dir / "verdict.json").write_text(
            json.dumps({"candidate_id": candidate_id, "status": "rejected"}) + "\n",
            encoding="utf-8",
        )
        return {
            **state,
            "route": "stop",
            "events": ["load_context", "record_batch", "route_next"],
            "candidate_ids": [candidate_id],
            "candidate_evaluations": [{"candidate_id": candidate_id, "status": "rejected", "metrics": {}}],
            "promoted_candidate_id": None,
        }


class TestProductionRun(unittest.TestCase):
    def test_prepare_production_config_creates_one_candidate_isolated_config(self):
        from langgraph_runner.production import prepare_production_config

        root = scratch_root("prepare_config")
        base_config = write_repo_fixture(root, min_interval_seconds=0)

        config_path = prepare_production_config(
            repo_root=root,
            base_config_path=base_config,
            artifact_root="automation_artifacts/prod",
            timestamp="20260605-120000",
        )

        self.assertEqual(
            config_path,
            root / "automation_artifacts" / "operator_configs" / "prod-run-20260605-120000.json",
        )
        data = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(data["artifact_root"], "automation_artifacts/prod")
        self.assertEqual(data["candidate_generation_batch_size"], 1)
        self.assertEqual(data["verifier"]["min_interval_seconds"], 30)
        self.assertEqual(data["verifier"]["timeout_seconds"], 1800)
        self.assertEqual(data["contract_path"], "docs/top-coordinator-contract.md")

    def test_prepare_production_config_enforces_contract_and_verifier_timeout_rules(self):
        from langgraph_runner.production import prepare_production_config

        timeout_root = scratch_root("prepare_config_timeout")
        low_timeout_config = write_repo_fixture(timeout_root, timeout_seconds=60)

        config_path = prepare_production_config(
            repo_root=timeout_root,
            base_config_path=low_timeout_config,
            artifact_root="automation_artifacts/prod",
            timestamp="20260605-120000",
        )

        data = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(data["verifier"]["timeout_seconds"], 1800)

        contract_root = scratch_root("prepare_config_contract")
        alternate_contract = contract_root / "docs" / "alternate-contract.md"
        base_config = write_repo_fixture(contract_root)
        alternate_contract.write_text("alternate\n", encoding="utf-8")
        update_config(base_config, contract_path="docs/alternate-contract.md")

        with self.assertRaisesRegex(ValueError, "contract_path"):
            prepare_production_config(
                repo_root=contract_root,
                base_config_path=base_config,
                artifact_root="automation_artifacts/prod",
                timestamp="20260605-120000",
            )

    def test_prepare_production_config_rejects_default_artifact_root(self):
        from langgraph_runner.production import prepare_production_config

        root = scratch_root("reject_default_artifact_root")
        base_config = write_repo_fixture(root)

        with self.assertRaisesRegex(ValueError, "isolated artifact root"):
            prepare_production_config(
                repo_root=root,
                base_config_path=base_config,
                artifact_root="automation_artifacts",
                timestamp="20260605-120000",
            )

    def test_prepare_production_config_rejects_overwriting_base_config(self):
        from langgraph_runner.production import prepare_production_config

        root = scratch_root("reject_config_output_base")
        base_config = write_repo_fixture(root)

        with self.assertRaisesRegex(ValueError, "production config output"):
            prepare_production_config(
                repo_root=root,
                base_config_path=base_config,
                artifact_root="automation_artifacts/prod",
                config_output=base_config,
                timestamp="20260605-120000",
            )

    def test_create_production_backup_copies_canonical_files_and_manifest(self):
        from langgraph_runner.config import load_runner_config
        from langgraph_runner.production import create_production_backup, prepare_production_config

        root = scratch_root("backup")
        base_config = write_repo_fixture(root)
        config_path = prepare_production_config(
            repo_root=root,
            base_config_path=base_config,
            artifact_root="automation_artifacts/prod",
            timestamp="20260605-120000",
        )
        config = load_runner_config(config_path)
        artifact_root = root / config.artifact_root
        (artifact_root / "candidates" / "cid").mkdir(parents=True, exist_ok=True)
        (artifact_root / "candidates" / "cid" / "proposal.json").write_text("{}\n", encoding="utf-8")
        (artifact_root / "workspaces").mkdir(parents=True, exist_ok=True)
        (artifact_root / "runs").mkdir(parents=True, exist_ok=True)
        (artifact_root / "state.json").write_text("{}\n", encoding="utf-8")
        (artifact_root / "ledger.jsonl").write_text("{}\n", encoding="utf-8")

        backup = create_production_backup(
            repo_root=root,
            config_path=config_path,
            config=config,
            timestamp="20260605-120100",
        )

        self.assertTrue((backup / "repo_files" / "amptest" / "dummy_neural_amp.scs").exists())
        self.assertTrue((backup / "repo_files" / "amptest" / "devices.csv").exists())
        self.assertTrue((backup / "artifact_root" / "state.json").exists())
        self.assertTrue((backup / "artifact_root" / "ledger.jsonl").exists())
        self.assertTrue((backup / "artifact_root" / "candidates" / "cid" / "proposal.json").exists())
        self.assertTrue((backup / "operator_config" / config_path.name).exists())
        manifest = json.loads((backup / "backup_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["config_path"], str(config_path))
        self.assertIn("amptest/dummy_neural_amp.scs", manifest["copied"])

    def test_create_production_backup_rejects_symlinked_artifacts(self):
        from langgraph_runner.config import load_runner_config
        from langgraph_runner.production import create_production_backup, prepare_production_config

        root = scratch_root("backup_symlink")
        base_config = write_repo_fixture(root)
        config_path = prepare_production_config(
            repo_root=root,
            base_config_path=base_config,
            artifact_root="automation_artifacts/prod",
            timestamp="20260605-120000",
        )
        config = load_runner_config(config_path)
        artifact_root = root / config.artifact_root
        candidate_dir = artifact_root / "candidates" / "cid"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        outside = scratch_root("backup_symlink_outside") / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        link = candidate_dir / "outside-link.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is not available in this environment")

        with self.assertRaisesRegex(ValueError, "link"):
            create_production_backup(
                repo_root=root,
                config_path=config_path,
                config=config,
                timestamp="20260605-120100",
            )

    def test_create_production_backup_rejects_symlinked_repo_file(self):
        from langgraph_runner.config import load_runner_config
        from langgraph_runner.production import create_production_backup, prepare_production_config

        root = scratch_root("backup_repo_file_symlink")
        base_config = write_repo_fixture(root)
        linked_devices = root / "amptest" / "linked_devices.csv"
        try:
            linked_devices.symlink_to(root / "amptest" / "devices.csv")
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is not available in this environment")
        update_config(base_config, devices_csv="amptest/linked_devices.csv")
        config_path = prepare_production_config(
            repo_root=root,
            base_config_path=base_config,
            artifact_root="automation_artifacts/prod",
            timestamp="20260605-120000",
        )
        config = load_runner_config(config_path)

        with self.assertRaisesRegex(ValueError, "link"):
            create_production_backup(
                repo_root=root,
                config_path=config_path,
                config=config,
                timestamp="20260605-120100",
            )

    def test_run_production_canary_persists_config_preflight_failure(self):
        from langgraph_runner.production import ProductionRunError, run_production_canary

        root = scratch_root("config_preflight_failure")
        alternate_contract = root / "docs" / "alternate-contract.md"
        base_config = write_repo_fixture(root)
        alternate_contract.write_text("alternate\n", encoding="utf-8")
        update_config(base_config, contract_path="docs/alternate-contract.md")

        with self.assertRaisesRegex(ProductionRunError, "contract_path"):
            run_production_canary(
                repo_root=root,
                base_config_path=base_config,
                artifact_root="automation_artifacts/prod",
                timestamp="20260605-120000",
                eda_signoff="Verifier owner approved this shell environment.",
                command_runner=CommandRecorder(),
                graph_factory=FakeGraph,
            )

        failure_path = root / "automation_artifacts" / "prod" / "runs" / "manual" / "production_run_failure.json"
        self.assertTrue(failure_path.exists())
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        self.assertIn("contract_path", failure["error"])

    def test_run_production_canary_requires_eda_evidence_before_graph(self):
        from langgraph_runner.production import ProductionRunError, run_production_canary

        root = scratch_root("requires_eda")
        base_config = write_repo_fixture(root)
        graph = FakeGraph()

        with self.assertRaisesRegex(ProductionRunError, "EDA smoke"):
            run_production_canary(
                repo_root=root,
                base_config_path=base_config,
                artifact_root="automation_artifacts/prod",
                timestamp="20260605-120000",
                command_runner=CommandRecorder(),
                graph_factory=lambda: graph,
            )

        self.assertEqual(graph.invocations, [])
        failure_path = root / "automation_artifacts" / "prod" / "runs" / "manual" / "production_run_failure.json"
        self.assertTrue(failure_path.exists())
        failure = json.loads(failure_path.read_text(encoding="utf-8"))
        self.assertEqual(failure["checks"]["eda_smoke"]["status"], "failed")

    def test_run_production_canary_stops_when_git_status_fails(self):
        from langgraph_runner.production import ProductionRunError, run_production_canary

        root = scratch_root("git_status_fails")
        base_config = write_repo_fixture(root)
        graph = FakeGraph()

        with self.assertRaisesRegex(ProductionRunError, "git status"):
            run_production_canary(
                repo_root=root,
                base_config_path=base_config,
                artifact_root="automation_artifacts/prod",
                timestamp="20260605-120000",
                eda_signoff="Verifier owner approved this shell environment.",
                command_runner=FailingGitStatusRecorder(),
                graph_factory=lambda: graph,
            )

        self.assertEqual(graph.invocations, [])

    def test_run_production_canary_checks_contract_before_state_initialization(self):
        from langgraph_runner.production import ProductionRunError, run_production_canary

        root = scratch_root("contract_before_init")
        base_config = write_repo_fixture(root, write_contract=False)
        graph = FakeGraph()

        with self.assertRaisesRegex(ProductionRunError, "contract"):
            run_production_canary(
                repo_root=root,
                base_config_path=base_config,
                artifact_root="automation_artifacts/prod",
                timestamp="20260605-120000",
                eda_signoff="Verifier owner approved this shell environment.",
                command_runner=CommandRecorder(),
                graph_factory=lambda: graph,
            )

        self.assertFalse((root / "automation_artifacts" / "prod" / "state.json").exists())
        self.assertFalse((root / "automation_artifacts" / "prod" / "ledger.jsonl").exists())
        failure_path = root / "automation_artifacts" / "prod" / "runs" / "manual" / "production_run_failure.json"
        self.assertTrue(failure_path.exists())
        self.assertEqual(json.loads(failure_path.read_text(encoding="utf-8"))["checks"]["contract"]["status"], "failed")

    def test_run_production_canary_rejects_invalid_devices_csv_before_graph(self):
        from langgraph_runner.production import ProductionRunError, run_production_canary

        root = scratch_root("invalid_devices_csv")
        base_config = write_repo_fixture(root)
        (root / "amptest" / "devices.csv").write_text(
            "name,type,count,include_in_ppa\nXOP,opamp,1,true\n",
            encoding="utf-8",
        )
        graph = FakeGraph()

        with self.assertRaisesRegex(ProductionRunError, "contract"):
            run_production_canary(
                repo_root=root,
                base_config_path=base_config,
                artifact_root="automation_artifacts/prod",
                timestamp="20260605-120000",
                eda_signoff="Verifier owner approved this shell environment.",
                command_runner=CommandRecorder(),
                graph_factory=lambda: graph,
            )

        self.assertEqual(graph.invocations, [])
        failure_path = root / "automation_artifacts" / "prod" / "runs" / "manual" / "production_run_failure.json"
        self.assertTrue(failure_path.exists())

    def test_run_production_canary_runs_preflights_backup_and_graph_with_stop_route(self):
        from langgraph_runner.production import run_production_canary

        root = scratch_root("run_canary")
        base_config = write_repo_fixture(root)
        commands = CommandRecorder()
        graph = FakeGraph()

        result = run_production_canary(
            repo_root=root,
            base_config_path=base_config,
            artifact_root="automation_artifacts/prod",
            timestamp="20260605-120000",
            eda_signoff="Verifier owner approved this shell environment.",
            command_runner=commands,
            graph_factory=lambda: graph,
        )

        self.assertEqual(graph.invocations[0][0]["route"], "stop")
        self.assertEqual(graph.invocations[0][0]["run_id"], "manual")
        self.assertEqual(json.loads(result.config_path.read_text(encoding="utf-8"))["candidate_generation_batch_size"], 1)
        self.assertTrue(result.backup_dir.exists())
        self.assertTrue(result.summary_path.exists())
        summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
        self.assertEqual(summary["config_path"], str(result.config_path))
        self.assertEqual(summary["backup_dir"], str(result.backup_dir))
        self.assertIn("Verifier owner approved", summary["checks"]["eda_smoke"]["details"])
        self.assertTrue(any(command[0][:3] == [sys.executable, "-m", "unittest"] for command in commands.commands))
        self.assertTrue(any(command[0] == ["codex", "exec", "--help"] for command in commands.commands))

    def test_cli_dispatches_production_run(self):
        from langgraph_runner.cli import main

        root = scratch_root("cli")
        base_config = write_repo_fixture(root)

        with patch("langgraph_runner.cli.run_production_canary") as run_mock:
            result = main(
                [
                    "--repo-root",
                    str(root),
                    "--config",
                    str(base_config),
                    "production-run",
                    "--artifact-root",
                    "automation_artifacts/prod",
                    "--eda-signoff",
                    "approved",
                ]
            )

        self.assertEqual(result, 0)
        self.assertEqual(run_mock.call_args.kwargs["repo_root"], root.resolve())
        self.assertEqual(run_mock.call_args.kwargs["base_config_path"], base_config.resolve())
        self.assertEqual(run_mock.call_args.kwargs["artifact_root"], "automation_artifacts/prod")
        self.assertEqual(run_mock.call_args.kwargs["eda_signoff"], "approved")


if __name__ == "__main__":
    unittest.main()
