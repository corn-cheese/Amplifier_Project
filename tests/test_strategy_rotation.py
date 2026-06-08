import json
import unittest

from tools.strategy_rotation import (
    STRATEGY_FAMILIES,
    analyze_ledger_lines,
    next_family_index,
    render_strategy_brief,
)


class TestStrategyRotation(unittest.TestCase):
    def test_analyze_ledger_lines_finds_best_and_target_hit(self):
        lines = [
            json.dumps(
                {
                    "candidate_id": "weak",
                    "status": "rejected",
                    "reason": "acceptance_gate_failed",
                    "metrics": {"performance_nrmse_combined": 0.5},
                }
            ),
            json.dumps(
                {
                    "candidate_id": "good",
                    "status": "rejected",
                    "reason": "acceptance_gate_failed",
                    "metrics": {"performance_nrmse_combined": 0.039},
                }
            ),
        ]

        analysis = analyze_ledger_lines(lines, target_performance=0.04)

        self.assertEqual(analysis["best_candidate_id"], "good")
        self.assertEqual(analysis["best_performance"], 0.039)
        self.assertTrue(analysis["target_hit"])

    def test_next_family_index_rotates_and_wraps(self):
        self.assertEqual(next_family_index(0), 1)
        self.assertEqual(next_family_index(len(STRATEGY_FAMILIES) - 1), 0)

    def test_render_strategy_brief_pins_family_and_q4_constraints(self):
        brief = render_strategy_brief(
            family=STRATEGY_FAMILIES[0],
            base_candidate_id="p1-b028-c03-arch-20260606-135953",
            best_candidate_id="p1-b054-c03-arch-20260607-031659",
            best_performance=0.14252325819318318,
            batches_per_family=3,
        )

        self.assertIn("# Topology Exploration Brief", brief)
        self.assertIn("strategy family: q4_compensation_shaping", brief)
        self.assertIn("p1-b028-c03-arch-20260606-135953", brief)
        self.assertIn("p1-b054-c03-arch-20260607-031659", brief)
        self.assertIn("Add exactly one BJT named `Q4`", brief)
        self.assertIn("Run this family for 3 batch", brief)


if __name__ == "__main__":
    unittest.main()
