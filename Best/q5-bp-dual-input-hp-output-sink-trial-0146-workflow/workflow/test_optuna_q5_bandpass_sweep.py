import math
import sys
import unittest
from pathlib import Path

WORKFLOW_DIR = Path(__file__).resolve().parent
if str(WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_DIR))

from optuna_q5_bandpass_sweep import (
    MAX_AREA_OBJECTIVE_PERFORMANCE_NRMSE,
    build_q5_bandpass_artifacts,
    build_parser,
    _family_spec,
    _objective_value_for_study,
    _pick_best,
    _random_params,
    _suggest_params,
    evaluate_raw_trial_objective,
)


def _metrics(performance, area):
    return {
        "performance_nrmse_combined": performance,
        "ac": {"midband_gain_db": 39.5, "upper_3db_hz": 28152.0},
        "tran": {"vout_peak_to_peak_v": 0.20},
        "area_power": {"area_total_p": area},
    }


def _candidate(performance, area, trial_no):
    return {
        "trial_no": trial_no,
        "objective": evaluate_raw_trial_objective(True, "passed", _metrics(performance, area)),
    }


TOPOLOGY_DIR = WORKFLOW_DIR.parent / "topology"
BASELINE_NETLIST = (TOPOLOGY_DIR / "dummy_neural_amp.scs").read_text(encoding="utf-8")
BASELINE_DEVICES = (TOPOLOGY_DIR / "devices.csv").read_text(encoding="utf-8")


def _input_params(topology):
    return {
        "input_pr_topology": topology,
        "CIN1_m": 240000.0,
        "CIN2_m": 60000.0,
        "DIN1_m": 2.0,
        "DIN2_m": 3.0,
    }


class TestSweepBestPolicy(unittest.TestCase):
    def test_objective_still_minimizes_performance_for_study(self):
        result = evaluate_raw_trial_objective(
            True,
            "passed",
            _metrics(MAX_AREA_OBJECTIVE_PERFORMANCE_NRMSE + 0.01, 500.0),
        )

        self.assertFalse(result["rejected"])
        self.assertEqual(result["objective"], MAX_AREA_OBJECTIVE_PERFORMANCE_NRMSE + 0.01)
        self.assertEqual(_objective_value_for_study(result), MAX_AREA_OBJECTIVE_PERFORMANCE_NRMSE + 0.01)

    def test_best_uses_area_once_performance_is_at_or_below_threshold(self):
        best = _pick_best(None, _candidate(0.82, 100.0, 1))
        best = _pick_best(best, _candidate(0.74, 500.0, 2))
        best = _pick_best(best, _candidate(0.70, 900.0, 3))
        best = _pick_best(best, _candidate(0.75, 300.0, 4))

        self.assertEqual(best["trial_no"], 4)
        self.assertEqual(best["objective"]["area_total_p"], 300.0)

    def test_best_falls_back_to_lowest_performance_when_no_area_eligible_trial_exists(self):
        best = _pick_best(None, _candidate(0.91, 100.0, 1))
        best = _pick_best(best, _candidate(0.86, 500.0, 2))
        best = _pick_best(best, _candidate(0.89, 10.0, 3))

        self.assertEqual(best["trial_no"], 2)
        self.assertEqual(best["objective"]["performance_nrmse_combined"], 0.86)

    def test_rejected_candidates_do_not_replace_accepted_best(self):
        best = _pick_best(None, _candidate(0.74, 500.0, 1))
        rejected = {"trial_no": 2, "objective": {"objective": math.inf, "rejected": True}}

        self.assertEqual(_pick_best(best, rejected)["trial_no"], 1)


class TestTrial0146InputPseudoResistorSweep(unittest.TestCase):
    def test_dual_input_family_replaces_rbin_with_b2b_cc_diode_paths(self):
        netlist, devices = build_q5_bandpass_artifacts(
            BASELINE_NETLIST,
            BASELINE_DEVICES,
            "dual-input-highpass-output-sink",
            _input_params("b2b-cc"),
        )

        self.assertIn("CIN1 VIN B1 GND cap_vpp_11p5x11p7_m1m4_noshield m=240000", netlist)
        self.assertIn("CIN2 B1 B2 GND cap_vpp_11p5x11p7_m1m4_noshield m=60000", netlist)
        self.assertIn("Q1 N1 B2 E1 GND npn_05v5_W1p00L2p00", netlist)
        self.assertNotIn("RBIN1", netlist)
        self.assertNotIn("RBIN2", netlist)
        self.assertIn("DHP1A B1 NHP1C diode_pd2nw_05v5 m=2", netlist)
        self.assertIn("DHP1B VREF NHP1C diode_pd2nw_05v5 m=2", netlist)
        self.assertIn("DHP2A B2 NHP2C diode_pd2nw_05v5 m=3", netlist)
        self.assertIn("DHP2B VREF NHP2C diode_pd2nw_05v5 m=3", netlist)

        device_names = {line.split(",", 1)[0] for line in devices.splitlines()[1:]}
        self.assertNotIn("RBIN1", device_names)
        self.assertNotIn("RBIN2", device_names)
        self.assertIn("DHP1A,diode,1,1.00u,1.00u,1", devices)
        self.assertIn("DHP2B,diode,1,1.00u,1.00u,1", devices)

    def test_dual_input_family_supports_all_requested_diode_topologies(self):
        expected_by_topology = {
            "b2b-ca": [
                "DHP1A NHP1A B1 diode_pd2nw_05v5 m=2",
                "DHP1B NHP1A VREF diode_pd2nw_05v5 m=2",
                "DHP2A NHP2A B2 diode_pd2nw_05v5 m=3",
                "DHP2B NHP2A VREF diode_pd2nw_05v5 m=3",
            ],
            "dual-b2b": [
                "DHP1A B1 NHP1K diode_pd2nw_05v5 m=2",
                "DHP1D NHP1A VREF diode_pd2nw_05v5 m=2",
                "DHP2A B2 NHP2K diode_pd2nw_05v5 m=3",
                "DHP2D NHP2A VREF diode_pd2nw_05v5 m=3",
            ],
            "series2-cc": [
                "DHP1A B1 NHP1K1 diode_pd2nw_05v5 m=2",
                "DHP1D VREF NHP1K2 diode_pd2nw_05v5 m=2",
                "DHP2A B2 NHP2K1 diode_pd2nw_05v5 m=3",
                "DHP2D VREF NHP2K2 diode_pd2nw_05v5 m=3",
            ],
            "reverse-antiparallel": [
                "DHP1A B1 VREF diode_pd2nw_05v5 m=2",
                "DHP1B VREF B1 diode_pd2nw_05v5 m=2",
                "DHP2A B2 VREF diode_pd2nw_05v5 m=3",
                "DHP2B VREF B2 diode_pd2nw_05v5 m=3",
            ],
        }

        for topology, expected_lines in expected_by_topology.items():
            with self.subTest(topology=topology):
                netlist, devices = build_q5_bandpass_artifacts(
                    BASELINE_NETLIST,
                    BASELINE_DEVICES,
                    "dual-input-highpass-output-sink",
                    _input_params(topology),
                )

                for expected_line in expected_lines:
                    self.assertIn(expected_line, netlist)
                diode_names = {line.split()[0] for line in expected_lines}
                for diode_name in diode_names:
                    self.assertIn(f"{diode_name},diode,1,1.00u,1.00u,1", devices)

    def test_random_params_use_topology_specific_initial_ranges(self):
        spec = _family_spec("dual-input-highpass-output-sink")
        ranges = {
            "b2b-cc": ((240000, 600000), (60000, 150000), (2, 8), (2, 8)),
            "b2b-ca": ((240000, 600000), (60000, 150000), (2, 8), (2, 8)),
            "dual-b2b": ((120000, 360000), (30000, 90000), (1, 4), (1, 4)),
            "series2-cc": ((60000, 240000), (15000, 60000), (1, 4), (1, 4)),
            "reverse-antiparallel": ((300000, 900000), (80000, 200000), (1, 6), (1, 6)),
        }

        for seed in range(200):
            params = _random_params(spec, __import__("random").Random(seed))
            topology = params["input_pr_topology"]
            cin1, cin2, din1, din2 = ranges[topology]
            self.assertGreaterEqual(params["CIN1_m"], cin1[0])
            self.assertLessEqual(params["CIN1_m"], cin1[1])
            self.assertGreaterEqual(params["CIN2_m"], cin2[0])
            self.assertLessEqual(params["CIN2_m"], cin2[1])
            self.assertGreaterEqual(params["DIN1_m"], din1[0])
            self.assertLessEqual(params["DIN1_m"], din1[1])
            self.assertGreaterEqual(params["DIN2_m"], din2[0])
            self.assertLessEqual(params["DIN2_m"], din2[1])

    def test_can_fix_input_pr_topology_for_independent_candidate_runs(self):
        spec = _family_spec("dual-input-highpass-output-sink")
        fixed = {"input_pr_topology": "series2-cc"}

        parsed = build_parser().parse_args(
            [
                "--family",
                "dual-input-highpass-output-sink",
                "--input-pr-topology",
                "series2-cc",
            ]
        )
        self.assertEqual(parsed.input_pr_topology, "series2-cc")

        random_params = _random_params(spec, __import__("random").Random(1), fixed)
        self.assertEqual(random_params["input_pr_topology"], "series2-cc")
        self.assertGreaterEqual(random_params["CIN1_m"], 60000)
        self.assertLessEqual(random_params["CIN1_m"], 240000)
        self.assertGreaterEqual(random_params["CIN2_m"], 15000)
        self.assertLessEqual(random_params["CIN2_m"], 60000)

        class FakeTrial:
            def suggest_categorical(self, name, choices):
                raise AssertionError(f"{name} should be fixed, not suggested")

            def suggest_float(self, name, low, high, log=False):
                return low

        optuna_params = _suggest_params(spec, FakeTrial(), fixed)
        self.assertEqual(optuna_params["input_pr_topology"], "series2-cc")
        self.assertEqual(optuna_params["CIN1_m"], 60000)
        self.assertEqual(optuna_params["CIN2_m"], 15000)
        self.assertEqual(optuna_params["DIN1_m"], 1)
        self.assertEqual(optuna_params["DIN2_m"], 1)


if __name__ == "__main__":
    unittest.main()
