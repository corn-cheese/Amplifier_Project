import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from langgraph_runner.schemas import VerificationResult
from langgraph_runner.verifier import Verifier


SCRATCH = Path(__file__).resolve().parents[2] / ".test_tmp_langgraph_runner" / "verifier"


def scratch_case(name: str) -> Path:
    root = SCRATCH / name / f"{os.getpid()}_{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def python_command(code: str) -> str:
    return subprocess.list2cmdline([sys.executable, "-c", code])


def valid_verification_command() -> str:
    code = (
        "from pathlib import Path; "
        "import json; "
        "out = Path(r'{output_dir}'); "
        "out.mkdir(parents=True, exist_ok=True); "
        "[(out / name).write_text(name + '\\n', encoding='utf-8') for name in "
        "['ppa_metrics.json', 'ppa_report.log', 'spectre_ac.log', 'spectre_tran.log']]; "
        "data = dict(candidate_id='{candidate_id}', status='passed', "
        "metrics_path=str(out / 'ppa_metrics.json'), report_path=str(out / 'ppa_report.log'), "
        "spectre_logs=[str(out / 'spectre_ac.log'), str(out / 'spectre_tran.log')], "
        "performance_nrmse_combined=0.125, area_total_p=45.0, "
        "power_score_basis_w=0.003, errors=[]); "
        "(out / 'verification.json').write_text(json.dumps(data), encoding='utf-8'); "
        "print('verified {candidate_id}')"
    )
    return python_command(code)


class TestVerifier(unittest.TestCase):
    def test_normalizes_ppa_wrapper_run_outputs_and_synthesizes_verification_json(self):
        root = scratch_case("normalizes_ppa_wrapper_run_outputs")
        candidate_dir = root / "candidate"
        workspace = root / "workspace"
        run_dir = workspace / "run"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        metrics = {
            "performance_nrmse_combined": 0.125,
            "area_power": {
                "area_total_p": 45.0,
                "power_score_basis_w": 0.003,
            },
        }
        command = python_command(
            "from pathlib import Path; import json; "
            f"run = Path(r'{run_dir}'); "
            "run.mkdir(parents=True, exist_ok=True); "
            "metrics = dict(performance_nrmse_combined=0.125, "
            "area_power=dict(area_total_p=45.0, power_score_basis_w=0.003)); "
            "(run / 'ppa_metrics.json').write_text(json.dumps(metrics), encoding='utf-8'); "
            "(run / 'ppa_report.log').write_text('ppa passed\\n', encoding='utf-8')"
        )
        verifier = Verifier(
            command=command,
            timeout_seconds=10,
            min_interval_seconds=0,
            required_outputs=["verification.json", "ppa_metrics.json", "ppa_report.log"],
        )

        result = verifier.run("cid", root, workspace, candidate_dir)

        self.assertEqual(result.status, "passed", result.errors)
        self.assertEqual(result.performance_nrmse_combined, 0.125)
        self.assertEqual(result.area_total_p, 45.0)
        self.assertEqual(result.power_score_basis_w, 0.003)
        self.assertTrue((candidate_dir / "ppa_metrics.json").exists())
        self.assertTrue((candidate_dir / "ppa_report.log").exists())
        self.assertTrue((candidate_dir / "verification.json").exists())
        self.assertEqual(json.loads((candidate_dir / "ppa_metrics.json").read_text(encoding="utf-8")), metrics)
        written = VerificationResult.model_validate_json(
            (candidate_dir / "verification.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written.status, "passed")
        self.assertEqual(written.spectre_logs, [])

    def test_normalizes_v2p3_static_transient_log_from_ppa_run_outputs(self):
        root = scratch_case("normalizes_v2p3_static_transient_log")
        candidate_dir = root / "candidate"
        workspace = root / "workspace"
        run_dir = workspace / "run"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        command = python_command(
            "from pathlib import Path; import json; "
            f"run = Path(r'{run_dir}'); "
            "run.mkdir(parents=True, exist_ok=True); "
            "metrics = dict(performance_nrmse_combined=0.125, "
            "area_power=dict(area_total_p=45.0, power_score_basis_w=0.003)); "
            "(run / 'ppa_metrics.json').write_text(json.dumps(metrics), encoding='utf-8'); "
            "(run / 'ppa_report.log').write_text('ppa passed\\n', encoding='utf-8'); "
            "[(run / name).write_text(name + '\\n', encoding='utf-8') for name in "
            "['spectre_ac.log', 'spectre_tran_static.log', 'spectre_tran.log']]"
        )
        verifier = Verifier(
            command=command,
            timeout_seconds=10,
            min_interval_seconds=0,
            required_outputs=[
                "verification.json",
                "ppa_metrics.json",
                "ppa_report.log",
                "spectre_ac.log",
                "spectre_tran_static.log",
                "spectre_tran.log",
            ],
        )

        result = verifier.run("cid", root, workspace, candidate_dir)

        self.assertEqual(result.status, "passed", result.errors)
        self.assertTrue((candidate_dir / "spectre_tran_static.log").exists())
        written = VerificationResult.model_validate_json(
            (candidate_dir / "verification.json").read_text(encoding="utf-8")
        )
        self.assertIn(str(candidate_dir / "spectre_tran_static.log"), written.spectre_logs)

    def test_ppa_wrapper_run_outputs_with_missing_required_metric_return_error(self):
        root = scratch_case("ppa_wrapper_run_outputs_missing_required_metric")
        candidate_dir = root / "candidate"
        workspace = root / "workspace"
        run_dir = workspace / "run"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        metrics = {
            "performance_nrmse_combined": None,
            "area_power": {
                "area_total_p": 1443.1374,
                "power_score_basis_w": 0.0,
            },
        }
        command = python_command(
            "from pathlib import Path; import json; "
            f"run = Path(r'{run_dir}'); "
            "run.mkdir(parents=True, exist_ok=True); "
            "metrics = dict(performance_nrmse_combined=None, "
            "area_power=dict(area_total_p=1443.1374, power_score_basis_w=0.0)); "
            "(run / 'ppa_metrics.json').write_text(json.dumps(metrics), encoding='utf-8'); "
            "(run / 'ppa_report.log').write_text('ppa failed\\n', encoding='utf-8')"
        )
        verifier = Verifier(
            command=command,
            timeout_seconds=10,
            min_interval_seconds=0,
            required_outputs=[
                "verification.json",
                "ppa_metrics.json",
                "ppa_report.log",
                "spectre_ac.log",
                "spectre_tran.log",
            ],
        )

        result = verifier.run("cid", root, workspace, candidate_dir)

        self.assertEqual(result.status, "error")
        self.assertTrue(result.errors)
        self.assertIn("invalid ppa_metrics.json", result.errors[0])
        self.assertIn("performance_nrmse_combined", result.errors[0])
        self.assertNotEqual(result.performance_nrmse_combined, 1.0)
        written = VerificationResult.model_validate_json(
            (candidate_dir / "verification.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written.status, "error")
        self.assertTrue(written.errors)
        self.assertIn("invalid ppa_metrics.json", written.errors[0])
        self.assertIn("performance_nrmse_combined", written.errors[0])
        self.assertNotEqual(written.performance_nrmse_combined, 1.0)

    def test_ppa_wrapper_run_outputs_with_boolean_performance_metric_return_error(self):
        root = scratch_case("ppa_wrapper_run_outputs_boolean_performance_metric")
        candidate_dir = root / "candidate"
        workspace = root / "workspace"
        run_dir = workspace / "run"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        command = python_command(
            "from pathlib import Path; import json; "
            f"run = Path(r'{run_dir}'); "
            "run.mkdir(parents=True, exist_ok=True); "
            "metrics = dict(performance_nrmse_combined=True, "
            "area_power=dict(area_total_p=1443.1374, power_score_basis_w=0.0)); "
            "(run / 'ppa_metrics.json').write_text(json.dumps(metrics), encoding='utf-8'); "
            "(run / 'ppa_report.log').write_text('ppa failed\\n', encoding='utf-8')"
        )
        verifier = Verifier(
            command=command,
            timeout_seconds=10,
            min_interval_seconds=0,
            required_outputs=["verification.json", "ppa_metrics.json", "ppa_report.log"],
        )

        result = verifier.run("cid", root, workspace, candidate_dir)

        self.assertEqual(result.status, "error")
        self.assertTrue(result.errors)
        self.assertIn("performance_nrmse_combined", result.errors[0])
        written = VerificationResult.model_validate_json(
            (candidate_dir / "verification.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written.status, "error")
        self.assertIn("performance_nrmse_combined", written.errors[0])

    def test_ppa_wrapper_run_outputs_overwrite_stale_verification_json(self):
        root = scratch_case("ppa_wrapper_run_outputs_overwrite_stale_verification")
        candidate_dir = root / "candidate"
        workspace = root / "workspace"
        run_dir = workspace / "run"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        stale = {
            "candidate_id": "cid",
            "status": "passed",
            "metrics_path": str(candidate_dir / "ppa_metrics.json"),
            "report_path": str(candidate_dir / "ppa_report.log"),
            "spectre_logs": [],
            "performance_nrmse_combined": 0.999,
            "area_total_p": 1.0,
            "power_score_basis_w": 1.0,
            "errors": [],
        }
        (candidate_dir / "verification.json").write_text(json.dumps(stale), encoding="utf-8")
        command = python_command(
            "from pathlib import Path; import json; "
            f"run = Path(r'{run_dir}'); "
            "run.mkdir(parents=True, exist_ok=True); "
            "metrics = dict(performance_nrmse_combined=0.25, "
            "area_power=dict(area_total_p=55.0, power_score_basis_w=0.004)); "
            "(run / 'ppa_metrics.json').write_text(json.dumps(metrics), encoding='utf-8'); "
            "(run / 'ppa_report.log').write_text('fresh ppa report\\n', encoding='utf-8')"
        )
        verifier = Verifier(
            command=command,
            timeout_seconds=10,
            min_interval_seconds=0,
            required_outputs=["verification.json", "ppa_metrics.json", "ppa_report.log"],
        )

        result = verifier.run("cid", root, workspace, candidate_dir)

        self.assertEqual(result.status, "passed", result.errors)
        self.assertEqual(result.performance_nrmse_combined, 0.25)
        self.assertEqual(result.area_total_p, 55.0)
        self.assertEqual(result.power_score_basis_w, 0.004)
        written = VerificationResult.model_validate_json(
            (candidate_dir / "verification.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written.performance_nrmse_combined, 0.25)
        self.assertEqual((candidate_dir / "ppa_report.log").read_text(encoding="utf-8"), "fresh ppa report\n")

    def test_ppa_wrapper_stale_run_outputs_are_not_normalized_as_current_results(self):
        root = scratch_case("ppa_wrapper_stale_run_outputs_are_not_normalized")
        candidate_dir = root / "candidate"
        workspace = root / "workspace"
        run_dir = workspace / "run"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        stale_metrics = {
            "performance_nrmse_combined": 0.125,
            "area_power": {
                "area_total_p": 45.0,
                "power_score_basis_w": 0.003,
            },
        }
        (run_dir / "ppa_metrics.json").write_text(json.dumps(stale_metrics), encoding="utf-8")
        (run_dir / "ppa_report.log").write_text("stale ppa report\n", encoding="utf-8")
        verifier = Verifier(
            command=python_command("print('current verifier wrote no artifacts')"),
            timeout_seconds=10,
            min_interval_seconds=0,
            required_outputs=["verification.json", "ppa_metrics.json", "ppa_report.log"],
        )

        result = verifier.run("cid", root, workspace, candidate_dir)

        self.assertEqual(result.status, "error")
        self.assertIn("missing required output", result.errors[0])
        self.assertFalse((candidate_dir / "ppa_metrics.json").exists())
        self.assertFalse((candidate_dir / "ppa_report.log").exists())
        written = VerificationResult.model_validate_json(
            (candidate_dir / "verification.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written.status, "error")
        self.assertIn("missing required output", written.errors[0])

    def test_default_runner_config_uses_ssh_verifier_and_requires_spectre_logs(self):
        config = json.loads(Path("runner_config.json").read_text(encoding="utf-8"))

        self.assertEqual(config["amptest_dir"], "amptest_v2p3/COREONLY")
        self.assertEqual(config["dut_netlist"], "amptest_v2p3/COREONLY/dummy_neural_amp.scs")
        self.assertEqual(config["devices_csv"], "amptest_v2p3/COREONLY/devices.csv")
        self.assertEqual(config["amptest_config"], "amptest_v2p3/COREONLY/config.json")
        self.assertIn("langgraph_runner.ssh_verifier", config["verifier"]["command"])
        self.assertIn("--amptest-dir amptest_v2p3/COREONLY", config["verifier"]["command"])
        self.assertIn("me59@163.180.160.78", config["verifier"]["command"])
        self.assertIn("/home/me59/amplifier_runner", config["verifier"]["command"])
        self.assertIn("--identity-file", config["verifier"]["command"])
        self.assertIn("eda_langgraph", config["verifier"]["command"])
        self.assertNotIn("{repo_root}/amptest/ppa_wrapper.py all", config["verifier"]["command"])
        self.assertEqual(
            config["verifier"]["required_outputs"],
            [
                "verification.json",
                "ppa_metrics.json",
                "ppa_report.log",
                "spectre_ac.log",
                "spectre_tran_static.log",
                "spectre_tran.log",
            ],
        )

    def test_templated_command_validates_required_outputs(self):
        root = scratch_case("templated_command_validates_required_outputs")
        candidate_dir = root / "candidate"
        workspace = root / "workspace"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        verifier = Verifier(
            command=valid_verification_command(),
            timeout_seconds=10,
            min_interval_seconds=0,
            required_outputs=[
                "verification.json",
                "ppa_metrics.json",
                "ppa_report.log",
                "spectre_ac.log",
                "spectre_tran.log",
            ],
        )

        result = verifier.run("cid", root, workspace, candidate_dir)

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.candidate_id, "cid")
        self.assertTrue((candidate_dir / "verification.json").exists())
        self.assertTrue((candidate_dir / "verifier_stdout.log").exists())
        self.assertTrue((candidate_dir / "verifier_stderr.log").exists())

    def test_missing_required_output_returns_error(self):
        root = scratch_case("missing_required_output_returns_error")
        candidate_dir = root / "candidate"
        workspace = root / "workspace"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        command = python_command("print('no verifier artifacts were written')")
        verifier = Verifier(
            command=command,
            timeout_seconds=10,
            min_interval_seconds=0,
            required_outputs=["verification.json"],
        )

        result = verifier.run("cid", root, workspace, candidate_dir)

        self.assertEqual(result.status, "error")
        self.assertIn("missing required output", result.errors[0])
        written = VerificationResult.model_validate_json(
            (candidate_dir / "verification.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written.status, "error")

    def test_stale_required_outputs_are_not_trusted(self):
        root = scratch_case("stale_required_outputs_are_not_trusted")
        candidate_dir = root / "candidate"
        workspace = root / "workspace"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        required_outputs = [
            "verification.json",
            "ppa_metrics.json",
            "ppa_report.log",
            "spectre_ac.log",
            "spectre_tran.log",
        ]
        for name in required_outputs:
            (candidate_dir / name).write_text(f"stale {name}\n", encoding="utf-8")
        stale_result = {
            "candidate_id": "cid",
            "status": "passed",
            "metrics_path": str(candidate_dir / "ppa_metrics.json"),
            "report_path": str(candidate_dir / "ppa_report.log"),
            "spectre_logs": [
                str(candidate_dir / "spectre_ac.log"),
                str(candidate_dir / "spectre_tran.log"),
            ],
            "performance_nrmse_combined": 0.125,
            "area_total_p": 45.0,
            "power_score_basis_w": 0.003,
            "errors": [],
        }
        (candidate_dir / "verification.json").write_text(json.dumps(stale_result), encoding="utf-8")
        verifier = Verifier(
            command=python_command("print('current verifier wrote no artifacts')"),
            timeout_seconds=10,
            min_interval_seconds=0,
            required_outputs=required_outputs,
        )

        result = verifier.run("cid", root, workspace, candidate_dir)

        self.assertEqual(result.status, "error")
        self.assertIn("not updated by current run", result.errors[0])
        written = VerificationResult.model_validate_json(
            (candidate_dir / "verification.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written.status, "error")

    def test_invalid_verification_json_returns_structured_error(self):
        root = scratch_case("invalid_verification_json_returns_structured_error")
        candidate_dir = root / "candidate"
        workspace = root / "workspace"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        code = (
            "from pathlib import Path; "
            "import json; "
            "out = Path(r'{output_dir}'); "
            "out.mkdir(parents=True, exist_ok=True); "
            "data = dict(candidate_id='cid', status='passed', performance_nrmse_combined='0.1'); "
            "(out / 'verification.json').write_text(json.dumps(data), encoding='utf-8')"
        )
        verifier = Verifier(
            command=python_command(code),
            timeout_seconds=10,
            min_interval_seconds=0,
            required_outputs=["verification.json"],
        )

        result = verifier.run("cid", root, workspace, candidate_dir)

        self.assertEqual(result.status, "error")
        self.assertIn("invalid verification.json", result.errors[0])
        written = VerificationResult.model_validate_json(
            (candidate_dir / "verification.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written.status, "error")

    def test_timeout_terminates_process_tree_and_writes_partial_logs(self):
        root = scratch_case("timeout_terminates_process_tree_and_writes_partial_logs")
        candidate_dir = root / "candidate"
        workspace = root / "workspace"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)

        class TimeoutProcess:
            pid = 4242

            def __init__(self):
                self.communicate_calls = 0

            def communicate(self, timeout=None):
                self.communicate_calls += 1
                if self.communicate_calls == 1:
                    raise subprocess.TimeoutExpired(
                        cmd="fake verifier",
                        timeout=timeout,
                        output="partial stdout",
                        stderr="partial stderr",
                    )
                return "partial stdout", "partial stderr"

        process = TimeoutProcess()
        verifier = Verifier(
            command=python_command("print('would time out')"),
            timeout_seconds=1,
            min_interval_seconds=0,
            required_outputs=["verification.json"],
        )

        with patch(
            "langgraph_runner.verifier.subprocess.run",
            side_effect=subprocess.TimeoutExpired(
                cmd="fake verifier",
                timeout=1,
                output="partial stdout",
                stderr="partial stderr",
            ),
        ):
            with patch("langgraph_runner.verifier.subprocess.Popen", return_value=process):
                with patch("langgraph_runner.verifier._terminate_process_tree", create=True) as cleanup:
                    result = verifier.run("cid", root, workspace, candidate_dir)

        self.assertEqual(result.status, "error")
        self.assertIn("timed out", result.errors[0])
        cleanup.assert_called_once_with(process)
        self.assertIn(
            "partial stdout",
            (candidate_dir / "verifier_stdout.log").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "partial stderr",
            (candidate_dir / "verifier_stderr.log").read_text(encoding="utf-8"),
        )

    def test_nonzero_command_exit_returns_structured_error_and_logs(self):
        root = scratch_case("nonzero_command_exit_returns_structured_error_and_logs")
        candidate_dir = root / "candidate"
        workspace = root / "workspace"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        command = python_command(
            "import sys; "
            "print('stdout before failure'); "
            "print('stderr before failure', file=sys.stderr); "
            "sys.exit(7)"
        )
        verifier = Verifier(
            command=command,
            timeout_seconds=10,
            min_interval_seconds=0,
            required_outputs=["verification.json"],
        )

        result = verifier.run("cid", root, workspace, candidate_dir)

        self.assertEqual(result.status, "error")
        self.assertIn("exited", result.errors[0])
        self.assertIn("status 7", result.errors[0])
        self.assertIn(
            "stdout before failure",
            (candidate_dir / "verifier_stdout.log").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "stderr before failure",
            (candidate_dir / "verifier_stderr.log").read_text(encoding="utf-8"),
        )

    def test_second_run_sleeps_for_remaining_interval(self):
        root = scratch_case("second_run_sleeps_for_remaining_interval")
        candidate_dir = root / "candidate"
        workspace = root / "workspace"
        first_output = root / "first_output"
        second_output = root / "second_output"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        verifier = Verifier(
            command=valid_verification_command(),
            timeout_seconds=10,
            min_interval_seconds=10,
            required_outputs=["verification.json"],
        )

        with patch("langgraph_runner.verifier.time.monotonic", side_effect=[100.0, 101.0, 102.0]):
            with patch("langgraph_runner.verifier.time.sleep") as sleep:
                first = verifier.run("cid", root, workspace, first_output)
                second = verifier.run("cid", root, workspace, second_output)

        self.assertEqual(first.status, "passed")
        self.assertEqual(second.status, "passed")
        sleep.assert_called_once_with(9.0)


if __name__ == "__main__":
    unittest.main()
