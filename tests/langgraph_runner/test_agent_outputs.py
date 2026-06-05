import json
import unittest
from pathlib import Path

from langgraph_runner.agent_outputs import parse_subagent_output


SCRATCH = Path(__file__).resolve().parents[2] / ".test_tmp_langgraph_runner" / "agent_outputs"


def scratch_case(name: str) -> Path:
    root = SCRATCH / name
    root.mkdir(parents=True, exist_ok=True)
    return root


def proposal(candidate_id: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "phase": "phase1_performance",
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
        "patch": "diff --git a/amptest/dummy_neural_amp.scs b/amptest/dummy_neural_amp.scs\n",
    }


class TestAgentOutputs(unittest.TestCase):
    def test_valid_subagent_output_parses_candidate_artifacts_and_prime_requests(self):
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        output = scratch_case("valid_subagent_output")
        (output / "proposal.json").write_text(json.dumps(proposal(candidate_id)), encoding="utf-8")
        (output / "patch.diff").write_text(proposal(candidate_id)["patch"], encoding="utf-8")
        (output / "notes.md").write_text("Candidate notes.\n", encoding="utf-8")
        (output / "prime_requests.json").write_text(
            json.dumps(
                [
                    {
                        "prime_role": "bias-prime",
                        "prompt": "Check bias current feasibility.",
                        "rationale": "The feedback change may need bias adjustment.",
                    }
                ]
            ),
            encoding="utf-8",
        )

        parsed = parse_subagent_output(output, candidate_id, agent_call_id="call-1")

        self.assertTrue(parsed.valid, parsed.errors)
        self.assertEqual(parsed.proposal.candidate_id, candidate_id)
        self.assertEqual(parsed.patch_path, output / "patch.diff")
        self.assertEqual(parsed.notes_path, output / "notes.md")
        self.assertEqual(len(parsed.prime_requests), 1)
        self.assertEqual(parsed.prime_requests[0]["candidate_id"], candidate_id)
        self.assertEqual(parsed.prime_requests[0]["parent_agent_call_id"], "call-1")

    def test_missing_patch_is_structured_error_without_raising(self):
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        output = scratch_case("missing_patch")
        (output / "proposal.json").write_text(json.dumps(proposal(candidate_id)), encoding="utf-8")
        (output / "notes.md").write_text("Candidate notes.\n", encoding="utf-8")

        parsed = parse_subagent_output(output, candidate_id, agent_call_id="call-1")

        self.assertFalse(parsed.valid)
        self.assertIn("missing_patch", parsed.errors)
        self.assertEqual(parsed.prime_requests, [])


if __name__ == "__main__":
    unittest.main()
