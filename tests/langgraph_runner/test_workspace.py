import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph_runner import workspace as workspace_module
from langgraph_runner.workspace import CandidateWorkspace


SCRATCH = Path(__file__).resolve().parents[2] / ".test_tmp_langgraph_runner" / "workspace"


def scratch_case(name: str) -> Path:
    root = SCRATCH / name
    root.mkdir(parents=True, exist_ok=True)
    return root


class TestCandidateWorkspace(unittest.TestCase):
    def test_workspace_copies_base_files_and_writes_candidate_config(self):
        root = scratch_case("copies_base_files_and_writes_candidate_config")
        base_dut = root / "amptest" / "dummy_neural_amp.scs"
        base_devices = root / "amptest" / "devices.csv"
        base_config = root / "amptest" / "config.json"
        base_dut.parent.mkdir(exist_ok=True)
        base_dut.write_text(
            "subckt dummy_neural_amp GND VDD VIN VOUT VREF\nends dummy_neural_amp\n",
            encoding="utf-8",
        )
        base_devices.write_text("name,type,count,include_in_ppa\nQ1,npn,1,true\n", encoding="utf-8")
        base_config.write_text(
            '{"dut_netlist":"old.scs","input_files":{"devices_csv":"old.csv","ac_csv":"old/ac.csv","tran_csv":"old/tran.csv"}}',
            encoding="utf-8",
        )
        manager = CandidateWorkspace(root / "automation_artifacts" / "workspaces")

        workspace = manager.create("p1-b001-c01-arch-20260604-231500", base_dut, base_devices, base_config)

        self.assertTrue((workspace / "dummy_neural_amp.scs").exists())
        self.assertTrue((workspace / "devices.csv").exists())
        self.assertTrue((workspace / "config.json").exists())
        config = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["dut_netlist"], "dummy_neural_amp.scs")
        self.assertEqual(config["input_files"]["devices_csv"], "devices.csv")
        self.assertEqual(config["input_files"]["ac_csv"], "run/ac.csv")
        self.assertEqual(config["input_files"]["tran_csv"], "run/tran.csv")

    def test_create_rejects_unsafe_candidate_id(self):
        root = scratch_case("create_rejects_unsafe_candidate_id")
        base_dut = root / "amptest" / "dummy_neural_amp.scs"
        base_devices = root / "amptest" / "devices.csv"
        base_config = root / "amptest" / "config.json"
        base_dut.parent.mkdir(exist_ok=True)
        base_dut.write_text("base netlist\n", encoding="utf-8")
        base_devices.write_text("base devices\n", encoding="utf-8")
        base_config.write_text('{"dut_netlist":"dummy_neural_amp.scs"}', encoding="utf-8")
        manager = CandidateWorkspace(root / "automation_artifacts" / "workspaces")

        for unsafe_candidate_id in (".", "../escape", "nested/id", "C:tmp"):
            with self.subTest(candidate_id=unsafe_candidate_id):
                with self.assertRaises(ValueError):
                    manager.create(unsafe_candidate_id, base_dut, base_devices, base_config)

    def test_promote_copies_workspace_to_canonical_files(self):
        root = scratch_case("promote_copies_workspace_to_canonical_files")
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "dummy_neural_amp.scs").write_text("accepted netlist\n", encoding="utf-8")
        (workspace / "devices.csv").write_text("accepted devices\n", encoding="utf-8")
        target_dut = root / "amptest" / "dummy_neural_amp.scs"
        target_devices = root / "amptest" / "devices.csv"
        target_dut.parent.mkdir(exist_ok=True)
        manager = CandidateWorkspace(root / "automation_artifacts" / "workspaces")

        manager.promote(workspace, target_dut, target_devices)

        self.assertEqual(target_dut.read_text(encoding="utf-8"), "accepted netlist\n")
        self.assertEqual(target_devices.read_text(encoding="utf-8"), "accepted devices\n")

    def test_promote_preflights_sources_before_copying_canonical_files(self):
        root = scratch_case("promote_preflights_sources_before_copying_canonical_files")
        workspace = root / "workspace_missing_devices"
        workspace.mkdir(exist_ok=True)
        (workspace / "dummy_neural_amp.scs").write_text("accepted netlist\n", encoding="utf-8")
        target_dut = root / "amptest" / "dummy_neural_amp.scs"
        target_devices = root / "amptest" / "devices.csv"
        target_dut.parent.mkdir(exist_ok=True)
        target_dut.write_text("old netlist\n", encoding="utf-8")
        target_devices.write_text("old devices\n", encoding="utf-8")
        manager = CandidateWorkspace(root / "automation_artifacts" / "workspaces")

        with self.assertRaises(FileNotFoundError):
            manager.promote(workspace, target_dut, target_devices)

        self.assertEqual(target_dut.read_text(encoding="utf-8"), "old netlist\n")
        self.assertEqual(target_devices.read_text(encoding="utf-8"), "old devices\n")

    def test_promote_stages_copies_before_replacing_canonical_files(self):
        root = scratch_case("promote_stages_copies_before_replacing_canonical_files")
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "dummy_neural_amp.scs").write_text("accepted netlist\n", encoding="utf-8")
        (workspace / "devices.csv").write_text("accepted devices\n", encoding="utf-8")
        target_dut = root / "amptest" / "dummy_neural_amp.scs"
        target_devices = root / "amptest" / "devices.csv"
        target_dut.parent.mkdir(exist_ok=True)
        target_dut.write_text("old netlist\n", encoding="utf-8")
        target_devices.write_text("old devices\n", encoding="utf-8")
        manager = CandidateWorkspace(root / "automation_artifacts" / "workspaces")
        original_copy2 = workspace_module.shutil.copy2

        def fail_on_devices_copy(src, dst, *args, **kwargs):
            if Path(src).name == "devices.csv":
                raise OSError("simulated devices copy failure")
            return original_copy2(src, dst, *args, **kwargs)

        with patch("langgraph_runner.workspace.shutil.copy2", side_effect=fail_on_devices_copy):
            with self.assertRaises(OSError):
                manager.promote(workspace, target_dut, target_devices)

        self.assertEqual(target_dut.read_text(encoding="utf-8"), "old netlist\n")
        self.assertEqual(target_devices.read_text(encoding="utf-8"), "old devices\n")

    def test_promote_rolls_back_if_second_final_replace_fails(self):
        root = scratch_case("promote_rolls_back_if_second_final_replace_fails")
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "dummy_neural_amp.scs").write_text("accepted netlist\n", encoding="utf-8")
        (workspace / "devices.csv").write_text("accepted devices\n", encoding="utf-8")
        target_dut = root / "amptest" / "dummy_neural_amp.scs"
        target_devices = root / "amptest" / "devices.csv"
        target_dut.parent.mkdir(exist_ok=True)
        target_dut.write_text("old netlist\n", encoding="utf-8")
        target_devices.write_text("old devices\n", encoding="utf-8")
        manager = CandidateWorkspace(root / "automation_artifacts" / "workspaces")
        original_replace = workspace_module._replace_staged_file

        def fail_on_devices_replace(temp, target):
            if Path(target).name == "devices.csv":
                raise OSError("simulated devices replace failure")
            original_replace(temp, target)

        with patch("langgraph_runner.workspace._replace_staged_file", side_effect=fail_on_devices_replace):
            with self.assertRaises(OSError):
                manager.promote(workspace, target_dut, target_devices)

        self.assertEqual(target_dut.read_text(encoding="utf-8"), "old netlist\n")
        self.assertEqual(target_devices.read_text(encoding="utf-8"), "old devices\n")

    def test_promote_rolls_back_if_second_final_replace_mutates_then_fails(self):
        root = scratch_case("promote_rolls_back_if_second_final_replace_mutates_then_fails")
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "dummy_neural_amp.scs").write_text("accepted netlist\n", encoding="utf-8")
        (workspace / "devices.csv").write_text("accepted devices\n", encoding="utf-8")
        target_dut = root / "amptest" / "dummy_neural_amp.scs"
        target_devices = root / "amptest" / "devices.csv"
        target_dut.parent.mkdir(exist_ok=True)
        target_dut.write_text("old netlist\n", encoding="utf-8")
        target_devices.write_text("old devices\n", encoding="utf-8")
        manager = CandidateWorkspace(root / "automation_artifacts" / "workspaces")
        original_replace = workspace_module._replace_staged_file

        def mutate_devices_then_fail(temp, target):
            if Path(target).name == "devices.csv":
                Path(target).write_text("corrupted devices\n", encoding="utf-8")
                raise OSError("simulated devices replace failure after mutation")
            original_replace(temp, target)

        with patch("langgraph_runner.workspace._replace_staged_file", side_effect=mutate_devices_then_fail):
            with self.assertRaises(OSError):
                manager.promote(workspace, target_dut, target_devices)

        self.assertEqual(target_dut.read_text(encoding="utf-8"), "old netlist\n")
        self.assertEqual(target_devices.read_text(encoding="utf-8"), "old devices\n")

    def test_apply_patch_rejects_non_zero_git_apply(self):
        root = scratch_case("apply_patch_rejects_non_zero_git_apply")
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "dummy_neural_amp.scs").write_text("base netlist\n", encoding="utf-8")
        (workspace / "devices.csv").write_text("base devices\n", encoding="utf-8")
        manager = CandidateWorkspace(root / "automation_artifacts" / "workspaces")

        result = manager.apply_patch(workspace, "not a unified diff")

        self.assertFalse(result.applied)
        self.assertIn("git apply failed", result.reason)

    def test_apply_patch_applies_valid_unified_diff_without_mocks(self):
        root = scratch_case("apply_patch_applies_valid_unified_diff_without_mocks")
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "dummy_neural_amp.scs").write_text(
            "simulator lang=spectre\n"
            "subckt dummy_neural_amp GND VDD VIN VOUT VREF\n"
            "R1 VDD VOUT 10k\n"
            "ends dummy_neural_amp\n",
            encoding="utf-8",
        )
        (workspace / "devices.csv").write_text(
            "name,type,count,include_in_ppa\n"
            "R1,resistor,1,true\n",
            encoding="utf-8",
        )
        patch_text = (
            "diff --git a/amptest/dummy_neural_amp.scs b/amptest/dummy_neural_amp.scs\n"
            "--- a/amptest/dummy_neural_amp.scs\n"
            "+++ b/amptest/dummy_neural_amp.scs\n"
            "@@ -1,4 +1,4 @@\n"
            " simulator lang=spectre\n"
            " subckt dummy_neural_amp GND VDD VIN VOUT VREF\n"
            "-R1 VDD VOUT 10k\n"
            "+R1 VDD VOUT 20k\n"
            " ends dummy_neural_amp\n"
            "diff --git a/amptest/devices.csv b/amptest/devices.csv\n"
            "--- a/amptest/devices.csv\n"
            "+++ b/amptest/devices.csv\n"
            "@@ -1,2 +1,2 @@\n"
            " name,type,count,include_in_ppa\n"
            "-R1,resistor,1,true\n"
            "+R1,resistor,2,true\n"
        )
        manager = CandidateWorkspace(root / "automation_artifacts" / "workspaces")

        result = manager.apply_patch(workspace, patch_text)

        self.assertTrue(result.applied, result.reason)
        self.assertIn("R1 VDD VOUT 20k\n", (workspace / "dummy_neural_amp.scs").read_text(encoding="utf-8"))
        self.assertIn("R1,resistor,2,true\n", (workspace / "devices.csv").read_text(encoding="utf-8"))

    def test_apply_patch_rejects_missing_patched_output_without_copy_back(self):
        root = scratch_case("apply_patch_rejects_missing_patched_output_without_copy_back")
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "dummy_neural_amp.scs").write_text("base netlist\n", encoding="utf-8")
        (workspace / "devices.csv").write_text("base devices\n", encoding="utf-8")
        patch_text = (
            "diff --git a/amptest/dummy_neural_amp.scs b/amptest/dummy_neural_amp.scs\n"
            "index 621d686..332c23f 100644\n"
            "--- a/amptest/dummy_neural_amp.scs\n"
            "+++ b/amptest/dummy_neural_amp.scs\n"
            "@@ -1 +1 @@\n"
            "-base netlist\n"
            "+patched netlist\n"
            "diff --git a/amptest/devices.csv b/amptest/devices.csv\n"
            "deleted file mode 100644\n"
            "index ad0b2e5..0000000\n"
            "--- a/amptest/devices.csv\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-base devices\n"
        )
        manager = CandidateWorkspace(root / "automation_artifacts" / "workspaces")

        def fake_patch_layout(_workspace):
            scratch = root / "mock_patch_root_missing_devices"
            amptest = scratch / "amptest"
            amptest.mkdir(parents=True, exist_ok=True)
            (amptest / "dummy_neural_amp.scs").write_text("patched netlist\n", encoding="utf-8")
            return scratch

        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("langgraph_runner.workspace._ensure_amptest_layout", fake_patch_layout):
            with patch("langgraph_runner.workspace.subprocess.run", return_value=completed):
                result = manager.apply_patch(workspace, patch_text)

        self.assertFalse(result.applied)
        self.assertIn("missing patched output", result.reason)
        self.assertEqual((workspace / "dummy_neural_amp.scs").read_text(encoding="utf-8"), "base netlist\n")
        self.assertEqual((workspace / "devices.csv").read_text(encoding="utf-8"), "base devices\n")

    def test_apply_patch_stages_copy_back_before_replacing_workspace_files(self):
        root = scratch_case("apply_patch_stages_copy_back_before_replacing_workspace_files")
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "dummy_neural_amp.scs").write_text("base netlist\n", encoding="utf-8")
        (workspace / "devices.csv").write_text("base devices\n", encoding="utf-8")
        manager = CandidateWorkspace(root / "automation_artifacts" / "workspaces")

        def fake_patch_layout(_workspace):
            scratch = root / "mock_patch_root_copy_failure"
            amptest = scratch / "amptest"
            amptest.mkdir(parents=True, exist_ok=True)
            (amptest / "dummy_neural_amp.scs").write_text("patched netlist\n", encoding="utf-8")
            (amptest / "devices.csv").write_text("patched devices\n", encoding="utf-8")
            return scratch

        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        original_copy2 = workspace_module.shutil.copy2

        def fail_on_devices_copy(src, dst, *args, **kwargs):
            if Path(src).name == "devices.csv":
                raise OSError("simulated devices copy failure")
            return original_copy2(src, dst, *args, **kwargs)

        with patch("langgraph_runner.workspace._ensure_amptest_layout", fake_patch_layout):
            with patch("langgraph_runner.workspace.subprocess.run", return_value=completed):
                with patch("langgraph_runner.workspace.shutil.copy2", side_effect=fail_on_devices_copy):
                    with self.assertRaises(OSError):
                        manager.apply_patch(workspace, "diff --git a/amptest/devices.csv b/amptest/devices.csv\n")

        self.assertEqual((workspace / "dummy_neural_amp.scs").read_text(encoding="utf-8"), "base netlist\n")
        self.assertEqual((workspace / "devices.csv").read_text(encoding="utf-8"), "base devices\n")

    def test_apply_patch_rolls_back_if_second_final_replace_fails(self):
        root = scratch_case("apply_patch_rolls_back_if_second_final_replace_fails")
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "dummy_neural_amp.scs").write_text("base netlist\n", encoding="utf-8")
        (workspace / "devices.csv").write_text("base devices\n", encoding="utf-8")
        manager = CandidateWorkspace(root / "automation_artifacts" / "workspaces")

        def fake_patch_layout(_workspace):
            scratch = root / "mock_patch_root_replace_failure"
            amptest = scratch / "amptest"
            amptest.mkdir(parents=True, exist_ok=True)
            (amptest / "dummy_neural_amp.scs").write_text("patched netlist\n", encoding="utf-8")
            (amptest / "devices.csv").write_text("patched devices\n", encoding="utf-8")
            return scratch

        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        original_replace = workspace_module._replace_staged_file

        def fail_on_devices_replace(temp, target):
            if Path(target).name == "devices.csv":
                raise OSError("simulated devices replace failure")
            original_replace(temp, target)

        with patch("langgraph_runner.workspace._ensure_amptest_layout", fake_patch_layout):
            with patch("langgraph_runner.workspace.subprocess.run", return_value=completed):
                with patch("langgraph_runner.workspace._replace_staged_file", side_effect=fail_on_devices_replace):
                    with self.assertRaises(OSError):
                        manager.apply_patch(workspace, "diff --git a/amptest/devices.csv b/amptest/devices.csv\n")

        self.assertEqual((workspace / "dummy_neural_amp.scs").read_text(encoding="utf-8"), "base netlist\n")
        self.assertEqual((workspace / "devices.csv").read_text(encoding="utf-8"), "base devices\n")

    def test_apply_patch_rolls_back_if_second_final_replace_mutates_then_fails(self):
        root = scratch_case("apply_patch_rolls_back_if_second_final_replace_mutates_then_fails")
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "dummy_neural_amp.scs").write_text("base netlist\n", encoding="utf-8")
        (workspace / "devices.csv").write_text("base devices\n", encoding="utf-8")
        manager = CandidateWorkspace(root / "automation_artifacts" / "workspaces")

        def fake_patch_layout(_workspace):
            scratch = root / "mock_patch_root_mutating_replace_failure"
            amptest = scratch / "amptest"
            amptest.mkdir(parents=True, exist_ok=True)
            (amptest / "dummy_neural_amp.scs").write_text("patched netlist\n", encoding="utf-8")
            (amptest / "devices.csv").write_text("patched devices\n", encoding="utf-8")
            return scratch

        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        original_replace = workspace_module._replace_staged_file

        def mutate_devices_then_fail(temp, target):
            if Path(target).name == "devices.csv":
                Path(target).write_text("corrupted devices\n", encoding="utf-8")
                raise OSError("simulated devices replace failure after mutation")
            original_replace(temp, target)

        with patch("langgraph_runner.workspace._ensure_amptest_layout", fake_patch_layout):
            with patch("langgraph_runner.workspace.subprocess.run", return_value=completed):
                with patch("langgraph_runner.workspace._replace_staged_file", side_effect=mutate_devices_then_fail):
                    with self.assertRaises(OSError):
                        manager.apply_patch(workspace, "diff --git a/amptest/devices.csv b/amptest/devices.csv\n")

        self.assertEqual((workspace / "dummy_neural_amp.scs").read_text(encoding="utf-8"), "base netlist\n")
        self.assertEqual((workspace / "devices.csv").read_text(encoding="utf-8"), "base devices\n")


if __name__ == "__main__":
    unittest.main()
