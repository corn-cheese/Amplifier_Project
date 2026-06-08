import re
import unittest
from datetime import datetime, timezone

from langgraph_runner.batch import plan_batch
from langgraph_runner.ids import candidate_id, phase_prefix
from langgraph_runner.schemas import RunnerState


class TestBatchPlanning(unittest.TestCase):
    def test_phase_prefixes_match_design_examples(self):
        self.assertEqual(phase_prefix("phase1_performance"), "p1")
        self.assertEqual(phase_prefix("phase2a_area"), "p2a")
        self.assertEqual(phase_prefix("phase2b_power"), "p2b")

    def test_candidate_id_format(self):
        now = datetime(2026, 6, 4, 23, 15, 0, tzinfo=timezone.utc)
        cid = candidate_id("phase1_performance", 3, 2, "architecture", now)

        self.assertEqual(cid, "p1-b003-c02-arch-20260604-231500")

    def test_default_batch_has_three_unique_assignments(self):
        state = RunnerState.initial(contract_hash="abc")
        assignments = plan_batch(state=state, batch_size=3, now=datetime(2026, 6, 4, 23, 15, 0, tzinfo=timezone.utc))

        self.assertEqual(len(assignments), 3)
        self.assertEqual(len({item.candidate_id for item in assignments}), 3)
        self.assertTrue(all(re.match(r"p1-b001-c0[1-3]-", item.candidate_id) for item in assignments))
        self.assertEqual([item.role for item in assignments], ["diagnosis", "architecture", "optimizer"])

    def test_stagnated_phase1_assigns_distinct_macro_topology_directives(self):
        state = RunnerState.initial(contract_hash="abc").model_copy(
            update={"batch_no": 27, "three_bjt_verified_count": 77}
        )
        recent_ledger = [
            {
                "phase": "phase1_performance",
                "status": "rejected",
                "metrics": {"performance_nrmse_combined": 0.14434820621531017},
            },
            {
                "phase": "phase1_performance",
                "status": "rejected",
                "metrics": {"performance_nrmse_combined": 0.15011794747338122},
            },
            {
                "phase": "phase1_performance",
                "status": "rejected",
                "metrics": {"performance_nrmse_combined": 0.15842150634871627},
            },
            {
                "phase": "phase1_performance",
                "status": "rejected",
                "metrics": {"performance_nrmse_combined": 0.14591887240415988},
            },
            {
                "phase": "phase1_performance",
                "status": "rejected",
                "metrics": {"performance_nrmse_combined": 0.5989119218856529},
            },
            {
                "phase": "phase1_performance",
                "status": "rejected",
                "metrics": {"performance_nrmse_combined": 0.14478234264132223},
            },
        ]

        assignments = plan_batch(
            state=state,
            batch_size=3,
            now=datetime(2026, 6, 6, 13, 30, 0, tzinfo=timezone.utc),
            recent_ledger=recent_ledger,
        )

        directives = [item.macro_topology_directive for item in assignments]
        self.assertEqual([item.role for item in assignments], ["architecture", "architecture", "architecture"])
        self.assertEqual([directive["stage_count"] for directive in directives], [1, 2, 3])
        self.assertEqual(len({directive["signal_path_class"] for directive in directives}), 3)
        self.assertTrue(all(item.avoid_patterns for item in assignments))

    def test_non_stagnated_phase1_keeps_default_roles_without_macro_directives(self):
        state = RunnerState.initial(contract_hash="abc").model_copy(update={"three_bjt_verified_count": 3})

        assignments = plan_batch(
            state=state,
            batch_size=3,
            now=datetime(2026, 6, 4, 23, 15, 0, tzinfo=timezone.utc),
            recent_ledger=[],
        )

        self.assertEqual([item.role for item in assignments], ["diagnosis", "architecture", "optimizer"])
        self.assertTrue(all(item.macro_topology_directive is None for item in assignments))


if __name__ == "__main__":
    unittest.main()
