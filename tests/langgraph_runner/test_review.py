import json
import unittest
from pathlib import Path

from langgraph_runner.review import DeterministicReviewer


SCRATCH = Path(".test_tmp_langgraph_runner") / "review"

VALID_NETLIST = """simulator lang=spectre
subckt dummy_neural_amp GND VDD VIN VOUT VREF
Q1 VOUT VIN GND GND sky130_fd_pr_main__npn_05v5
R1 VDD VOUT sky130_fd_pr_main__res_high_po_5p73 l=5.73u w=0.35u m=10
ends dummy_neural_amp
"""

VALID_DEVICES = """name,type,count,width,length,multiplier,segments,seg_length,seg_width,ft_hz,area_p,include_in_ppa
Q1,npn,1,,,,,,,,,true
R1,resistor,1,,,,10,5.73u,0.35u,,,true
"""


def scratch_case(name: str) -> Path:
    root = SCRATCH / name
    root.mkdir(parents=True, exist_ok=True)
    return root


class TestDeterministicReview(unittest.TestCase):
    def write_candidate(self, root: Path, netlist: str, devices: str, touched=None):
        candidate = root / "candidate"
        candidate.mkdir(exist_ok=True)
        proposal = {
            "candidate_id": "p1-b001-c01-arch-20260604-231500",
            "phase": "phase1_performance",
            "agent": "architecture",
            "hypothesis": "Valid BJT candidate.",
            "primary_objective": "performance",
            "changed_blocks": ["gain_stage"],
            "files_touched": touched or ["amptest/dummy_neural_amp.scs", "amptest/devices.csv"],
            "expected_effect": {
                "performance_nrmse_combined": "decrease",
                "area_total_p": "increase",
                "power_score_basis_w": "unknown",
            },
            "risk": "May not meet cutoff targets.",
            "patch": "diff --git a/amptest/dummy_neural_amp.scs b/amptest/dummy_neural_amp.scs\n",
        }
        (candidate / "proposal.json").write_text(json.dumps(proposal), encoding="utf-8")
        (candidate / "patch.diff").write_text(proposal["patch"], encoding="utf-8")
        (candidate / "notes.md").write_text("notes\n", encoding="utf-8")
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "dummy_neural_amp.scs").write_text(netlist, encoding="utf-8")
        (workspace / "devices.csv").write_text(devices, encoding="utf-8")
        return candidate, workspace

    def test_accepts_valid_candidate_workspace(self):
        root = scratch_case("valid_candidate_workspace")
        candidate, workspace = self.write_candidate(root, VALID_NETLIST, VALID_DEVICES)
        reviewer = DeterministicReviewer(
            allowed_files={"amptest/dummy_neural_amp.scs", "amptest/devices.csv"},
            dut_subckt="dummy_neural_amp",
            dut_pins_order=["GND", "VDD", "VIN", "VOUT", "VREF"],
        )

        result = reviewer.review(candidate, workspace, "p1-b001-c01-arch-20260604-231500")

        self.assertTrue(result.passed)
        self.assertEqual(result.errors, [])

    def test_rejects_candidate_id_mismatch(self):
        root = scratch_case("candidate_id_mismatch")
        candidate, workspace = self.write_candidate(root, VALID_NETLIST, VALID_DEVICES)
        reviewer = DeterministicReviewer(
            {"amptest/dummy_neural_amp.scs", "amptest/devices.csv"},
            "dummy_neural_amp",
            ["GND", "VDD", "VIN", "VOUT", "VREF"],
        )

        result = reviewer.review(candidate, workspace, "different-id")

        self.assertFalse(result.passed)
        self.assertIn("candidate_id_mismatch", result.errors)

    def test_rejects_opamp_and_behavioral_shortcuts(self):
        root = scratch_case("opamp_and_behavioral_shortcuts")
        netlist = VALID_NETLIST.replace(
            "Q1 VOUT VIN GND GND sky130_fd_pr_main__npn_05v5",
            "XOP VIN VOUT ahdLib_opamp",
        )
        candidate, workspace = self.write_candidate(root, netlist, VALID_DEVICES)
        reviewer = DeterministicReviewer(
            {"amptest/dummy_neural_amp.scs", "amptest/devices.csv"},
            "dummy_neural_amp",
            ["GND", "VDD", "VIN", "VOUT", "VREF"],
        )

        result = reviewer.review(candidate, workspace, "p1-b001-c01-arch-20260604-231500")

        self.assertFalse(result.passed)
        self.assertIn("forbidden_shortcut", result.errors)

    def test_rejects_opamp_accounting_row_in_devices_csv(self):
        root = scratch_case("opamp_accounting_row_in_devices_csv")
        devices = VALID_DEVICES + "XOP,opamp,1,,,,,,,,,true\n"
        candidate, workspace = self.write_candidate(root, VALID_NETLIST, devices)
        reviewer = DeterministicReviewer(
            {"amptest/dummy_neural_amp.scs", "amptest/devices.csv"},
            "dummy_neural_amp",
            ["GND", "VDD", "VIN", "VOUT", "VREF"],
        )

        result = reviewer.review(candidate, workspace, "p1-b001-c01-arch-20260604-231500")

        self.assertFalse(result.passed)
        self.assertIn("devices_csv_invalid", result.errors)

    def test_rejects_unallowed_accounting_type_in_devices_csv(self):
        root = scratch_case("unallowed_accounting_type_in_devices_csv")
        devices = VALID_DEVICES + "M1,nmos,1,,,,,,,,,true\n"
        candidate, workspace = self.write_candidate(root, VALID_NETLIST, devices)
        reviewer = DeterministicReviewer(
            {"amptest/dummy_neural_amp.scs", "amptest/devices.csv"},
            "dummy_neural_amp",
            ["GND", "VDD", "VIN", "VOUT", "VREF"],
        )

        result = reviewer.review(candidate, workspace, "p1-b001-c01-arch-20260604-231500")

        self.assertFalse(result.passed)
        self.assertIn("devices_csv_invalid", result.errors)

    def test_rejects_illegal_file_touch(self):
        root = scratch_case("illegal_file_touch")
        candidate, workspace = self.write_candidate(
            root,
            VALID_NETLIST,
            VALID_DEVICES,
            touched=["amptest/config.json"],
        )
        reviewer = DeterministicReviewer(
            {"amptest/dummy_neural_amp.scs", "amptest/devices.csv"},
            "dummy_neural_amp",
            ["GND", "VDD", "VIN", "VOUT", "VREF"],
        )

        result = reviewer.review(candidate, workspace, "p1-b001-c01-arch-20260604-231500")

        self.assertFalse(result.passed)
        self.assertIn("illegal_file_touch", result.errors)

    def test_rejects_illegal_patch_diff_file_touch(self):
        root = scratch_case("illegal_patch_diff_file_touch")
        candidate, workspace = self.write_candidate(root, VALID_NETLIST, VALID_DEVICES)
        (candidate / "patch.diff").write_text(
            "diff --git a/amptest/config.json b/amptest/config.json\n"
            "--- a/amptest/config.json\n"
            "+++ b/amptest/config.json\n",
            encoding="utf-8",
        )
        reviewer = DeterministicReviewer(
            {"amptest/dummy_neural_amp.scs", "amptest/devices.csv"},
            "dummy_neural_amp",
            ["GND", "VDD", "VIN", "VOUT", "VREF"],
        )

        result = reviewer.review(candidate, workspace, "p1-b001-c01-arch-20260604-231500")

        self.assertFalse(result.passed)
        self.assertFalse(result.checks["file_scope"])
        self.assertIn("illegal_file_touch", result.errors)

    def test_rejects_invalid_proposal_schema_as_review_result(self):
        root = scratch_case("invalid_proposal_schema")
        candidate, workspace = self.write_candidate(root, VALID_NETLIST, VALID_DEVICES)
        invalid_proposal = {
            "candidate_id": "p1-b001-c01-arch-20260604-231500",
            "phase": "phase1_performance",
            "agent": "architecture",
            "hypothesis": "Valid BJT candidate.",
            "primary_objective": "performance",
            "changed_blocks": ["gain_stage"],
            "files_touched": ["amptest/dummy_neural_amp.scs", "amptest/devices.csv"],
            "expected_effect": {
                "performance_nrmse_combined": "decrease",
                "area_total_p": "increase",
                "power_score_basis_w": "unknown",
            },
            "risk": "May not meet cutoff targets.",
        }
        (candidate / "proposal.json").write_text(json.dumps(invalid_proposal), encoding="utf-8")
        reviewer = DeterministicReviewer(
            {"amptest/dummy_neural_amp.scs", "amptest/devices.csv"},
            "dummy_neural_amp",
            ["GND", "VDD", "VIN", "VOUT", "VREF"],
        )

        try:
            result = reviewer.review(candidate, workspace, "p1-b001-c01-arch-20260604-231500")
        except Exception as exc:  # pragma: no cover - documents the pre-fix bug
            self.fail(f"review raised instead of returning ReviewResult: {exc!r}")

        self.assertFalse(result.passed)
        self.assertFalse(result.checks["proposal_schema"])
        self.assertIn("proposal_schema_invalid", result.errors)

    def test_rejects_missing_workspace_netlist_as_review_result(self):
        root = scratch_case("missing_workspace_netlist")
        candidate, _ = self.write_candidate(root, VALID_NETLIST, VALID_DEVICES)
        workspace = root / "workspace_without_netlist"
        workspace.mkdir(exist_ok=True)
        (workspace / "devices.csv").write_text(VALID_DEVICES, encoding="utf-8")
        reviewer = DeterministicReviewer(
            {"amptest/dummy_neural_amp.scs", "amptest/devices.csv"},
            "dummy_neural_amp",
            ["GND", "VDD", "VIN", "VOUT", "VREF"],
        )

        try:
            result = reviewer.review(candidate, workspace, "p1-b001-c01-arch-20260604-231500")
        except Exception as exc:  # pragma: no cover - documents the pre-fix bug
            self.fail(f"review raised instead of returning ReviewResult: {exc!r}")

        self.assertFalse(result.passed)
        self.assertIn("workspace_netlist_missing", result.errors)


if __name__ == "__main__":
    unittest.main()
