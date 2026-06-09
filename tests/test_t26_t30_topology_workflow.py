import json
import random
import unittest
from pathlib import Path

from tools import q5_topology_spec_sweep as spec_sweep


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = REPO_ROOT / "tools" / "topology_specs" / "t26_t30"
BASELINE = (
    REPO_ROOT
    / "Best"
    / "q5-bp-dual-input-hp-output-sink-trial-0146-workflow"
    / "best_run"
    / "trial_0146"
    / "workspace"
)


class TestT26T30TopologyWorkflow(unittest.TestCase):
    def test_specs_cover_t26_to_t30_trial0146_candidates(self):
        specs = [_load_spec(path) for path in sorted(SPEC_DIR.glob("T*.json"))]

        self.assertEqual([spec["id"] for spec in specs], ["T26", "T27", "T28", "T29", "T30"])
        for spec in specs:
            self.assertEqual(
                spec["baseline_workspace"],
                "Best/q5-bp-dual-input-hp-output-sink-trial-0146-workflow/best_run/trial_0146/workspace",
            )
            self.assertEqual(
                spec["sweep_output_root"],
                "automation_artifacts/sweeps/q5-bandpass-t26-t30-targeted/runs",
            )
            self.assertTrue(spec["candidate_prefix"].startswith("q5-bp-t"))
            self.assertIn("performance_nrmse_combined", spec["target"])

    def test_specs_build_requested_topology_blocks_from_trial0146_baseline(self):
        baseline_netlist = (BASELINE / "dummy_neural_amp.scs").read_text(encoding="utf-8")
        baseline_devices = (BASELINE / "devices.csv").read_text(encoding="utf-8")
        expectations = {
            "T26": [
                "CE1 E1B GND GND",
                "RME1 E1B NME1 GND",
                "CME1 NME1 N1 GND",
                "RME2 E2B NME2 GND",
                "CME2 NME2 NDRV GND",
            ],
            "T27": [
                "RE1B E1B NE1T GND",
                "RE1G NE1T GND GND",
                "RE1BR E1B GND GND",
                "CE1 NE1T GND GND",
                "RE2B E2B NE2T GND",
                "RE2G NE2T GND GND",
                "RE2BR E2B GND GND",
                "CE2 NE2T GND GND",
            ],
            "T28": [
                "RCM1U VREF NCM1 GND",
                "RCM1B NCM1 E1B GND",
                "CCM1 NCM1 GND GND",
                "Q6 NCM1 NCM1 E1B GND npn_05v5_W1p00L1p00",
                "RCM2U VREF NCM2 GND",
                "RCM2B NCM2 E2B GND",
                "CCM2 NCM2 GND GND",
                "Q7 NCM2 NCM2 E2B GND npn_05v5_W1p00L1p00",
            ],
            "T29": [
                "RINL1 B1 NINL1 GND",
                "CINL1 NINL1 B2 GND",
                "RINL2 B1 NINL2 GND",
                "CINL2 NINL2 B2 GND",
            ],
            "T30": [
                "RQ4FB VOUT BQ4 GND",
                "RHFQ4 VOUT NHFQ4 GND",
                "CHFQ4 NHFQ4 BQ4 GND",
                "RLQ4 BQ4 NLQ4 GND",
                "CLQ4 NLQ4 VREF GND",
            ],
        }

        for spec_id, expected_lines in expectations.items():
            with self.subTest(spec_id=spec_id):
                spec = _load_spec(SPEC_DIR / f"{spec_id}.json")
                params = spec_sweep._random_params(spec, random.Random(26))
                netlist, devices = spec_sweep.build_topology_artifacts(
                    baseline_netlist,
                    baseline_devices,
                    spec,
                    params,
                )

                self.assertIn("Q1 N1 B2 E1 GND", netlist)
                self.assertIn("Q5 VOUT BQ5 EQ5 GND", netlist)
                for expected in expected_lines:
                    self.assertIn(expected, netlist)
                for support in spec["support_devices"]:
                    self.assertIn(f"{support['name']},", devices)

    def test_specs_encode_requested_t26_t30_ranges(self):
        t26 = _load_spec(SPEC_DIR / "T26.json")["swept_params"]
        self.assertEqual((t26["CE1_m"]["low"], t26["CE1_m"]["high"]), (4000000.0, 9000000.0))
        self.assertEqual((t26["CE2_m"]["low"], t26["CE2_m"]["high"]), (4000000.0, 9000000.0))
        self.assertEqual((t26["RME1_l"]["low"], t26["RME1_l"]["high"]), (800.0, 12000.0))
        self.assertEqual((t26["CME1_m"]["low"], t26["CME1_m"]["high"]), (200000.0, 1500000.0))

        t27 = _load_spec(SPEC_DIR / "T27.json")["swept_params"]
        self.assertLessEqual(t27["CE1_m"]["high"], 8000000.0)
        self.assertLessEqual(t27["CE2_m"]["high"], 8000000.0)
        self.assertIn("RQ4FB_l", t27)
        self.assertIn("RBUF_l", t27)

        t28 = _load_spec(SPEC_DIR / "T28.json")["swept_params"]
        self.assertEqual((t28["CE1_m"]["low"], t28["CE1_m"]["high"]), (1000000.0, 3000000.0))
        self.assertEqual((t28["CE2_m"]["low"], t28["CE2_m"]["high"]), (1000000.0, 3000000.0))

        t29 = _load_spec(SPEC_DIR / "T29.json")["swept_params"]
        self.assertEqual((t29["CE1_m"]["low"], t29["CE1_m"]["high"]), (5000000.0, 8000000.0))
        self.assertEqual((t29["CINL1_m"]["low"], t29["CINL1_m"]["high"]), (3000000.0, 6000000.0))
        self.assertEqual((t29["CIN1_m"]["low"], t29["CIN1_m"]["high"]), (2000000.0, 4000000.0))
        self.assertEqual((t29["CIN2_m"]["low"], t29["CIN2_m"]["high"]), (500000.0, 2500000.0))

        t30 = _load_spec(SPEC_DIR / "T30.json")["swept_params"]
        self.assertEqual((t30["CE1_m"]["low"], t30["CE1_m"]["high"]), (8000000.0, 12000000.0))
        self.assertEqual((t30["RQ4FB_l"]["low"], t30["RQ4FB_l"]["high"]), (12000.0, 50000.0))
        self.assertEqual((t30["RHFQ4_l"]["low"], t30["RHFQ4_l"]["high"]), (20000.0, 120000.0))
        self.assertEqual((t30["CHFQ4_m"]["low"], t30["CHFQ4_m"]["high"]), (2.0, 100.0))
        self.assertEqual((t30["CLQ4_m"]["low"], t30["CLQ4_m"]["high"]), (2.0, 100.0))

    def test_t28_accounts_for_support_npn_devices(self):
        baseline_netlist = (BASELINE / "dummy_neural_amp.scs").read_text(encoding="utf-8")
        baseline_devices = (BASELINE / "devices.csv").read_text(encoding="utf-8")
        spec = _load_spec(SPEC_DIR / "T28.json")
        params = spec_sweep._random_params(spec, random.Random(28))

        _netlist, devices = spec_sweep.build_topology_artifacts(
            baseline_netlist,
            baseline_devices,
            spec,
            params,
        )

        self.assertIn("Q6,npn,1,1.00u,1.00u,1,,,,10meg,1.0000,true", devices)
        self.assertIn("Q7,npn,1,1.00u,1.00u,1,,,,10meg,1.0000,true", devices)

    def test_sequential_launcher_runs_priority_order_with_three_cadence_workers(self):
        ps1 = (REPO_ROOT / "run_t26_t30_sequential_sweeps.ps1").read_text(encoding="utf-8")
        launcher = (SPEC_DIR / "run_all_sequential_3w.ps1").read_text(encoding="utf-8")

        self.assertIn("$CadenceWorkers = 3", ps1)
        self.assertIn("Running T26-T30 targeted workflow", ps1)
        self.assertIn("Priority order: T26 -> T30 -> T29 -> T27 -> T28", ps1)
        self.assertIn("--cadence-workers", ps1)
        self.assertIn("$CadenceWorkers", ps1)
        self.assertNotIn("Start-Process", ps1)

        order = [
            ps1.index("tools/topology_specs/t26_t30/T26.json"),
            ps1.index("tools/topology_specs/t26_t30/T30.json"),
            ps1.index("tools/topology_specs/t26_t30/T29.json"),
            ps1.index("tools/topology_specs/t26_t30/T27.json"),
            ps1.index("tools/topology_specs/t26_t30/T28.json"),
        ]
        self.assertEqual(order, sorted(order))

        self.assertFalse((SPEC_DIR / "run_all_sequential_5w.bat").exists())
        self.assertIn("$CadenceWorkers = 3", launcher)
        self.assertIn("run_t26_t30_sequential_sweeps.ps1", launcher)
        self.assertIn("-TrialsPerCandidate $TrialsPerCandidate", launcher)
        self.assertIn("-CadenceWorkers $CadenceWorkers", launcher)


def _load_spec(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
