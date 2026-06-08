import subprocess
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from langgraph_runner.ssh_verifier import run_ssh_verifier


SCRATCH = Path(__file__).resolve().parents[2] / ".test_tmp_langgraph_runner" / "ssh_verifier"


def scratch_case(name: str) -> Path:
    root = SCRATCH / name / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_fixture(root: Path, amptest_dir: str = "amptest") -> tuple[Path, Path]:
    amptest = root / amptest_dir
    workspace = root / "workspace"
    ssh_dir = root / ".ssh"
    amptest.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    ssh_dir.mkdir(parents=True, exist_ok=True)
    for path in (
        amptest / "ppa_wrapper.py",
        amptest / "ppa_wrapper_core.py",
        amptest / "runtest.sh",
        workspace / "dummy_neural_amp.scs",
        workspace / "devices.csv",
        workspace / "config.json",
        ssh_dir / "eda_langgraph",
    ):
        path.write_text(path.name + "\n", encoding="utf-8")
    return root, workspace


class TestSshVerifier(unittest.TestCase):
    def test_runs_scp_ssh_scp_flow_without_calling_amptest_locally(self):
        repo_root, workspace = write_fixture(scratch_case("scp_ssh_scp_flow"))
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)

        with patch("langgraph_runner.ssh_verifier.subprocess.run", side_effect=fake_run):
            result = run_ssh_verifier(
                ssh_target="me59@163.180.160.78",
                remote_root="/home/me59/amplifier_runner",
                candidate_id="p1-b001-c01",
                repo_root=repo_root,
                local_candidate_dir=workspace,
                identity_file=repo_root / ".ssh" / "eda_langgraph",
            )

        self.assertEqual(result, 0)
        self.assertEqual([call[0][0] for call in calls], ["ssh", "scp", "ssh", "scp"])
        identity_options = [
            "-o",
            "BatchMode=yes",
            "-i",
            str((repo_root / ".ssh" / "eda_langgraph").resolve()),
            "-o",
            "IdentitiesOnly=yes",
        ]
        self.assertEqual(calls[0][0][1:7], identity_options)
        self.assertEqual(calls[1][0][1:7], identity_options)
        self.assertIn("rm -rf /home/me59/amplifier_runner/p1-b001-c01", calls[0][0][8])
        self.assertIn("mkdir -p /home/me59/amplifier_runner/p1-b001-c01", calls[0][0][8])
        self.assertEqual(calls[1][0][-1], "me59@163.180.160.78:/home/me59/amplifier_runner/p1-b001-c01/")
        self.assertIn(str(repo_root / "amptest" / "ppa_wrapper.py"), calls[1][0])
        self.assertIn(str(repo_root / "amptest" / "ppa_wrapper_core.py"), calls[1][0])
        self.assertIn(str(repo_root / "amptest" / "runtest.sh"), calls[1][0])
        self.assertIn("cd /home/me59/amplifier_runner/p1-b001-c01", calls[2][0][8])
        self.assertIn("./runtest.sh", calls[2][0][8])
        self.assertEqual(calls[3][0][-1], str(workspace / "run"))
        self.assertIn(
            "me59@163.180.160.78:/home/me59/amplifier_runner/p1-b001-c01/run/ppa_metrics.json",
            calls[3][0],
        )

    def test_copies_configured_amptest_v2p3_coreonly_support_files_and_static_log(self):
        repo_root, workspace = write_fixture(
            scratch_case("configured_amptest_v2p3_coreonly"),
            "amptest_v2p3/COREONLY",
        )
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)

        with patch("langgraph_runner.ssh_verifier.subprocess.run", side_effect=fake_run):
            result = run_ssh_verifier(
                ssh_target="me59@163.180.160.78",
                remote_root="/home/me59/amplifier_runner",
                candidate_id="p1-b001-c01",
                repo_root=repo_root,
                local_candidate_dir=workspace,
                amptest_dir=Path("amptest_v2p3/COREONLY"),
            )

        self.assertEqual(result, 0)
        self.assertIn(str(repo_root / "amptest_v2p3" / "COREONLY" / "ppa_wrapper.py"), calls[1][0])
        self.assertIn(str(repo_root / "amptest_v2p3" / "COREONLY" / "ppa_wrapper_core.py"), calls[1][0])
        self.assertIn(str(repo_root / "amptest_v2p3" / "COREONLY" / "runtest.sh"), calls[1][0])
        self.assertIn(
            "me59@163.180.160.78:/home/me59/amplifier_runner/p1-b001-c01/run/spectre_tran_static.log",
            calls[3][0],
        )

    def test_uploads_candidate_veriloga_sidecar_when_present(self):
        repo_root, workspace = write_fixture(scratch_case("candidate_veriloga_sidecar"))
        (workspace / "dummy_neural_amp.scs").write_text(
            'simulator lang=spectre\nahdl_include "dummy_neural_amp.va"\n',
            encoding="utf-8",
        )
        (workspace / "dummy_neural_amp.va").write_text("module dummy_neural_amp_va; endmodule\n", encoding="utf-8")
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)

        with patch("langgraph_runner.ssh_verifier.subprocess.run", side_effect=fake_run):
            result = run_ssh_verifier(
                ssh_target="me59@163.180.160.78",
                remote_root="/home/me59/amplifier_runner",
                candidate_id="p1-b001-c01",
                repo_root=repo_root,
                local_candidate_dir=workspace,
            )

        self.assertEqual(result, 0)
        self.assertIn(str(workspace / "dummy_neural_amp.va"), calls[1][0])

    def test_stops_after_first_failed_remote_step(self):
        repo_root, workspace = write_fixture(scratch_case("failed_remote_step"))
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 23)

        with patch("langgraph_runner.ssh_verifier.subprocess.run", side_effect=fake_run):
            result = run_ssh_verifier(
                ssh_target="me59@163.180.160.78",
                remote_root="/home/me59/amplifier_runner",
                candidate_id="p1-b001-c01",
                repo_root=repo_root,
                local_candidate_dir=workspace,
            )

        self.assertEqual(result, 23)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
