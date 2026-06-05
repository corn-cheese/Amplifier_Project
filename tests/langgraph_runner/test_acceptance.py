import math
import unittest

from langgraph_runner.acceptance import (
    AcceptanceDecision,
    evaluate_candidate,
    ppa_surrogate_score,
    ppa_scores_against_baseline,
    three_bjt_fallback_allowed,
)
from langgraph_runner.schemas import Phase, RunnerState


def metrics(
    *,
    performance: float = 0.03,
    power: float = 2.0,
    area: float = 100.0,
) -> dict[str, float]:
    return {
        "performance_nrmse_combined": performance,
        "power_score_basis_w": power,
        "area_total_p": area,
    }


def accepted_state(
    *,
    phase: Phase = Phase.PHASE2A_AREA,
    accepted_metrics: dict[str, float] | None = None,
    accepted_score: float | None = None,
    ppa_baseline_metrics: dict[str, float] | None = None,
) -> RunnerState:
    state = RunnerState.initial(contract_hash="abc123")
    state.current_phase = phase
    state.accepted_metrics = accepted_metrics
    state.accepted_ppa_surrogate_score = accepted_score
    if ppa_baseline_metrics is not None:
        state.ppa_baseline_metrics = ppa_baseline_metrics
    return state


def evaluate(
    state: RunnerState,
    candidate_metrics: dict[str, float],
    *,
    review_passed: bool = True,
    verification_status: str = "passed",
    safety_passed: bool = True,
    ppa_baseline_metrics: dict[str, float] | None = None,
) -> AcceptanceDecision:
    kwargs = {}
    if ppa_baseline_metrics is not None:
        kwargs["ppa_baseline_metrics"] = ppa_baseline_metrics
    return evaluate_candidate(
        state,
        "p1-b001-c01-arch-20260604-231500",
        candidate_metrics,
        review_passed=review_passed,
        verification_status=verification_status,
        safety_passed=safety_passed,
        **kwargs,
    )


class TestAcceptanceGates(unittest.TestCase):
    def test_phase1_gate_accepts_only_performance_at_or_below_cutoff(self):
        state = RunnerState.initial(contract_hash="abc123")

        self.assertEqual(
            evaluate(state, metrics(performance=0.04)),
            AcceptanceDecision.ACCEPT,
        )
        self.assertEqual(
            evaluate(state, metrics(performance=0.0401)),
            AcceptanceDecision.REJECT,
        )

    def test_review_verification_and_safety_failures_reject_or_error(self):
        state = RunnerState.initial(contract_hash="abc123")

        self.assertEqual(
            evaluate(state, metrics(), review_passed=False),
            AcceptanceDecision.REJECT,
        )
        self.assertEqual(
            evaluate(state, metrics(), safety_passed=False),
            AcceptanceDecision.REJECT,
        )
        self.assertEqual(
            evaluate(state, metrics(), verification_status="failed"),
            AcceptanceDecision.REJECT,
        )
        self.assertEqual(
            evaluate(state, metrics(), verification_status="error"),
            AcceptanceDecision.ERROR,
        )

    def test_positive_ppa_score_rejects_when_it_does_not_beat_accepted_score(self):
        state = accepted_state(
            accepted_metrics=metrics(performance=0.03, power=2.0, area=100.0),
            accepted_score=0.50,
        )

        score = ppa_surrogate_score(
            metrics(performance=0.03, power=2.0, area=100.0),
            state.accepted_metrics,
        )

        self.assertGreater(score, 0.0)
        self.assertEqual(
            evaluate(state, metrics(performance=0.03, power=2.0, area=100.0)),
            AcceptanceDecision.REJECT,
        )

    def test_three_bjt_fallback_requires_count_and_recent_stagnation(self):
        self.assertFalse(three_bjt_fallback_allowed(11, [0.0, 0.0, 0.0, 0.0, 0.0]))
        self.assertFalse(three_bjt_fallback_allowed(12, [0.0, 0.0, 0.0, 0.0]))
        self.assertFalse(three_bjt_fallback_allowed(12, [0.001] * 5))
        self.assertTrue(three_bjt_fallback_allowed(12, [0.01, 0.001, 0.001, 0.001, 0.001, 0.0009]))

    def test_missing_or_non_finite_performance_metric_returns_error(self):
        state = RunnerState.initial(contract_hash="abc123")

        self.assertEqual(
            evaluate(state, {"power_score_basis_w": 2.0, "area_total_p": 100.0}),
            AcceptanceDecision.ERROR,
        )
        for value in (math.inf, -math.inf, math.nan):
            with self.subTest(value=repr(value)):
                self.assertEqual(
                    evaluate(state, metrics(performance=value)),
                    AcceptanceDecision.ERROR,
                )

    def test_phase2_rejects_performance_above_ceiling(self):
        state = accepted_state(accepted_metrics=None, accepted_score=None)

        self.assertEqual(
            evaluate(state, metrics(performance=0.1001)),
            AcceptanceDecision.REJECT,
        )

    def test_phase2_accepts_without_baseline_metrics_or_score(self):
        no_metrics = accepted_state(accepted_metrics=None, accepted_score=0.1)
        no_score = accepted_state(accepted_metrics=metrics(), accepted_score=None)

        self.assertEqual(evaluate(no_metrics, metrics(performance=0.08)), AcceptanceDecision.ACCEPT)
        self.assertEqual(evaluate(no_score, metrics(performance=0.08)), AcceptanceDecision.ACCEPT)

    def test_phase2_compares_surrogate_score_against_accepted_score(self):
        baseline = metrics(performance=0.04, power=4.0, area=200.0)
        state = accepted_state(accepted_metrics=baseline, accepted_score=0.55)

        self.assertEqual(
            evaluate(state, metrics(performance=0.03, power=2.0, area=100.0)),
            AcceptanceDecision.ACCEPT,
        )
        self.assertEqual(
            evaluate(state, metrics(performance=0.04, power=4.0, area=200.0)),
            AcceptanceDecision.REJECT,
        )

    def test_phase2_accepts_incremental_improvement_against_shared_baseline(self):
        original_baseline = metrics(performance=0.04, power=4.0, area=200.0)
        current_best = metrics(performance=0.032, power=3.2, area=160.0)
        incremental_improvement = metrics(performance=0.031, power=3.1, area=155.0)
        state = accepted_state(
            accepted_metrics=current_best,
            accepted_score=ppa_surrogate_score(current_best, original_baseline),
        )

        self.assertEqual(
            evaluate(state, incremental_improvement, ppa_baseline_metrics=original_baseline),
            AcceptanceDecision.ACCEPT,
        )

    def test_phase2_uses_state_ppa_baseline_for_incremental_improvement(self):
        original_baseline = metrics(performance=0.04, power=4.0, area=200.0)
        current_best = metrics(performance=0.032, power=3.2, area=160.0)
        incremental_improvement = metrics(performance=0.031, power=3.1, area=155.0)
        state = accepted_state(
            accepted_metrics=current_best,
            accepted_score=ppa_surrogate_score(current_best, original_baseline),
            ppa_baseline_metrics=original_baseline,
        )

        self.assertEqual(
            evaluate(state, incremental_improvement),
            AcceptanceDecision.ACCEPT,
        )

    def test_state_ppa_baseline_takes_precedence_over_argument(self):
        state_baseline = metrics(performance=0.04, power=4.0, area=200.0)
        argument_baseline = metrics(performance=0.01, power=1.0, area=50.0)
        current_best = metrics(performance=0.032, power=3.2, area=160.0)
        incremental_improvement = metrics(performance=0.031, power=3.1, area=155.0)
        state = accepted_state(
            accepted_metrics=current_best,
            accepted_score=ppa_surrogate_score(current_best, state_baseline),
            ppa_baseline_metrics=state_baseline,
        )

        self.assertEqual(
            evaluate(state, incremental_improvement, ppa_baseline_metrics=argument_baseline),
            AcceptanceDecision.ACCEPT,
        )

    def test_shared_baseline_helper_scores_current_and_candidate_comparably(self):
        original_baseline = metrics(performance=0.04, power=4.0, area=200.0)
        current_best = metrics(performance=0.032, power=3.2, area=160.0)
        incremental_improvement = metrics(performance=0.031, power=3.1, area=155.0)

        candidate_score, current_score = ppa_scores_against_baseline(
            incremental_improvement,
            current_best,
            original_baseline,
        )

        self.assertLess(candidate_score, current_score)

    def test_phase2_errors_when_provided_baseline_disagrees_with_stored_score(self):
        original_baseline = metrics(performance=0.04, power=4.0, area=200.0)
        current_best = metrics(performance=0.032, power=3.2, area=160.0)
        state = accepted_state(
            accepted_metrics=current_best,
            accepted_score=ppa_surrogate_score(current_best, original_baseline) + 0.01,
        )

        self.assertEqual(
            evaluate(state, metrics(performance=0.031, power=3.1, area=155.0), ppa_baseline_metrics=original_baseline),
            AcceptanceDecision.ERROR,
        )

    def test_ppa_surrogate_score_matches_weighted_log_formula(self):
        score = ppa_surrogate_score(
            metrics(performance=0.02, power=1.0, area=50.0),
            metrics(performance=0.04, power=2.0, area=100.0),
        )

        self.assertAlmostEqual(score, math.log(1.5))

    def test_ppa_surrogate_score_rejects_zero_or_non_finite_inputs(self):
        valid_metrics = metrics()

        for bad_baseline in (
            metrics(performance=0.0),
            metrics(power=0.0),
            metrics(area=0.0),
            metrics(performance=math.inf),
        ):
            with self.subTest(baseline=bad_baseline):
                with self.assertRaises(ValueError):
                    ppa_surrogate_score(valid_metrics, bad_baseline)

        for bad_metrics in (
            metrics(power=math.inf),
            metrics(area=math.nan),
        ):
            with self.subTest(metrics=bad_metrics):
                with self.assertRaises(ValueError):
                    ppa_surrogate_score(bad_metrics, valid_metrics)

    def test_evaluate_returns_error_for_bad_ppa_inputs_without_zero_division(self):
        state = accepted_state(
            accepted_metrics=metrics(performance=0.03, power=0.0, area=100.0),
            accepted_score=0.5,
        )

        self.assertEqual(evaluate(state, metrics()), AcceptanceDecision.ERROR)


if __name__ == "__main__":
    unittest.main()
