import unittest

from langgraph_runner.prime_limits import PrimeLimitTracker


class TestPrimeLimits(unittest.TestCase):
    def test_allows_two_active_and_four_total_per_subagent(self):
        tracker = PrimeLimitTracker(max_active=2, max_total=4)

        self.assertTrue(tracker.request("sub-1", "bias-prime").approved)
        self.assertTrue(tracker.request("sub-1", "R-prime").approved)
        self.assertFalse(tracker.request("sub-1", "C-prime").approved)

        tracker.finish("sub-1", "bias-prime")
        self.assertTrue(tracker.request("sub-1", "C-prime").approved)
        tracker.finish("sub-1", "R-prime")
        self.assertTrue(tracker.request("sub-1", "LOW-prime").approved)
        tracker.finish("sub-1", "C-prime")
        self.assertFalse(tracker.request("sub-1", "HIGH-prime").approved)

    def test_rejects_prime_role_for_finished_unknown_prime(self):
        tracker = PrimeLimitTracker(max_active=2, max_total=4)

        with self.assertRaises(ValueError):
            tracker.finish("sub-1", "bias-prime")


if __name__ == "__main__":
    unittest.main()
