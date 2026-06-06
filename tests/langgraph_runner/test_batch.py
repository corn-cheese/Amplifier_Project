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


if __name__ == "__main__":
    unittest.main()
