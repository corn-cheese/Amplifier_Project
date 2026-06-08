import json
import math
import uuid
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.optuna_q5_bandpass_sweep import (
    FAMILY_SPECS,
    INPUT_DIODE_PSEUDO_RESISTOR_TOPOLOGIES,
    build_q5_bandpass_artifacts,
    evaluate_raw_trial_objective,
    _objective_value_for_study,
    _run_trial,
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

TRIAL_0066_NETLIST = BASE_NETLIST.replace(
    "RVREF VIN VREF GND res_high_po_5p73 l=39409.1u w=5.73u m=1\n\nRC1",
    "\n".join(
        [
            "RVREF VIN VREF GND res_high_po_5p73 l=39409.1u w=5.73u m=1",
            "CIN1 VIN B1 GND cap_vpp_11p5x11p7_m1m4_noshield m=3093904",
            "RBIN1 B1 VREF GND res_high_po_5p73 l=6096.81u w=5.73u m=1",
            "CIN2 B1 B2 GND cap_vpp_11p5x11p7_m1m4_noshield m=590811",
            "RBIN2 B2 VREF GND res_high_po_5p73 l=1707.87u w=5.73u m=1",
            "",
            "RC1",
        ]
    ),
).replace("Q1 N1 VIN E1 GND", "Q1 N1 B2 E1 GND")

TRIAL_0066_DEVICES = BASE_DEVICES + """CIN1,capacitor,1,11.5u,11.7u,3093904,,,,,,true
RBIN1,resistor,1,,,,1,6096.81u,5.73u,,,true
CIN2,capacitor,1,11.5u,11.7u,590811,,,,,,true
RBIN2,resistor,1,,,,1,1707.87u,5.73u,,,true
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
    "active-lf-servo-bq4": {
        "RQ5U_l": 6200.0,
        "RQ5B_l": 1400.0,
        "RQ5FB_l": 125000.0,
        "REQ5_l": 5200.0,
        "CQ5_m": 120.0,
        "CP1_m": 2400.0,
        "CP2_m": 1600.0,
        "CP3_m": 9000.0,
        "CE1_m": 66000000.0,
        "CE2_m": 45000000.0,
        "RQ4FB_l": 8000.0,
    },
    "q1-cap-feedback-highpass": {
        "input_pr_topology": "b2b-cc",
        "CIN1_m": 240000.0,
        "CIN2_m": 60000.0,
        "DIN1_m": 3.0,
        "DIN2_m": 4.0,
    },
    "lf-servo-damped-zero-shaping": {
        "RQ5FB_l": 400000.0,
        "CQ5_m": 40.0,
        "RZ12_l": 1800.0,
        "CM12_m": 20.0,
        "RZOUT_l": 8000.0,
        "CMOUT_m": 140.0,
        "CP1_m": 1000.0,
        "CP2_m": 800.0,
        "CP3_m": 12000.0,
        "RQ4FB_l": 8000.0,
    },
    "ce1-b2b-shunt-cc": {
        "CE1_m": 50000.0,
        "CE2_m": 45000000.0,
        "RE1B_l": 32000.0,
        "DCE1A1_m": 3.0,
        "DCE1A2_m": 3.0,
    },
    "ce1-series-extender-cc": {
        "CE1_m": 60000.0,
        "CE2_m": 44000000.0,
        "RE1B_l": 18000.0,
        "DCE1B1_m": 2.0,
        "DCE1B2_m": 2.0,
    },
    "ce1-collector-assisted-cc": {
        "CE1_m": 40000.0,
        "CE2_m": 42000000.0,
        "RE1B_l": 12000.0,
        "CME1_m": 800.0,
        "DCE1C1_m": 4.0,
        "DCE1C2_m": 4.0,
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

    def test_active_lf_servo_bq4_replaces_q5_sink_and_sweeps_analysis_knobs(self):
        netlist, devices = build_q5_bandpass_artifacts(
            BASE_NETLIST,
            BASE_DEVICES,
            "active-lf-servo-bq4",
            PARAMS["active-lf-servo-bq4"],
        )

        self.assertNotIn("Q5 VOUT BQ5 EQ5 GND npn_05v5_W1p00L1p00", netlist)
        self.assertIn("Q5 BQ4 BQ5 EQ5 GND npn_05v5_W1p00L1p00", netlist)
        self.assertIn("RQ5U VREF BQ5 GND res_high_po_5p73 l=6200u w=5.73u m=1", netlist)
        self.assertIn("RQ5B BQ5 GND GND res_high_po_5p73 l=1400u w=5.73u m=1", netlist)
        self.assertIn("RQ5FB VOUT BQ5 GND res_high_po_5p73 l=125000u w=5.73u m=1", netlist)
        self.assertIn("REQ5 EQ5 GND GND res_high_po_5p73 l=5200u w=5.73u m=1", netlist)
        self.assertIn("CQ5 BQ5 GND GND cap_vpp_11p5x11p7_m1m4_noshield m=120", netlist)
        self.assertIn("RQ4FB VOUT BQ4 GND res_high_po_5p73 l=8000u w=5.73u m=1", netlist)
        self.assertIn("CE1 E1B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=66000000", netlist)
        self.assertIn("CE2 E2B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=45000000", netlist)
        self.assertIn("CP1 N1 GND GND cap_vpp_11p5x11p7_m1m4_noshield m=2400", netlist)
        self.assertIn("CP2 NDRV GND GND cap_vpp_11p5x11p7_m1m4_noshield m=1600", netlist)
        self.assertIn("CP3 VOUT GND GND cap_vpp_11p5x11p7_m1m4_noshield m=9000", netlist)
        self.assertIn("RQ5FB,resistor,1,,,,1,125000u,5.73u,,,true", devices)
        self.assertIn("CQ5,capacitor,1,11.5u,11.7u,120,,,,,,true", devices)
        self.assertIn("CP1,capacitor,1,11.5u,11.7u,2400,,,,,,true", devices)
        self.assertIn("CP2,capacitor,1,11.5u,11.7u,1600,,,,,,true", devices)

    def test_q1_cap_feedback_highpass_replaces_rbin_with_diode_pseudoresistor(self):
        netlist, devices = build_q5_bandpass_artifacts(
            TRIAL_0066_NETLIST,
            TRIAL_0066_DEVICES,
            "q1-cap-feedback-highpass",
            PARAMS["q1-cap-feedback-highpass"],
        )

        self.assertIn("Q1 N1 B2 E1 GND npn_05v5_W1p00L2p00", netlist)
        self.assertIn("Q5 VOUT BQ5 EQ5 GND npn_05v5_W1p00L1p00", netlist)
        self.assertIn("CIN1 VIN B1 GND cap_vpp_11p5x11p7_m1m4_noshield m=240000", netlist)
        self.assertIn("CIN2 B1 B2 GND cap_vpp_11p5x11p7_m1m4_noshield m=60000", netlist)
        self.assertNotIn("RBIN1 B1 VREF", netlist)
        self.assertNotIn("RBIN2 B2 VREF", netlist)
        self.assertIn("DHP1A B1 NHP1C diode_pd2nw_05v5 m=3", netlist)
        self.assertIn("DHP1B VREF NHP1C diode_pd2nw_05v5 m=3", netlist)
        self.assertIn("DHP2A B2 NHP2C diode_pd2nw_05v5 m=4", netlist)
        self.assertIn("DHP2B VREF NHP2C diode_pd2nw_05v5 m=4", netlist)
        self.assertNotIn("CFB ", netlist)
        self.assertNotIn("RBHP ", netlist)
        self.assertIn("CIN1,capacitor,1,11.5u,11.7u,240000,,,,,,true", devices)
        self.assertIn("CIN2,capacitor,1,11.5u,11.7u,60000,,,,,,true", devices)
        self.assertNotIn("RBIN1,resistor", devices)
        self.assertNotIn("RBIN2,resistor", devices)
        self.assertIn("DHP1A,diode,1,1.00u,1.00u,1,,,,,,true", devices)
        self.assertIn("DHP2B,diode,1,1.00u,1.00u,1,,,,,,true", devices)

    def test_q1_cap_feedback_highpass_exposes_five_input_diode_topologies(self):
        spec = FAMILY_SPECS["q1-cap-feedback-highpass"]

        self.assertIsNone(spec.q1_input_node)
        self.assertEqual(spec.params["input_pr_topology"].kind, "choice")
        self.assertEqual(spec.params["input_pr_topology"].choices, INPUT_DIODE_PSEUDO_RESISTOR_TOPOLOGIES)
        self.assertEqual(len(INPUT_DIODE_PSEUDO_RESISTOR_TOPOLOGIES), 5)
        self.assertLess(spec.params["CIN1_m"].high, 3093904.0)
        self.assertLess(spec.params["CIN2_m"].high, 590811.0)
        self.assertEqual(set(spec.params), {"input_pr_topology", "CIN1_m", "CIN2_m", "DIN1_m", "DIN2_m"})

    def test_q1_input_diode_topologies_cover_back_to_back_series_and_reverse_paths(self):
        expectations = {
            "b2b-cc": ("DHP1A B1 NHP1C diode_pd2nw_05v5", 4),
            "b2b-ca": ("DHP1A NHP1A B1 diode_pd2nw_05v5", 4),
            "dual-b2b": ("DHP1D NHP1A VREF diode_pd2nw_05v5", 8),
            "series2-cc": ("DHP1C NHP1M NHP1K2 diode_pd2nw_05v5", 8),
            "reverse-antiparallel": ("DHP1A B1 VREF diode_pd2nw_05v5", 4),
        }

        for topology, (expected_line, diode_count) in expectations.items():
            with self.subTest(topology=topology):
                params = {**PARAMS["q1-cap-feedback-highpass"], "input_pr_topology": topology}
                netlist, devices = build_q5_bandpass_artifacts(
                    TRIAL_0066_NETLIST,
                    TRIAL_0066_DEVICES,
                    "q1-cap-feedback-highpass",
                    params,
                )

                self.assertIn(expected_line, netlist)
                self.assertEqual(netlist.count("diode_pd2nw_05v5"), diode_count)
                self.assertNotIn("mos", netlist.lower())
                self.assertEqual(devices.count(",diode,"), diode_count)

    def test_lf_servo_damped_zero_shaping_combines_servo_with_series_compensation(self):
        netlist, devices = build_q5_bandpass_artifacts(
            BASE_NETLIST,
            BASE_DEVICES,
            "lf-servo-damped-zero-shaping",
            PARAMS["lf-servo-damped-zero-shaping"],
        )

        self.assertIn("Q1 N1 VIN E1 GND npn_05v5_W1p00L2p00", netlist)
        self.assertNotIn("Q5 VOUT BQ5 EQ5 GND npn_05v5_W1p00L1p00", netlist)
        self.assertIn("Q5 BQ4 BQ5 EQ5 GND npn_05v5_W1p00L1p00", netlist)
        self.assertIn("RQ5FB VOUT BQ5 GND res_high_po_5p73 l=400000u w=5.73u m=1", netlist)
        self.assertIn("CQ5 BQ5 GND GND cap_vpp_11p5x11p7_m1m4_noshield m=40", netlist)
        self.assertIn("RZ12 NDRV NZ12 GND res_high_po_5p73 l=1800u w=5.73u m=1", netlist)
        self.assertIn("CM12 NZ12 N1 GND cap_vpp_11p5x11p7_m1m4_noshield m=20", netlist)
        self.assertIn("RZOUT VOUT NZOUT GND res_high_po_5p73 l=8000u w=5.73u m=1", netlist)
        self.assertIn("CMOUT NZOUT NDRV GND cap_vpp_11p5x11p7_m1m4_noshield m=140", netlist)
        self.assertIn("CP1 N1 GND GND cap_vpp_11p5x11p7_m1m4_noshield m=1000", netlist)
        self.assertIn("CP2 NDRV GND GND cap_vpp_11p5x11p7_m1m4_noshield m=800", netlist)
        self.assertIn("CP3 VOUT GND GND cap_vpp_11p5x11p7_m1m4_noshield m=12000", netlist)
        self.assertIn("RQ4FB VOUT BQ4 GND res_high_po_5p73 l=8000u w=5.73u m=1", netlist)
        self.assertIn("RQ5FB,resistor,1,,,,1,400000u,5.73u,,,true", devices)
        self.assertIn("CQ5,capacitor,1,11.5u,11.7u,40,,,,,,true", devices)
        self.assertIn("RZ12,resistor,1,,,,1,1800u,5.73u,,,true", devices)
        self.assertIn("CMOUT,capacitor,1,11.5u,11.7u,140,,,,,,true", devices)

    def test_ac_shape_batch_launcher_runs_q1_cap_feedback_highpass_sweep(self):
        batch = Path("run_q5_ac_shape_sweeps.bat").read_text(encoding="utf-8")

        self.assertIn("python -m tools.optuna_q5_bandpass_sweep", batch)
        self.assertIn("--family q1-cap-feedback-highpass", batch)
        self.assertIn('--trials %TRIALS%', batch)
        self.assertIn('--timestamp %TIMESTAMP%', batch)
        self.assertIn('set "TRIALS=2000"', batch)
        self.assertIn('set "TIMESTAMP=q5-q1-cap-feedback-highpass-rerange2-2000"', batch)
        self.assertNotIn("--family active-lf-servo-bq4", batch)
        self.assertNotIn("--family dual-input-highpass-output-sink", batch)
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
        self.assertEqual(spec.params["CP1_m"].high, 3600.0)
        self.assertEqual(spec.params["CP2_m"].low, 800.0)
        self.assertEqual(spec.params["CP2_m"].high, 2200.0)
        self.assertEqual(spec.params["CP3_m"].low, 6500.0)
        self.assertEqual(spec.params["CP3_m"].high, 12000.0)
        self.assertEqual(spec.params["CE1_m"].low, 52000000.0)
        self.assertEqual(spec.params["CE1_m"].high, 72000000.0)
        self.assertEqual(spec.params["CE2_m"].low, 42000000.0)
        self.assertEqual(spec.params["CE2_m"].high, 90000000.0)
        self.assertEqual(spec.params["RQ4FB_l"].low, 11000.0)
        self.assertEqual(spec.params["RQ4FB_l"].high, 32000.0)

    def test_active_lf_servo_family_uses_feedback_servo_and_rerun_high_side_ranges(self):
        spec = FAMILY_SPECS["active-lf-servo-bq4"]

        self.assertTrue(spec.allow_q5_lf_servo)
        self.assertEqual(set(spec.params), {
            "RQ5U_l",
            "RQ5B_l",
            "RQ5FB_l",
            "REQ5_l",
            "CQ5_m",
            "CP1_m",
            "CP2_m",
            "CP3_m",
            "CE1_m",
            "CE2_m",
            "RQ4FB_l",
        })
        expected_ranges = {
            "RQ5U_l": (6000.0, 30000.0),
            "RQ5B_l": (1200.0, 9000.0),
            "RQ5FB_l": (50000.0, 160000.0),
            "REQ5_l": (3500.0, 8000.0),
            "CQ5_m": (1.0, 250.0),
            "CP1_m": (700.0, 2400.0),
            "CP2_m": (200.0, 1600.0),
            "CP3_m": (8000.0, 14000.0),
            "CE1_m": (56000000.0, 70000000.0),
            "CE2_m": (20000000.0, 65000000.0),
            "RQ4FB_l": (2000.0, 16000.0),
        }
        for name, (low, high) in expected_ranges.items():
            with self.subTest(param=name):
                self.assertEqual(spec.params[name].low, low)
                self.assertEqual(spec.params[name].high, high)

    def test_candidate_2_and_3_families_use_analysis_knobs(self):
        cap_feedback = FAMILY_SPECS["q1-cap-feedback-highpass"]
        self.assertIsNone(cap_feedback.q1_input_node)
        self.assertFalse(cap_feedback.allow_q5_lf_servo)
        self.assertEqual(set(cap_feedback.params), {
            "input_pr_topology",
            "CIN1_m",
            "CIN2_m",
            "DIN1_m",
            "DIN2_m",
        })
        cap_feedback_ranges = {
            "CIN1_m": (20000.0, 1200000.0),
            "CIN2_m": (5000.0, 250000.0),
            "DIN1_m": (1.0, 64.0),
            "DIN2_m": (1.0, 64.0),
        }
        for name, (low, high) in cap_feedback_ranges.items():
            with self.subTest(family="q1-cap-feedback-highpass", param=name):
                self.assertEqual(cap_feedback.params[name].low, low)
                self.assertEqual(cap_feedback.params[name].high, high)

        damped = FAMILY_SPECS["lf-servo-damped-zero-shaping"]
        self.assertTrue(damped.allow_q5_lf_servo)
        self.assertEqual(set(damped.params), {
            "RQ5FB_l",
            "CQ5_m",
            "RZ12_l",
            "CM12_m",
            "RZOUT_l",
            "CMOUT_m",
            "CP1_m",
            "CP2_m",
            "CP3_m",
            "RQ4FB_l",
        })
        damped_ranges = {
            "RQ5FB_l": (250000.0, 1200000.0),
            "CQ5_m": (1.0, 120.0),
            "RZ12_l": (600.0, 4000.0),
            "CM12_m": (1.0, 80.0),
            "RZOUT_l": (4000.0, 30000.0),
            "CMOUT_m": (10.0, 180.0),
            "CP1_m": (200.0, 1800.0),
            "CP2_m": (200.0, 1400.0),
            "CP3_m": (6500.0, 22000.0),
            "RQ4FB_l": (2000.0, 16000.0),
        }
        for name, (low, high) in damped_ranges.items():
            with self.subTest(family="lf-servo-damped-zero-shaping", param=name):
                self.assertEqual(damped.params[name].low, low)
                self.assertEqual(damped.params[name].high, high)

    def test_ce1_diode_area_families_expose_three_groups_of_five_topologies(self):
        expected_groups = {
            "shunt": {
                "ce1-b2b-shunt-cc",
                "ce1-b2b-shunt-ca",
                "ce1-b2b-shunt-dual",
                "ce1-b2b-shunt-series2",
                "ce1-b2b-shunt-vref",
            },
            "series": {
                "ce1-series-extender-cc",
                "ce1-series-extender-ca",
                "ce1-series-extender-series2",
                "ce1-series-extender-split-bleed",
                "ce1-series-extender-vref",
            },
            "collector": {
                "ce1-collector-assisted-cc",
                "ce1-collector-assisted-ca",
                "ce1-collector-assisted-series2",
                "ce1-collector-assisted-damped",
                "ce1-driver-assisted-cc",
            },
        }

        all_expected = set().union(*expected_groups.values())
        self.assertTrue(all_expected.issubset(FAMILY_SPECS))
        for family in all_expected:
            with self.subTest(family=family):
                spec = FAMILY_SPECS[family]
                self.assertLessEqual(spec.params["CE1_m"].high, 200000.0)
                self.assertGreaterEqual(
                    len([name for name, param in spec.params.items() if param.kind == "diode" and param.support]),
                    2,
                )

    def test_ce1_b2b_shunt_common_cathode_generates_small_ce1_and_diode_rows(self):
        netlist, devices = build_q5_bandpass_artifacts(
            BASE_NETLIST,
            BASE_DEVICES,
            "ce1-b2b-shunt-cc",
            PARAMS["ce1-b2b-shunt-cc"],
        )

        self.assertIn("CE1 E1B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=50000", netlist)
        self.assertIn("RE1B E1B GND GND res_high_po_5p73 l=32000u w=5.73u m=1", netlist)
        self.assertIn("DCE1A1 E1B NCE1A diode_pd2nw_05v5 m=3", netlist)
        self.assertIn("DCE1A2 GND NCE1A diode_pd2nw_05v5 m=3", netlist)
        self.assertIn("CE1,capacitor,1,11.5u,11.7u,50000,,,,,,true", devices)
        self.assertIn("DCE1A1,diode,1,1.00u,1.00u,1,,,,,,true", devices)
        self.assertIn("DCE1A2,diode,1,1.00u,1.00u,1,,,,,,true", devices)

    def test_ce1_series_extender_rewires_re1b_bottom_into_leakage_node(self):
        netlist, devices = build_q5_bandpass_artifacts(
            BASE_NETLIST,
            BASE_DEVICES,
            "ce1-series-extender-cc",
            PARAMS["ce1-series-extender-cc"],
        )

        self.assertIn("RE1B E1B E1D GND res_high_po_5p73 l=18000u w=5.73u m=1", netlist)
        self.assertIn("CE1 E1B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=60000", netlist)
        self.assertIn("DCE1B1 E1D NCE1B diode_pd2nw_05v5 m=2", netlist)
        self.assertIn("DCE1B2 GND NCE1B diode_pd2nw_05v5 m=2", netlist)
        self.assertIn("RE1B,resistor,1,,,,1,18000u,5.73u,,,true", devices)
        self.assertIn("DCE1B1,diode,1,1.00u,1.00u,1,,,,,,true", devices)

    def test_ce1_collector_assisted_family_adds_miller_cap_and_reverse_leakage_path(self):
        netlist, devices = build_q5_bandpass_artifacts(
            BASE_NETLIST,
            BASE_DEVICES,
            "ce1-collector-assisted-cc",
            PARAMS["ce1-collector-assisted-cc"],
        )

        self.assertIn("CE1 E1B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=40000", netlist)
        self.assertIn("CME1 E1B N1 GND cap_vpp_11p5x11p7_m1m4_noshield m=800", netlist)
        self.assertIn("DCE1C1 E1B NCE1C diode_pd2nw_05v5 m=4", netlist)
        self.assertIn("DCE1C2 N1 NCE1C diode_pd2nw_05v5 m=4", netlist)
        self.assertIn("CME1,capacitor,1,11.5u,11.7u,800,,,,,,true", devices)
        self.assertIn("DCE1C2,diode,1,1.00u,1.00u,1,,,,,,true", devices)

    def test_raw_objective_uses_area_when_performance_is_within_0_08(self):
        metrics = {
            "performance_nrmse_combined": 0.079,
            "ac": {"midband_gain_db": 39.5, "upper_3db_hz": 28152.0},
            "tran": {"vout_peak_to_peak_v": 0.20, "tran_nrmse_vs_target_filter": 0.9},
            "area_power": {"area_total_p": 12345.0},
        }

        result = evaluate_raw_trial_objective(True, "passed", metrics)

        self.assertFalse(result["rejected"])
        self.assertEqual(result["objective"], metrics["area_power"]["area_total_p"])
        self.assertEqual(result["penalties"], {})

        too_slow = evaluate_raw_trial_objective(True, "passed", {**metrics, "performance_nrmse_combined": 0.081})
        self.assertTrue(too_slow["rejected"])
        self.assertEqual(too_slow["reason"], "performance_above_0_08")

        rejected = evaluate_raw_trial_objective(True, "passed", {**metrics, "ac": {"midband_gain_db": 20.0, "upper_3db_hz": 28152.0}})
        self.assertTrue(rejected["rejected"])
        self.assertTrue(math.isinf(rejected["objective"]))
        self.assertEqual(rejected["reason"], "gain_collapse")

    def test_rejected_objective_value_is_larger_than_area_scale(self):
        self.assertGreater(
            _objective_value_for_study({"objective": math.inf}),
            1.0e20,
        )
        self.assertEqual(
            _objective_value_for_study({"objective": 8.9e17}),
            8.9e17,
        )

    def test_q1_cap_feedback_highpass_defaults_to_trial_0066_workspace(self):
        repo = _scratch("q1_trial_0066_baseline")
        workspace = _workspace(repo / "Best" / "trial_0066" / "workspace", TRIAL_0066_NETLIST, TRIAL_0066_DEVICES)

        path, source = resolve_baseline_workspace(repo, None, None, "q1-cap-feedback-highpass")

        self.assertEqual(path, workspace)
        self.assertEqual(source["source"], "best_trial_0066")

    def test_run_trial_uses_configured_file_scope_for_review(self):
        repo = _scratch("q5_bandpass_configured_file_scope")
        amptest_dir = repo / "amptest_v2p3" / "COREONLY"
        amptest_dir.mkdir(parents=True)
        (amptest_dir / "config.json").write_text(
            json.dumps(
                {
                    "dut_subckt": "dummy_neural_amp",
                    "dut_pins_order": ["GND", "VDD", "VIN", "VOUT", "VREF"],
                }
            ),
            encoding="utf-8",
        )
        config = {
            "artifact_root": "automation_artifacts",
            "amptest_config": "amptest_v2p3/COREONLY/config.json",
            "dut_netlist": "amptest_v2p3/COREONLY/dummy_neural_amp.scs",
            "devices_csv": "amptest_v2p3/COREONLY/devices.csv",
        }
        result = _run_trial(
            0,
            PARAMS["q1-cap-feedback-highpass"],
            SimpleNamespace(family="q1-cap-feedback-highpass", no_verify=True),
            repo,
            config,
            repo / "automation_artifacts" / "sweeps" / "q5-bandpass-q1-cap-feedback-highpass" / "scope-test",
            TRIAL_0066_NETLIST,
            TRIAL_0066_DEVICES,
        )

        self.assertTrue(result["review"]["passed"], result["review"])
        proposal = json.loads((Path(result["trial_dir"]) / "proposal.json").read_text(encoding="utf-8"))
        self.assertEqual(
            proposal["files_touched"],
            ["amptest_v2p3/COREONLY/dummy_neural_amp.scs", "amptest_v2p3/COREONLY/devices.csv"],
        )


def _workspace(path: Path, netlist: str = BASE_NETLIST, devices: str = BASE_DEVICES) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "dummy_neural_amp.scs").write_text(netlist, encoding="utf-8")
    (path / "devices.csv").write_text(devices, encoding="utf-8")
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
