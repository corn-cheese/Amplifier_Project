import json
import math
import uuid
import unittest
from pathlib import Path

from tools.optuna_q5_bandpass_sweep import (
    FAMILY_SPECS,
    build_q5_bandpass_artifacts,
    evaluate_raw_trial_objective,
    resolve_baseline_workspace,
)


BASE_NETLIST = """simulator lang=spectre

subckt dummy_neural_amp GND VDD VIN VOUT VREF
RVREF VIN VREF GND res_high_po_5p73 l=39409.1u w=5.73u m=1

RC1 VDD N1 GND res_high_po_5p73 l=642.248u w=5.73u m=1
Q1 N1 VIN E1 GND npn_05v5_W1p00L2p00
RE1U E1 E1B GND res_high_po_5p73 l=23.6296u w=5.73u m=1
RE1B E1B GND GND res_high_po_5p73 l=806.725u w=5.73u m=1
CE1 E1B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=26168493

RC2 VDD NDRV GND res_high_po_5p73 l=471.255u w=5.73u m=1
RQ4U VDD BQ4 GND res_high_po_5p73 l=675.815u w=5.73u m=1
RQ4R BQ4 VREF GND res_high_po_5p73 l=1255.49u w=5.73u m=1
RQ4FB VOUT BQ4 GND res_high_po_5p73 l=7693.91u w=5.73u m=1
REQ4 VDD Q4E GND res_high_po_5p73 l=118.244u w=5.73u m=1
Q4 NDRV BQ4 Q4E VDD pnp_05v5_W3p40L3p40
Q2 NDRV N1 E2 GND npn_05v5_W1p00L2p00
RE2U E2 E2B GND res_high_po_5p73 l=33.4817u w=5.73u m=1
RE2B E2B GND GND res_high_po_5p73 l=639.455u w=5.73u m=1
CE2 E2B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=47734195

Q3 VDD NDRV VOUT GND npn_05v5_W1p00L1p00
RBUF VOUT GND GND res_high_po_5p73 l=2999.61u w=5.73u m=1
RQ5U VREF BQ5 GND res_high_po_5p73 l=5588.01u w=5.73u m=1
RQ5B BQ5 GND GND res_high_po_5p73 l=1191.29u w=5.73u m=1
REQ5 EQ5 GND GND res_high_po_5p73 l=3754.37u w=5.73u m=1
Q5 VOUT BQ5 EQ5 GND npn_05v5_W1p00L1p00

CP1 N1 GND GND cap_vpp_11p5x11p7_m1m4_noshield m=2026
CP2 NDRV GND GND cap_vpp_11p5x11p7_m1m4_noshield m=522
CP3 VOUT GND GND cap_vpp_11p5x11p7_m1m4_noshield m=3539
ends dummy_neural_amp
"""

BASE_DEVICES = """name,type,count,width,length,multiplier,segments,seg_length,seg_width,ft_hz,area_p,include_in_ppa
Q1,npn,1,1.00u,2.00u,1,,,,10meg,2.0000,true
Q2,npn,1,1.00u,2.00u,1,,,,10meg,2.0000,true
Q3,npn,1,1.00u,1.00u,1,,,,10meg,1.0000,true
Q4,pnp,1,3.40u,3.40u,1,,,,10meg,11.5600,true
Q5,npn,1,1.00u,1.00u,1,,,,10meg,1.0000,true
RVREF,resistor,1,,,,1,39409.1u,5.73u,,,true
RC1,resistor,1,,,,1,642.248u,5.73u,,,true
RE1U,resistor,1,,,,1,23.6296u,5.73u,,,true
RE1B,resistor,1,,,,1,806.725u,5.73u,,,true
RC2,resistor,1,,,,1,471.255u,5.73u,,,true
RQ4U,resistor,1,,,,1,675.815u,5.73u,,,true
RQ4R,resistor,1,,,,1,1255.49u,5.73u,,,true
RQ4FB,resistor,1,,,,1,7693.91u,5.73u,,,true
REQ4,resistor,1,,,,1,118.244u,5.73u,,,true
RE2U,resistor,1,,,,1,33.4817u,5.73u,,,true
RE2B,resistor,1,,,,1,639.455u,5.73u,,,true
RBUF,resistor,1,,,,1,2999.61u,5.73u,,,true
RQ5U,resistor,1,,,,1,5588.01u,5.73u,,,true
RQ5B,resistor,1,,,,1,1191.29u,5.73u,,,true
REQ5,resistor,1,,,,1,3754.37u,5.73u,,,true
CE1,capacitor,1,11.5u,11.7u,26168493,,,,,,true
CE2,capacitor,1,11.5u,11.7u,47734195,,,,,,true
CP1,capacitor,1,11.5u,11.7u,2026,,,,,,true
CP2,capacitor,1,11.5u,11.7u,522,,,,,,true
CP3,capacitor,1,11.5u,11.7u,3539,,,,,,true
"""

PARAMS = {
    "output-active-sink-expanded": {
        "RBUF_l": 12000.0,
        "CP1_m": 3500.0,
        "CP2_m": 2800.0,
        "CP3_m": 20000.0,
        "CE1_m": 33000000.0,
        "CE2_m": 62000000.0,
    },
    "input-highpass": {
        "CIN_m": 2500000.0,
        "RBIN_l": 42000.0,
        "CE1_m": 31000000.0,
        "CE2_m": 61000000.0,
    },
    "feedback-lowpass-n1": {
        "CM12_m": 2400.0,
        "CP1_m": 3400.0,
        "CP2_m": 2600.0,
        "CP3_m": 19000.0,
        "RQ4FB_l": 18000.0,
    },
    "feedback-lowpass-output": {
        "CMOUT_m": 1700.0,
        "CP1_m": 3300.0,
        "CP2_m": 2700.0,
        "CP3_m": 18000.0,
        "RQ4FB_l": 19000.0,
    },
    "input-highpass-output-sink": {
        "CIN_m": 3000000.0,
        "RBIN_l": 52000.0,
        "RBUF_l": 9000.0,
        "CP1_m": 3200.0,
        "CP2_m": 2500.0,
        "CP3_m": 16000.0,
        "CE1_m": 34000000.0,
        "CE2_m": 64000000.0,
        "RQ4FB_l": 21000.0,
    },
    "dual-input-highpass-output-sink": {
        "CIN1_m": 1800000.0,
        "RBIN1_l": 8000.0,
        "CIN2_m": 1200000.0,
        "RBIN2_l": 6000.0,
        "RBUF_l": 7600.0,
        "CP1_m": 2200.0,
        "CP2_m": 2100.0,
        "CP3_m": 5800.0,
        "CE1_m": 62000000.0,
        "CE2_m": 54000000.0,
        "RQ4FB_l": 11000.0,
    },
    "input-highpass-damped-miller": {
        "CIN_m": 1400000.0,
        "RBIN_l": 7000.0,
        "RZ12_l": 1600.0,
        "CM12_m": 80.0,
        "RZOUT_l": 2400.0,
        "CMOUT_m": 120.0,
        "CP1_m": 2100.0,
        "CP2_m": 2000.0,
        "CP3_m": 5200.0,
        "RQ4FB_l": 9800.0,
    },
    "lf-servo-bq4": {
        "RQ5U_l": 5600.0,
        "RQ5B_l": 1200.0,
        "RQ5FB_l": 90000.0,
        "REQ5_l": 4200.0,
        "CQ5_m": 160.0,
        "RBUF_l": 7400.0,
        "CP3_m": 5200.0,
        "CE1_m": 58000000.0,
        "CE2_m": 52000000.0,
        "RQ4FB_l": 10500.0,
    },
}


class TestOptunaQ5BandpassSweep(unittest.TestCase):
    def test_resolves_explicit_and_latest_output_active_sink_baseline(self):
        repo = _scratch("q5_bandpass_baseline")
        explicit = _workspace(repo / "explicit")
        path, source = resolve_baseline_workspace(repo, explicit, None)
        self.assertEqual(path, explicit.resolve())
        self.assertEqual(source["source"], "explicit")

        older = _sweep_summary(repo, "20260608-010101", 3)
        latest = _sweep_summary(repo, "20260608-020202", 7)
        self.assertTrue(older.exists())

        latest_workspace = (latest.parent / "trial_0007" / "workspace").resolve()
        path, source = resolve_baseline_workspace(repo, None, latest)
        self.assertEqual(path, latest_workspace)
        self.assertEqual(source["source"], "explicit_summary")

        path, source = resolve_baseline_workspace(repo, None, None)
        self.assertEqual(path, latest_workspace)
        self.assertEqual(source["source"], "latest_q5_output_active_sink_summary")
        self.assertEqual(source["candidate_id"], "q5-output-sink-trial-0007")

    def test_output_active_sink_expanded_retunes_wide_output_and_caps(self):
        netlist, devices = build_q5_bandpass_artifacts(
            BASE_NETLIST,
            BASE_DEVICES,
            "output-active-sink-expanded",
            PARAMS["output-active-sink-expanded"],
        )

        self.assertIn("RBUF VOUT GND GND res_high_po_5p73 l=12000u w=5.73u m=1", netlist)
        self.assertIn("CP1 N1 GND GND cap_vpp_11p5x11p7_m1m4_noshield m=3500", netlist)
        self.assertIn("CP2 NDRV GND GND cap_vpp_11p5x11p7_m1m4_noshield m=2800", netlist)
        self.assertIn("CP3 VOUT GND GND cap_vpp_11p5x11p7_m1m4_noshield m=20000", netlist)
        self.assertIn("CE1 E1B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=33000000", netlist)
        self.assertIn("CE2 E2B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=62000000", netlist)
        self.assertIn("Q5 VOUT BQ5 EQ5 GND npn_05v5_W1p00L1p00", netlist)
        self.assertEqual(netlist.count("Q5 VOUT BQ5 EQ5 GND"), 1)
        self.assertIn("RBUF,resistor,1,,,,1,12000u,5.73u,,,true", devices)
        self.assertIn("CP3,capacitor,1,11.5u,11.7u,20000,,,,,,true", devices)

    def test_input_highpass_rewires_only_q1_and_adds_cin_rbin(self):
        netlist, devices = build_q5_bandpass_artifacts(
            BASE_NETLIST,
            BASE_DEVICES,
            "input-highpass",
            PARAMS["input-highpass"],
        )

        self.assertIn("RVREF VIN VREF GND res_high_po_5p73 l=39409.1u w=5.73u m=1", netlist)
        self.assertNotIn("Q1 N1 VIN E1 GND npn_05v5_W1p00L2p00", netlist)
        self.assertIn("Q1 N1 B1 E1 GND npn_05v5_W1p00L2p00", netlist)
        self.assertIn("CIN VIN B1 GND cap_vpp_11p5x11p7_m1m4_noshield m=2500000", netlist)
        self.assertIn("RBIN B1 VREF GND res_high_po_5p73 l=42000u w=5.73u m=1", netlist)
        for line in BASE_NETLIST.splitlines():
            if line.startswith(("Q2 ", "Q3 ", "Q4 ", "Q5 ")):
                self.assertIn(line, netlist)
        self.assertIn("CIN,capacitor,1,11.5u,11.7u,2500000,,,,,,true", devices)
        self.assertIn("RBIN,resistor,1,,,,1,42000u,5.73u,,,true", devices)

    def test_feedback_lowpass_families_add_miller_cap_and_retune_feedback(self):
        cases = {
            "feedback-lowpass-n1": "CM12 NDRV N1 GND cap_vpp_11p5x11p7_m1m4_noshield m=2400",
            "feedback-lowpass-output": "CMOUT VOUT NDRV GND cap_vpp_11p5x11p7_m1m4_noshield m=1700",
        }

        for family, expected_line in cases.items():
            with self.subTest(family=family):
                netlist, devices = build_q5_bandpass_artifacts(BASE_NETLIST, BASE_DEVICES, family, PARAMS[family])

                self.assertIn(expected_line, netlist)
                self.assertIn("RQ4FB VOUT BQ4 GND res_high_po_5p73 l=", netlist)
                device_name = expected_line.split()[0]
                self.assertIn(f"{device_name},capacitor,1,11.5u,11.7u,", devices)
                self.assertIn("Q5 VOUT BQ5 EQ5 GND npn_05v5_W1p00L1p00", netlist)

    def test_combined_family_keeps_output_sink_and_adds_highpass_sweep(self):
        netlist, devices = build_q5_bandpass_artifacts(
            BASE_NETLIST,
            BASE_DEVICES,
            "input-highpass-output-sink",
            PARAMS["input-highpass-output-sink"],
        )

        self.assertIn("Q5 VOUT BQ5 EQ5 GND npn_05v5_W1p00L1p00", netlist)
        self.assertIn("Q1 N1 B1 E1 GND npn_05v5_W1p00L2p00", netlist)
        self.assertIn("CIN VIN B1 GND cap_vpp_11p5x11p7_m1m4_noshield m=3000000", netlist)
        self.assertIn("RBIN B1 VREF GND res_high_po_5p73 l=52000u w=5.73u m=1", netlist)
        self.assertIn("RBUF VOUT GND GND res_high_po_5p73 l=9000u w=5.73u m=1", netlist)
        self.assertIn("CP3 VOUT GND GND cap_vpp_11p5x11p7_m1m4_noshield m=16000", netlist)
        self.assertIn("RQ4FB VOUT BQ4 GND res_high_po_5p73 l=21000u w=5.73u m=1", netlist)
        self.assertIn("CIN,capacitor,1,11.5u,11.7u,3000000,,,,,,true", devices)
        self.assertIn("RBIN,resistor,1,,,,1,52000u,5.73u,,,true", devices)

    def test_combined_family_uses_boundary_focused_rbin_rbuf_ranges(self):
        spec = FAMILY_SPECS["input-highpass-output-sink"]

        rbin = spec.params["RBIN_l"]
        self.assertEqual(rbin.low, 600.0)
        self.assertEqual(rbin.high, 1800.0)
        self.assertTrue(rbin.log_scale)

        rbuf = spec.params["RBUF_l"]
        self.assertEqual(rbuf.low, 4500.0)
        self.assertEqual(rbuf.high, 10000.0)
        self.assertFalse(rbuf.log_scale)

    def test_dual_input_highpass_adds_two_coupled_q1_bias_sections(self):
        netlist, devices = build_q5_bandpass_artifacts(
            BASE_NETLIST,
            BASE_DEVICES,
            "dual-input-highpass-output-sink",
            PARAMS["dual-input-highpass-output-sink"],
        )

        self.assertIn("CIN1 VIN B1 GND cap_vpp_11p5x11p7_m1m4_noshield m=1800000", netlist)
        self.assertIn("RBIN1 B1 VREF GND res_high_po_5p73 l=8000u w=5.73u m=1", netlist)
        self.assertIn("CIN2 B1 B2 GND cap_vpp_11p5x11p7_m1m4_noshield m=1200000", netlist)
        self.assertIn("RBIN2 B2 VREF GND res_high_po_5p73 l=6000u w=5.73u m=1", netlist)
        self.assertIn("Q1 N1 B2 E1 GND npn_05v5_W1p00L2p00", netlist)
        self.assertIn("Q5 VOUT BQ5 EQ5 GND npn_05v5_W1p00L1p00", netlist)
        self.assertIn("CIN1,capacitor,1,11.5u,11.7u,1800000,,,,,,true", devices)
        self.assertIn("RBIN2,resistor,1,,,,1,6000u,5.73u,,,true", devices)

    def test_input_highpass_damped_miller_adds_series_damped_compensation(self):
        netlist, devices = build_q5_bandpass_artifacts(
            BASE_NETLIST,
            BASE_DEVICES,
            "input-highpass-damped-miller",
            PARAMS["input-highpass-damped-miller"],
        )

        self.assertIn("Q1 N1 B1 E1 GND npn_05v5_W1p00L2p00", netlist)
        self.assertIn("CIN VIN B1 GND cap_vpp_11p5x11p7_m1m4_noshield m=1400000", netlist)
        self.assertIn("RZ12 NDRV NZ12 GND res_high_po_5p73 l=1600u w=5.73u m=1", netlist)
        self.assertIn("CM12 NZ12 N1 GND cap_vpp_11p5x11p7_m1m4_noshield m=80", netlist)
        self.assertIn("RZOUT VOUT NZOUT GND res_high_po_5p73 l=2400u w=5.73u m=1", netlist)
        self.assertIn("CMOUT NZOUT NDRV GND cap_vpp_11p5x11p7_m1m4_noshield m=120", netlist)
        self.assertIn("RZ12,resistor,1,,,,1,1600u,5.73u,,,true", devices)
        self.assertIn("CMOUT,capacitor,1,11.5u,11.7u,120,,,,,,true", devices)

    def test_lf_servo_bq4_replaces_q5_sink_with_weak_bq4_servo(self):
        netlist, devices = build_q5_bandpass_artifacts(
            BASE_NETLIST,
            BASE_DEVICES,
            "lf-servo-bq4",
            PARAMS["lf-servo-bq4"],
        )

        self.assertNotIn("Q5 VOUT BQ5 EQ5 GND npn_05v5_W1p00L1p00", netlist)
        self.assertIn("Q5 BQ4 BQ5 EQ5 GND npn_05v5_W1p00L1p00", netlist)
        self.assertIn("RQ5FB VOUT BQ5 GND res_high_po_5p73 l=90000u w=5.73u m=1", netlist)
        self.assertIn("CQ5 BQ5 GND GND cap_vpp_11p5x11p7_m1m4_noshield m=160", netlist)
        self.assertIn("RBUF VOUT GND GND res_high_po_5p73 l=7400u w=5.73u m=1", netlist)
        self.assertIn("RQ5FB,resistor,1,,,,1,90000u,5.73u,,,true", devices)
        self.assertIn("CQ5,capacitor,1,11.5u,11.7u,160,,,,,,true", devices)

    def test_ac_shape_batch_launcher_runs_dual_input_sweep(self):
        batch = Path("run_q5_ac_shape_sweeps.bat").read_text(encoding="utf-8")

        self.assertIn("python -m tools.optuna_q5_bandpass_sweep", batch)
        self.assertIn("--family dual-input-highpass-output-sink", batch)
        self.assertIn('--trials %TRIALS%', batch)
        self.assertIn('--timestamp %TIMESTAMP%', batch)
        self.assertIn('set "TRIALS=2000"', batch)
        self.assertIn('set "TIMESTAMP=q5-dual-input-hp-cp3hi-6500-12000-2000"', batch)
        self.assertNotIn("--family input-highpass-output-sink", batch)
        self.assertNotIn("--family input-highpass-damped-miller", batch)
        self.assertNotIn("--family lf-servo-bq4", batch)
        self.assertNotIn("--seed", batch)

    def test_dual_input_family_uses_recentered_boundary_relief_ranges(self):
        spec = FAMILY_SPECS["dual-input-highpass-output-sink"]

        self.assertEqual(spec.params["CIN1_m"].low, 1200000.0)
        self.assertEqual(spec.params["CIN1_m"].high, 4200000.0)
        self.assertEqual(spec.params["RBIN1_l"].low, 4200.0)
        self.assertEqual(spec.params["RBIN1_l"].high, 16000.0)
        self.assertEqual(spec.params["CIN2_m"].low, 180000.0)
        self.assertEqual(spec.params["CIN2_m"].high, 900000.0)
        self.assertEqual(spec.params["RBIN2_l"].low, 900.0)
        self.assertEqual(spec.params["RBIN2_l"].high, 4200.0)
        self.assertEqual(spec.params["RBUF_l"].low, 4500.0)
        self.assertEqual(spec.params["RBUF_l"].high, 8500.0)
        self.assertEqual(spec.params["CP1_m"].low, 900.0)
        self.assertEqual(spec.params["CP1_m"].high, 2400.0)
        self.assertEqual(spec.params["CP3_m"].low, 6500.0)
        self.assertEqual(spec.params["CP3_m"].high, 12000.0)
        self.assertEqual(spec.params["RQ4FB_l"].low, 11000.0)
        self.assertEqual(spec.params["RQ4FB_l"].high, 18000.0)

    def test_raw_objective_uses_combined_performance_without_penalties(self):
        metrics = {
            "performance_nrmse_combined": 0.11661416379072626,
            "ac": {"midband_gain_db": 39.5, "upper_3db_hz": 28152.0},
            "tran": {"vout_peak_to_peak_v": 0.20, "tran_nrmse_vs_target_filter": 0.9},
        }

        result = evaluate_raw_trial_objective(True, "passed", metrics)

        self.assertFalse(result["rejected"])
        self.assertEqual(result["objective"], metrics["performance_nrmse_combined"])
        self.assertEqual(result["penalties"], {})

        rejected = evaluate_raw_trial_objective(True, "passed", {**metrics, "ac": {"midband_gain_db": 20.0, "upper_3db_hz": 28152.0}})
        self.assertTrue(rejected["rejected"])
        self.assertTrue(math.isinf(rejected["objective"]))
        self.assertEqual(rejected["reason"], "gain_collapse")


def _workspace(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "dummy_neural_amp.scs").write_text(BASE_NETLIST, encoding="utf-8")
    (path / "devices.csv").write_text(BASE_DEVICES, encoding="utf-8")
    return path.resolve()


def _scratch(name: str) -> Path:
    path = Path(".test_tmp_langgraph_runner") / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _sweep_summary(repo: Path, timestamp: str, trial_no: int) -> Path:
    trial = _workspace(repo / "automation_artifacts" / "sweeps" / "q5-output-active-sink" / timestamp / f"trial_{trial_no:04d}" / "workspace").parent
    summary = {
        "trial_no": trial_no,
        "candidate_id": f"q5-output-sink-trial-{trial_no:04d}",
        "trial_dir": str(trial),
        "objective": {"objective": 0.1, "rejected": False},
    }
    path = trial.parent / "best_trial_summary.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    return path


if __name__ == "__main__":
    unittest.main()
