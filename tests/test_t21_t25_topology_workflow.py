import json
import random
import unittest
from pathlib import Path

from tools import q5_topology_spec_sweep as spec_sweep


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = REPO_ROOT / "tools" / "topology_specs" / "t21_t25"
BASELINE = (
    REPO_ROOT
    / "Best"
    / "q5-bp-dual-input-hp-output-sink-trial-0146-workflow"
    / "best_run"
    / "trial_0146"
    / "workspace"
)


class TestT21T25TopologyWorkflow(unittest.TestCase):
    def test_specs_cover_ranked_t21_to_t25_candidates(self):
        specs = [_load_spec(path) for path in sorted(SPEC_DIR.glob("T*.json"))]

        self.assertEqual([spec["id"] for spec in specs], ["T21", "T22", "T23", "T24", "T25"])
        for spec in specs:
            self.assertEqual(
                spec["baseline_workspace"],
                "Best/q5-bp-dual-input-hp-output-sink-trial-0146-workflow/best_run/trial_0146/workspace",
            )
            self.assertEqual(
                spec["sweep_output_root"],
                "automation_artifacts/sweeps/q5-bandpass-t21-t25-targeted/runs",
            )
            self.assertTrue(spec["candidate_prefix"].startswith("q5-bp-t"))
            self.assertIn("performance_nrmse_combined", spec["target"])

    def test_specs_build_expected_topology_blocks_from_trial0146_baseline(self):
        baseline_netlist = (BASELINE / "dummy_neural_amp.scs").read_text(encoding="utf-8")
        baseline_devices = (BASELINE / "devices.csv").read_text(encoding="utf-8")
        expectations = {
            "T21": [
                "RZ12 NDRV NZ12 GND",
                "CM12 NZ12 N1 GND",
                "RZOUT VOUT NZOUT GND",
                "CMOUT NZOUT NDRV GND",
            ],
            "T22": [
                "RZIN2A B1 ZIN2A GND",
                "CIN2A ZIN2A ZIN2B GND",
                "RZIN2B ZIN2B B2 GND",
                "RZIN2C B1 ZIN2C GND",
                "CIN2C ZIN2C ZIN2D GND",
                "RZIN2D ZIN2D B2 GND",
            ],
            "T23": [
                "RHFQ4 VOUT NHFQ4 GND",
                "CHFQ4 NHFQ4 BQ4 GND",
                "RZIN2A B1 ZIN2A GND",
                "RZIN2B ZIN2B B2 GND",
            ],
            "T24": [
                "RNT1 VOUT NNT1 GND",
                "CNT1 NNT1 GND GND",
                "CNT2 VOUT NNT2 GND",
                "RNTBR NNT1 NNT2 GND",
                "CMOUT NZOUT NDRV GND",
            ],
            "T25": [
                "RZIN2A B1 ZIN2A GND",
                "CIN2A ZIN2A B2 GND",
                "RZIN2B B1 ZIN2B GND",
                "CIN2B ZIN2B B2 GND",
                "RZ12 NDRV NZ12 GND",
                "CM12 NZ12 N1 GND",
            ],
        }

        for spec_id, expected_lines in expectations.items():
            with self.subTest(spec_id=spec_id):
                spec = _load_spec(SPEC_DIR / f"{spec_id}.json")
                params = spec_sweep._random_params(spec, random.Random(21))
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

    def test_specs_encode_requested_seed_ranges(self):
        t21 = _load_spec(SPEC_DIR / "T21.json")["swept_params"]
        self.assertEqual((t21["CM12_m"]["low"], t21["CM12_m"]["high"]), (4.0, 15.0))
        self.assertEqual((t21["RZ12_l"]["low"], t21["RZ12_l"]["high"]), (3000.0, 8000.0))
        self.assertEqual((t21["CMOUT_m"]["low"], t21["CMOUT_m"]["high"]), (5.0, 30.0))

        t22 = _load_spec(SPEC_DIR / "T22.json")["swept_params"]
        self.assertEqual((t22["RBIN2_l"]["low"], t22["RBIN2_l"]["high"]), (1000.0, 6000.0))
        self.assertEqual((t22["RBIN1_l"]["low"], t22["RBIN1_l"]["high"]), (3000.0, 8000.0))
        self.assertEqual((t22["CIN1_m"]["low"], t22["CIN1_m"]["high"]), (4000000.0, 8000000.0))

        t23 = _load_spec(SPEC_DIR / "T23.json")["swept_params"]
        self.assertEqual((t23["RHFQ4_l"]["low"], t23["RHFQ4_l"]["high"]), (6000.0, 25000.0))
        self.assertEqual((t23["CHFQ4_m"]["low"], t23["CHFQ4_m"]["high"]), (2.0, 25.0))

        t24 = _load_spec(SPEC_DIR / "T24.json")["swept_params"]
        self.assertEqual((t24["CNT1_m"]["low"], t24["CNT1_m"]["high"]), (5.0, 80.0))
        self.assertEqual((t24["RNT1_l"]["low"], t24["RNT1_l"]["high"]), (2000.0, 30000.0))
        self.assertLessEqual(t24["CMOUT_m"]["high"], 15.0)

        t25 = _load_spec(SPEC_DIR / "T25.json")["swept_params"]
        self.assertEqual((t25["CIN1_m"]["low"], t25["CIN1_m"]["high"]), (5000000.0, 6500000.0))
        self.assertEqual((t25["CIN2_m"]["low"], t25["CIN2_m"]["high"]), (1500000.0, 2500000.0))
        self.assertLessEqual(t25["CM12_m"]["high"], 20.0)
        self.assertLessEqual(t25["CP3_m"]["high"], 10000.0)

    def test_parallel_launcher_splits_each_candidate_into_ten_50_trial_sweeps(self):
        launcher = (REPO_ROOT / "run_t21_t25_parallel_sweeps.ps1").read_text(encoding="utf-8")

        self.assertIn("$Splits = 10", launcher)
        self.assertIn("$TrialsPerSplit = 50", launcher)
        self.assertIn("Start-Process", launcher)
        self.assertNotIn("Start-Job", launcher)
        self.assertIn("Starting all candidate split jobs", launcher)
        self.assertIn("Total split jobs: $($CandidateSpecPaths.Count * $Splits)", launcher)
        self.assertIn("split_logs", launcher)
        self.assertIn("RedirectStandardOutput", launcher)
        self.assertIn("Wait-Process", launcher)
        self.assertIn("--trials", launcher)
        self.assertIn("$TrialsPerSplit", launcher)
        self.assertNotIn("Priority order", launcher)
        self.assertNotIn("Finished $specId", launcher)
        for spec_id in ("T21", "T22", "T23", "T24", "T25"):
            self.assertIn(f"tools/topology_specs/t21_t25/{spec_id}.json", launcher)

    def test_batch_launcher_runs_five_candidates_with_twenty_workers(self):
        launcher = SPEC_DIR / "run_all_parallel_5x20.bat"

        self.assertTrue(launcher.exists(), f"missing launcher: {launcher}")
        text = launcher.read_text(encoding="utf-8")

        self.assertIn('set "TRIALS=%~1"', text)
        self.assertIn('set "CADENCE_WORKERS=%~2"', text)
        self.assertIn('set "CADENCE_WORKERS=20"', text)
        self.assertIn("run_t21_t25_parallel_sweeps.ps1", text)
        self.assertIn("-Splits 1", text)
        self.assertIn("-TrialsPerSplit %TRIALS%", text)
        self.assertIn("-CadenceWorkers %CADENCE_WORKERS%", text)
        self.assertIn("Running T21-T25 as five parallel candidate sweeps", text)


def _load_spec(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
