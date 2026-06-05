import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
import unittest
from unittest.mock import patch

import langgraph_runner.graph as graph_module
from langgraph_runner.graph import GRAPH_NODE_NAMES, build_graph
from langgraph_runner.state_store import StateStore


def scratch_root(case: str) -> Path:
    root = Path(__file__).resolve().parents[2] / ".test_tmp_langgraph_runner" / "graph" / case
    path = root / uuid.uuid4().hex
    path.mkdir(mode=0o777, parents=True, exist_ok=True)
    return path


def write_runner_config(root: Path) -> Path:
    docs = root / "docs"
    docs.mkdir(mode=0o777, exist_ok=True)
    (docs / "contract.md").write_text("contract\n", encoding="utf-8")
    config = {
        "artifact_root": "automation_artifacts",
        "contract_path": "docs/contract.md",
        "amptest_dir": "amptest",
        "dut_netlist": "dut.sp",
        "devices_csv": "devices.csv",
        "amptest_config": "amptest.yaml",
        "candidate_generation_batch_size": 2,
        "max_active_primes_per_subagent": 1,
        "max_total_primes_per_subagent": 2,
        "agent_timeouts_seconds": {"architecture": 60, "implementation": 60},
        "verifier": {
            "command": "python -m unittest",
            "timeout_seconds": 60,
            "min_interval_seconds": 0,
            "required_outputs": ["report.json"],
        },
    }
    path = root / "runner_config.json"
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def python_command(code: str) -> str:
    return subprocess.list2cmdline([sys.executable, "-c", code])


def write_workflow_fixture(root: Path, *, batch_size: int = 1) -> Path:
    docs = root / "docs"
    docs.mkdir(mode=0o777, exist_ok=True)
    (docs / "contract.md").write_text("contract\n", encoding="utf-8")
    amptest = root / "amptest"
    amptest.mkdir(mode=0o777, exist_ok=True)
    (amptest / "dummy_neural_amp.scs").write_text(
        "simulator lang=spectre\n"
        "subckt dummy_neural_amp GND VDD VIN VOUT VREF\n"
        "R1 VDD VOUT 10k\n"
        "ends dummy_neural_amp\n",
        encoding="utf-8",
    )
    (amptest / "devices.csv").write_text(
        "name,type,count,include_in_ppa\n"
        "R1,resistor,1,true\n",
        encoding="utf-8",
    )
    (amptest / "config.json").write_text(
        json.dumps(
            {
                "dut_netlist": "dummy_neural_amp.scs",
                "dut_subckt": "dummy_neural_amp",
                "dut_pins_order": ["GND", "VDD", "VIN", "VOUT", "VREF"],
                "input_files": {"devices_csv": "devices.csv"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    verifier_code = (
        "from pathlib import Path; import json; "
        "out = Path(r'{output_dir}'); out.mkdir(parents=True, exist_ok=True); "
        "metrics = dict(performance_nrmse_combined=0.031, area_total_p=42.0, power_score_basis_w=0.0025); "
        "(out / 'ppa_metrics.json').write_text(json.dumps(metrics), encoding='utf-8'); "
        "(out / 'ppa_report.log').write_text('ppa passed\\n', encoding='utf-8'); "
        "(out / 'spectre_ac.log').write_text('ac passed\\n', encoding='utf-8'); "
        "(out / 'spectre_tran.log').write_text('tran passed\\n', encoding='utf-8'); "
        "data = dict(candidate_id='{candidate_id}', status='passed', "
        "metrics_path=str(out / 'ppa_metrics.json'), report_path=str(out / 'ppa_report.log'), "
        "spectre_logs=[str(out / 'spectre_ac.log'), str(out / 'spectre_tran.log')], "
        "performance_nrmse_combined=metrics['performance_nrmse_combined'], "
        "area_total_p=metrics['area_total_p'], power_score_basis_w=metrics['power_score_basis_w'], errors=[]); "
        "(out / 'verification.json').write_text(json.dumps(data), encoding='utf-8')"
    )
    config = {
        "artifact_root": "automation_artifacts",
        "contract_path": "docs/contract.md",
        "amptest_dir": "amptest",
        "dut_netlist": "amptest/dummy_neural_amp.scs",
        "devices_csv": "amptest/devices.csv",
        "amptest_config": "amptest/config.json",
        "candidate_generation_batch_size": batch_size,
        "max_active_primes_per_subagent": 1,
        "max_total_primes_per_subagent": 2,
        "agent_timeouts_seconds": {"subagent": 60, "prime": 30, "top": 30},
        "verifier": {
            "command": python_command(verifier_code),
            "timeout_seconds": 10,
            "min_interval_seconds": 0,
            "required_outputs": [
                "verification.json",
                "ppa_metrics.json",
                "ppa_report.log",
                "spectre_ac.log",
                "spectre_tran.log",
            ],
        },
    }
    path = root / "runner_config.json"
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def valid_patch_text() -> str:
    return (
        "diff --git a/amptest/dummy_neural_amp.scs b/amptest/dummy_neural_amp.scs\n"
        "--- a/amptest/dummy_neural_amp.scs\n"
        "+++ b/amptest/dummy_neural_amp.scs\n"
        "@@ -1,4 +1,4 @@\n"
        " simulator lang=spectre\n"
        " subckt dummy_neural_amp GND VDD VIN VOUT VREF\n"
        "-R1 VDD VOUT 10k\n"
        "+R1 VDD VOUT 20k\n"
        " ends dummy_neural_amp\n"
        "diff --git a/amptest/devices.csv b/amptest/devices.csv\n"
        "--- a/amptest/devices.csv\n"
        "+++ b/amptest/devices.csv\n"
        "@@ -1,2 +1,2 @@\n"
        " name,type,count,include_in_ppa\n"
        "-R1,resistor,1,true\n"
        "+R1,resistor,2,true\n"
    )


class FakeAgentRunner:
    def __init__(self):
        self.calls = []

    def run(self, call):
        self.calls.append(call)
        context = (call.context_path / "context.md").read_text(encoding="utf-8")
        values = {}
        for line in context.splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                values[key] = value
        candidate_id = values["candidate_id"]
        proposal = {
            "candidate_id": candidate_id,
            "phase": values["phase"],
            "agent": values["# Agent Context"] if "# Agent Context" in values else "architecture",
            "hypothesis": "Use a larger feedback resistor in the fixture circuit.",
            "primary_objective": values["primary_objective"],
            "changed_blocks": ["feedback"],
            "files_touched": ["amptest/dummy_neural_amp.scs", "amptest/devices.csv"],
            "expected_effect": {
                "performance_nrmse_combined": "decrease",
                "area_total_p": "increase",
                "power_score_basis_w": "no_major_change",
            },
            "risk": "Fixture-only change.",
            "patch": valid_patch_text(),
        }
        call.output_dir.mkdir(parents=True, exist_ok=True)
        (call.output_dir / "proposal.json").write_text(json.dumps(proposal), encoding="utf-8")
        (call.output_dir / "patch.diff").write_text(valid_patch_text(), encoding="utf-8")
        (call.output_dir / "notes.md").write_text("Fixture candidate.\n", encoding="utf-8")
        (call.output_dir / "stdout.log").write_text("agent stdout\n", encoding="utf-8")
        (call.output_dir / "stderr.log").write_text("", encoding="utf-8")
        return graph_module.AgentRunResult(
            exit_code=0,
            stdout_path=call.output_dir / "stdout.log",
            stderr_path=call.output_dir / "stderr.log",
        )


def write_reviewable_candidate(paths: graph_module.ArtifactPaths, candidate_id: str) -> None:
    candidate_dir = paths.candidate_dir(candidate_id)
    workspace_dir = paths.workspace_dir(candidate_id)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    proposal = {
        "candidate_id": candidate_id,
        "phase": "phase1_performance",
        "agent": "architecture",
        "hypothesis": "Fixture candidate.",
        "primary_objective": "performance",
        "changed_blocks": ["feedback"],
        "files_touched": ["amptest/dummy_neural_amp.scs", "amptest/devices.csv"],
        "expected_effect": {
            "performance_nrmse_combined": "decrease",
            "area_total_p": "increase",
            "power_score_basis_w": "no_major_change",
        },
        "risk": "Fixture-only.",
        "patch": valid_patch_text(),
    }
    (candidate_dir / "proposal.json").write_text(json.dumps(proposal), encoding="utf-8")
    (candidate_dir / "patch.diff").write_text(valid_patch_text(), encoding="utf-8")
    (candidate_dir / "notes.md").write_text("Fixture notes.\n", encoding="utf-8")
    (workspace_dir / "dummy_neural_amp.scs").write_text(
        "simulator lang=spectre\n"
        "subckt dummy_neural_amp GND VDD VIN VOUT VREF\n"
        "R1 VDD VOUT 20k\n"
        "ends dummy_neural_amp\n",
        encoding="utf-8",
    )
    (workspace_dir / "devices.csv").write_text("name,type,count,include_in_ppa\nR1,resistor,2,true\n", encoding="utf-8")


class TestGraph(unittest.TestCase):
    def test_graph_contains_design_node_sequence(self):
        self.assertEqual(
            GRAPH_NODE_NAMES,
            [
                "load_context",
                "plan_batch",
                "spawn_subagents",
                "collect_subagent_requests",
                "spawn_prime_agents",
                "collect_prime_outputs",
                "assemble_candidate_proposals",
                "deterministic_review",
                "verify_queue",
                "evaluate_candidates",
                "top_anomaly_check",
                "record_batch",
                "route_next",
            ],
        )

    def test_graph_compiles(self):
        graph = build_graph()

        self.assertTrue(hasattr(graph, "invoke"))

    def test_stop_route_records_all_node_events(self):
        graph = build_graph()

        state = graph.invoke({"repo_root": ".", "run_id": "test", "route": "stop"})

        self.assertEqual(state["events"], GRAPH_NODE_NAMES)
        self.assertEqual(state["route"], "stop")

    def test_record_event_copies_and_appends(self):
        state = {"events": ["existing"], "repo_root": ".", "run_id": "test"}
        record_event = getattr(graph_module, "_record_event", None)

        self.assertTrue(callable(record_event), "_record_event")
        result = record_event(state, "new_event")

        self.assertEqual(result["events"], ["existing", "new_event"])
        self.assertEqual(state["events"], ["existing"])

    def test_named_boundary_nodes_exist_and_append_expected_event(self):
        cases = [
            ("load_context_node", "load_context"),
            ("plan_batch_node", "plan_batch"),
            ("deterministic_review_node", "deterministic_review"),
            ("verify_queue_node", "verify_queue"),
            ("evaluate_candidates_node", "evaluate_candidates"),
            ("record_batch_node", "record_batch"),
        ]

        for function_name, expected_event in cases:
            with self.subTest(function_name=function_name):
                node = getattr(graph_module, function_name, None)

                self.assertTrue(callable(node), function_name)
                result = node({"events": ["existing"], "repo_root": ".", "run_id": "test"})
                self.assertEqual(result["events"][-2:], ["existing", expected_event])

    def test_deterministic_review_uses_amptest_config_dut_contract(self):
        repo_root = scratch_root("deterministic_review_amptest_config")
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        amptest = repo_root / "amptest"
        candidate_dir = repo_root / "automation_artifacts" / "candidates" / candidate_id
        workspace_dir = repo_root / "automation_artifacts" / "workspaces" / candidate_id
        amptest.mkdir(mode=0o777, parents=True, exist_ok=True)
        candidate_dir.mkdir(mode=0o777, parents=True, exist_ok=True)
        workspace_dir.mkdir(mode=0o777, parents=True, exist_ok=True)
        (amptest / "config.json").write_text(
            json.dumps(
                {
                    "dut_subckt": "custom_amp",
                    "dut_pins_order": ["VIN", "VREF", "VDD", "GND", "VOUT"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (candidate_dir / "proposal.json").write_text(
            json.dumps(
                {
                    "candidate_id": candidate_id,
                    "phase": "phase1_performance",
                    "agent": "architecture",
                    "hypothesis": "Exercise config-defined DUT pin validation.",
                    "primary_objective": "performance",
                    "changed_blocks": ["dut-header"],
                    "files_touched": ["amptest/dummy_neural_amp.scs", "amptest/devices.csv"],
                    "expected_effect": {
                        "performance_nrmse_combined": "unknown",
                        "area_total_p": "no_major_change",
                        "power_score_basis_w": "no_major_change",
                    },
                    "risk": "Pin contract only.",
                    "patch": (
                        "diff --git a/amptest/dummy_neural_amp.scs b/amptest/dummy_neural_amp.scs\n"
                        "--- a/amptest/dummy_neural_amp.scs\n"
                        "+++ b/amptest/dummy_neural_amp.scs\n"
                    ),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (candidate_dir / "patch.diff").write_text(
            "diff --git a/amptest/dummy_neural_amp.scs b/amptest/dummy_neural_amp.scs\n"
            "--- a/amptest/dummy_neural_amp.scs\n"
            "+++ b/amptest/dummy_neural_amp.scs\n",
            encoding="utf-8",
        )
        (candidate_dir / "notes.md").write_text("Config-derived DUT contract test.\n", encoding="utf-8")
        (workspace_dir / "dummy_neural_amp.scs").write_text(
            "subckt custom_amp VIN VREF VDD GND VOUT\n"
            "R1 VDD VOUT 10k\n"
            "ends custom_amp\n",
            encoding="utf-8",
        )
        (workspace_dir / "devices.csv").write_text(
            "name,type,count,include_in_ppa\nR1,resistor,1,true\n",
            encoding="utf-8",
        )

        result = graph_module.deterministic_review_node(
            {
                "repo_root": str(repo_root),
                "run_id": "test",
                "artifact_root": str(repo_root / "automation_artifacts"),
                "runner_config": {
                    "dut_netlist": "amptest/dummy_neural_amp.scs",
                    "devices_csv": "amptest/devices.csv",
                    "amptest_config": "amptest/config.json",
                },
                "candidate_ids": [candidate_id],
            }
        )

        self.assertEqual(result["events"], ["deterministic_review"])
        self.assertEqual(result["review_results"][0]["errors"], [])
        self.assertTrue(result["review_results"][0]["checks"]["pin_contract"])
        self.assertTrue(result["review_results"][0]["passed"])

    def test_load_context_missing_config_records_structured_error(self):
        repo_root = scratch_root("load_context_missing_config")
        node = getattr(graph_module, "load_context_node", None)

        self.assertTrue(callable(node), "load_context_node")
        result = node({"repo_root": str(repo_root), "run_id": "test"})

        self.assertEqual(result["events"], ["load_context"])
        self.assertTrue(result["errors"])
        self.assertIn("load_context", result["errors"][0])

    def test_load_context_resolves_relative_state_path_under_repo_root(self):
        repo_root = scratch_root("load_context_relative_state_path")
        process_cwd = scratch_root("load_context_relative_state_path_cwd")
        write_runner_config(repo_root)
        original_cwd = Path.cwd()

        try:
            os.chdir(process_cwd)
            result = graph_module.load_context_node(
                {
                    "repo_root": str(repo_root),
                    "run_id": "test",
                    "state_path": "custom_artifacts/state.json",
                }
            )
        finally:
            os.chdir(original_cwd)

        self.assertEqual(result["artifact_root"], str(repo_root.resolve() / "custom_artifacts"))
        self.assertEqual(result["state_path"], str(repo_root.resolve() / "custom_artifacts" / "state.json"))
        self.assertTrue((repo_root / "custom_artifacts" / "state.json").exists())

    def test_invalid_route_stops_and_records_error(self):
        graph = build_graph()

        state = graph.invoke({"repo_root": ".", "run_id": "test", "route": "invalid-route"})

        self.assertEqual(state["events"], GRAPH_NODE_NAMES)
        self.assertEqual(state["route"], "stop")
        self.assertTrue(any("route" in error for error in state["errors"]))

    def test_unhashable_route_stops_and_records_error(self):
        graph = build_graph()

        state = graph.invoke({"repo_root": ".", "run_id": "test", "route": ["next_batch"]})

        self.assertEqual(state["events"], GRAPH_NODE_NAMES)
        self.assertEqual(state["route"], "stop")
        self.assertTrue(any("route" in error for error in state["errors"]))

    def test_falsey_non_string_route_stops_and_records_error(self):
        graph = build_graph()

        state = graph.invoke({"repo_root": ".", "run_id": "test", "route": []})

        self.assertEqual(state["events"], GRAPH_NODE_NAMES)
        self.assertEqual(state["route"], "stop")
        self.assertTrue(any("route_next" in error for error in state["errors"]))

    def test_evaluate_candidates_handles_malformed_result_payloads(self):
        result = graph_module.evaluate_candidates_node(
            {
                "repo_root": ".",
                "run_id": "test",
                "runner_state": graph_module.RunnerState.initial("contract").model_dump(mode="json"),
                "candidate_ids": ["candidate-1"],
                "review_results": [{"candidate_id": "candidate-1", "passed": True}],
                "verification_results": [{"status": "passed"}],
            }
        )

        self.assertEqual(result["events"], ["evaluate_candidates"])
        self.assertEqual(result["candidate_evaluations"][0]["status"], "error")
        self.assertEqual(result["candidate_evaluations"][0]["reason"], "missing_verification_result")
        self.assertTrue(any("evaluate_candidates" in error for error in result["errors"]))
        self.assertTrue(any("candidate_id" in error for error in result["errors"]))

    def test_evaluate_candidates_handles_none_result_payloads(self):
        result = graph_module.evaluate_candidates_node(
            {
                "repo_root": ".",
                "run_id": "test",
                "runner_state": graph_module.RunnerState.initial("contract").model_dump(mode="json"),
                "candidate_ids": ["candidate-1"],
                "review_results": None,
                "verification_results": None,
            }
        )

        self.assertEqual(result["events"], ["evaluate_candidates"])
        self.assertEqual(result["candidate_evaluations"][0]["status"], "error")
        self.assertEqual(result["candidate_evaluations"][0]["reason"], "missing_review_result")
        self.assertTrue(any("review_results" in error and "not a list" in error for error in result["errors"]))
        self.assertTrue(any("verification_results" in error and "not a list" in error for error in result["errors"]))

    def test_verify_queue_runs_verifier_only_for_review_passing_candidates(self):
        repo_root = scratch_root("verify_queue_review_gate")
        config_path = write_workflow_fixture(repo_root, batch_size=2)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        passing = "p1-b001-c01-arch-20260605-120000"
        rejected = "p1-b001-c02-arch-20260605-120000"
        for candidate_id in (passing, rejected):
            paths.candidate_dir(candidate_id).mkdir(parents=True, exist_ok=True)
            paths.workspace_dir(candidate_id).mkdir(parents=True, exist_ok=True)

        result = graph_module.verify_queue_node(
            {
                "repo_root": str(repo_root),
                "run_id": "test",
                "config_path": str(config_path),
                "artifact_root": str(paths.artifact_root),
                "runner_config": json.loads(config_path.read_text(encoding="utf-8")),
                "candidate_ids": [passing, rejected],
                "review_results": [
                    {"candidate_id": passing, "passed": True, "checks": {}, "errors": []},
                    {"candidate_id": rejected, "passed": False, "checks": {}, "errors": ["illegal_file_touch"]},
                ],
            }
        )

        self.assertEqual(result["verification_queue"], [passing])
        self.assertEqual(result["verification_results"][0]["candidate_id"], passing)
        self.assertTrue((paths.candidate_dir(passing) / "verification.json").exists())
        self.assertFalse((paths.candidate_dir(rejected) / "verification.json").exists())

    def test_assembly_error_does_not_enter_review_or_verification(self):
        repo_root = scratch_root("assembly_error_review_gate")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        write_reviewable_candidate(paths, candidate_id)

        reviewed = graph_module.deterministic_review_node(
            {
                "repo_root": str(repo_root),
                "run_id": "test",
                "config_path": str(config_path),
                "artifact_root": str(paths.artifact_root),
                "runner_config": json.loads(config_path.read_text(encoding="utf-8")),
                "candidate_ids": [candidate_id],
                "candidate_artifacts": [
                    {
                        "candidate_id": candidate_id,
                        "status": "error",
                        "errors": ["patch_apply_failed"],
                        "candidate_dir": str(paths.candidate_dir(candidate_id)),
                        "workspace_dir": str(paths.workspace_dir(candidate_id)),
                    }
                ],
            }
        )
        verified = graph_module.verify_queue_node(
            {
                **reviewed,
                "runner_config": json.loads(config_path.read_text(encoding="utf-8")),
            }
        )

        self.assertFalse(reviewed["review_results"][0]["passed"])
        self.assertIn("assembly_failed", reviewed["review_results"][0]["errors"])
        self.assertEqual(verified["verification_queue"], [])
        self.assertFalse((paths.candidate_dir(candidate_id) / "verification.json").exists())

    def test_evaluate_candidates_writes_verdict_for_every_candidate(self):
        repo_root = scratch_root("evaluate_writes_verdicts")
        config_path = write_workflow_fixture(repo_root, batch_size=2)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        state = StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        accepted = "p1-b001-c01-arch-20260605-120000"
        rejected = "p1-b001-c02-arch-20260605-120000"
        for candidate_id in (accepted, rejected):
            paths.candidate_dir(candidate_id).mkdir(parents=True, exist_ok=True)
        verification = {
            "candidate_id": accepted,
            "status": "passed",
            "metrics_path": str(paths.candidate_dir(accepted) / "ppa_metrics.json"),
            "report_path": str(paths.candidate_dir(accepted) / "ppa_report.log"),
            "spectre_logs": [],
            "performance_nrmse_combined": 0.031,
            "area_total_p": 42.0,
            "power_score_basis_w": 0.0025,
            "errors": [],
        }

        result = graph_module.evaluate_candidates_node(
            {
                "repo_root": str(repo_root),
                "run_id": "test",
                "config_path": str(config_path),
                "artifact_root": str(paths.artifact_root),
                "runner_state": state.model_dump(mode="json"),
                "candidate_ids": [accepted, rejected],
                "review_results": [
                    {"candidate_id": accepted, "passed": True, "checks": {}, "errors": []},
                    {"candidate_id": rejected, "passed": False, "checks": {}, "errors": ["illegal_file_touch"]},
                ],
                "verification_results": [verification],
            }
        )

        self.assertEqual(
            {item["candidate_id"]: item["status"] for item in result["candidate_evaluations"]},
            {accepted: "accepted", rejected: "rejected"},
        )
        self.assertEqual(json.loads((paths.candidate_dir(accepted) / "verdict.json").read_text(encoding="utf-8"))["status"], "accepted")
        self.assertEqual(json.loads((paths.candidate_dir(rejected) / "verdict.json").read_text(encoding="utf-8"))["status"], "rejected")

    def test_record_batch_promotes_single_accepted_candidate_and_updates_canonical_state(self):
        repo_root = scratch_root("record_batch_promotes")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        runner_state = StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        workspace = paths.workspace_dir(candidate_id)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "dummy_neural_amp.scs").write_text(
            "simulator lang=spectre\nsubckt dummy_neural_amp GND VDD VIN VOUT VREF\nR1 VDD VOUT 20k\nends dummy_neural_amp\n",
            encoding="utf-8",
        )
        (workspace / "devices.csv").write_text("name,type,count,include_in_ppa\nR1,resistor,2,true\n", encoding="utf-8")
        paths.candidate_dir(candidate_id).mkdir(parents=True, exist_ok=True)
        metrics = {"performance_nrmse_combined": 0.031, "area_total_p": 42.0, "power_score_basis_w": 0.0025}

        result = graph_module.record_batch_node(
            {
                "repo_root": str(repo_root),
                "run_id": "test",
                "config_path": str(config_path),
                "artifact_root": str(paths.artifact_root),
                "contract_path": str(repo_root / "docs" / "contract.md"),
                "runner_config": json.loads(config_path.read_text(encoding="utf-8")),
                "runner_state": runner_state.model_dump(mode="json"),
                "batch_assignments": [
                    {
                        "candidate_id": candidate_id,
                        "batch_id": "p1-b001",
                        "role": "architecture",
                        "phase": "phase1_performance",
                        "primary_objective": "performance",
                    }
                ],
                "candidate_evaluations": [
                    {"candidate_id": candidate_id, "status": "accepted", "reason": "accepted", "metrics": metrics}
                ],
            }
        )

        self.assertEqual(result["promoted_candidate_id"], candidate_id)
        self.assertIn("R1 VDD VOUT 20k", (repo_root / "amptest" / "dummy_neural_amp.scs").read_text(encoding="utf-8"))
        ledger_lines = paths.ledger_jsonl.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(ledger_lines), 1)
        self.assertEqual(json.loads(ledger_lines[0])["status"], "accepted")
        updated = StateStore(paths, repo_root / "docs" / "contract.md").load_state()
        self.assertEqual(updated.batch_no, 1)
        self.assertEqual(updated.accepted_candidate_id, candidate_id)

    def test_record_batch_skips_canonical_mutation_on_human_interrupt(self):
        repo_root = scratch_root("record_batch_human_interrupt")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        runner_state = StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        workspace = paths.workspace_dir(candidate_id)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "dummy_neural_amp.scs").write_text(
            "simulator lang=spectre\nsubckt dummy_neural_amp GND VDD VIN VOUT VREF\nR1 VDD VOUT 20k\nends dummy_neural_amp\n",
            encoding="utf-8",
        )
        (workspace / "devices.csv").write_text("name,type,count,include_in_ppa\nR1,resistor,2,true\n", encoding="utf-8")
        metrics = {"performance_nrmse_combined": 0.031, "area_total_p": 42.0, "power_score_basis_w": 0.0025}

        result = graph_module.record_batch_node(
            {
                "repo_root": str(repo_root),
                "run_id": "test",
                "config_path": str(config_path),
                "artifact_root": str(paths.artifact_root),
                "contract_path": str(repo_root / "docs" / "contract.md"),
                "runner_config": json.loads(config_path.read_text(encoding="utf-8")),
                "runner_state": runner_state.model_dump(mode="json"),
                "batch_assignments": [
                    {
                        "candidate_id": candidate_id,
                        "batch_id": "p1-b001",
                        "role": "architecture",
                        "phase": "phase1_performance",
                        "primary_objective": "performance",
                    }
                ],
                "candidate_evaluations": [
                    {"candidate_id": candidate_id, "status": "accepted", "reason": "accepted", "metrics": metrics}
                ],
                "top_decision": {
                    "decision": "human_interrupt",
                    "reason": "Need operator confirmation.",
                },
            }
        )

        self.assertIsNone(result["promoted_candidate_id"])
        self.assertEqual(paths.ledger_jsonl.read_text(encoding="utf-8"), "")
        self.assertEqual(StateStore(paths, repo_root / "docs" / "contract.md").load_state().batch_no, 0)
        self.assertIn("R1 VDD VOUT 10k", (repo_root / "amptest" / "dummy_neural_amp.scs").read_text(encoding="utf-8"))

    def test_record_batch_skips_canonical_mutation_on_rerun_verification(self):
        repo_root = scratch_root("record_batch_rerun_verification")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        runner_state = StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        paths.workspace_dir(candidate_id).mkdir(parents=True, exist_ok=True)
        (paths.workspace_dir(candidate_id) / "dummy_neural_amp.scs").write_text("accepted\n", encoding="utf-8")
        (paths.workspace_dir(candidate_id) / "devices.csv").write_text("accepted\n", encoding="utf-8")

        result = graph_module.record_batch_node(
            {
                "repo_root": str(repo_root),
                "run_id": "test",
                "config_path": str(config_path),
                "artifact_root": str(paths.artifact_root),
                "contract_path": str(repo_root / "docs" / "contract.md"),
                "runner_config": json.loads(config_path.read_text(encoding="utf-8")),
                "runner_state": runner_state.model_dump(mode="json"),
                "candidate_evaluations": [
                    {
                        "candidate_id": candidate_id,
                        "status": "accepted",
                        "reason": "accepted",
                        "metrics": {"performance_nrmse_combined": 0.031, "area_total_p": 42.0, "power_score_basis_w": 0.0025},
                    }
                ],
                "top_decision": {"decision": "rerun_verification", "reason": "Rerun requested."},
            }
        )

        self.assertIsNone(result["promoted_candidate_id"])
        self.assertEqual(paths.ledger_jsonl.read_text(encoding="utf-8"), "")

    def test_record_batch_prevalidates_ledger_before_promotion_or_append(self):
        repo_root = scratch_root("record_batch_prevalidates_ledger")
        config_path = write_workflow_fixture(repo_root, batch_size=2)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        runner_state = StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        winner = "p1-b001-c01-arch-20260605-120000"
        invalid = "p1-b001-c02-bad-20260605-120000"
        paths.workspace_dir(winner).mkdir(parents=True, exist_ok=True)
        (paths.workspace_dir(winner) / "dummy_neural_amp.scs").write_text("accepted\n", encoding="utf-8")
        (paths.workspace_dir(winner) / "devices.csv").write_text("accepted\n", encoding="utf-8")
        metrics = {"performance_nrmse_combined": 0.031, "area_total_p": 42.0, "power_score_basis_w": 0.0025}

        result = graph_module.record_batch_node(
            {
                "repo_root": str(repo_root),
                "run_id": "test",
                "config_path": str(config_path),
                "artifact_root": str(paths.artifact_root),
                "contract_path": str(repo_root / "docs" / "contract.md"),
                "runner_config": json.loads(config_path.read_text(encoding="utf-8")),
                "runner_state": runner_state.model_dump(mode="json"),
                "batch_assignments": [
                    {"candidate_id": winner, "batch_id": "p1-b001", "role": "architecture", "phase": "phase1_performance", "primary_objective": "performance"},
                    {"candidate_id": invalid, "batch_id": "p1-b001", "role": "not-a-role", "phase": "phase1_performance", "primary_objective": "performance"},
                ],
                "candidate_evaluations": [
                    {"candidate_id": winner, "status": "accepted", "reason": "accepted", "metrics": metrics},
                    {"candidate_id": invalid, "status": "rejected", "reason": "bad role", "metrics": {}},
                ],
            }
        )

        self.assertTrue(any("precompute ledger" in error for error in result["errors"]))
        self.assertEqual(paths.ledger_jsonl.read_text(encoding="utf-8"), "")
        self.assertIn("R1 VDD VOUT 10k", (repo_root / "amptest" / "dummy_neural_amp.scs").read_text(encoding="utf-8"))

    def test_top_human_interrupt_writes_pending_file_and_routes_to_interrupt(self):
        repo_root = scratch_root("top_human_interrupt")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        interrupt_decision = {
            "decision": "human_interrupt",
            "reason": "Need operator confirmation.",
            "anomaly_level": "warning",
            "candidate_ids": ["cid"],
            "next_batch_strategy": "Wait for response.",
            "human_interrupt": {
                "required": True,
                "question": "Continue?",
                "recommended_action": "continue",
                "evidence_paths": [],
            },
        }

        checked = graph_module.top_anomaly_check_node(
            {
                "repo_root": str(repo_root),
                "run_id": "manual",
                "config_path": str(config_path),
                "artifact_root": str(paths.artifact_root),
                "top_decision": interrupt_decision,
            }
        )
        routed = graph_module._route_next({**checked, "route": "next_batch"})

        self.assertTrue((paths.run_dir("manual") / "human_interrupt.json").exists())
        self.assertEqual(routed["route"], "human_interrupt")

    def test_resume_human_response_consumes_pending_interrupt_without_spawning_new_agents(self):
        repo_root = scratch_root("resume_human_response_pending")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        run_dir = paths.run_dir("manual")
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "human_interrupt.json").write_text(
            json.dumps(
                {
                    "decision": "human_interrupt",
                    "reason": "Need operator confirmation.",
                    "human_interrupt": {"required": True, "question": "Continue?"},
                }
            ),
            encoding="utf-8",
        )

        with patch("langgraph_runner.graph.AgentRunner", side_effect=AssertionError("resume should not spawn agents")):
            result = build_graph().invoke(
                {
                    "repo_root": str(repo_root),
                    "run_id": "manual",
                    "config_path": str(config_path),
                    "state_path": str(paths.state_json),
                    "route": "next_batch",
                    "stop_after_current_pass": True,
                    "human_response": "continue",
                },
                config={"recursion_limit": 20},
            )

        pending = json.loads((run_dir / "human_interrupt.json").read_text(encoding="utf-8"))
        self.assertEqual(pending["human_response"], "continue")
        self.assertEqual(result["route"], "stop")
        self.assertNotIn("candidate_ids", result)
        self.assertEqual(paths.ledger_jsonl.read_text(encoding="utf-8"), "")

    def test_one_bounded_batch_with_fake_agent_and_verifier_records_artifacts(self):
        repo_root = scratch_root("bounded_batch_fake_agent")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        fake_runner = FakeAgentRunner()

        with patch("langgraph_runner.graph.AgentRunner", return_value=fake_runner):
            result = build_graph().invoke(
                {
                    "repo_root": str(repo_root),
                    "run_id": "manual",
                    "config_path": str(config_path),
                    "state_path": str(paths.state_json),
                    "route": "stop",
                },
                config={"recursion_limit": 20},
            )

        candidate_id = result["candidate_ids"][0]
        candidate_dir = paths.candidate_dir(candidate_id)
        self.assertEqual(result["events"], GRAPH_NODE_NAMES)
        self.assertEqual(result["promoted_candidate_id"], candidate_id)
        self.assertTrue((candidate_dir / "proposal.json").exists())
        self.assertTrue((candidate_dir / "review.json").exists())
        self.assertTrue((candidate_dir / "verification.json").exists())
        self.assertEqual(json.loads((candidate_dir / "verdict.json").read_text(encoding="utf-8"))["status"], "accepted")
        self.assertEqual(len(paths.ledger_jsonl.read_text(encoding="utf-8").splitlines()), 1)
        self.assertIn("R1 VDD VOUT 20k", (repo_root / "amptest" / "dummy_neural_amp.scs").read_text(encoding="utf-8"))

    def test_next_batch_route_hits_recursion_limit(self):
        graph = build_graph()

        with self.assertRaises(Exception) as context:
            graph.invoke(
                {"repo_root": ".", "run_id": "test", "route": "next_batch"},
                config={"recursion_limit": 20},
            )

        self.assertIn("recursion", str(context.exception).lower())

    def test_next_batch_can_stop_after_current_pass_for_shell_cli(self):
        graph = build_graph()

        state = graph.invoke(
            {
                "repo_root": ".",
                "run_id": "test",
                "route": "next_batch",
                "stop_after_current_pass": True,
            },
            config={"recursion_limit": 20},
        )

        self.assertEqual(state["events"], GRAPH_NODE_NAMES)
        self.assertEqual(state["route"], "stop")


if __name__ == "__main__":
    unittest.main()
