import json
import shutil
import uuid
import unittest
import io
from pathlib import Path
from contextlib import nullcontext, redirect_stdout
from unittest import mock

from langgraph_workflow import cli
from langgraph_workflow.backend import BackendConfigError, BackendError, EdaSshBackend, MockBackend, RunOutcome
from langgraph_workflow.state import WorkflowConfig
from langgraph_workflow.workflow import StateGraph, build_state_graph, run_amptest_node, run_mock_workflow_once
from tests.test_validation import VALID_SEED


class WorkspaceTempDir:
    def __enter__(self):
        self.path = Path.cwd() / ".test_tmp" / uuid.uuid4().hex
        self.path.mkdir(parents=True, exist_ok=False)
        return str(self.path)

    def __exit__(self, exc_type, exc, tb):
        shutil.rmtree(self.path, ignore_errors=True)


class WorkflowBackendTests(unittest.TestCase):
    def test_mock_backend_copies_fixture_outputs(self):
        with WorkspaceTempDir() as tmp:
            fixture = Path(tmp) / "fixture"
            fixture.mkdir()
            (fixture / "ppa_metrics.json").write_text(json.dumps({"performance_nrmse_combined": 0.2}))
            (fixture / "ppa_report.log").write_text("report\n")
            artifact_dir = Path(tmp) / "trial"
            artifact_dir.mkdir()

            outcome = MockBackend(fixture).run(artifact_dir)

            self.assertEqual(0, outcome.exit_code)
            self.assertTrue((artifact_dir / "ppa_metrics.json").exists())
            self.assertTrue((artifact_dir / "ppa_report.log").exists())

    def test_eda_ssh_backend_requires_remote_config(self):
        backend = EdaSshBackend({"host": "", "user": "student", "base_dir": "/tmp/work"})

        with self.assertRaises(BackendConfigError):
            backend.validate()

    def test_eda_ssh_backend_rejects_missing_identity_file(self):
        with WorkspaceTempDir() as tmp:
            amp = Path(tmp) / "amptest"
            amp.mkdir()
            (amp / "ppa_wrapper.py").write_text("")
            (amp / "ppa_wrapper_core.py").write_text("")
            backend = EdaSshBackend(
                {
                    "host": "eda",
                    "user": "student",
                    "base_dir": "/tmp/work",
                    "identity_file": str(Path(tmp) / "missing_key"),
                },
                amptest_local_dir=amp,
            )

            with self.assertRaises(BackendConfigError):
                backend.validate()

    def test_eda_ssh_backend_builds_open_ssh_options_for_ssh_and_scp(self):
        with WorkspaceTempDir() as tmp:
            key = Path(tmp) / "eda_key"
            config = Path(tmp) / "ssh_config"
            known_hosts = Path(tmp) / "known_hosts"
            key.write_text("private key")
            config.write_text("Host eda\n")
            known_hosts.write_text("eda ssh-ed25519 AAAA\n")
            backend = EdaSshBackend(
                {
                    "host": "eda",
                    "user": "student",
                    "base_dir": "/tmp/work",
                    "port": 2222,
                    "identity_file": str(key),
                    "ssh_config": str(config),
                    "connect_timeout_s": 7,
                    "strict_host_key_checking": "accept-new",
                    "known_hosts_file": str(known_hosts),
                    "server_alive_interval_s": 30,
                    "server_alive_count_max": 2,
                }
            )

            ssh_cmd = backend._ssh_command("echo ok")
            scp_cmd = backend._scp_command(["local.txt"], "student@eda:/tmp/work/")

            self.assertEqual("ssh", ssh_cmd[0])
            self.assertIn("-p", ssh_cmd)
            self.assertIn("2222", ssh_cmd)
            self.assertIn("-i", ssh_cmd)
            self.assertIn(str(key), ssh_cmd)
            self.assertIn("-F", ssh_cmd)
            self.assertIn(str(config), ssh_cmd)
            self.assertIn("BatchMode=yes", " ".join(ssh_cmd))
            self.assertIn("ConnectTimeout=7", " ".join(ssh_cmd))
            self.assertIn("StrictHostKeyChecking=accept-new", " ".join(ssh_cmd))
            self.assertIn(f"UserKnownHostsFile={known_hosts}", " ".join(ssh_cmd))
            self.assertIn("ServerAliveInterval=30", " ".join(ssh_cmd))
            self.assertIn("ServerAliveCountMax=2", " ".join(ssh_cmd))
            self.assertEqual("scp", scp_cmd[0])
            self.assertIn("-P", scp_cmd)
            self.assertIn("2222", scp_cmd)
            self.assertIn("BatchMode=yes", " ".join(scp_cmd))

    def test_eda_ssh_backend_daily_rate_limit_blocks_second_trial(self):
        with WorkspaceTempDir() as tmp:
            artifact_dir = Path(tmp) / "runs" / "seed_0" / "trial_0"
            artifact_dir.mkdir(parents=True)
            backend = EdaSshBackend(
                {"host": "eda", "user": "student", "base_dir": "/tmp/work"},
                min_interval_s=0,
                daily_max_trials=1,
            )

            backend._enforce_rate_limit(artifact_dir)

            with self.assertRaises(BackendError):
                backend._enforce_rate_limit(artifact_dir)

    def test_eda_ssh_backend_lock_does_not_create_workspace_lock_file(self):
        with WorkspaceTempDir() as tmp:
            artifact_dir = Path(tmp) / "runs" / "seed_0" / "trial_0"
            artifact_dir.mkdir(parents=True)
            backend = EdaSshBackend({"host": "eda", "user": "student", "base_dir": "/tmp/work"})
            workspace_lock = Path(tmp) / "runs" / "cadence_execution.lock"

            with backend._cadence_execution_lock(artifact_dir):
                self.assertFalse(workspace_lock.exists())

            self.assertFalse(workspace_lock.exists())

    def test_eda_ssh_download_uses_configured_timeout(self):
        backend = EdaSshBackend(
            {"host": "eda", "user": "student", "base_dir": "/tmp/work"},
            timeout_s=123,
        )
        with WorkspaceTempDir() as tmp:
            artifact_dir = Path(tmp)

            with mock.patch("langgraph_workflow.backend.subprocess.run") as run:
                run.return_value = mock.Mock(stdout="", stderr="")
                backend._download_file("/tmp/work/seed/trial", "ppa_metrics.json", artifact_dir)

            self.assertEqual(123, run.call_args.kwargs["timeout"])

    def test_eda_ssh_backend_runs_remote_command_with_bash_lc_and_pre_command(self):
        with WorkspaceTempDir() as tmp:
            root = Path(tmp)
            amp = root / "amptest"
            amp.mkdir()
            (amp / "ppa_wrapper.py").write_text("")
            (amp / "ppa_wrapper_core.py").write_text("")
            artifact_dir = root / "runs" / "seed_0" / "trial_0"
            artifact_dir.mkdir(parents=True)
            for filename in ("candidate.scs", "devices.csv", "config.json", "trial_metadata.json"):
                (artifact_dir / filename).write_text("")
            backend = EdaSshBackend(
                {
                    "host": "eda",
                    "user": "student",
                    "base_dir": "/tmp/work",
                    "pre_command": "source /cadence/setup.sh",
                },
                amptest_local_dir=amp,
                min_interval_s=0,
            )

            with (
                mock.patch.object(backend, "_cadence_execution_lock", return_value=nullcontext()),
                mock.patch.object(backend, "_enforce_rate_limit"),
                mock.patch("langgraph_workflow.backend.subprocess.run") as run,
            ):
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                backend.run(artifact_dir)

            ssh_commands = [call.args[0] for call in run.call_args_list if call.args[0][0] == "ssh"]
            remote_run = [cmd for cmd in ssh_commands if "bash -lc" in cmd[-1]][0]
            self.assertIn("source /cadence/setup.sh", remote_run[-1])
            self.assertIn("cd /tmp/work/seed_0/trial_0", remote_run[-1])
            self.assertIn("remote_stdout.log", remote_run[-1])
            self.assertIn("remote_stderr.log", remote_run[-1])

    def test_eda_ssh_backend_preserves_failed_remote_stdout_stderr_logs(self):
        with WorkspaceTempDir() as tmp:
            root = Path(tmp)
            amp = root / "amptest"
            amp.mkdir()
            (amp / "ppa_wrapper.py").write_text("")
            (amp / "ppa_wrapper_core.py").write_text("")
            artifact_dir = root / "runs" / "seed_0" / "trial_0"
            artifact_dir.mkdir(parents=True)
            for filename in ("candidate.scs", "devices.csv", "config.json", "trial_metadata.json"):
                (artifact_dir / filename).write_text("")
            backend = EdaSshBackend(
                {"host": "eda", "user": "student", "base_dir": "/tmp/work"},
                amptest_local_dir=amp,
                min_interval_s=0,
            )

            def fake_run(cmd, **_kwargs):
                if cmd[0] == "ssh" and "bash -lc" in cmd[-1]:
                    return mock.Mock(returncode=42, stdout="ssh stdout\n", stderr="ssh stderr\n")
                if cmd[0] == "scp" and "remote_stdout.log" in cmd[-2]:
                    Path(cmd[-1]).write_text("remote stdout\n")
                    return mock.Mock(returncode=0, stdout="", stderr="")
                if cmd[0] == "scp" and "remote_stderr.log" in cmd[-2]:
                    Path(cmd[-1]).write_text("remote stderr\n")
                    return mock.Mock(returncode=0, stdout="", stderr="")
                if cmd[0] == "scp" and cmd[-2].startswith("student@eda:"):
                    raise subprocess.CalledProcessError(1, cmd)
                return mock.Mock(returncode=0, stdout="", stderr="")

            import subprocess

            with (
                mock.patch.object(backend, "_cadence_execution_lock", return_value=nullcontext()),
                mock.patch.object(backend, "_enforce_rate_limit"),
                mock.patch("langgraph_workflow.backend.subprocess.run", side_effect=fake_run),
            ):
                outcome = backend.run(artifact_dir)

            self.assertEqual(42, outcome.exit_code)
            self.assertIn("ssh stdout", outcome.stdout)
            self.assertIn("remote stdout", outcome.stdout)
            self.assertIn("ssh stderr", outcome.stderr)
            self.assertIn("remote stderr", outcome.stderr)
            self.assertTrue((artifact_dir / "remote_stdout.log").exists())
            self.assertTrue((artifact_dir / "remote_stderr.log").exists())

    def test_eda_ssh_backend_optional_download_timeout_does_not_hide_remote_failure_logs(self):
        with WorkspaceTempDir() as tmp:
            root = Path(tmp)
            amp = root / "amptest"
            amp.mkdir()
            (amp / "ppa_wrapper.py").write_text("")
            (amp / "ppa_wrapper_core.py").write_text("")
            artifact_dir = root / "runs" / "seed_0" / "trial_0"
            artifact_dir.mkdir(parents=True)
            for filename in ("candidate.scs", "devices.csv", "config.json", "trial_metadata.json"):
                (artifact_dir / filename).write_text("")
            backend = EdaSshBackend(
                {"host": "eda", "user": "student", "base_dir": "/tmp/work"},
                amptest_local_dir=amp,
                min_interval_s=0,
            )

            def fake_run(cmd, **_kwargs):
                if cmd[0] == "ssh" and "bash -lc" in cmd[-1]:
                    return mock.Mock(returncode=42, stdout="ssh stdout\n", stderr="ssh stderr\n")
                if cmd[0] == "scp" and "ppa_metrics.json" in cmd[-2]:
                    raise subprocess.TimeoutExpired(cmd, 123)
                if cmd[0] == "scp" and "remote_stdout.log" in cmd[-2]:
                    Path(cmd[-1]).write_text("remote stdout\n")
                    return mock.Mock(returncode=0, stdout="", stderr="")
                if cmd[0] == "scp" and "remote_stderr.log" in cmd[-2]:
                    Path(cmd[-1]).write_text("remote stderr\n")
                    return mock.Mock(returncode=0, stdout="", stderr="")
                if cmd[0] == "scp" and cmd[-2].startswith("student@eda:"):
                    raise subprocess.CalledProcessError(1, cmd)
                return mock.Mock(returncode=0, stdout="", stderr="")

            import subprocess

            with (
                mock.patch.object(backend, "_cadence_execution_lock", return_value=nullcontext()),
                mock.patch.object(backend, "_enforce_rate_limit"),
                mock.patch("langgraph_workflow.backend.subprocess.run", side_effect=fake_run),
            ):
                outcome = backend.run(artifact_dir)

            self.assertEqual(42, outcome.exit_code)
            self.assertIn("remote stdout", outcome.stdout)
            self.assertIn("remote stderr", outcome.stderr)

    def test_cli_ssh_check_prints_json_success(self):
        with WorkspaceTempDir() as tmp:
            path = Path(tmp) / "workflow.json"
            path.write_text(
                json.dumps(
                    {
                        "backend": "eda_ssh",
                        "amptest_local_dir": "amptest",
                        "remote": {"host": "eda", "user": "student", "base_dir": "/tmp/work"},
                    }
                )
            )

            with mock.patch("langgraph_workflow.cli.EdaSshBackend") as backend_cls:
                backend_cls.return_value.check.return_value = {"ok": True, "checks": [{"name": "ssh", "ok": True}]}
                out = io.StringIO()
                with redirect_stdout(out):
                    exit_code = cli.main(["ssh-check", "--config", str(path)])

            self.assertEqual(0, exit_code)
            payload = json.loads(out.getvalue())
            self.assertTrue(payload["ok"])

    def test_run_amptest_node_preserves_downloaded_files_in_remote_run_state(self):
        with WorkspaceTempDir() as tmp:
            artifact_dir = Path(tmp) / "runs" / "seed_0" / "trial_0"
            artifact_dir.mkdir(parents=True)
            downloaded = [artifact_dir / "remote_stderr.log"]
            backend = mock.Mock()
            backend.run.return_value = RunOutcome(
                exit_code=1,
                stdout="",
                stderr="remote stderr",
                downloaded_files=downloaded,
            )
            state = {
                "trial_results": [
                    {
                        "trial_id": "trial_0",
                        "seed_id": "seed_0",
                        "params": {"x": 1},
                        "status": "rendered",
                        "metrics": None,
                        "objective": None,
                        "artifact_dir": str(artifact_dir),
                        "error": None,
                    }
                ]
            }

            result = run_amptest_node(state, backend)

            self.assertEqual([str(path) for path in downloaded], result["remote_run"]["downloaded_files"])

    @unittest.skipIf(StateGraph is None, "langgraph is not installed")
    def test_smoke_failure_repairs_seed_before_parameter_optimization(self):
        repaired_seed = {
            **VALID_SEED,
            "seed_id": f"{VALID_SEED['seed_id']}_repair1",
            "topology_name": "bjt_bandpass_repaired",
        }

        class SmokeThenPassBackend:
            def __init__(self):
                self.calls = 0

            def run(self, artifact_dir):
                self.calls += 1
                artifact_path = Path(artifact_dir)
                if self.calls == 1:
                    (artifact_path / "ppa_report.log").write_text("netlist failed\n")
                    (artifact_path / "spectre_ac.log").write_text("unknown model bad_cell\n")
                    return RunOutcome(1, "stdout failed", "stderr failed", [])
                metrics = {
                    "performance_nrmse_combined": 0.1,
                    "area_power": {"area_total_p": 100.0, "power_score_basis_w": 1e-3},
                    "ac": {"midband_gain_db": 40.0, "lower_3db_hz": 10.0, "upper_3db_hz": 20000.0},
                    "tran": {"vout_ac_peak_to_peak_v": 0.2, "vout_mean_v": 2.5, "thd_db": -50.0},
                }
                (artifact_path / "ppa_metrics.json").write_text(json.dumps(metrics))
                return RunOutcome(0, "stdout repaired", "", [artifact_path / "ppa_metrics.json"])

        repair_inputs = []

        def repair_provider(state):
            repair_inputs.append(state["trial_results"][-1]["error"])
            return repaired_seed

        with WorkspaceTempDir() as tmp:
            root = Path(tmp)
            cfg = WorkflowConfig(
                backend="mock",
                run_root=root / "runs",
                amptest_local_dir=Path("amptest"),
                optuna_storage=None,
                max_seeds=2,
                max_trials_per_seed=1,
                max_seed_repair_attempts=2,
                objective_target=0.0,
            )
            backend = SmokeThenPassBackend()

            graph = build_state_graph(
                cfg,
                seed_provider=lambda _state: [VALID_SEED],
                repair_provider=repair_provider,
                backend=backend,
            )
            result = graph.compile().invoke({})

        self.assertEqual(2, backend.calls)
        self.assertEqual([VALID_SEED["seed_id"], repaired_seed["seed_id"]], [seed["seed_id"] for seed in result["seeds"]])
        self.assertEqual([VALID_SEED["seed_id"], repaired_seed["seed_id"]], [trial["seed_id"] for trial in result["trial_results"]])
        self.assertEqual({repaired_seed["seed_id"]: True}, result["seed_smoke_passed"])
        self.assertIn(VALID_SEED["seed_id"], result["abandoned_seed_ids"])
        self.assertEqual(0, result["consecutive_smoke_failures"])
        self.assertEqual(["simulation failed or missing ppa_metrics.json"], repair_inputs)

    @unittest.skipIf(StateGraph is None, "langgraph is not installed")
    def test_repair_attempt_cap_stops_without_infinite_failures(self):
        class AlwaysFailBackend:
            def __init__(self):
                self.calls = 0

            def run(self, artifact_dir):
                self.calls += 1
                artifact_path = Path(artifact_dir)
                (artifact_path / "ppa_report.log").write_text(f"failure {self.calls}\n")
                return RunOutcome(1, "", f"stderr {self.calls}", [])

        repair_seed = {
            **VALID_SEED,
            "seed_id": f"{VALID_SEED['seed_id']}_repair1",
            "topology_name": "still_failing_repair",
        }

        with WorkspaceTempDir() as tmp:
            cfg = WorkflowConfig(
                backend="mock",
                run_root=Path(tmp) / "runs",
                amptest_local_dir=Path("amptest"),
                optuna_storage=None,
                max_seeds=2,
                max_trials_per_seed=1,
                max_seed_repair_attempts=1,
                max_consecutive_smoke_failures=6,
                objective_target=0.0,
            )
            backend = AlwaysFailBackend()
            graph = build_state_graph(
                cfg,
                seed_provider=lambda _state: [VALID_SEED],
                repair_provider=lambda _state: repair_seed,
                backend=backend,
            )
            result = graph.compile().invoke({})

        self.assertEqual(2, backend.calls)
        self.assertEqual("stop", result["next_route"])
        self.assertEqual(2, result["consecutive_smoke_failures"])
        self.assertIn(VALID_SEED["seed_id"], result["abandoned_seed_ids"])
        self.assertIn(repair_seed["seed_id"], result["abandoned_seed_ids"])

    @unittest.skipIf(StateGraph is None, "langgraph is not installed")
    def test_static_invalid_seed_skips_backend_and_uses_next_seed(self):
        invalid_seed = {**VALID_SEED, "seed_id": "seed_invalid", "topology_name": "opamp invalid metadata"}
        valid_second_seed = {**VALID_SEED, "seed_id": "seed_valid_second"}

        class PassBackend:
            def __init__(self):
                self.calls = 0

            def run(self, artifact_dir):
                self.calls += 1
                artifact_path = Path(artifact_dir)
                metrics = {
                    "performance_nrmse_combined": 0.1,
                    "area_power": {"area_total_p": 100.0, "power_score_basis_w": 1e-3},
                    "ac": {"midband_gain_db": 40.0, "lower_3db_hz": 10.0, "upper_3db_hz": 20000.0},
                    "tran": {"vout_ac_peak_to_peak_v": 0.2, "vout_mean_v": 2.5, "thd_db": -50.0},
                }
                (artifact_path / "ppa_metrics.json").write_text(json.dumps(metrics))
                return RunOutcome(0, "", "", [artifact_path / "ppa_metrics.json"])

        with WorkspaceTempDir() as tmp:
            cfg = WorkflowConfig(
                backend="mock",
                run_root=Path(tmp) / "runs",
                amptest_local_dir=Path("amptest"),
                optuna_storage=None,
                max_seeds=2,
                max_trials_per_seed=1,
                objective_target=0.0,
            )
            backend = PassBackend()
            result = build_state_graph(
                cfg,
                seed_provider=lambda _state: [invalid_seed, valid_second_seed],
                backend=backend,
            ).compile().invoke({})

        self.assertEqual(1, backend.calls)
        self.assertEqual([valid_second_seed["seed_id"]], [trial["seed_id"] for trial in result["trial_results"]])
        self.assertIn(invalid_seed["seed_id"], result["abandoned_seed_ids"])

    @unittest.skipIf(StateGraph is None, "langgraph is not installed")
    def test_smoke_pass_allows_remaining_trials_for_same_seed(self):
        class PassBackend:
            def __init__(self):
                self.calls = 0

            def run(self, artifact_dir):
                self.calls += 1
                artifact_path = Path(artifact_dir)
                metrics = {
                    "performance_nrmse_combined": 0.9,
                    "area_power": {"area_total_p": 100.0, "power_score_basis_w": 1e-3},
                    "ac": {"midband_gain_db": 40.0, "lower_3db_hz": 10.0, "upper_3db_hz": 20000.0},
                    "tran": {"vout_ac_peak_to_peak_v": 0.2, "vout_mean_v": 2.5, "thd_db": -50.0},
                }
                (artifact_path / "ppa_metrics.json").write_text(json.dumps(metrics))
                return RunOutcome(0, "", "", [artifact_path / "ppa_metrics.json"])

        with WorkspaceTempDir() as tmp:
            cfg = WorkflowConfig(
                backend="mock",
                run_root=Path(tmp) / "runs",
                amptest_local_dir=Path("amptest"),
                optuna_storage=None,
                max_seeds=1,
                max_trials_per_seed=2,
                objective_target=0.0,
            )
            backend = PassBackend()
            result = build_state_graph(cfg, seed_provider=lambda _state: [VALID_SEED], backend=backend).compile().invoke({})

        self.assertEqual(2, backend.calls)
        self.assertEqual([VALID_SEED["seed_id"], VALID_SEED["seed_id"]], [trial["seed_id"] for trial in result["trial_results"]])
        self.assertEqual({VALID_SEED["seed_id"]: True}, result["seed_smoke_passed"])

    @unittest.skipIf(StateGraph is None, "langgraph is not installed")
    def test_objective_penalties_do_not_fail_smoke_when_metrics_exist(self):
        class PenalizedButSimulatedBackend:
            def run(self, artifact_dir):
                artifact_path = Path(artifact_dir)
                metrics = {
                    "performance_nrmse_combined": 0.9,
                    "area_power": {"area_total_p": 100.0, "power_score_basis_w": 1e-3},
                    "ac": {"midband_gain_db": 20.0, "lower_3db_hz": 100.0, "upper_3db_hz": 5000.0},
                    "tran": {"vout_ac_peak_to_peak_v": 0.2, "vout_mean_v": 2.5, "thd_db": -50.0},
                }
                metrics_path = artifact_path / "ppa_metrics.json"
                metrics_path.write_text(json.dumps(metrics))
                return RunOutcome(0, "", "", [metrics_path])

        with WorkspaceTempDir() as tmp:
            cfg = WorkflowConfig(
                backend="mock",
                run_root=Path(tmp) / "runs",
                amptest_local_dir=Path("amptest"),
                optuna_storage=None,
                max_seeds=1,
                max_trials_per_seed=1,
                objective_target=0.0,
            )
            result = build_state_graph(
                cfg,
                seed_provider=lambda _state: [VALID_SEED],
                backend=PenalizedButSimulatedBackend(),
            ).compile().invoke({})

        self.assertEqual({VALID_SEED["seed_id"]: True}, result["seed_smoke_passed"])
        self.assertEqual([], result["failure_reasons"])
        self.assertIn("midband gain outside", result["trial_results"][0]["error"])

    @unittest.skipIf(StateGraph is None, "langgraph is not installed")
    def test_consecutive_smoke_failure_cap_stops_before_repair(self):
        class FailBackend:
            def __init__(self):
                self.calls = 0

            def run(self, artifact_dir):
                self.calls += 1
                return RunOutcome(1, "", "smoke failed", [])

        repair_calls = []

        def repair_provider(state):
            repair_calls.append(state)
            return {**VALID_SEED, "seed_id": f"{VALID_SEED['seed_id']}_repair1"}

        with WorkspaceTempDir() as tmp:
            root = Path(tmp)
            cfg = WorkflowConfig(
                backend="mock",
                run_root=root / "runs",
                amptest_local_dir=Path("amptest"),
                optuna_storage=None,
                max_seeds=3,
                max_trials_per_seed=1,
                max_seed_repair_attempts=2,
                max_consecutive_smoke_failures=1,
                objective_target=0.0,
            )
            backend = FailBackend()
            result = build_state_graph(
                cfg,
                seed_provider=lambda _state: [VALID_SEED],
                repair_provider=repair_provider,
                backend=backend,
            ).compile().invoke({})

        self.assertEqual(1, backend.calls)
        self.assertEqual([], repair_calls)
        self.assertEqual("stop", result["next_route"])
        self.assertEqual(1, result["consecutive_smoke_failures"])
        self.assertEqual("max_consecutive_smoke_failures_reached", result["interrupt"]["reason"])
        self.assertTrue((root / "runs" / "final_report.md").exists())

    @unittest.skipIf(StateGraph is None, "langgraph is not installed")
    def test_invalid_repair_seed_is_not_simulated(self):
        class FailBackend:
            def __init__(self):
                self.calls = 0

            def run(self, artifact_dir):
                self.calls += 1
                return RunOutcome(1, "", "smoke failed", [])

        invalid_repair = {
            **VALID_SEED,
            "seed_id": f"{VALID_SEED['seed_id']}_repair1",
            "topology_name": "opamp invalid repair",
        }

        with WorkspaceTempDir() as tmp:
            cfg = WorkflowConfig(
                backend="mock",
                run_root=Path(tmp) / "runs",
                amptest_local_dir=Path("amptest"),
                optuna_storage=None,
                max_seeds=2,
                max_trials_per_seed=1,
                max_seed_repair_attempts=1,
                objective_target=0.0,
            )
            backend = FailBackend()
            result = build_state_graph(
                cfg,
                seed_provider=lambda _state: [VALID_SEED],
                repair_provider=lambda _state: invalid_repair,
                backend=backend,
            ).compile().invoke({})

        self.assertEqual(1, backend.calls)
        self.assertEqual([VALID_SEED["seed_id"]], [trial["seed_id"] for trial in result["trial_results"]])
        self.assertIn(invalid_repair["seed_id"], result["abandoned_seed_ids"])
        self.assertEqual("stop", result["next_route"])

    def test_run_mock_workflow_once_scores_fixture_metrics(self):
        with WorkspaceTempDir() as tmp:
            root = Path(tmp)
            fixture = root / "fixture"
            fixture.mkdir()
            (fixture / "ppa_metrics.json").write_text(
                json.dumps(
                    {
                        "performance_nrmse_combined": 0.1,
                        "area_power": {"area_total_p": 100.0, "power_score_basis_w": 1e-3},
                        "ac": {"midband_gain_db": 40.0, "lower_3db_hz": 10.0, "upper_3db_hz": 20000.0},
                        "tran": {"vout_ac_peak_to_peak_v": 0.2, "vout_mean_v": 2.5, "thd_db": -50.0},
                    }
                )
            )
            (fixture / "ppa_report.log").write_text("ok\n")
            amptest_config = root / "config.json"
            amptest_config.write_text(
                json.dumps(
                    {
                        "design_name": "template",
                        "work_dir": "run",
                        "include_files": [],
                        "library_sections": [],
                        "ahdl_include_files": [],
                        "dut_netlist": "dummy.scs",
                        "dut_subckt": "dummy",
                        "dut_pins_order": ["VIN", "VREF", "VDD", "GND", "VOUT"],
                        "spec": {"vdd": 5.0, "load_cap_f": 1e-11},
                        "sim": {"run_spectre": True, "run_ocean_export": True, "ac": {}, "tran": {}},
                        "input_files": {"devices_csv": "devices.csv", "ac_csv": "ac.csv", "tran_csv": "tran.csv"},
                    }
                )
            )

            state = run_mock_workflow_once(
                seed=VALID_SEED,
                params={"q_mult": 2, "r_bias": 10000.0, "c_load": 1e-11},
                run_root=root / "runs",
                amptest_config_path=amptest_config,
                fixture_dir=fixture,
            )

            self.assertEqual("scored", state["trial_results"][0]["status"])
            self.assertIsNotNone(state["best_result"])
            self.assertAlmostEqual(0.190309, state["best_result"]["objective"], places=5)
            self.assertTrue((root / "runs" / VALID_SEED["seed_id"] / "trial_0" / "final_report.md").exists())

    @unittest.skipIf(StateGraph is None, "langgraph is not installed")
    def test_langgraph_state_graph_mock_run_preserves_workflow_state(self):
        with WorkspaceTempDir() as tmp:
            root = Path(tmp)
            cfg = WorkflowConfig(
                backend="mock",
                run_root=root / "runs",
                amptest_local_dir=Path("amptest"),
                optuna_storage=None,
                max_seeds=1,
                max_trials_per_seed=1,
                objective_target=0.0,
                mock_fixture_dir=Path("amptest/run"),
            )

            graph = build_state_graph(cfg, seed_provider=lambda _state: [VALID_SEED])
            result = graph.compile().invoke({})

            self.assertEqual("scored", result["trial_results"][0]["status"])
            self.assertIsNotNone(result["best_result"])
            self.assertEqual("stop", result["next_route"])
            self.assertTrue((root / "runs" / VALID_SEED["seed_id"] / "trial_0" / "final_report.md").exists())

    @unittest.skipIf(StateGraph is None, "langgraph is not installed")
    def test_langgraph_state_graph_evaluates_each_seed_before_stopping(self):
        second_seed = {
            **VALID_SEED,
            "seed_id": "seed_1_efgh5678",
            "topology_name": "bjt_bandpass_second",
        }
        with WorkspaceTempDir() as tmp:
            root = Path(tmp)
            cfg = WorkflowConfig(
                backend="mock",
                run_root=root / "runs",
                amptest_local_dir=Path("amptest"),
                optuna_storage=None,
                max_seeds=2,
                max_trials_per_seed=1,
                objective_target=0.0,
                mock_fixture_dir=Path("amptest/run"),
            )

            graph = build_state_graph(cfg, seed_provider=lambda _state: [VALID_SEED, second_seed])
            result = graph.compile().invoke({})

            self.assertEqual(
                [VALID_SEED["seed_id"], second_seed["seed_id"]],
                [trial["seed_id"] for trial in result["trial_results"]],
            )
            self.assertEqual("stop", result["next_route"])


if __name__ == "__main__":
    unittest.main()
