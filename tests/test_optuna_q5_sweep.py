import json
import uuid
import unittest
from pathlib import Path

from langgraph_runner.schemas import Proposal
from tools.optuna_q5_sweep import (
    build_q5_artifacts,
    resolve_baseline_workspace,
    write_candidate_artifacts,
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
RQ4FB VOUT BQ4 GND res_high_po_5p73 l=8132.59u w=5.73u m=1
REQ4 VDD Q4E GND res_high_po_5p73 l=118.244u w=5.73u m=1
Q4 NDRV BQ4 Q4E VDD pnp_05v5_W3p40L3p40
Q2 NDRV N1 E2 GND npn_05v5_W1p00L2p00
RE2U E2 E2B GND res_high_po_5p73 l=33.4817u w=5.73u m=1
RE2B E2B GND GND res_high_po_5p73 l=639.455u w=5.73u m=1
CE2 E2B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=47734195

Q3 VDD NDRV VOUT GND npn_05v5_W1p00L1p00
RBUF VOUT GND GND res_high_po_5p73 l=1552.33u w=5.73u m=1

CP1 N1 GND GND cap_vpp_11p5x11p7_m1m4_noshield m=2026
CP2 NDRV GND GND cap_vpp_11p5x11p7_m1m4_noshield m=522
CP3 VOUT GND GND cap_vpp_11p5x11p7_m1m4_noshield m=2156
ends dummy_neural_amp
"""

BASE_DEVICES = """name,type,count,width,length,multiplier,segments,seg_length,seg_width,ft_hz,area_p,include_in_ppa
Q1,npn,1,1.00u,2.00u,1,,,,10meg,2.0000,true
Q2,npn,1,1.00u,2.00u,1,,,,10meg,2.0000,true
Q3,npn,1,1.00u,1.00u,1,,,,10meg,1.0000,true
Q4,pnp,1,3.40u,3.40u,1,,,,10meg,11.5600,true
RVREF,resistor,1,,,,1,39409.1u,5.73u,,,true
RC1,resistor,1,,,,1,642.248u,5.73u,,,true
RE1U,resistor,1,,,,1,23.6296u,5.73u,,,true
RE1B,resistor,1,,,,1,806.725u,5.73u,,,true
RC2,resistor,1,,,,1,471.255u,5.73u,,,true
RQ4U,resistor,1,,,,1,675.815u,5.73u,,,true
RQ4R,resistor,1,,,,1,1255.49u,5.73u,,,true
RQ4FB,resistor,1,,,,1,8132.59u,5.73u,,,true
REQ4,resistor,1,,,,1,118.244u,5.73u,,,true
RE2U,resistor,1,,,,1,33.4817u,5.73u,,,true
RE2B,resistor,1,,,,1,639.455u,5.73u,,,true
RBUF,resistor,1,,,,1,1552.33u,5.73u,,,true
CE1,capacitor,1,11.5u,11.7u,26168493,,,,,,true
CE2,capacitor,1,11.5u,11.7u,47734195,,,,,,true
CP1,capacitor,1,11.5u,11.7u,2026,,,,,,true
CP2,capacitor,1,11.5u,11.7u,522,,,,,,true
CP3,capacitor,1,11.5u,11.7u,2156,,,,,,true
"""


PARAMS = {
    "lf-servo": {
        "RQ5FB_l": 22000.0,
        "RQ5REF_l": 18000.0,
        "REQ5_l": 1800.0,
        "CQ5_m": 80.0,
        "CE1_m": 30000000.0,
        "CE2_m": 44000000.0,
        "CP1_m": 1800.0,
        "CP2_m": 650.0,
        "CP3_m": 1900.0,
    },
    "output-active-sink": {
        "RQ5U_l": 30000.0,
        "RQ5B_l": 12000.0,
        "REQ5_l": 2200.0,
        "RBUF_l": 1700.0,
        "CP3_m": 1800.0,
        "RQ4FB_l": 12000.0,
    },
    "q4-reference": {
        "RQ5E_l": 90.0,
        "RQ5R_l": 2400.0,
        "RQ5C_l": 42000.0,
        "REQ4_l": 95.0,
        "RQ4U_l": 700.0,
        "RQ4R_l": 1100.0,
        "RQ4FB_l": 15000.0,
        "RC2_l": 450.0,
        "CP2_m": 700.0,
    },
    "q2-emitter-helper": {
        "RQ5U_l": 26000.0,
        "RQ5B_l": 9000.0,
        "REQ5_l": 1600.0,
        "RE2U_l": 36.0,
        "RE2B_l": 620.0,
        "CE2_m": 42000000.0,
        "RC2_l": 440.0,
        "CP2_m": 640.0,
    },
}


class TestOptunaQ5Sweep(unittest.TestCase):
    def test_builds_each_family_with_q5_and_preserves_q1_to_q4(self):
        expected_q5_lines = {
            "lf-servo": "Q5 BQ4 BQ5 EQ5 GND npn_05v5_W1p00L1p00",
            "output-active-sink": "Q5 VOUT BQ5 EQ5 GND npn_05v5_W1p00L1p00",
            "q4-reference": "Q5 BQ5 BQ5 EQ5 VDD pnp_05v5_W3p40L3p40",
            "q2-emitter-helper": "Q5 E2 BQ5 EQ5 GND npn_05v5_W1p00L1p00",
        }
        expected_support_lines = {
            "lf-servo": "REQ5 EQ5 GND GND res_high_po_5p73",
            "output-active-sink": "REQ5 EQ5 GND GND res_high_po_5p73",
            "q4-reference": "RQ5E VDD EQ5 GND res_high_po_5p73",
            "q2-emitter-helper": "REQ5 EQ5 GND GND res_high_po_5p73",
        }

        for family, q5_line in expected_q5_lines.items():
            with self.subTest(family=family):
                netlist, devices = build_q5_artifacts(BASE_NETLIST, BASE_DEVICES, family, PARAMS[family])

                for line in BASE_NETLIST.splitlines():
                    if line.startswith(("Q1 ", "Q2 ", "Q3 ", "Q4 ")):
                        self.assertIn(line, netlist)
                q_names = {line.split()[0] for line in netlist.splitlines() if line.startswith("Q")}
                self.assertEqual(q_names, {"Q1", "Q2", "Q3", "Q4", "Q5"})
                self.assertIn(q5_line, netlist)
                self.assertIn(expected_support_lines[family], netlist)
                self.assertIn("Q5,", devices)

    def test_devices_csv_tracks_q5_support_and_retuned_passives(self):
        netlist, devices = build_q5_artifacts(BASE_NETLIST, BASE_DEVICES, "lf-servo", PARAMS["lf-servo"])

        self.assertIn("RQ5FB VOUT BQ5 GND res_high_po_5p73 l=22000u w=5.73u m=1", netlist)
        self.assertIn("CQ5 BQ5 GND GND cap_vpp_11p5x11p7_m1m4_noshield m=80", netlist)
        self.assertIn("CE1 E1B GND GND cap_vpp_11p5x11p7_m1m4_noshield m=30000000", netlist)
        self.assertIn("Q5,npn,1,1.00u,1.00u,1,,,,10meg,1.0000,true", devices)
        self.assertIn("RQ5FB,resistor,1,,,,1,22000u,5.73u,,,true", devices)
        self.assertIn("CQ5,capacitor,1,11.5u,11.7u,80,,,,,,true", devices)
        self.assertIn("CE1,capacitor,1,11.5u,11.7u,30000000,,,,,,true", devices)

    def test_rejects_baseline_without_exact_four_bjt_topology(self):
        with self.assertRaisesRegex(ValueError, "exactly Q1/Q2/Q3/Q4"):
            build_q5_artifacts(BASE_NETLIST.replace("Q4 NDRV BQ4 Q4E VDD pnp_05v5_W3p40L3p40\n", ""), BASE_DEVICES, "lf-servo", PARAMS["lf-servo"])

        with self.assertRaisesRegex(ValueError, "exactly Q1/Q2/Q3/Q4"):
            build_q5_artifacts(
                BASE_NETLIST.replace("ends dummy_neural_amp", "Q5 BQ4 BQ5 EQ5 GND npn_05v5_W1p00L1p00\nends dummy_neural_amp"),
                BASE_DEVICES,
                "lf-servo",
                PARAMS["lf-servo"],
            )

        with self.assertRaisesRegex(ValueError, "exactly Q1/Q2/Q3/Q4"):
            build_q5_artifacts(
                BASE_NETLIST.replace("ends dummy_neural_amp", "Q6 BQ4 BQ6 EQ6 GND npn_05v5_W1p00L1p00\nends dummy_neural_amp"),
                BASE_DEVICES,
                "lf-servo",
                PARAMS["lf-servo"],
            )

    def test_resolves_baseline_workspace_priority(self):
        repo = _scratch("q5_explicit_baseline")
        explicit = _workspace(repo / "explicit")
        path, source = resolve_baseline_workspace(repo, explicit)
        self.assertEqual(path, explicit.resolve())
        self.assertEqual(source["source"], "explicit")

        repo = _scratch("q5_completed_baseline")
        trial = _workspace(repo / "automation_artifacts" / "sweeps" / "best-topology-fixed" / "20260607-010101" / "trial_0003" / "workspace")
        summary = trial.parent / "trial_summary.json"
        summary.write_text(json.dumps({"trial_dir": str(trial.parent), "objective": {"objective": 0.3}}), encoding="utf-8")
        (trial.parent.parent / "best_trial_summary.json").write_text(json.dumps({"trial_dir": str(trial.parent)}), encoding="utf-8")
        path, source = resolve_baseline_workspace(repo, None)
        self.assertEqual(path, trial.resolve())
        self.assertEqual(source["source"], "completed_best_topology_summary")

        repo = _scratch("q5_running_baseline")
        sweep = repo / "automation_artifacts" / "sweeps" / "best-topology-fixed" / "20260607-020202"
        rejected = _running_trial(sweep, 1, 0.01, verification_status="failed")
        accepted = _running_trial(sweep, 2, 0.2)
        better = _running_trial(sweep, 3, 0.1)
        self.assertTrue(rejected.exists())
        self.assertTrue(accepted.exists())
        path, source = resolve_baseline_workspace(repo, None)
        self.assertEqual(path, better.resolve())
        self.assertEqual(source["source"], "running_best_topology_best_so_far")
        self.assertEqual(source["candidate_id"], "best-topology-trial-0003")

        repo = _scratch("q5_strategy_baseline")
        strategy = _workspace(repo / "automation_artifacts" / "workspaces" / "p1-best")
        state = repo / "automation_artifacts" / "strategy_rotation.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"best_candidate_id": "p1-best"}), encoding="utf-8")
        path, source = resolve_baseline_workspace(repo, None)
        self.assertEqual(path, strategy.resolve())
        self.assertEqual(source["source"], "strategy_rotation")

    def test_latest_running_sweep_beats_older_completed_summary(self):
        repo = _scratch("q5_running_beats_older_completed")
        completed = _workspace(repo / "automation_artifacts" / "sweeps" / "best-topology-fixed" / "20260607-010101" / "trial_0009" / "workspace")
        (completed.parent.parent / "best_trial_summary.json").write_text(json.dumps({"trial_dir": str(completed.parent)}), encoding="utf-8")
        running_sweep = repo / "automation_artifacts" / "sweeps" / "best-topology-fixed" / "20260607-020202"
        running = _running_trial(running_sweep, 3, 0.1)

        path, source = resolve_baseline_workspace(repo, None)

        self.assertEqual(path, running.resolve())
        self.assertEqual(source["source"], "running_best_topology_best_so_far")
        self.assertEqual(source["candidate_id"], "best-topology-trial-0003")

    def test_write_candidate_artifacts_uses_existing_candidate_protocol(self):
        root = _scratch("q5_candidate_artifacts")
        netlist, devices = build_q5_artifacts(BASE_NETLIST, BASE_DEVICES, "output-active-sink", PARAMS["output-active-sink"])

        write_candidate_artifacts(
            root,
            candidate_id="q5-output-sink-trial-0001",
            family="output-active-sink",
            baseline_netlist=BASE_NETLIST,
            baseline_devices=BASE_DEVICES,
            trial_netlist=netlist,
            trial_devices=devices,
            params=PARAMS["output-active-sink"],
            objective={"objective": 0.05, "rejected": False, "reason": "passed"},
        )

        proposal = json.loads((root / "proposal.json").read_text(encoding="utf-8"))
        Proposal.model_validate(proposal)
        self.assertEqual(proposal["candidate_id"], "q5-output-sink-trial-0001")
        self.assertEqual(proposal["files_touched"], ["amptest/dummy_neural_amp.scs", "amptest/devices.csv"])
        self.assertEqual(proposal["patch"], (root / "patch.diff").read_text(encoding="utf-8"))
        self.assertIn("diff --git a/amptest/dummy_neural_amp.scs b/amptest/dummy_neural_amp.scs", proposal["patch"])
        self.assertIn("Q5 5BJT Trial", (root / "notes.md").read_text(encoding="utf-8"))


def _workspace(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "dummy_neural_amp.scs").write_text(BASE_NETLIST, encoding="utf-8")
    (path / "devices.csv").write_text(BASE_DEVICES, encoding="utf-8")
    return path


def _scratch(name: str) -> Path:
    path = Path(".test_tmp_langgraph_runner") / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _running_trial(sweep: Path, number: int, objective: float, verification_status: str = "passed") -> Path:
    workspace = _workspace(sweep / f"trial_{number:04d}" / "workspace")
    summary = {
        "trial_no": number,
        "candidate_id": f"best-topology-trial-{number:04d}",
        "trial_dir": str(workspace.parent),
        "review": {"passed": True},
        "verification_status": verification_status,
        "objective": {"objective": objective, "rejected": False},
    }
    (workspace.parent / "trial_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return workspace


if __name__ == "__main__":
    unittest.main()
