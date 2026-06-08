import json
import math
import unittest
from pathlib import Path

from langgraph_runner.schemas import Proposal
from tools.optuna_q4_sweep import (
    build_q4_active_load_artifacts,
    classify_metrics_failure,
    evaluate_trial_objective,
    write_candidate_artifacts,
)


BASE_NETLIST = """simulator lang=spectre

subckt dummy_neural_amp GND VDD VIN VOUT VREF
RVREF VIN VREF GND res_high_po_5p73 l=20000u w=5.73u m=1

RC1 VDD N1 GND res_high_po_5p73 l=560u w=5.73u m=1
Q1 N1 VIN E1 GND npn_05v5_W1p00L2p00
RE1U E1 E1B GND res_high_po_5p73 l=28u w=5.73u m=1
RE1B E1B GND GND res_high_po_5p73 l=350u w=5.73u m=1
CE1 E1B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=56000000

RC2 VDD NDRV GND res_high_po_5p73 l=410u w=5.73u m=1
Q2 NDRV N1 E2 GND npn_05v5_W1p00L2p00
RE2U E2 E2B GND res_high_po_5p73 l=30u w=5.73u m=1
RE2B E2B GND GND res_high_po_5p73 l=350u w=5.73u m=1
CE2 E2B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=54000000

Q3 VDD NDRV VOUT GND npn_05v5_W1p00L1p00
RBUF VOUT GND GND res_high_po_5p73 l=820u w=5.73u m=1
ends dummy_neural_amp
"""

BASE_DEVICES = """name,type,count,width,length,multiplier,segments,seg_length,seg_width,ft_hz,area_p,include_in_ppa
Q1,npn,1,1.00u,2.00u,1,,,,10meg,2.0000,true
Q2,npn,1,1.00u,2.00u,1,,,,10meg,2.0000,true
Q3,npn,1,1.00u,1.00u,1,,,,10meg,1.0000,true
RC2,resistor,1,,,,1,410u,5.73u,,,true
RBUF,resistor,1,,,,1,820u,5.73u,,,true
"""


class TestOptunaQ4Sweep(unittest.TestCase):
    def test_q4_template_preserves_q1_q2_q3_and_only_retunes_rc2(self):
        params = {
            "RC2_l": 390.0,
            "RB4T_l": 1200.0,
            "RB4B_l": 430.0,
            "RQ4E_l": 80.0,
        }

        netlist, devices = build_q4_active_load_artifacts(BASE_NETLIST, BASE_DEVICES, params)

        for line in BASE_NETLIST.splitlines():
            if line.startswith(("Q1 ", "Q2 ", "Q3 ")):
                self.assertIn(line, netlist)
        self.assertIn("RC2 VDD NDRV GND res_high_po_5p73 l=390u w=5.73u m=1", netlist)
        self.assertIn("Q4 NDRV NB4 E4 VDD pnp_05v5_W3p40L3p40", netlist)
        self.assertIn("RB4T VDD NB4 GND res_high_po_5p73 l=1200u w=5.73u m=1", netlist)
        self.assertIn("RB4B NB4 GND GND res_high_po_5p73 l=430u w=5.73u m=1", netlist)
        self.assertIn("RQ4E VDD E4 GND res_high_po_5p73 l=80u w=5.73u m=1", netlist)
        self.assertIn("Q4,pnp,1,3.40u,3.40u,1,,,,10meg,11.5600,true", devices)
        self.assertIn("RC2,resistor,1,,,,1,390u,5.73u,,,true", devices)
        self.assertIn("RB4T,resistor,1,,,,1,1200u,5.73u,,,true", devices)

    def test_trial_objective_hard_rejects_invalid_or_collapsed_metrics(self):
        base_metrics = {
            "performance_nrmse_combined": 0.03,
            "ac": {"midband_gain_db": 40.0, "upper_3db_hz": 30000.0},
            "tran": {"vout_peak_to_peak_v": 0.2, "tran_nrmse_vs_target_filter": 0.04},
        }

        accepted = evaluate_trial_objective(True, "passed", base_metrics)
        self.assertFalse(accepted["rejected"])
        self.assertAlmostEqual(accepted["objective"], 0.03 + 0.04 * 0.25)

        cases = [
            (False, "passed", base_metrics, "review_failed"),
            (True, "error", base_metrics, "sim_failed"),
            (True, "passed", {**base_metrics, "performance_nrmse_combined": math.nan}, "non_finite_performance_nrmse_combined"),
            (True, "passed", {**base_metrics, "ac": {"midband_gain_db": 34.9, "upper_3db_hz": 30000.0}}, "gain_collapse"),
            (True, "passed", {**base_metrics, "ac": {"midband_gain_db": 40.0, "upper_3db_hz": 19000.0}}, "bandwidth_collapse"),
            (True, "passed", {**base_metrics, "tran": {"vout_peak_to_peak_v": 0.005}}, "output_swing_collapse"),
        ]
        for review_passed, verification_status, metrics, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                result = evaluate_trial_objective(review_passed, verification_status, metrics)
                self.assertTrue(result["rejected"])
                self.assertTrue(math.isinf(result["objective"]))
                self.assertEqual(result["reason"], expected_reason)

    def test_objective_penalizes_gain_cutoff_transient_and_swing(self):
        result = evaluate_trial_objective(
            True,
            "passed",
            {
                "performance_nrmse_combined": 0.03,
                "ac": {"midband_gain_db": 45.0, "upper_3db_hz": 60000.0},
                "tran": {"vout_peak_to_peak_v": 0.04, "tran_nrmse_vs_target_filter": 0.20},
            },
        )

        self.assertFalse(result["rejected"])
        self.assertGreater(result["objective"], 0.03)
        self.assertIn("gain_target_penalty", result["penalties"])
        self.assertIn("cutoff_target_penalty", result["penalties"])
        self.assertIn("transient_nrmse_penalty", result["penalties"])
        self.assertIn("output_swing_penalty", result["penalties"])

    def test_write_candidate_artifacts_uses_existing_protocol(self):
        root = Path(".test_tmp_langgraph_runner") / "q4_sweep_candidate"
        root.mkdir(parents=True, exist_ok=True)
        netlist, devices = build_q4_active_load_artifacts(
            BASE_NETLIST,
            BASE_DEVICES,
            {"RC2_l": 390.0, "RB4T_l": 1200.0, "RB4B_l": 430.0, "RQ4E_l": 80.0},
        )

        write_candidate_artifacts(
            root,
            candidate_id="q4-active-load-trial-0001",
            baseline_netlist=BASE_NETLIST,
            baseline_devices=BASE_DEVICES,
            trial_netlist=netlist,
            trial_devices=devices,
            params={"RC2_l": 390.0, "RB4T_l": 1200.0, "RB4B_l": 430.0, "RQ4E_l": 80.0},
            objective={"objective": 0.05, "rejected": False, "reason": "passed"},
        )

        proposal = json.loads((root / "proposal.json").read_text(encoding="utf-8"))
        Proposal.model_validate(proposal)
        self.assertEqual(proposal["candidate_id"], "q4-active-load-trial-0001")
        self.assertEqual(proposal["files_touched"], ["amptest/dummy_neural_amp.scs", "amptest/devices.csv"])
        self.assertEqual(proposal["patch"], (root / "patch.diff").read_text(encoding="utf-8"))
        self.assertIn("diff --git a/amptest/dummy_neural_amp.scs b/amptest/dummy_neural_amp.scs", proposal["patch"])
        self.assertTrue((root / "notes.md").read_text(encoding="utf-8").startswith("# Q4 Active Load Trial"))

    def test_classifies_metrics_failures_for_feedback(self):
        metrics = {
            "performance_nrmse_combined": 0.8,
            "ac": {"midband_gain_db": 20.0, "upper_3db_hz": 1000.0},
            "tran": {"vout_peak_to_peak_v": 0.001},
        }

        self.assertEqual(
            classify_metrics_failure(metrics),
            ["gain_collapse", "bandwidth_collapse", "output_swing_collapse"],
        )


if __name__ == "__main__":
    unittest.main()
