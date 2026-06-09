import csv
import io
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path

WORKFLOW_DIR = Path(__file__).resolve().parent
if str(WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_DIR))

import recursive_diode_feedback_graph as graph_module


BASELINE_WORKSPACE = WORKFLOW_DIR.parent / "best_run" / "trial_0146" / "workspace"
BASELINE_NETLIST = (BASELINE_WORKSPACE / "dummy_neural_amp.scs").read_text(encoding="utf-8")
BASELINE_DEVICES = (BASELINE_WORKSPACE / "devices.csv").read_text(encoding="utf-8")


def _metrics(performance=0.06, lower=60.0, area=100.0, gain=38.0, upper=21000.0, swing=0.2):
    return {
        "performance_nrmse_combined": performance,
        "ac": {
            "lower_3db_hz": lower,
            "midband_gain_db": gain,
            "upper_3db_hz": upper,
        },
        "tran": {"vout_peak_to_peak_v": swing},
        "area_power": {"area_total_p": area},
    }


class TestProposalValidation(unittest.TestCase):
    def test_proposal_schema_rejects_non_single_diode_edits(self):
        with self.assertRaises(ValueError):
            graph_module.SingleDiodeProposal.from_dict(
                {
                    "target_resistor": "RBIN1",
                    "orientation": "node_to_vref",
                    "diode_count": 2,
                    "rationale": "back-to-back cell",
                }
            )

    def test_proposal_schema_accepts_only_known_targets_and_orientations(self):
        proposal = graph_module.SingleDiodeProposal.from_dict(
            {
                "target_resistor": "RBIN2",
                "orientation": "vref_to_node",
                "diode_count": 1,
                "rationale": "weak reverse path",
            }
        )

        self.assertEqual(proposal.target_resistor, "RBIN2")
        self.assertEqual(proposal.orientation, "vref_to_node")

        with self.assertRaises(ValueError):
            graph_module.SingleDiodeProposal.from_dict(
                {
                    "target_resistor": "RC1",
                    "orientation": "node_to_vref",
                    "diode_count": 1,
                    "rationale": "not an input bias resistor",
                }
            )


class TestSingleDiodeEdit(unittest.TestCase):
    def test_rbin1_removal_inserts_exactly_one_diode_and_retunes_only_cin1(self):
        proposal = graph_module.SingleDiodeProposal("RBIN1", "node_to_vref", "test")

        netlist, devices = graph_module.apply_single_diode_candidate(
            BASELINE_NETLIST,
            BASELINE_DEVICES,
            proposal=proposal,
            diode_name="DLG0001",
            cap_multiplier=12345,
            diode_multiplier=3,
        )

        self.assertIn("DLG0001 B1 VREF diode_pd2nw_05v5 m=3", netlist)
        self.assertEqual(netlist.count("diode_pd2nw_05v5"), 1)
        self.assertIn("CIN1 VIN B1 GND cap_vpp_11p5x11p7_m1m4_noshield m=12345", netlist)
        self.assertIn("CIN2 B1 B2 GND cap_vpp_11p5x11p7_m1m4_noshield m=726723", netlist)
        self.assertNotIn("RBIN1 B1 VREF", netlist)
        self.assertIn("RBIN2 B2 VREF", netlist)

        names = _device_names(devices)
        self.assertIn("DLG0001", names)
        self.assertIn("RBIN2", names)
        self.assertNotIn("RBIN1", names)
        self.assertEqual(_device_row(devices, "CIN1")["multiplier"], "12345")
        self.assertEqual(_device_row(devices, "CIN2")["multiplier"], "726723")

    def test_rbin2_removal_inserts_exactly_one_diode_and_retunes_only_cin2(self):
        proposal = graph_module.SingleDiodeProposal("RBIN2", "vref_to_node", "test")

        netlist, devices = graph_module.apply_single_diode_candidate(
            BASELINE_NETLIST,
            BASELINE_DEVICES,
            proposal=proposal,
            diode_name="DLG0002",
            cap_multiplier=54321,
            diode_multiplier=5,
        )

        self.assertIn("DLG0002 VREF B2 diode_pd2nw_05v5 m=5", netlist)
        self.assertEqual(netlist.count("diode_pd2nw_05v5"), 1)
        self.assertIn("CIN1 VIN B1 GND cap_vpp_11p5x11p7_m1m4_noshield m=2486389", netlist)
        self.assertIn("CIN2 B1 B2 GND cap_vpp_11p5x11p7_m1m4_noshield m=54321", netlist)
        self.assertIn("RBIN1 B1 VREF", netlist)
        self.assertNotIn("RBIN2 B2 VREF", netlist)

        names = _device_names(devices)
        self.assertIn("DLG0002", names)
        self.assertIn("RBIN1", names)
        self.assertNotIn("RBIN2", names)
        self.assertEqual(_device_row(devices, "CIN1")["multiplier"], "2486389")
        self.assertEqual(_device_row(devices, "CIN2")["multiplier"], "54321")

    def test_candidate_edit_rejects_missing_target_resistor(self):
        proposal = graph_module.SingleDiodeProposal("RBIN1", "node_to_vref", "test")
        edited_once, edited_devices = graph_module.apply_single_diode_candidate(
            BASELINE_NETLIST,
            BASELINE_DEVICES,
            proposal=proposal,
            diode_name="DLG0001",
            cap_multiplier=1000,
            diode_multiplier=1,
        )

        with self.assertRaises(ValueError):
            graph_module.apply_single_diode_candidate(
                edited_once,
                edited_devices,
                proposal=proposal,
                diode_name="DLG0002",
                cap_multiplier=1000,
                diode_multiplier=1,
            )


class TestAcceptance(unittest.TestCase):
    def test_acceptance_requires_low_cutoff_performance_area_and_cap_reduction(self):
        baseline = graph_module.BaselineSnapshot(
            workspace_dir="baseline",
            netlist=BASELINE_NETLIST,
            devices=BASELINE_DEVICES,
            metrics=_metrics(performance=0.06, lower=60.0, area=100.0),
        )
        proposal = graph_module.SingleDiodeProposal("RBIN1", "node_to_vref", "test")
        good = _trial_summary(proposal, cin1=100.0, metrics=_metrics(performance=0.065, lower=70.0, area=80.0))

        decision = graph_module.evaluate_candidate_for_acceptance(baseline, good)

        self.assertTrue(decision.accepted)

        cases = {
            "low cutoff": _trial_summary(proposal, cin1=100.0, metrics=_metrics(performance=0.065, lower=80.0, area=80.0)),
            "performance": _trial_summary(proposal, cin1=100.0, metrics=_metrics(performance=0.071, lower=70.0, area=80.0)),
            "area": _trial_summary(proposal, cin1=100.0, metrics=_metrics(performance=0.065, lower=70.0, area=101.0)),
            "cap": _trial_summary(proposal, cin1=2_100_000.0, metrics=_metrics(performance=0.065, lower=70.0, area=80.0)),
            "review": {**good, "review": {"passed": False}},
        }
        for name, candidate in cases.items():
            with self.subTest(name=name):
                self.assertFalse(graph_module.evaluate_candidate_for_acceptance(baseline, candidate).accepted)

    def test_select_best_prefers_cap_reduction_then_area_then_performance(self):
        baseline = graph_module.BaselineSnapshot(
            workspace_dir="baseline",
            netlist=BASELINE_NETLIST,
            devices=BASELINE_DEVICES,
            metrics=_metrics(performance=0.06, lower=60.0, area=100.0),
        )
        proposal = graph_module.SingleDiodeProposal("RBIN1", "node_to_vref", "test")
        moderate_cap = _trial_summary(proposal, trial_no=1, cin1=200.0, metrics=_metrics(performance=0.061, lower=60.0, area=50.0))
        best_cap = _trial_summary(proposal, trial_no=2, cin1=100.0, metrics=_metrics(performance=0.069, lower=60.0, area=90.0))

        selected = graph_module.select_best_acceptable_trial(baseline, [moderate_cap, best_cap])

        self.assertEqual(selected["trial_no"], 2)


class TestGraphRouting(unittest.TestCase):
    def test_load_baseline_writes_json_serializable_baseline_source(self):
        timestamp = f"unit-baseline-source-{uuid.uuid4().hex}"

        result = graph_module.load_baseline_node(
            {
                "repo_root": str(WORKFLOW_DIR.parents[2]),
                "config_path": str(WORKFLOW_DIR / "runner_config.json"),
                "baseline_workspace": str(BASELINE_WORKSPACE),
                "timestamp": timestamp,
                "events": [],
            }
        )

        source_path = Path(result["sweep_root"]) / "baseline_source.json"
        source = json.loads(source_path.read_text(encoding="utf-8"))
        self.assertEqual(source["baseline_workspace"], str(BASELINE_WORKSPACE.resolve()))
        self.assertIsInstance(source["metrics_source"], str)

    def test_rejected_round_keeps_baseline_unchanged(self):
        baseline = {"workspace_dir": "baseline-a", "metrics": _metrics()}
        state = {
            "baseline": baseline,
            "round_index": 1,
            "consecutive_rejects": 0,
            "round_decision": {"accepted": False, "reason": "no passing trial"},
            "round_summary": {"round": 1},
            "rejected_chain": [],
            "accepted_chain": [],
        }

        result = graph_module.update_baseline_or_reject_node(state)

        self.assertEqual(result["baseline"], baseline)
        self.assertEqual(result["consecutive_rejects"], 1)
        self.assertEqual(len(result["rejected_chain"]), 1)
        self.assertEqual(result["accepted_chain"], [])

    def test_accepted_round_promotes_best_trial_workspace(self):
        state = {
            "baseline": {"workspace_dir": "baseline-a", "metrics": _metrics()},
            "round_index": 1,
            "consecutive_rejects": 2,
            "round_decision": {"accepted": True, "reason": "accepted"},
            "best_trial": {"trial_dir": "round/trial_0001", "workspace_dir": "round/trial_0001/workspace", "metrics": _metrics(area=80.0)},
            "round_summary": {"round": 1},
            "rejected_chain": [],
            "accepted_chain": [],
        }

        result = graph_module.update_baseline_or_reject_node(state)

        self.assertEqual(result["baseline"]["workspace_dir"], "round/trial_0001/workspace")
        self.assertEqual(result["baseline"]["metrics"]["area_power"]["area_total_p"], 80.0)
        self.assertEqual(result["consecutive_rejects"], 0)
        self.assertEqual(len(result["accepted_chain"]), 1)

    def test_route_stops_at_round_reject_and_target_limits(self):
        self.assertEqual(
            graph_module.route_next_node({"round_index": 10, "max_rounds": 10, "consecutive_rejects": 0, "available_targets": ["RBIN1"]})["route"],
            "finalize",
        )
        self.assertEqual(
            graph_module.route_next_node({"round_index": 1, "max_rounds": 10, "consecutive_rejects": 3, "available_targets": ["RBIN1"]})["route"],
            "finalize",
        )
        self.assertEqual(
            graph_module.route_next_node({"round_index": 1, "max_rounds": 10, "consecutive_rejects": 0, "available_targets": []})["route"],
            "finalize",
        )
        self.assertEqual(
            graph_module.route_next_node({"round_index": 1, "max_rounds": 10, "consecutive_rejects": 0, "available_targets": ["RBIN1"]})["route"],
            "continue",
        )

    def test_graph_compiles(self):
        compiled = graph_module.build_graph()

        self.assertTrue(hasattr(compiled, "invoke"))

    def test_finalize_writes_recursive_summary(self):
        tmp = WORKFLOW_DIR.parents[2] / "automation_artifacts" / f"test_recursive_summary_output_{uuid.uuid4().hex}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            summary_path = tmp / "recursive_summary.json"
            state = {
                "sweep_root": str(tmp),
                "accepted_chain": [{"round": 1}],
                "rejected_chain": [{"round": 2}],
                "baseline": {"workspace_dir": "best"},
                "events": [],
            }

            result = graph_module.finalize_node(state)

            self.assertTrue(summary_path.exists())
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["accepted_count"], 1)
            self.assertEqual(summary["rejected_count"], 1)
            self.assertEqual(result["events"][-1], "finalize")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _trial_summary(proposal, trial_no=1, cin1=None, cin2=None, metrics=None):
    params = {"DNEW_m": 1.0}
    if cin1 is not None:
        params["CIN1_m"] = cin1
    if cin2 is not None:
        params["CIN2_m"] = cin2
    return {
        "trial_no": trial_no,
        "candidate_id": f"trial-{trial_no}",
        "trial_dir": f"trial_{trial_no:04d}",
        "workspace_dir": f"trial_{trial_no:04d}/workspace",
        "proposal": proposal.to_dict(),
        "params": params,
        "review": {"passed": True},
        "verification_status": "passed",
        "metrics": metrics or _metrics(),
        "objective": {"rejected": False},
    }


def _device_names(text):
    return {row["name"] for row in csv.DictReader(io.StringIO(text))}


def _device_row(text, name):
    for row in csv.DictReader(io.StringIO(text)):
        if row["name"] == name:
            return row
    raise AssertionError(f"missing device row {name}")


if __name__ == "__main__":
    unittest.main()
