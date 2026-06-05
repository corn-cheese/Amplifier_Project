import json
import unittest
from pathlib import Path

from langgraph_runner.agent_outputs import parse_subagent_output
from langgraph_runner.artifacts import ArtifactPaths
from langgraph_runner.candidate_assembly import CandidateAssembler


SCRATCH = Path(__file__).resolve().parents[2] / ".test_tmp_langgraph_runner" / "candidate_assembly"


def scratch_case(name: str) -> Path:
    root = SCRATCH / name
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_fixture_repo(root: Path) -> dict:
    amptest = root / "amptest"
    amptest.mkdir(parents=True, exist_ok=True)
    (amptest / "dummy_neural_amp.scs").write_text(
        "simulator lang=spectre\n"
        "subckt dummy_neural_amp GND VDD VIN VOUT VREF\n"
        "R1 VDD VOUT 10k\n"
        "ends dummy_neural_amp\n",
        encoding="utf-8",
    )
    (amptest / "devices.csv").write_text(
        "name,type,count,include_in_ppa\n"
        "R1,resistor,1,true\n",
        encoding="utf-8",
    )
    (amptest / "config.json").write_text(
        json.dumps(
            {
                "dut_netlist": "dummy_neural_amp.scs",
                "dut_subckt": "dummy_neural_amp",
                "dut_pins_order": ["GND", "VDD", "VIN", "VOUT", "VREF"],
                "input_files": {"devices_csv": "devices.csv"},
            }
        ),
        encoding="utf-8",
    )
    return {
        "dut_netlist": "amptest/dummy_neural_amp.scs",
        "devices_csv": "amptest/devices.csv",
        "amptest_config": "amptest/config.json",
    }


def write_output(output: Path, candidate_id: str, *, phase: str = "phase1_performance") -> None:
    proposal = {
        "candidate_id": candidate_id,
        "phase": phase,
        "agent": "architecture",
        "hypothesis": "Increase passive feedback while preserving DUT pins.",
        "primary_objective": "performance",
        "changed_blocks": ["feedback"],
        "files_touched": ["amptest/dummy_neural_amp.scs", "amptest/devices.csv"],
        "expected_effect": {
            "performance_nrmse_combined": "decrease",
            "area_total_p": "increase",
            "power_score_basis_w": "no_major_change",
        },
        "risk": "May increase area.",
        "patch": (
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
        ),
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "proposal.json").write_text(json.dumps(proposal), encoding="utf-8")
    (output / "patch.diff").write_text(proposal["patch"], encoding="utf-8")
    (output / "notes.md").write_text("Candidate notes.\n", encoding="utf-8")


class TestCandidateAssembly(unittest.TestCase):
    def test_assembles_valid_output_into_candidate_dir_and_patched_workspace(self):
        root = scratch_case("assembles_valid_output")
        config = write_fixture_repo(root)
        paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        output = root / "agent-output"
        write_output(output, candidate_id)
        parsed = parse_subagent_output(output, candidate_id, agent_call_id="call-1")
        assignment = {
            "candidate_id": candidate_id,
            "batch_id": "p1-b001",
            "role": "architecture",
            "phase": "phase1_performance",
            "primary_objective": "performance",
        }

        result = CandidateAssembler(paths=paths, repo_root=root, config=config).assemble(assignment, parsed)

        self.assertEqual(result.status, "assembled", result.errors)
        self.assertTrue((paths.candidate_dir(candidate_id) / "proposal.json").exists())
        self.assertIn("R1 VDD VOUT 20k", (paths.workspace_dir(candidate_id) / "dummy_neural_amp.scs").read_text(encoding="utf-8"))
        self.assertIn("R1,resistor,2,true", (paths.workspace_dir(candidate_id) / "devices.csv").read_text(encoding="utf-8"))

    def test_assignment_echo_mismatch_marks_candidate_error(self):
        root = scratch_case("assignment_echo_mismatch")
        config = write_fixture_repo(root)
        paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        output = root / "agent-output"
        write_output(output, candidate_id, phase="phase2a_area")
        parsed = parse_subagent_output(output, candidate_id, agent_call_id="call-1")
        assignment = {
            "candidate_id": candidate_id,
            "batch_id": "p1-b001",
            "role": "architecture",
            "phase": "phase1_performance",
            "primary_objective": "performance",
        }

        result = CandidateAssembler(paths=paths, repo_root=root, config=config).assemble(assignment, parsed)

        self.assertEqual(result.status, "error")
        self.assertIn("assignment_echo_mismatch", result.errors)

    def test_configured_source_paths_must_stay_inside_repo(self):
        root = scratch_case("source_paths_must_stay_inside_repo")
        config = write_fixture_repo(root)
        config["dut_netlist"] = "../outside.scs"
        paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        output = root / "agent-output"
        write_output(output, candidate_id)
        parsed = parse_subagent_output(output, candidate_id, agent_call_id="call-1")
        assignment = {
            "candidate_id": candidate_id,
            "batch_id": "p1-b001",
            "role": "architecture",
            "phase": "phase1_performance",
            "primary_objective": "performance",
        }

        result = CandidateAssembler(paths=paths, repo_root=root, config=config).assemble(assignment, parsed)

        self.assertEqual(result.status, "error")
        self.assertTrue(any("path_outside_repo" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
