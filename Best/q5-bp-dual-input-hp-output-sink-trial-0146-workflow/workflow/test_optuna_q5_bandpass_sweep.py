import math
import json
import sys
import threading
import time
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

WORKFLOW_DIR = Path(__file__).resolve().parent
if str(WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_DIR))

import optuna_q5_bandpass_sweep as sweep_mod
from optuna_q5_bandpass_sweep import (
    CAP_MODEL,
    MAX_AREA_OBJECTIVE_PERFORMANCE_NRMSE,
    RES_MODEL,
    build_q5_bandpass_artifacts,
    build_parser,
    _candidate_id,
    _family_spec,
    _objective_value_for_study,
    _pick_best,
    _random_params,
    _suggest_params,
    evaluate_raw_trial_objective,
    run_sweep,
    write_candidate_artifacts,
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
BASELINE_WORKSPACE_DIR = WORKFLOW_DIR.parent / "baseline" / "q5-output-sink-trial-0237-workspace"
BASELINE_WORKSPACE_NETLIST = (BASELINE_WORKSPACE_DIR / "dummy_neural_amp.scs").read_text(encoding="utf-8")
BASELINE_WORKSPACE_DEVICES = (BASELINE_WORKSPACE_DIR / "devices.csv").read_text(encoding="utf-8")
OUTPUT_SHAPING_RANGES = {
    "RBUF_l": (4500, 8500),
    "CP1_m": (900, 3600),
    "CP2_m": (800, 2200),
    "CP3_m": (6500, 12000),
    "CE1_m": (52000000, 72000000),
    "CE2_m": (42000000, 90000000),
    "RQ4FB_l": (11000, 32000),
}


def _input_params(topology):
    return {
        "input_pr_topology": topology,
        "CIN1_m": 240000.0,
        "CIN2_m": 60000.0,
        "DIN1_m": 2.0,
        "DIN2_m": 3.0,
        "RBUF_l": 6000.0,
        "CP1_m": 1000.0,
        "CP2_m": 1200.0,
        "CP3_m": 7000.0,
        "CE1_m": 54000000.0,
        "CE2_m": 44000000.0,
        "RQ4FB_l": 12000.0,
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

    def test_best_always_uses_lowest_performance_before_area(self):
        best = _pick_best(None, _candidate(0.82, 100.0, 1))
        best = _pick_best(best, _candidate(0.74, 500.0, 2))
        best = _pick_best(best, _candidate(0.70, 900.0, 3))
        best = _pick_best(best, _candidate(0.75, 300.0, 4))

        self.assertEqual(best["trial_no"], 3)
        self.assertEqual(best["objective"]["performance_nrmse_combined"], 0.70)

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

    def test_candidate_artifact_contract_stays_on_area_phase(self):
        params = _input_params("b2b-cc")
        netlist, devices = build_q5_bandpass_artifacts(
            BASELINE_NETLIST,
            BASELINE_DEVICES,
            "dual-input-highpass-output-sink",
            params,
        )

        writes = {}

        def capture_write_text(path, text, *args, **kwargs):
            writes[path.name] = text
            return len(text)

        with (
            mock.patch.object(Path, "mkdir"),
            mock.patch.object(Path, "write_text", capture_write_text),
        ):
            write_candidate_artifacts(
                Path("candidate-contract-check-output"),
                candidate_id="candidate-contract-check",
                family="dual-input-highpass-output-sink",
                baseline_netlist=BASELINE_NETLIST,
                baseline_devices=BASELINE_DEVICES,
                trial_netlist=netlist,
                trial_devices=devices,
                params=params,
                objective={"objective": 0.7, "rejected": False},
            )
        proposal = json.loads(writes["proposal.json"])

        self.assertEqual(proposal["phase"], "phase2a_area")
        self.assertEqual(proposal["primary_objective"], "area")


class TestTrial0146InputPseudoResistorSweep(unittest.TestCase):
    def test_verifier_timeout_is_short_for_parallel_screening_runs(self):
        config = json.loads((WORKFLOW_DIR / "runner_config.json").read_text(encoding="utf-8"))

        self.assertEqual(config["verifier"]["timeout_seconds"], 150)

    def test_parser_accepts_configurable_cadence_workers(self):
        parsed = build_parser().parse_args(["--family", "dual-input-highpass-output-sink", "--cadence-workers", "3"])

        self.assertEqual(parsed.cadence_workers, 3)

    def test_random_sweep_uses_configured_cadence_workers_for_trial_dispatch(self):
        repo = WORKFLOW_DIR.parents[2]
        config = {
            "artifact_root": "automation_artifacts",
            "amptest_config": "amptest_v2p3/COREONLY/config.json",
            "dut_netlist": "amptest_v2p3/COREONLY/dummy_neural_amp.scs",
            "devices_csv": "amptest_v2p3/COREONLY/devices.csv",
        }
        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_run_trial(index, params, args, repo_root, config_dict, sweep_root, baseline_netlist, baseline_devices):
            nonlocal active, max_active
            trial_no = index + 1
            trial_dir = sweep_root / f"trial_{trial_no:04d}"
            trial_dir.mkdir(parents=True, exist_ok=True)
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            for name in ("proposal.json", "patch.diff", "notes.md"):
                (trial_dir / name).write_text(name, encoding="utf-8")
            return {
                "trial_no": trial_no,
                "candidate_id": f"parallel-trial-{trial_no:04d}",
                "trial_dir": str(trial_dir),
                "params": params,
                "review": {"passed": True},
                "verification_status": "passed",
                "metrics": {},
                "objective": {"objective": float(trial_no), "performance_nrmse_combined": float(trial_no), "rejected": False},
            }

        args = SimpleNamespace(
            repo_root=repo,
            config=WORKFLOW_DIR / "runner_config.json",
            family="dual-input-highpass-output-sink",
            baseline_workspace=BASELINE_WORKSPACE_DIR,
            baseline_summary=None,
            trials=2,
            timeout_seconds=None,
            study_name="parallel-workers",
            timestamp="parallel-workers",
            seed=23,
            input_pr_topology=None,
            cin2_boost_topology=None,
            no_verify=True,
            cadence_workers=2,
        )

        with (
            mock.patch.object(sweep_mod, "load_runner_config", return_value=SimpleNamespace(model_dump=lambda mode: config)),
            mock.patch.object(sweep_mod, "_load_optuna", return_value=None),
            mock.patch.object(sweep_mod, "_run_trial", side_effect=fake_run_trial),
        ):
            self.assertEqual(run_sweep(args), 0)

        self.assertEqual(max_active, 2)

    def test_parallel_launcher_covers_all_input_pr_topologies_with_timestamped_runs(self):
        launcher = (WORKFLOW_DIR / "run_5_input_pr_topology_sweeps.bat").read_text(encoding="utf-8")

        for topology in ("b2b-cc", "b2b-ca", "dual-b2b", "series2-cc", "reverse-antiparallel"):
            self.assertIn(topology, launcher)
        self.assertIn("Get-Date -Format yyyyMMdd-HHmmss", launcher)
        self.assertIn("run_q5_ac_shape_sweeps.bat", launcher)
        self.assertIn("CADENCE_WORKERS", launcher)

    def test_candidate_id_includes_sweep_timestamp_to_avoid_parallel_remote_collisions(self):
        spec = _family_spec("dual-input-highpass-output-sink")

        self.assertEqual(
            _candidate_id(spec, "q5-trial0146-b2b-cc-2000", 1),
            "q5-bp-dual-input-hp-output-sink-q5-trial0146-b2b-cc-2000-trial-0001",
        )

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
        self.assertIn("RBUF VOUT GND GND res_high_po_5p73 l=6000u w=5.73u m=1", netlist)
        self.assertIn("RQ4FB VOUT BQ4 GND res_high_po_5p73 l=12000u w=5.73u m=1", netlist)
        self.assertIn("CP1 N1 GND GND cap_vpp_11p5x11p7_m1m4_noshield m=1000", netlist)
        self.assertIn("CE1 E1B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=54000000", netlist)

        device_names = {line.split(",", 1)[0] for line in devices.splitlines()[1:]}
        self.assertNotIn("RBIN1", device_names)
        self.assertNotIn("RBIN2", device_names)
        self.assertIn("DHP1A,diode,1,1.00u,1.00u,1", devices)
        self.assertIn("DHP2B,diode,1,1.00u,1.00u,1", devices)
        self.assertIn("RBUF,resistor,1,,,,1,6000u,5.73u,,,true", devices)
        self.assertIn("RQ4FB,resistor,1,,,,1,12000u,5.73u,,,true", devices)
        self.assertIn("CP1,capacitor,1,11.5u,11.7u,1000,,,,,,true", devices)
        self.assertIn("CE1,capacitor,1,11.5u,11.7u,54000000,,,,,,true", devices)

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
            for name, (low, high) in OUTPUT_SHAPING_RANGES.items():
                self.assertGreaterEqual(params[name], low)
                self.assertLessEqual(params[name], high)

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
        self.assertEqual(set(random_params), {"input_pr_topology", "CIN1_m", "CIN2_m", "DIN1_m", "DIN2_m", *OUTPUT_SHAPING_RANGES})

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
        self.assertEqual(optuna_params["RBUF_l"], 4500)
        self.assertEqual(optuna_params["CP1_m"], 900)
        self.assertEqual(optuna_params["CP2_m"], 800)
        self.assertEqual(optuna_params["CP3_m"], 6500)
        self.assertEqual(optuna_params["CE1_m"], 52000000)
        self.assertEqual(optuna_params["CE2_m"], 42000000)
        self.assertEqual(optuna_params["RQ4FB_l"], 11000)


class TestAreaReductionParallelSweeps(unittest.TestCase):
    def test_area_reduction_families_constrain_dominant_emitter_bypass_caps(self):
        capdown = _family_spec("area-capdown-retune")
        ce2_first = _family_spec("area-ce2-first-retune")

        self.assertEqual(capdown.params["CE1_m"].low, 14000000.0)
        self.assertEqual(capdown.params["CE1_m"].high, 22000000.0)
        self.assertEqual(capdown.params["CE2_m"].low, 18000000.0)
        self.assertEqual(capdown.params["CE2_m"].high, 30000000.0)

        self.assertEqual(ce2_first.params["CE1_m"].low, 18000000.0)
        self.assertEqual(ce2_first.params["CE1_m"].high, 28000000.0)
        self.assertEqual(ce2_first.params["CE2_m"].low, 14000000.0)
        self.assertEqual(ce2_first.params["CE2_m"].high, 24000000.0)

    def test_area_reduction_families_preserve_q_device_topology_and_retune_loads(self):
        params = {
            "CE1_m": 18000000.0,
            "CE2_m": 22000000.0,
            "CP1_m": 900.0,
            "CP2_m": 1600.0,
            "CP3_m": 7000.0,
            "RBUF_l": 7000.0,
            "RQ4FB_l": 9000.0,
        }

        netlist, devices = build_q5_bandpass_artifacts(
            BASELINE_WORKSPACE_NETLIST,
            BASELINE_WORKSPACE_DEVICES,
            "area-capdown-retune",
            params,
        )

        self.assertIn("Q1 N1 VIN E1 GND npn_05v5_W1p00L2p00", netlist)
        self.assertIn("Q5 VOUT BQ5 EQ5 GND npn_05v5_W1p00L1p00", netlist)
        self.assertIn("CE1 E1B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=18000000", netlist)
        self.assertIn("CE2 E2B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=22000000", netlist)
        self.assertIn("RBUF VOUT GND GND res_high_po_5p73 l=7000u w=5.73u m=1", netlist)
        self.assertIn("RQ4FB VOUT BQ4 GND res_high_po_5p73 l=9000u w=5.73u m=1", netlist)
        self.assertIn("CE1,capacitor,1,11.5u,11.7u,18000000,,,,,,true", devices)
        self.assertIn("CE2,capacitor,1,11.5u,11.7u,22000000,,,,,,true", devices)

    def test_parallel_area_launcher_runs_both_baseline_area_sweeps(self):
        launcher = (WORKFLOW_DIR / "run_area_reduction_parallel_sweeps.ps1").read_text(encoding="utf-8")

        self.assertIn("amptest_v2p3", launcher)
        self.assertIn("baseline/q5-output-sink-trial-0237-workspace", launcher)
        self.assertIn("area-capdown-retune", launcher)
        self.assertIn("area-ce2-first-retune", launcher)
        self.assertIn("Start-Job", launcher)
        self.assertIn("Wait-Job", launcher)

    def test_area_capdown_300_launcher_runs_single_focused_sweep(self):
        launcher = (WORKFLOW_DIR / "run_area_capdown_300_sweep.bat").read_text(encoding="utf-8")

        self.assertIn("--family", launcher)
        self.assertIn("area-capdown-retune", launcher)
        self.assertIn("--baseline-workspace", launcher)
        self.assertIn("baseline\\q5-output-sink-trial-0237-workspace", launcher)
        self.assertIn("--trials", launcher)
        self.assertIn("300", launcher)
        self.assertIn("area-capdown-300-", launcher)
        self.assertNotIn("area-ce2-first-retune", launcher)

    def test_area_capdown_best_300_launcher_uses_trial0146_workspace(self):
        launcher = (WORKFLOW_DIR / "run_area_capdown_best_300_sweep.bat").read_text(encoding="utf-8")

        self.assertIn("--family", launcher)
        self.assertIn("area-capdown-retune", launcher)
        self.assertIn("--baseline-workspace", launcher)
        self.assertIn("best_run\\trial_0146\\workspace", launcher)
        self.assertIn("--trials", launcher)
        self.assertIn("300", launcher)
        self.assertIn("area-capdown-best-300-", launcher)
        self.assertNotIn("baseline\\q5-output-sink-trial-0237-workspace", launcher)
        self.assertNotIn("area-ce2-first-retune", launcher)


class TestCin2SeriesBoostTopologySweep(unittest.TestCase):
    TOPOLOGY_CHOICES = (
        "candidate_01_single_series_boost",
        "candidate_02_dual_end_isolated_boost",
        "candidate_03_split_staggered_boost",
    )
    COMMON_PARAMS = {
        "CIN1_m": 2600000.0,
        "CIN2_m": 640000.0,
        "RBIN1_l": 5600.0,
        "RBIN2_l": 1200.0,
        "CE1_m": 60000000.0,
        "CE2_m": 58000000.0,
        "CP1_m": 1800.0,
        "CP2_m": 1700.0,
        "CP3_m": 9000.0,
        "RBUF_l": 7600.0,
        "RQ4FB_l": 18000.0,
    }
    COMMON_PARAM_NAMES = frozenset(COMMON_PARAMS)

    def test_family_exposes_readme_candidates_as_optuna_choice(self):
        spec = _family_spec("cin2-series-boost-topology")

        self.assertEqual(spec.params["cin2_boost_topology"].kind, "choice")
        self.assertEqual(spec.params["cin2_boost_topology"].choices, self.TOPOLOGY_CHOICES)
        self.assertIn("series_damped_cin2_boost", spec.changed_blocks)

    def test_family_opens_existing_input_and_shape_parameters_with_boost_topology(self):
        spec = _family_spec("cin2-series-boost-topology")

        for name in self.COMMON_PARAM_NAMES:
            self.assertIn(name, spec.params)
            self.assertFalse(spec.params[name].support)
        self.assertEqual(spec.params["CIN1_m"].device, "CIN1")
        self.assertEqual(spec.params["CIN2_m"].device, "CIN2")
        self.assertEqual(spec.params["RBIN1_l"].device, "RBIN1")
        self.assertEqual(spec.params["RBIN2_l"].device, "RBIN2")
        self.assertEqual(spec.params["CIN2_m"].high, 2200000.0)
        self.assertEqual(spec.params["RBUF_l"].low, 3500.0)
        self.assertEqual(spec.params["RQ4FB_l"].high, 48000.0)

    def test_builds_each_readme_candidate_branch_from_trial0146_baseline(self):
        cases = {
            "candidate_01_single_series_boost": (
                {
                    **self.COMMON_PARAMS,
                    "cin2_boost_topology": "candidate_01_single_series_boost",
                    "RZIN2_l": 180.0,
                    "CIN2A_m": 2000000.0,
                },
                [
                    f"RZIN2 B1 ZIN2A GND {RES_MODEL} l=180u w=5.73u m=1",
                    f"CIN2A ZIN2A B2 GND {CAP_MODEL} m=2000000",
                ],
                ["RZIN2", "CIN2A"],
                ["RZIN2A", "RZIN2B", "CIN2B"],
            ),
            "candidate_02_dual_end_isolated_boost": (
                {
                    **self.COMMON_PARAMS,
                    "cin2_boost_topology": "candidate_02_dual_end_isolated_boost",
                    "RZIN2A_l": 330.0,
                    "CIN2A_m": 3500000.0,
                    "RZIN2B_l": 330.0,
                },
                [
                    f"RZIN2A B1 ZIN2A GND {RES_MODEL} l=330u w=5.73u m=1",
                    f"CIN2A ZIN2A ZIN2B GND {CAP_MODEL} m=3500000",
                    f"RZIN2B ZIN2B B2 GND {RES_MODEL} l=330u w=5.73u m=1",
                ],
                ["RZIN2A", "CIN2A", "RZIN2B"],
                ["RZIN2 ", "CIN2B"],
            ),
            "candidate_03_split_staggered_boost": (
                {
                    **self.COMMON_PARAMS,
                    "cin2_boost_topology": "candidate_03_split_staggered_boost",
                    "RZIN2A_l": 220.0,
                    "CIN2A_m": 1800000.0,
                    "RZIN2B_l": 900.0,
                    "CIN2B_m": 2200000.0,
                },
                [
                    f"RZIN2A B1 ZIN2A GND {RES_MODEL} l=220u w=5.73u m=1",
                    f"CIN2A ZIN2A B2 GND {CAP_MODEL} m=1800000",
                    f"RZIN2B B1 ZIN2B GND {RES_MODEL} l=900u w=5.73u m=1",
                    f"CIN2B ZIN2B B2 GND {CAP_MODEL} m=2200000",
                ],
                ["RZIN2A", "CIN2A", "RZIN2B", "CIN2B"],
                ["RZIN2 "],
            ),
        }

        for topology, (params, expected_lines, expected_devices, absent_tokens) in cases.items():
            with self.subTest(topology=topology):
                netlist, devices = build_q5_bandpass_artifacts(
                    BASELINE_NETLIST,
                    BASELINE_DEVICES,
                    "cin2-series-boost-topology",
                    params,
                )

                for expected_line in expected_lines:
                    self.assertIn(expected_line, netlist)
                for device in expected_devices:
                    self.assertIn(f"{device},", devices)
                for token in absent_tokens:
                    self.assertNotIn(token, netlist)
                self.assertIn(f"CIN1 VIN B1 GND {CAP_MODEL} m=2600000", netlist)
                self.assertIn(f"CIN2 B1 B2 GND {CAP_MODEL} m=640000", netlist)
                self.assertIn(f"RBIN1 B1 VREF GND {RES_MODEL} l=5600u w=5.73u m=1", netlist)
                self.assertIn(f"RBIN2 B2 VREF GND {RES_MODEL} l=1200u w=5.73u m=1", netlist)
                self.assertIn(f"CE1 E1B GND GND {CAP_MODEL} m=60000000", netlist)
                self.assertIn(f"CE2 E2B GND GND {CAP_MODEL} m=58000000", netlist)
                self.assertIn(f"CP1 N1 GND GND {CAP_MODEL} m=1800", netlist)
                self.assertIn(f"CP2 NDRV GND GND {CAP_MODEL} m=1700", netlist)
                self.assertIn(f"CP3 VOUT GND GND {CAP_MODEL} m=9000", netlist)
                self.assertIn(f"RBUF VOUT GND GND {RES_MODEL} l=7600u w=5.73u m=1", netlist)
                self.assertIn(f"RQ4FB VOUT BQ4 GND {RES_MODEL} l=18000u w=5.73u m=1", netlist)
                self.assertIn("CIN1,capacitor,1,11.5u,11.7u,2600000,,,,,,true", devices)
                self.assertIn("RBIN2,resistor,1,,,,1,1200u,5.73u,,,true", devices)
                self.assertIn("RQ4FB,resistor,1,,,,1,18000u,5.73u,,,true", devices)

    def test_optuna_and_fixed_topology_params_only_include_selected_branch_devices(self):
        spec = _family_spec("cin2-series-boost-topology")
        seen_choices = {}
        seen_float_ranges = {}

        class FakeTrial:
            def suggest_categorical(self, name, choices):
                seen_choices[name] = tuple(choices)
                return "candidate_02_dual_end_isolated_boost"

            def suggest_float(self, name, low, high, log=False):
                seen_float_ranges[name] = (low, high, log)
                return low

        optuna_params = _suggest_params(spec, FakeTrial())
        self.assertEqual(seen_choices["cin2_boost_topology"], self.TOPOLOGY_CHOICES)
        self.assertEqual(optuna_params["cin2_boost_topology"], "candidate_02_dual_end_isolated_boost")
        self.assertEqual(
            set(optuna_params),
            {"cin2_boost_topology", "RZIN2A_l", "CIN2A_m", "RZIN2B_l", *self.COMMON_PARAM_NAMES},
        )
        self.assertEqual(optuna_params["RZIN2A_l"], 120.0)
        self.assertEqual(optuna_params["CE1_m"], 38000000.0)
        self.assertEqual(optuna_params["CE2_m"], 30000000.0)
        self.assertEqual(optuna_params["CIN2A_m"], 900000.0)
        self.assertEqual(optuna_params["RZIN2B_l"], 120.0)
        self.assertEqual(seen_float_ranges["RZIN2B_l"], (120.0, 2400.0, True))
        self.assertEqual(optuna_params["CIN1_m"], 1200000.0)
        self.assertEqual(optuna_params["RBIN2_l"], 450.0)
        self.assertEqual(optuna_params["RQ4FB_l"], 8000.0)

        fixed = {"cin2_boost_topology": "candidate_03_split_staggered_boost"}
        random_params = _random_params(spec, __import__("random").Random(1), fixed)
        self.assertEqual(random_params["cin2_boost_topology"], "candidate_03_split_staggered_boost")
        self.assertEqual(
            set(random_params),
            {"cin2_boost_topology", "RZIN2A_l", "CIN2A_m", "RZIN2B_l", "CIN2B_m", *self.COMMON_PARAM_NAMES},
        )
        self.assertGreaterEqual(random_params["CIN1_m"], 1200000.0)
        self.assertLessEqual(random_params["CIN1_m"], 6000000.0)
        self.assertGreaterEqual(random_params["CIN2_m"], 250000.0)
        self.assertLessEqual(random_params["CIN2_m"], 3000000.0)
        self.assertGreaterEqual(random_params["RBIN1_l"], 1600.0)
        self.assertLessEqual(random_params["RBIN1_l"], 12000.0)
        self.assertGreaterEqual(random_params["CIN2B_m"], 700000.0)
        self.assertLessEqual(random_params["CIN2B_m"], 4500000.0)

        c03_float_ranges = {}

        class FakeC03Trial:
            def suggest_categorical(self, name, choices):
                return "candidate_03_split_staggered_boost"

            def suggest_float(self, name, low, high, log=False):
                c03_float_ranges[name] = (low, high, log)
                return low

        _suggest_params(spec, FakeC03Trial())
        self.assertEqual(c03_float_ranges["CIN1_m"], (1200000.0, 6000000.0, True))
        self.assertEqual(c03_float_ranges["CIN2_m"], (250000.0, 3000000.0, True))
        self.assertEqual(c03_float_ranges["RBIN1_l"], (1600.0, 12000.0, True))
        self.assertEqual(c03_float_ranges["CE2_m"], (30000000.0, 95000000.0, False))
        self.assertEqual(c03_float_ranges["RBUF_l"], (3500.0, 11500.0, False))
        self.assertEqual(c03_float_ranges["CIN2A_m"], (700000.0, 4000000.0, True))
        self.assertEqual(c03_float_ranges["RZIN2B_l"], (300.0, 2400.0, True))
        self.assertEqual(c03_float_ranges["CIN2B_m"], (700000.0, 4500000.0, True))

    def test_can_fix_cin2_boost_topology_for_independent_candidate_runs(self):
        parsed = build_parser().parse_args(
            [
                "--family",
                "cin2-series-boost-topology",
                "--cin2-boost-topology",
                "candidate_01_single_series_boost",
            ]
        )

        self.assertEqual(parsed.cin2_boost_topology, "candidate_01_single_series_boost")

    def test_cin2_series_boost_launcher_runs_trial0146_sweep(self):
        launcher = (WORKFLOW_DIR / "run_cin2_series_boost_topology_sweep.bat").read_text(encoding="utf-8")

        self.assertIn("--family cin2-series-boost-topology", launcher)
        self.assertIn("--baseline-workspace", launcher)
        self.assertIn("best_run\\trial_0146\\workspace", launcher)
        self.assertIn("--cin2-boost-topology", launcher)
        self.assertIn("--cadence-workers %CADENCE_WORKERS%", launcher)
        self.assertIn("cin2-series-boost-topology-", launcher)


if __name__ == "__main__":
    unittest.main()
