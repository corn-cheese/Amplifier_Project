import math
import unittest
from datetime import datetime
from typing import get_args

from pydantic import ValidationError

from langgraph_runner.schemas import (
    CandidateStatus,
    LedgerEntry,
    MetricEffect,
    Phase,
    PrimaryObjective,
    PrimeRole,
    Proposal,
    RunnerState,
    TopDecision,
    VerificationResult,
)


class TestSchemas(unittest.TestCase):
    def test_proposal_accepts_required_candidate_fields(self):
        proposal = Proposal.model_validate(
            {
                "candidate_id": "p1-b003-c02-arch-20260604-231500",
                "phase": "phase1_performance",
                "agent": "architecture",
                "hypothesis": "Increase low-frequency shaping without changing evaluator inputs.",
                "primary_objective": "performance",
                "changed_blocks": ["low_frequency"],
                "files_touched": ["amptest/dummy_neural_amp.scs", "amptest/devices.csv"],
                "expected_effect": {
                    "performance_nrmse_combined": "decrease",
                    "area_total_p": "increase",
                    "power_score_basis_w": "no_major_change"
                },
                "risk": "Additional passives may increase area.",
                "patch": "diff --git a/amptest/dummy_neural_amp.scs b/amptest/dummy_neural_amp.scs\n"
            }
        )

        self.assertEqual(proposal.phase, Phase.PHASE1_PERFORMANCE)

    def test_proposal_rejects_extra_keys(self):
        data = {
            "candidate_id": "p1-b001-c01-arch-20260604-231500",
            "phase": "phase1_performance",
            "agent": "architecture",
            "hypothesis": "Valid idea.",
            "primary_objective": "performance",
            "changed_blocks": ["bias"],
            "files_touched": ["amptest/dummy_neural_amp.scs"],
            "expected_effect": {
                "performance_nrmse_combined": "decrease",
                "area_total_p": "unknown",
                "power_score_basis_w": "unknown"
            },
            "risk": "None identified.",
            "patch": "diff --git a/amptest/dummy_neural_amp.scs b/amptest/dummy_neural_amp.scs\n",
            "stdout_claim": "trust me"
        }

        with self.assertRaises(ValidationError):
            Proposal.model_validate(data)

    def test_runner_state_defaults_to_phase1(self):
        state = RunnerState.initial(contract_hash="abc123")

        self.assertEqual(state.current_phase, Phase.PHASE1_PERFORMANCE)
        self.assertEqual(state.batch_no, 0)
        self.assertIsNone(state.baseline_candidate_id)
        self.assertIsNone(state.accepted_candidate_id)
        self.assertIsNone(state.accepted_metrics)
        self.assertIsNone(state.accepted_ppa_surrogate_score)
        self.assertIsNone(state.ppa_baseline_metrics)
        self.assertIsNone(state.best_failed_candidate_id)
        self.assertIsNone(state.best_failed_metrics)
        self.assertEqual(state.three_bjt_verified_count, 0)
        self.assertFalse(state.three_bjt_stagnated)
        self.assertEqual(state.phase2a_verified_count, 0)
        self.assertFalse(state.phase2a_stagnated)
        self.assertIsNone(state.last_verification_at)
        self.assertIsNone(state.last_top_decision_path)
        self.assertEqual(state.contract_hash, "abc123")

    def test_runner_state_accepts_missing_ppa_baseline_for_legacy_state(self):
        state_data = RunnerState.initial(contract_hash="abc123").model_dump()
        state_data.pop("ppa_baseline_metrics", None)

        state = RunnerState.model_validate(state_data)

        self.assertIsNone(state.ppa_baseline_metrics)

    def test_verification_requires_finite_metrics(self):
        result = VerificationResult.model_validate(
            {
                "candidate_id": "p1-b001-c01-arch-20260604-231500",
                "status": "passed",
                "metrics_path": "automation_artifacts/candidates/x/ppa_metrics.json",
                "report_path": "automation_artifacts/candidates/x/ppa_report.log",
                "spectre_logs": ["automation_artifacts/candidates/x/spectre_ac.log"],
                "performance_nrmse_combined": 0.03,
                "area_total_p": 100.0,
                "power_score_basis_w": 0.001,
                "errors": []
            }
        )

        self.assertEqual(result.status, "passed")

    def test_verification_rejects_non_finite_metrics(self):
        for value in (math.inf, float("nan")):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValidationError):
                    VerificationResult.model_validate(
                        {
                            "candidate_id": "p1-b001-c01-arch-20260604-231500",
                            "status": "passed",
                            "metrics_path": "automation_artifacts/candidates/x/ppa_metrics.json",
                            "report_path": "automation_artifacts/candidates/x/ppa_report.log",
                            "spectre_logs": ["automation_artifacts/candidates/x/spectre_ac.log"],
                            "performance_nrmse_combined": value,
                            "area_total_p": 100.0,
                            "power_score_basis_w": 0.001,
                            "errors": []
                        }
                    )

    def test_verification_rejects_numeric_string_metrics(self):
        with self.assertRaises(ValidationError):
            VerificationResult.model_validate(
                {
                    "candidate_id": "p1-b001-c01-arch-20260604-231500",
                    "status": "passed",
                    "metrics_path": "automation_artifacts/candidates/x/ppa_metrics.json",
                    "report_path": "automation_artifacts/candidates/x/ppa_report.log",
                    "spectre_logs": ["automation_artifacts/candidates/x/spectre_ac.log"],
                    "performance_nrmse_combined": "0.03",
                    "area_total_p": 100.0,
                    "power_score_basis_w": 0.001,
                    "errors": []
                }
            )

    def test_top_decision_rejects_invalid_decision(self):
        with self.assertRaises(ValidationError):
            TopDecision.model_validate(
                {
                    "decision": "invent_new_route",
                    "reason": "Invalid.",
                    "anomaly_level": "none",
                    "candidate_ids": [],
                    "next_batch_strategy": "Continue.",
                    "human_interrupt": {
                        "required": False,
                        "question": None,
                        "recommended_action": None,
                        "evidence_paths": []
                    }
                }
            )

    def test_candidate_status_enum_contains_required_values(self):
        self.assertEqual(
            {item.value for item in CandidateStatus},
            {"accepted", "rejected", "error", "interrupted"}
        )

    def test_prime_role_enum_contains_required_values(self):
        self.assertEqual(
            {item.value for item in PrimeRole},
            {
                "bias-prime",
                "R-prime",
                "C-prime",
                "LOW-prime",
                "HIGH-prime",
                "gain-stage-prime",
                "output-stage-prime",
            }
        )

    def test_metric_effect_contains_required_values(self):
        self.assertEqual(
            set(get_args(MetricEffect)),
            {"decrease", "increase", "no_major_change", "unknown"}
        )

    def test_primary_objective_contains_required_values(self):
        self.assertEqual(
            set(get_args(PrimaryObjective)),
            {"performance", "area", "power"}
        )

    def test_ledger_entry_accepts_required_artifact_fields(self):
        entry = LedgerEntry.model_validate(
            {
                "candidate_id": "p1-b001-c01-arch-20260604-231500",
                "batch_id": "p1-b001",
                "phase": "phase2a_area",
                "agent": "prime",
                "status": "accepted",
                "reason": "Best verified candidate.",
                "metrics": {
                    "performance_nrmse_combined": 0.03,
                    "area_total_p": 100.0,
                    "power_score_basis_w": 0.001
                },
                "ppa_surrogate_score": None,
                "artifact_dir": "automation_artifacts/candidates/p1-b001-c01",
                "workspace_dir": "automation_artifacts/workspaces/p1-b001-c01",
                "created_at": "2026-06-04T23:15:00",
                "contract_hash": "abc123"
            }
        )

        self.assertEqual(entry.phase, Phase.PHASE2A_AREA)
        self.assertEqual(entry.status, CandidateStatus.ACCEPTED)
        self.assertIsInstance(entry.created_at, datetime)


if __name__ == "__main__":
    unittest.main()
