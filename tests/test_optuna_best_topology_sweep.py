import json
import unittest
from pathlib import Path

from langgraph_runner.schemas import Proposal
from tools.optuna_best_topology_sweep import build_best_topology_artifacts, write_candidate_artifacts


BASE_NETLIST = """simulator lang=spectre

subckt dummy_neural_amp GND VDD VIN VOUT VREF
RVREF VIN VREF GND res_high_po_5p73 l=20000u w=5.73u m=1
RC1 VDD N1 GND res_high_po_5p73 l=560u w=5.73u m=1
Q1 N1 VIN E1 GND npn_05v5_W1p00L2p00
RE1U E1 E1B GND res_high_po_5p73 l=28u w=5.73u m=1
RE1B E1B GND GND res_high_po_5p73 l=350u w=5.73u m=1
CE1 E1B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=56000000
RC2 VDD NDRV GND res_high_po_5p73 l=410u w=5.73u m=1
RQ4U VDD BQ4 GND res_high_po_5p73 l=275u w=5.73u m=1
RQ4R BQ4 VREF GND res_high_po_5p73 l=725u w=5.73u m=1
RQ4FB VOUT BQ4 GND res_high_po_5p73 l=18000u w=5.73u m=1
REQ4 VDD Q4E GND res_high_po_5p73 l=34u w=5.73u m=1
Q4 NDRV BQ4 Q4E VDD pnp_05v5_W3p40L3p40
Q2 NDRV N1 E2 GND npn_05v5_W1p00L2p00
RE2U E2 E2B GND res_high_po_5p73 l=30u w=5.73u m=1
RE2B E2B GND GND res_high_po_5p73 l=350u w=5.73u m=1
CE2 E2B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=54000000
Q3 VDD NDRV VOUT GND npn_05v5_W1p00L1p00
RBUF VOUT GND GND res_high_po_5p73 l=820u w=5.73u m=1
CP1 N1 GND GND cap_vpp_11p5x11p7_m1m4_noshield m=950
CP2 NDRV GND GND cap_vpp_11p5x11p7_m1m4_noshield m=1400
CP3 VOUT GND GND cap_vpp_11p5x11p7_m1m4_noshield m=800
ends dummy_neural_amp
"""

BASE_DEVICES = """name,type,count,width,length,multiplier,segments,seg_length,seg_width,ft_hz,area_p,include_in_ppa
Q1,npn,1,1.00u,2.00u,1,,,,10meg,2.0000,true
Q2,npn,1,1.00u,2.00u,1,,,,10meg,2.0000,true
Q3,npn,1,1.00u,1.00u,1,,,,10meg,1.0000,true
Q4,pnp,1,3.40u,3.40u,1,,,,10meg,11.5600,true
RC1,resistor,1,,,,1,560u,5.73u,,,true
RC2,resistor,1,,,,1,410u,5.73u,,,true
RQ4FB,resistor,1,,,,1,18000u,5.73u,,,true
CE1,capacitor,1,11.5u,11.7u,56000000,,,,,,true
CP2,capacitor,1,11.5u,11.7u,1400,,,,,,true
"""


class TestOptunaBestTopologySweep(unittest.TestCase):
    def test_retunes_values_without_changing_topology_device_lines(self):
        params = {
            "RC1_l": 600.0,
            "RC2_l": 390.0,
            "RQ4FB_l": 22000.0,
            "CE1_m": 62000000.0,
            "CP2_m": 1750.0,
        }

        netlist, devices = build_best_topology_artifacts(BASE_NETLIST, BASE_DEVICES, params)

        for line in BASE_NETLIST.splitlines():
            if line.startswith(("Q1 ", "Q2 ", "Q3 ", "Q4 ")):
                self.assertIn(line, netlist)
        self.assertIn("RC1 VDD N1 GND res_high_po_5p73 l=600u w=5.73u m=1", netlist)
        self.assertIn("RC2 VDD NDRV GND res_high_po_5p73 l=390u w=5.73u m=1", netlist)
        self.assertIn("RQ4FB VOUT BQ4 GND res_high_po_5p73 l=22000u w=5.73u m=1", netlist)
        self.assertIn("CE1 E1B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=62000000", netlist)
        self.assertIn("CP2 NDRV GND GND cap_vpp_11p5x11p7_m1m4_noshield m=1750", netlist)
        self.assertIn("RC1,resistor,1,,,,1,600u,5.73u,,,true", devices)
        self.assertIn("RC2,resistor,1,,,,1,390u,5.73u,,,true", devices)
        self.assertIn("CE1,capacitor,1,11.5u,11.7u,62000000,,,,,,true", devices)
        self.assertIn("CP2,capacitor,1,11.5u,11.7u,1750,,,,,,true", devices)

    def test_write_candidate_artifacts_uses_existing_candidate_protocol(self):
        root = Path(".test_tmp_langgraph_runner") / "best_topology_sweep_candidate"
        root.mkdir(parents=True, exist_ok=True)
        netlist, devices = build_best_topology_artifacts(
            BASE_NETLIST,
            BASE_DEVICES,
            {"RC2_l": 390.0, "CP2_m": 1750.0},
        )

        write_candidate_artifacts(
            root,
            candidate_id="best-topology-trial-0001",
            baseline_netlist=BASE_NETLIST,
            baseline_devices=BASE_DEVICES,
            trial_netlist=netlist,
            trial_devices=devices,
            params={"RC2_l": 390.0, "CP2_m": 1750.0},
            objective={"objective": 0.05, "rejected": False, "reason": "passed"},
        )

        proposal = json.loads((root / "proposal.json").read_text(encoding="utf-8"))
        Proposal.model_validate(proposal)
        self.assertEqual(proposal["candidate_id"], "best-topology-trial-0001")
        self.assertEqual(proposal["files_touched"], ["amptest/dummy_neural_amp.scs", "amptest/devices.csv"])
        self.assertEqual(proposal["patch"], (root / "patch.diff").read_text(encoding="utf-8"))
        self.assertIn("Best Topology Fixed-Structure Trial", (root / "notes.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
