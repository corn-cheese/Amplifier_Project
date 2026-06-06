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
from langgraph_runner.verifier import STDERR_LOG, STDOUT_LOG


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


def write_default_shape_ppa_wrapper(root: Path) -> None:
    wrapper = root / "amptest" / "ppa_wrapper.py"
    wrapper.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "\n"
        "def main():\n"
        "    if len(sys.argv) != 4 or sys.argv[1] != 'analyze' or sys.argv[2] != '--config':\n"
        "        print('unexpected argv: ' + repr(sys.argv[1:]), file=sys.stderr)\n"
        "        return 2\n"
        "    config_path = Path(sys.argv[3])\n"
        "    local_candidate_dir = config_path.parent\n"
        "    run_dir = local_candidate_dir / 'run'\n"
        "    run_dir.mkdir(parents=True, exist_ok=True)\n"
        "    metrics = {\n"
        "        'performance_nrmse_combined': 0.031,\n"
        "        'area_power': {'area_total_p': 42.0, 'power_score_basis_w': 0.0025},\n"
        "    }\n"
        "    (run_dir / 'ppa_metrics.json').write_text(json.dumps(metrics), encoding='utf-8')\n"
        "    (run_dir / 'ppa_report.log').write_text('ppa wrapper shape passed\\n', encoding='utf-8')\n"
        "    (run_dir / 'spectre_ac.log').write_text('ac passed\\n', encoding='utf-8')\n"
        "    (run_dir / 'spectre_tran.log').write_text('tran passed\\n', encoding='utf-8')\n"
        "    return 0\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )


def write_null_performance_ppa_wrapper(root: Path) -> None:
    wrapper = root / "amptest" / "ppa_wrapper.py"
    wrapper.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "\n"
        "def main():\n"
        "    if len(sys.argv) != 4 or sys.argv[1] != 'analyze' or sys.argv[2] != '--config':\n"
        "        print('unexpected argv: ' + repr(sys.argv[1:]), file=sys.stderr)\n"
        "        return 2\n"
        "    config_path = Path(sys.argv[3])\n"
        "    run_dir = config_path.parent / 'run'\n"
        "    run_dir.mkdir(parents=True, exist_ok=True)\n"
        "    metrics = {\n"
        "        'performance_nrmse_combined': None,\n"
        "        'area_power': {'area_total_p': 1443.1374, 'power_score_basis_w': 0.0},\n"
        "    }\n"
        "    (run_dir / 'ppa_metrics.json').write_text(json.dumps(metrics), encoding='utf-8')\n"
        "    (run_dir / 'ppa_report.log').write_text('performance_nrmse_combined: None\\n', encoding='utf-8')\n"
        "    return 0\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )


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
        artifact_output = call.context_path / "output"
        artifact_output.mkdir(parents=True, exist_ok=True)
        call.output_dir.mkdir(parents=True, exist_ok=True)
        (artifact_output / "proposal.json").write_text(json.dumps(proposal), encoding="utf-8")
        (artifact_output / "patch.diff").write_text(valid_patch_text(), encoding="utf-8")
        (artifact_output / "notes.md").write_text("Fixture candidate.\n", encoding="utf-8")
        (call.output_dir / "stdout.log").write_text("agent stdout\n", encoding="utf-8")
        (call.output_dir / "stderr.log").write_text("", encoding="utf-8")
        return graph_module.AgentRunResult(
            exit_code=0,
            stdout_path=call.output_dir / "stdout.log",
            stderr_path=call.output_dir / "stderr.log",
        )


class PrimeRequestAgentRunner:
    def __init__(self):
        self.calls = []

    def run(self, call):
        self.calls.append(call)
        call.output_dir.mkdir(parents=True, exist_ok=True)
        (call.output_dir / "stdout.log").write_text("agent stdout\n", encoding="utf-8")
        (call.output_dir / "stderr.log").write_text("", encoding="utf-8")
        if call.role == "R-prime":
            (call.output_dir / "notes.md").write_text("Prime advisory notes.\n", encoding="utf-8")
            return graph_module.AgentRunResult(
                exit_code=0,
                stdout_path=call.output_dir / "stdout.log",
                stderr_path=call.output_dir / "stderr.log",
            )

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
            "agent": "architecture",
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
        artifact_output = call.context_path / "output"
        artifact_output.mkdir(parents=True, exist_ok=True)
        (artifact_output / "proposal.json").write_text(json.dumps(proposal), encoding="utf-8")
        (artifact_output / "patch.diff").write_text(valid_patch_text(), encoding="utf-8")
        (artifact_output / "notes.md").write_text("Fixture candidate.\n", encoding="utf-8")
        (artifact_output / "prime_requests.json").write_text(
            json.dumps(
                [
                    {
                        "prime_role": "R-prime",
                        "prompt": "Review implementation risk.",
                        "rationale": "Exercise batch-local prime cleanup.",
                    }
                ]
            ),
            encoding="utf-8",
        )
        return graph_module.AgentRunResult(
            exit_code=0,
            stdout_path=call.output_dir / "stdout.log",
            stderr_path=call.output_dir / "stderr.log",
        )


class NoOutputAgentRunner:
    def __init__(self):
        self.calls = []

    def run(self, call):
        self.calls.append(call)
        call.output_dir.mkdir(parents=True, exist_ok=True)
        (call.output_dir / "stdout.log").write_text("agent stdout\n", encoding="utf-8")
        (call.output_dir / "stderr.log").write_text("", encoding="utf-8")
        return graph_module.AgentRunResult(
            exit_code=0,
            stdout_path=call.output_dir / "stdout.log",
            stderr_path=call.output_dir / "stderr.log",
        )


class InvalidOutputAgentRunner:
    def __init__(self):
        self.calls = []

    def run(self, call):
        self.calls.append(call)
        artifact_output = call.context_path / "output"
        artifact_output.mkdir(parents=True, exist_ok=True)
        call.output_dir.mkdir(parents=True, exist_ok=True)
        (artifact_output / "proposal.json").write_text("{not json", encoding="utf-8")
        (artifact_output / "patch.diff").write_text("", encoding="utf-8")
        (artifact_output / "notes.md").write_text("", encoding="utf-8")
        (call.output_dir / "stdout.log").write_text("agent stdout\n", encoding="utf-8")
        (call.output_dir / "stderr.log").write_text("", encoding="utf-8")
        return graph_module.AgentRunResult(
            exit_code=0,
            stdout_path=call.output_dir / "stdout.log",
            stderr_path=call.output_dir / "stderr.log",
        )


class ExecutionFailureAgentRunner:
    def __init__(self, error_class="agent_execution_failed", exit_code=1):
        self.calls = []
        self.error_class = error_class
        self.exit_code = exit_code

    def run(self, call):
        self.calls.append(call)
        call.output_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = call.output_dir / "stdout.log"
        stderr_path = call.output_dir / "stderr.log"
        agent_run_path = call.output_dir / "agent_run.json"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("[WinError 5] access is denied\n", encoding="utf-8")
        agent_run_path.write_text(
            json.dumps(
                {
                    "command": ["codex.cmd", "exec", "-C", str(call.context_path), "-"],
                    "context_path": str(call.context_path),
                    "artifact_output_dir": str(call.context_path / "output"),
                    "log_dir": str(call.output_dir),
                    "exit_code": self.exit_code,
                    "status": "error",
                    "error_class": self.error_class,
                    "error": "[WinError 5] access is denied",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return graph_module.AgentRunResult(
            exit_code=self.exit_code,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            agent_run_path=agent_run_path,
            status="error",
            error_class=self.error_class,
            error="[WinError 5] access is denied",
            command=["codex.cmd", "exec", "-C", str(call.context_path), "-"],
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

    def test_spawn_subagents_context_uses_configured_dut_and_devices_paths(self):
        repo_root = scratch_root("spawn_subagents_configured_context_paths")
        docs = repo_root / "docs"
        circuits = repo_root / "circuits"
        accounting = repo_root / "accounting"
        docs.mkdir(parents=True, exist_ok=True)
        circuits.mkdir(parents=True, exist_ok=True)
        accounting.mkdir(parents=True, exist_ok=True)
        contract_path = docs / "contract.md"
        contract_path.write_text("Only configured DUT and devices files may change.\n", encoding="utf-8")
        (circuits / "custom_amp.scs").write_text("simulator lang=spectre\n", encoding="utf-8")
        (accounting / "custom_devices.csv").write_text("name,type\nR1,resistor\n", encoding="utf-8")
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        runner_state = StateStore(paths, contract_path).initialize()
        fake_runner = NoOutputAgentRunner()
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        assignment = {
            "candidate_id": candidate_id,
            "batch_id": "p1-b001",
            "role": "architecture",
            "phase": "phase1_performance",
            "primary_objective": "performance",
        }

        with patch("langgraph_runner.graph.AgentRunner", return_value=fake_runner):
            result = graph_module.spawn_subagents_node(
                {
                    "repo_root": str(repo_root),
                    "run_id": "manual",
                    "artifact_root": str(paths.artifact_root),
                    "contract_path": str(contract_path),
                    "runner_config": {
                        "artifact_root": "automation_artifacts",
                        "contract_path": "docs/contract.md",
                        "dut_netlist": "circuits/custom_amp.scs",
                        "devices_csv": "accounting/custom_devices.csv",
                        "agent_timeouts_seconds": {"subagent": 60},
                    },
                    "runner_state": runner_state.model_dump(mode="json"),
                    "batch_assignments": [assignment],
                }
            )

        context_text = (fake_runner.calls[0].context_path / "context.md").read_text(encoding="utf-8")
        proposal_marker = "```json\n"
        proposal_start = context_text.index(proposal_marker) + len(proposal_marker)
        proposal_end = context_text.index("\n```", proposal_start)
        proposal_contract = json.loads(context_text[proposal_start:proposal_end])
        self.assertEqual(result["events"], ["spawn_subagents"])
        self.assertEqual(proposal_contract["files_touched"], ["circuits/custom_amp.scs", "accounting/custom_devices.csv"])
        self.assertIn("Allowed file changes: circuits/custom_amp.scs and accounting/custom_devices.csv only.", context_text)
        self.assertNotIn("Allowed file changes: amptest/dummy_neural_amp.scs and amptest/devices.csv only.", context_text)

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

    def test_deterministic_review_rejects_basename_only_file_scope_for_custom_config_paths(self):
        repo_root = scratch_root("deterministic_review_rejects_basename_scope")
        amptest = repo_root / "amptest"
        amptest.mkdir(mode=0o777, parents=True, exist_ok=True)
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
        full_path_patch = (
            "diff --git a/circuits/custom_amp.scs b/circuits/custom_amp.scs\n"
            "--- a/circuits/custom_amp.scs\n"
            "+++ b/circuits/custom_amp.scs\n"
        )
        basename_patch = (
            "diff --git a/custom_amp.scs b/custom_amp.scs\n"
            "--- a/custom_amp.scs\n"
            "+++ b/custom_amp.scs\n"
        )
        cases = [
            (
                "p1-b001-c01-arch-20260605-120000",
                ["custom_amp.scs", "custom_devices.csv"],
                full_path_patch,
            ),
            (
                "p1-b001-c02-arch-20260605-120000",
                ["circuits/custom_amp.scs", "accounting/custom_devices.csv"],
                basename_patch,
            ),
        ]
        for candidate_id, files_touched, patch_text in cases:
            candidate_dir = repo_root / "automation_artifacts" / "candidates" / candidate_id
            workspace_dir = repo_root / "automation_artifacts" / "workspaces" / candidate_id
            candidate_dir.mkdir(mode=0o777, parents=True, exist_ok=True)
            workspace_dir.mkdir(mode=0o777, parents=True, exist_ok=True)
            (candidate_dir / "proposal.json").write_text(
                json.dumps(
                    {
                        "candidate_id": candidate_id,
                        "phase": "phase1_performance",
                        "agent": "architecture",
                        "hypothesis": "Exercise configured path scope validation.",
                        "primary_objective": "performance",
                        "changed_blocks": ["dut-header"],
                        "files_touched": files_touched,
                        "expected_effect": {
                            "performance_nrmse_combined": "unknown",
                            "area_total_p": "no_major_change",
                            "power_score_basis_w": "no_major_change",
                        },
                        "risk": "File scope only.",
                        "patch": patch_text,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (candidate_dir / "patch.diff").write_text(patch_text, encoding="utf-8")
            (candidate_dir / "notes.md").write_text("File scope regression test.\n", encoding="utf-8")
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
                    "dut_netlist": "circuits/custom_amp.scs",
                    "devices_csv": "accounting/custom_devices.csv",
                    "amptest_config": "amptest/config.json",
                },
                "candidate_ids": [candidate_id for candidate_id, _files_touched, _patch_text in cases],
            }
        )

        reviews = {item["candidate_id"]: item for item in result["review_results"]}
        for candidate_id, _files_touched, _patch_text in cases:
            self.assertFalse(reviews[candidate_id]["checks"]["file_scope"])
            self.assertIn("illegal_file_touch", reviews[candidate_id]["errors"])
            self.assertFalse(reviews[candidate_id]["passed"])

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

    def test_evaluate_candidates_prefers_artifact_verification_over_stale_state(self):
        repo_root = scratch_root("evaluate_prefers_artifact_verification")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        state = StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        candidate_dir = paths.candidate_dir(candidate_id)
        candidate_dir.mkdir(parents=True, exist_ok=True)
        review = {"candidate_id": candidate_id, "passed": True, "checks": {}, "errors": []}
        artifact_verification = {
            "candidate_id": candidate_id,
            "status": "error",
            "metrics_path": str(candidate_dir / "ppa_metrics.json"),
            "report_path": str(candidate_dir / "ppa_report.log"),
            "spectre_logs": [],
            "performance_nrmse_combined": 1.0,
            "area_total_p": 0.0,
            "power_score_basis_w": 0.0,
            "errors": ["verifier artifact failure"],
        }
        stale_state_verification = {
            **artifact_verification,
            "status": "passed",
            "performance_nrmse_combined": 0.031,
            "area_total_p": 42.0,
            "power_score_basis_w": 0.0025,
            "errors": [],
        }
        (candidate_dir / "review.json").write_text(json.dumps(review), encoding="utf-8")
        (candidate_dir / "verification.json").write_text(json.dumps(artifact_verification), encoding="utf-8")

        result = graph_module.evaluate_candidates_node(
            {
                "repo_root": str(repo_root),
                "run_id": "test",
                "config_path": str(config_path),
                "artifact_root": str(paths.artifact_root),
                "runner_state": state.model_dump(mode="json"),
                "candidate_ids": [candidate_id],
                "review_results": [review],
                "verification_results": [stale_state_verification],
            }
        )

        evaluation = result["candidate_evaluations"][0]
        self.assertEqual(evaluation["status"], "error")
        self.assertEqual(evaluation["reason"], "verifier artifact failure")
        self.assertEqual(json.loads((candidate_dir / "verdict.json").read_text(encoding="utf-8"))["status"], "error")

    def test_verify_queue_rerun_verification_uses_canonical_review_for_selected_candidates_and_consumes_decision(self):
        repo_root = scratch_root("verify_queue_rerun_selected")
        config_path = write_workflow_fixture(repo_root, batch_size=2)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        selected = "p1-b001-c01-arch-20260605-120000"
        unselected = "p1-b001-c02-arch-20260605-120000"
        for candidate_id in (selected, unselected):
            paths.candidate_dir(candidate_id).mkdir(parents=True, exist_ok=True)
            paths.workspace_dir(candidate_id).mkdir(parents=True, exist_ok=True)
            (paths.candidate_dir(candidate_id) / "review.json").write_text(
                json.dumps({"candidate_id": candidate_id, "passed": True, "checks": {}, "errors": []}),
                encoding="utf-8",
            )
        verified_ids = []

        def fake_run(self, candidate_id, repo_root_arg, workspace_dir, output_dir):
            del self, repo_root_arg, workspace_dir
            verified_ids.append(candidate_id)
            result = graph_module.VerificationResult(
                candidate_id=candidate_id,
                status="passed",
                metrics_path=str(output_dir / "ppa_metrics.json"),
                report_path=str(output_dir / "ppa_report.log"),
                spectre_logs=[],
                performance_nrmse_combined=0.031,
                area_total_p=42.0,
                power_score_basis_w=0.0025,
                errors=[],
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "verification.json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
            return result

        with patch("langgraph_runner.graph.Verifier.run", autospec=True, side_effect=fake_run):
            verified = graph_module.verify_queue_node(
                {
                    "repo_root": str(repo_root),
                    "run_id": "test",
                    "config_path": str(config_path),
                    "artifact_root": str(paths.artifact_root),
                    "runner_config": json.loads(config_path.read_text(encoding="utf-8")),
                    "candidate_ids": [selected, unselected],
                    "review_results": [
                        {"candidate_id": selected, "passed": False, "checks": {"stale": False}, "errors": ["stale_state_review_failed"]},
                        {"candidate_id": unselected, "passed": True, "checks": {}, "errors": []},
                    ],
                    "top_decision": {
                        "decision": "rerun_verification",
                        "reason": "Rerun selected candidate.",
                        "anomaly_level": "warning",
                        "candidate_ids": [selected],
                        "next_batch_strategy": "Rerun selected verifier output.",
                        "human_interrupt": {"required": False, "question": None, "recommended_action": None, "evidence_paths": []},
                    },
                }
            )

        checked = graph_module.top_anomaly_check_node(verified)

        self.assertEqual(verified_ids, [selected])
        self.assertEqual(verified["verification_queue"], [selected])
        self.assertEqual(verified["top_decision"], {})
        self.assertEqual(checked["top_decision"]["decision"], "continue")

    def test_evaluate_candidates_invalid_verification_artifact_does_not_fall_back_to_stale_state(self):
        repo_root = scratch_root("evaluate_invalid_verification_artifact")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        state = StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        candidate_dir = paths.candidate_dir(candidate_id)
        candidate_dir.mkdir(parents=True, exist_ok=True)
        review = {"candidate_id": candidate_id, "passed": True, "checks": {}, "errors": []}
        stale_state_verification = {
            "candidate_id": candidate_id,
            "status": "passed",
            "metrics_path": str(candidate_dir / "ppa_metrics.json"),
            "report_path": str(candidate_dir / "ppa_report.log"),
            "spectre_logs": [],
            "performance_nrmse_combined": 0.031,
            "area_total_p": 42.0,
            "power_score_basis_w": 0.0025,
            "errors": [],
        }
        (candidate_dir / "review.json").write_text(json.dumps(review), encoding="utf-8")
        (candidate_dir / "verification.json").write_text("{not json", encoding="utf-8")

        result = graph_module.evaluate_candidates_node(
            {
                "repo_root": str(repo_root),
                "run_id": "test",
                "config_path": str(config_path),
                "artifact_root": str(paths.artifact_root),
                "runner_state": state.model_dump(mode="json"),
                "candidate_ids": [candidate_id],
                "review_results": [review],
                "verification_results": [stale_state_verification],
            }
        )

        evaluation = result["candidate_evaluations"][0]
        self.assertEqual(evaluation["status"], "error")
        self.assertIn("invalid_verification_artifact", evaluation["reason"])
        self.assertTrue(any("invalid_verification_artifact" in error for error in result["errors"]))

    def test_verify_queue_invalid_review_artifact_does_not_fall_back_to_stale_state(self):
        repo_root = scratch_root("verify_queue_invalid_review_artifact")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        paths.candidate_dir(candidate_id).mkdir(parents=True, exist_ok=True)
        paths.workspace_dir(candidate_id).mkdir(parents=True, exist_ok=True)
        (paths.candidate_dir(candidate_id) / "review.json").write_text("{not json", encoding="utf-8")

        with patch("langgraph_runner.graph.Verifier.run", side_effect=AssertionError("should not verify invalid review artifact")):
            result = graph_module.verify_queue_node(
                {
                    "repo_root": str(repo_root),
                    "run_id": "test",
                    "config_path": str(config_path),
                    "artifact_root": str(paths.artifact_root),
                    "runner_config": json.loads(config_path.read_text(encoding="utf-8")),
                    "candidate_ids": [candidate_id],
                    "review_results": [{"candidate_id": candidate_id, "passed": True, "checks": {}, "errors": []}],
                }
            )

        self.assertEqual(result["verification_queue"], [])
        self.assertEqual(result["verification_results"], [])
        self.assertFalse((paths.candidate_dir(candidate_id) / "verification.json").exists())
        self.assertTrue(any("invalid_review_artifact" in error for error in result["errors"]))

    def test_verify_queue_writes_error_artifact_when_verifier_raises(self):
        repo_root = scratch_root("verify_queue_verifier_exception")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        state = StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        paths.candidate_dir(candidate_id).mkdir(parents=True, exist_ok=True)
        paths.workspace_dir(candidate_id).mkdir(parents=True, exist_ok=True)

        with patch("langgraph_runner.graph.Verifier.run", side_effect=OSError("verifier socket unavailable")):
            verified = graph_module.verify_queue_node(
                {
                    "repo_root": str(repo_root),
                    "run_id": "test",
                    "config_path": str(config_path),
                    "artifact_root": str(paths.artifact_root),
                    "runner_config": json.loads(config_path.read_text(encoding="utf-8")),
                    "candidate_ids": [candidate_id],
                    "review_results": [{"candidate_id": candidate_id, "passed": True, "checks": {}, "errors": []}],
                }
            )

        verification_path = paths.candidate_dir(candidate_id) / "verification.json"
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
        evaluated = graph_module.evaluate_candidates_node(
            {
                **verified,
                "runner_state": state.model_dump(mode="json"),
            }
        )

        self.assertEqual(verification["status"], "error")
        self.assertIn("verifier socket unavailable", verification["errors"][0])
        self.assertEqual(verified["verification_results"][0]["status"], "error")
        self.assertIn("verifier socket unavailable", verified["verification_results"][0]["errors"][0])
        self.assertEqual(evaluated["candidate_evaluations"][0]["status"], "error")
        self.assertIn("verifier socket unavailable", evaluated["candidate_evaluations"][0]["reason"])

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

    def test_record_batch_records_error_candidates_without_advancing_verified_progress(self):
        repo_root = scratch_root("record_batch_error_metrics_no_progress")
        config_path = write_workflow_fixture(repo_root, batch_size=3)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        store = StateStore(paths, repo_root / "docs" / "contract.md")
        runner_state = store.initialize()
        runner_state.three_bjt_verified_count = 25
        runner_state.last_verification_at = "old"
        store.write_state(runner_state)
        candidate_ids = [
            "p1-b033-c01-arch-20260605-120000",
            "p1-b033-c02-diag-20260605-120000",
            "p1-b033-c03-opt-20260605-120000",
        ]
        metrics = {"performance_nrmse_combined": 999.0, "area_total_p": 0.0, "power_score_basis_w": 0.0}

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
                        "batch_id": "p1-b033",
                        "role": "architecture",
                        "phase": "phase1_performance",
                        "primary_objective": "performance",
                    }
                    for candidate_id in candidate_ids
                ],
                "candidate_evaluations": [
                    {
                        "candidate_id": candidate_id,
                        "status": "error",
                        "reason": "verifier command exited with status 1",
                        "metrics": metrics,
                    }
                    for candidate_id in candidate_ids
                ],
            }
        )

        self.assertNotIn("errors", result)
        ledger_lines = paths.ledger_jsonl.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(ledger_lines), 3)
        self.assertEqual([json.loads(line)["status"] for line in ledger_lines], ["error", "error", "error"])
        updated = store.load_state()
        self.assertEqual(updated.three_bjt_verified_count, 25)
        self.assertEqual(updated.last_verification_at, "old")

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
                    "counted_run_total": 1,
                    "counted_run_remaining": 1,
                    "human_response": "continue",
                },
                config={"recursion_limit": 20},
            )

        pending = json.loads((run_dir / "human_interrupt.json").read_text(encoding="utf-8"))
        self.assertEqual(pending["human_response"], "continue")
        self.assertEqual(result["route"], "stop")
        self.assertNotIn("candidate_ids", result)
        self.assertEqual(paths.ledger_jsonl.read_text(encoding="utf-8"), "")

    def test_one_counted_batch_with_fake_agent_and_verifier_records_artifacts(self):
        repo_root = scratch_root("counted_batch_fake_agent")
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
        self.assertTrue((fake_runner.calls[0].context_path / "output" / "proposal.json").exists())
        self.assertEqual(Path(result["agent_calls"][0]["output_dir"]), fake_runner.calls[0].context_path / "output")
        self.assertEqual(json.loads((candidate_dir / "verdict.json").read_text(encoding="utf-8"))["status"], "accepted")
        self.assertEqual(len(paths.ledger_jsonl.read_text(encoding="utf-8").splitlines()), 1)
        self.assertIn("R1 VDD VOUT 20k", (repo_root / "amptest" / "dummy_neural_amp.scs").read_text(encoding="utf-8"))

    def test_local_deterministic_backend_runs_graph_without_codex_agent_runner(self):
        repo_root = scratch_root("local_deterministic_graph")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["agent_backend"] = {"mode": "local_deterministic"}
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        StateStore(paths, repo_root / "docs" / "contract.md").initialize()

        with patch("langgraph_runner.graph.AgentRunner", side_effect=AssertionError("codex backend should not be used")):
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
        agent_call = result["agent_calls"][0]
        self.assertEqual(result["promoted_candidate_id"], candidate_id)
        self.assertTrue((candidate_dir / "proposal.json").exists())
        self.assertTrue((candidate_dir / "verification.json").exists())
        self.assertIn("local_deterministic_agent", agent_call["command"])
        self.assertNotIn("codex", " ".join(agent_call["command"]).lower())
        self.assertFalse(list(paths.run_dir("manual").glob("agent_outputs/*/codex_home")))

    def test_local_deterministic_backend_runs_default_ppa_wrapper_command_shape(self):
        repo_root = scratch_root("local_deterministic_default_verifier_shape")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        write_default_shape_ppa_wrapper(repo_root)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["agent_backend"] = {"mode": "local_deterministic"}
        config["verifier"] = {
            "command": "python {repo_root}/amptest/ppa_wrapper.py analyze --config {local_candidate_dir}/config.json",
            "timeout_seconds": 10,
            "min_interval_seconds": 0,
            "required_outputs": ["verification.json", "ppa_metrics.json", "ppa_report.log"],
        }
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        StateStore(paths, repo_root / "docs" / "contract.md").initialize()

        with patch("langgraph_runner.graph.AgentRunner", side_effect=AssertionError("codex backend should not be used")):
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
        verification = json.loads((candidate_dir / "verification.json").read_text(encoding="utf-8"))
        self.assertEqual(verification["status"], "passed")
        self.assertEqual(verification["performance_nrmse_combined"], 0.031)
        self.assertTrue((candidate_dir / "ppa_metrics.json").exists())
        self.assertIn("ppa wrapper shape passed", (candidate_dir / "ppa_report.log").read_text(encoding="utf-8"))
        self.assertIn("local_deterministic_agent", result["agent_calls"][0]["command"])

    def test_missing_performance_metrics_from_verifier_are_not_promoted(self):
        repo_root = scratch_root("missing_performance_metrics_not_promoted")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        write_null_performance_ppa_wrapper(repo_root)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["agent_backend"] = {"mode": "local_deterministic"}
        config["verifier"] = {
            "command": "python {repo_root}/amptest/ppa_wrapper.py analyze --config {local_candidate_dir}/config.json",
            "timeout_seconds": 10,
            "min_interval_seconds": 0,
            "required_outputs": ["verification.json", "ppa_metrics.json", "ppa_report.log"],
        }
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        StateStore(paths, repo_root / "docs" / "contract.md").initialize()

        with patch("langgraph_runner.graph.AgentRunner", side_effect=AssertionError("codex backend should not be used")):
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
        verification = json.loads((candidate_dir / "verification.json").read_text(encoding="utf-8"))
        verdict = json.loads((candidate_dir / "verdict.json").read_text(encoding="utf-8"))
        self.assertEqual(verification["status"], "error")
        self.assertIn("performance_nrmse_combined", verification["errors"][0])
        self.assertEqual(verdict["status"], "error")
        self.assertIsNone(result["promoted_candidate_id"])
        self.assertNotIn("R1 VDD VOUT 20k", (repo_root / "amptest" / "dummy_neural_amp.scs").read_text(encoding="utf-8"))

    def test_local_deterministic_backend_runs_prime_request_path_without_codex_agent_runner(self):
        repo_root = scratch_root("local_deterministic_prime_graph")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["agent_backend"] = {"mode": "local_deterministic"}
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        output = repo_root / "subagent-output"
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        proposal = {
            "candidate_id": candidate_id,
            "phase": "phase1_performance",
            "agent": "architecture",
            "hypothesis": "Use a larger feedback resistor in the fixture circuit.",
            "primary_objective": "performance",
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
        output.mkdir(parents=True, exist_ok=True)
        (output / "proposal.json").write_text(json.dumps(proposal), encoding="utf-8")
        (output / "patch.diff").write_text(valid_patch_text(), encoding="utf-8")
        (output / "notes.md").write_text("Fixture candidate.\n", encoding="utf-8")
        (output / "prime_requests.json").write_text(
            json.dumps(
                [
                    {
                        "prime_role": "R-prime",
                        "prompt": "Review implementation risk.",
                        "rationale": "Exercise local prime graph execution.",
                    }
                ]
            ),
            encoding="utf-8",
        )

        collected = graph_module.collect_subagent_requests_node(
            {
                "repo_root": str(repo_root),
                "run_id": "manual",
                "artifact_root": str(paths.artifact_root),
                "runner_config": config,
                "agent_calls": [
                    {
                        "candidate_id": candidate_id,
                        "agent_call_id": "call-1",
                        "output_dir": str(output),
                        "status": "completed",
                    }
                ],
            }
        )
        with patch("langgraph_runner.graph.AgentRunner", side_effect=AssertionError("codex backend should not be used")):
            spawned = graph_module.spawn_prime_agents_node(collected)
        prime_outputs = graph_module.collect_prime_outputs_node(spawned)

        self.assertEqual(len(collected["prime_requests"]), 1)
        self.assertEqual(len(spawned["prime_calls"]), 1)
        self.assertEqual(spawned["prime_calls"][0]["status"], "completed")
        self.assertIn("local_deterministic_agent", spawned["prime_calls"][0]["command"])
        self.assertNotIn("codex", " ".join(spawned["prime_calls"][0]["command"]).lower())
        self.assertTrue(prime_outputs["prime_outputs"][0]["valid"])
        self.assertTrue((paths.candidate_dir(candidate_id) / "primes" / "call-1-prime-0" / "notes.md").exists())

    def test_local_deterministic_backend_exception_metadata_does_not_report_codex_exec(self):
        class RaisingLocalRunner:
            def run(self, call):
                raise OSError("local deterministic failure")

        repo_root = scratch_root("local_deterministic_exception_metadata")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["agent_backend"] = {"mode": "local_deterministic"}
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        StateStore(paths, repo_root / "docs" / "contract.md").initialize()

        with patch("langgraph_runner.local_agent.LocalDeterministicAgentRunner", return_value=RaisingLocalRunner()):
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

        command = result["agent_calls"][0]["command"]
        self.assertIn("local_deterministic_agent", command)
        self.assertNotIn("codex", " ".join(command).lower())
        self.assertEqual(result["agent_calls"][0]["error_class"], "agent_execution_failed")

    def test_counted_run_clears_prime_state_between_passes(self):
        repo_root = scratch_root("counted_run_prime_state_cleanup")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        verifier_code = (
            "from pathlib import Path; import json; "
            "out = Path(r'{output_dir}'); out.mkdir(parents=True, exist_ok=True); "
            "metrics = dict(performance_nrmse_combined=0.05, area_total_p=42.0, power_score_basis_w=0.0025); "
            "(out / 'ppa_metrics.json').write_text(json.dumps(metrics), encoding='utf-8'); "
            "(out / 'ppa_report.log').write_text('ppa failed\\n', encoding='utf-8'); "
            "(out / 'spectre_ac.log').write_text('ac passed\\n', encoding='utf-8'); "
            "(out / 'spectre_tran.log').write_text('tran passed\\n', encoding='utf-8'); "
            "data = dict(candidate_id='{candidate_id}', status='passed', "
            "metrics_path=str(out / 'ppa_metrics.json'), report_path=str(out / 'ppa_report.log'), "
            "spectre_logs=[str(out / 'spectre_ac.log'), str(out / 'spectre_tran.log')], "
            "performance_nrmse_combined=metrics['performance_nrmse_combined'], "
            "area_total_p=metrics['area_total_p'], power_score_basis_w=metrics['power_score_basis_w'], errors=[]); "
            "(out / 'verification.json').write_text(json.dumps(data), encoding='utf-8')"
        )
        config["verifier"]["command"] = python_command(verifier_code)
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        fake_runner = PrimeRequestAgentRunner()

        with patch("langgraph_runner.graph.AgentRunner", return_value=fake_runner):
            result = build_graph().invoke(
                {
                    "repo_root": str(repo_root),
                    "run_id": "manual",
                    "config_path": str(config_path),
                    "state_path": str(paths.state_json),
                    "route": "next_batch",
                    "counted_run_total": 2,
                    "counted_run_remaining": 2,
                },
                config={"recursion_limit": 40},
            )

        executed_prime_call_ids = [call.agent_call_id for call in fake_runner.calls if call.role == "R-prime"]
        final_prime_call_ids = [call["prime_call_id"] for call in result["prime_calls"]]
        final_prime_output_ids = [output["prime_call_id"] for output in result["prime_outputs"]]
        prime_output_notes = list(paths.run_dir("manual").glob("prime_outputs/*/notes.md"))
        self.assertEqual(result["route"], "stop")
        self.assertEqual(result["counted_run_remaining"], 0)
        self.assertEqual(len(executed_prime_call_ids), 2)
        self.assertEqual(len(set(executed_prime_call_ids)), 2)
        self.assertEqual(len(prime_output_notes), 2)
        self.assertEqual(len(final_prime_call_ids), 1)
        self.assertEqual(len(final_prime_output_ids), 1)

    def test_missing_subagent_outputs_after_retry_are_structured_candidate_errors(self):
        repo_root = scratch_root("missing_subagent_outputs_structured")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        fake_runner = NoOutputAgentRunner()

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
        assembly = json.loads((paths.candidate_dir(candidate_id) / "assembly.json").read_text(encoding="utf-8"))
        verdict = json.loads((paths.candidate_dir(candidate_id) / "verdict.json").read_text(encoding="utf-8"))
        retry_context = fake_runner.calls[1].context_path
        validation_errors = json.loads((retry_context / "validation_errors.json").read_text(encoding="utf-8"))
        self.assertEqual(len(fake_runner.calls), 2)
        self.assertTrue((fake_runner.calls[0].context_path / "output").is_dir())
        self.assertTrue((retry_context / "output").is_dir())
        self.assertEqual(set(validation_errors), {"missing_proposal", "missing_patch", "missing_notes"})
        self.assertEqual(assembly["error_class"], "agent_output_missing")
        self.assertEqual(assembly["reason"], "agent_output_missing")
        self.assertIn("missing_valid_subagent_output", assembly["errors"])
        self.assertIn("missing_proposal", assembly["errors"])
        self.assertIn("missing_patch", assembly["errors"])
        self.assertIn("missing_notes", assembly["errors"])
        self.assertEqual(verdict["status"], "error")
        self.assertIn("missing_valid_subagent_output", verdict["reason"])

    def test_invalid_subagent_output_after_retry_preserves_validation_errors_in_assembly(self):
        repo_root = scratch_root("invalid_subagent_output_preserved")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        fake_runner = InvalidOutputAgentRunner()

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
        assembly = json.loads((paths.candidate_dir(candidate_id) / "assembly.json").read_text(encoding="utf-8"))
        self.assertEqual(len(fake_runner.calls), 2)
        self.assertTrue(any(error.startswith("invalid_proposal:") for error in assembly["errors"]))
        self.assertIn("empty_patch", assembly["errors"])
        self.assertIn("empty_notes", assembly["errors"])

    def test_agent_execution_failure_is_not_retried_or_classified_as_missing_output(self):
        repo_root = scratch_root("execution_failure_no_retry")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        fake_runner = ExecutionFailureAgentRunner()

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
        assembly = json.loads((paths.candidate_dir(candidate_id) / "assembly.json").read_text(encoding="utf-8"))
        verdict = json.loads((paths.candidate_dir(candidate_id) / "verdict.json").read_text(encoding="utf-8"))
        self.assertEqual(len(fake_runner.calls), 1)
        self.assertEqual(result["agent_calls"][0]["error_class"], "agent_execution_failed")
        self.assertEqual(result["agent_calls"][0]["context_path"], str(fake_runner.calls[0].context_path))
        self.assertEqual(result["agent_calls"][0]["output_dir"], str(fake_runner.calls[0].context_path / "output"))
        self.assertEqual(result["agent_calls"][0]["log_dir"], str(fake_runner.calls[0].output_dir))
        self.assertEqual(result["subagent_outputs"][0]["error_class"], "agent_execution_failed")
        self.assertEqual(assembly["error_class"], "agent_execution_failed")
        self.assertEqual(assembly["reason"], "agent_execution_failed")
        self.assertIn("agent_execution_failed", assembly["errors"])
        self.assertNotIn("missing_valid_subagent_output", assembly["errors"])
        self.assertNotIn("missing_proposal", assembly["errors"])
        self.assertEqual(verdict["status"], "error")
        self.assertIn("agent_execution_failed", verdict["reason"])

    def test_missing_context_setup_failure_is_not_retried_or_classified_as_missing_output(self):
        repo_root = scratch_root("missing_context_setup_failure_no_retry")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        runner_state = StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        config = json.loads(config_path.read_text(encoding="utf-8"))
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        assignment = {
            "candidate_id": candidate_id,
            "batch_id": "p1-b001",
            "role": "architecture",
            "phase": "phase1_performance",
            "primary_objective": "performance",
        }

        def write_context_without_context_md(**kwargs):
            package = kwargs["run_dir"] / "agent_calls" / kwargs["agent_call_id"]
            (package / "output").mkdir(parents=True, exist_ok=True)
            return package

        state = {
            "repo_root": str(repo_root),
            "run_id": "manual",
            "config_path": str(config_path),
            "artifact_root": str(paths.artifact_root),
            "contract_path": str(repo_root / "docs" / "contract.md"),
            "runner_config": config,
            "runner_state": runner_state.model_dump(mode="json"),
            "batch_assignments": [assignment],
            "candidate_ids": [candidate_id],
        }

        with patch("langgraph_runner.graph.write_context_package", side_effect=write_context_without_context_md):
            spawned = graph_module.spawn_subagents_node(state)
        collected = graph_module.collect_subagent_requests_node(spawned)
        assembled = graph_module.assemble_candidate_proposals_node(collected)

        assembly = json.loads((paths.candidate_dir(candidate_id) / "assembly.json").read_text(encoding="utf-8"))
        self.assertEqual(len(collected["agent_calls"]), 1)
        self.assertEqual(spawned["agent_calls"][0]["error_class"], "agent_execution_failed")
        self.assertIn("context.md", spawned["agent_calls"][0]["error"])
        self.assertEqual(collected["subagent_outputs"][0]["error_class"], "agent_execution_failed")
        self.assertEqual(assembled["candidate_artifacts"][0]["error_class"], "agent_execution_failed")
        self.assertEqual(assembly["error_class"], "agent_execution_failed")
        self.assertNotIn("missing_valid_subagent_output", assembly["errors"])

    def test_log_path_collision_setup_failure_is_not_retried_or_classified_as_missing_output(self):
        repo_root = scratch_root("log_path_collision_setup_failure_no_retry")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        runner_state = StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        config = json.loads(config_path.read_text(encoding="utf-8"))
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        assignment = {
            "candidate_id": candidate_id,
            "batch_id": "p1-b001",
            "role": "architecture",
            "phase": "phase1_performance",
            "primary_objective": "performance",
        }
        collision_path = paths.run_dir("manual") / "agent_outputs" / f"{candidate_id}-subagent-a1"
        collision_path.parent.mkdir(parents=True, exist_ok=True)
        collision_path.write_text("not a directory\n", encoding="utf-8")
        state = {
            "repo_root": str(repo_root),
            "run_id": "manual",
            "config_path": str(config_path),
            "artifact_root": str(paths.artifact_root),
            "contract_path": str(repo_root / "docs" / "contract.md"),
            "runner_config": config,
            "runner_state": runner_state.model_dump(mode="json"),
            "batch_assignments": [assignment],
            "candidate_ids": [candidate_id],
        }

        spawned = graph_module.spawn_subagents_node(state)
        collected = graph_module.collect_subagent_requests_node(spawned)
        assembled = graph_module.assemble_candidate_proposals_node(collected)

        assembly = json.loads((paths.candidate_dir(candidate_id) / "assembly.json").read_text(encoding="utf-8"))
        self.assertEqual(len(collected["agent_calls"]), 1)
        self.assertEqual(spawned["agent_calls"][0]["error_class"], "agent_execution_failed")
        self.assertIn("agent_outputs", spawned["agent_calls"][0]["error"])
        self.assertEqual(collected["subagent_outputs"][0]["error_class"], "agent_execution_failed")
        self.assertEqual(assembled["candidate_artifacts"][0]["error_class"], "agent_execution_failed")
        self.assertEqual(assembly["error_class"], "agent_execution_failed")
        self.assertNotIn("missing_valid_subagent_output", assembly["errors"])

    def test_timeout_and_process_failures_are_not_retried(self):
        cases = [
            ("agent_timeout", 124),
            ("agent_process_failed", 2),
        ]
        for error_class, exit_code in cases:
            with self.subTest(error_class=error_class):
                repo_root = scratch_root(f"{error_class}_no_retry")
                config_path = write_workflow_fixture(repo_root, batch_size=1)
                paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
                StateStore(paths, repo_root / "docs" / "contract.md").initialize()
                fake_runner = ExecutionFailureAgentRunner(error_class=error_class, exit_code=exit_code)

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
                assembly = json.loads((paths.candidate_dir(candidate_id) / "assembly.json").read_text(encoding="utf-8"))
                self.assertEqual(len(fake_runner.calls), 1)
                self.assertEqual(result["agent_calls"][0]["error_class"], error_class)
                self.assertEqual(result["subagent_outputs"][0]["error_class"], error_class)
                self.assertEqual(assembly["error_class"], error_class)
                self.assertEqual(assembly["reason"], error_class)
                self.assertNotIn("missing_valid_subagent_output", assembly["errors"])

    def test_prime_process_failure_preserves_agent_run_metadata_through_collection(self):
        repo_root = scratch_root("prime_process_failure_metadata")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        runner_state = StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        config = json.loads(config_path.read_text(encoding="utf-8"))
        fake_runner = ExecutionFailureAgentRunner(error_class="agent_process_failed", exit_code=2)
        request = {
            "candidate_id": "p1-b001-c01-arch-20260605-120000",
            "parent_agent_call_id": "p1-b001-c01-arch-20260605-120000-subagent-a1",
            "request_index": 0,
            "prime_role": "implementation",
            "prompt": "Review implementation risks.",
        }
        state = {
            "repo_root": str(repo_root),
            "run_id": "manual",
            "config_path": str(config_path),
            "artifact_root": str(paths.artifact_root),
            "runner_config": config,
            "runner_state": runner_state.model_dump(mode="json"),
            "prime_requests": [request],
        }

        with patch("langgraph_runner.graph.AgentRunner", return_value=fake_runner):
            spawned = graph_module.spawn_prime_agents_node(state)
        collected = graph_module.collect_prime_outputs_node(spawned)

        prime_call = spawned["prime_calls"][0]
        prime_output = collected["prime_outputs"][0]
        self.assertEqual(len(fake_runner.calls), 1)
        self.assertEqual(prime_call["status"], "error")
        self.assertEqual(prime_call["exit_code"], 2)
        self.assertEqual(prime_call["error_class"], "agent_process_failed")
        self.assertEqual(prime_call["error"], "[WinError 5] access is denied")
        self.assertEqual(prime_call["agent_run_path"], str(fake_runner.calls[0].output_dir / "agent_run.json"))
        self.assertEqual(prime_call["log_dir"], str(fake_runner.calls[0].output_dir))
        self.assertEqual(prime_call["command"], ["codex.cmd", "exec", "-C", str(fake_runner.calls[0].context_path), "-"])
        self.assertEqual(prime_call["stdout_path"], str(fake_runner.calls[0].output_dir / "stdout.log"))
        self.assertEqual(prime_call["stderr_path"], str(fake_runner.calls[0].output_dir / "stderr.log"))
        self.assertNotIn("prime_agent_exit_nonzero", prime_call["errors"])
        self.assertFalse(prime_output["valid"])
        self.assertEqual(prime_output["error_class"], "agent_process_failed")
        self.assertEqual(prime_output["agent_run_path"], prime_call["agent_run_path"])
        self.assertEqual(prime_output["log_dir"], prime_call["log_dir"])
        self.assertEqual(prime_output["command"], prime_call["command"])

    def test_prime_setup_failure_preserves_execution_error_metadata_through_collection(self):
        repo_root = scratch_root("prime_setup_failure_metadata")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        runner_state = StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        config = json.loads(config_path.read_text(encoding="utf-8"))
        request = {
            "candidate_id": "p1-b001-c01-arch-20260605-120000",
            "parent_agent_call_id": "p1-b001-c01-arch-20260605-120000-subagent-a1",
            "request_index": 0,
            "prime_role": "implementation",
            "prompt": "Review implementation risks.",
        }
        prime_call_id = f"{request['parent_agent_call_id']}-prime-{request['request_index']}"
        collision_path = paths.run_dir("manual") / "prime_outputs" / prime_call_id
        collision_path.parent.mkdir(parents=True, exist_ok=True)
        collision_path.write_text("not a directory\n", encoding="utf-8")
        state = {
            "repo_root": str(repo_root),
            "run_id": "manual",
            "config_path": str(config_path),
            "artifact_root": str(paths.artifact_root),
            "runner_config": config,
            "runner_state": runner_state.model_dump(mode="json"),
            "prime_requests": [request],
        }

        spawned = graph_module.spawn_prime_agents_node(state)
        collected = graph_module.collect_prime_outputs_node(spawned)

        prime_call = spawned["prime_calls"][0]
        prime_output = collected["prime_outputs"][0]
        self.assertEqual(prime_call["status"], "error")
        self.assertEqual(prime_call["error_class"], "agent_execution_failed")
        self.assertIn("prime_outputs", prime_call["error"])
        self.assertEqual(prime_call["log_dir"], str(collision_path))
        self.assertIsNone(prime_call.get("agent_run_path"))
        self.assertEqual(prime_call["stdout_path"], str(collision_path / "stdout.log"))
        self.assertEqual(prime_call["stderr_path"], str(collision_path / "stderr.log"))
        self.assertTrue(prime_call["command"])
        self.assertNotIn("prime_agent_exit_nonzero", prime_call["errors"])
        self.assertFalse(prime_output["valid"])
        self.assertEqual(prime_output["error_class"], "agent_execution_failed")
        self.assertEqual(prime_output["error"], prime_call["error"])
        self.assertEqual(prime_output["log_dir"], prime_call["log_dir"])
        self.assertIsNone(prime_output.get("agent_run_path"))

    def test_all_missing_subagent_outputs_write_batch_error_and_stop_next_batch_route(self):
        repo_root = scratch_root("missing_subagent_outputs_stop_batch")
        config_path = write_workflow_fixture(repo_root, batch_size=2)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        fake_runner = NoOutputAgentRunner()

        with patch("langgraph_runner.graph.AgentRunner", return_value=fake_runner):
            result = build_graph().invoke(
                {
                    "repo_root": str(repo_root),
                    "run_id": "manual",
                    "config_path": str(config_path),
                    "state_path": str(paths.state_json),
                    "route": "next_batch",
                },
                config={"recursion_limit": 20},
            )

        run_dir = paths.run_dir("manual")
        decision = json.loads((run_dir / "top_decision.json").read_text(encoding="utf-8"))
        batch_error = json.loads((run_dir / "batch_error.json").read_text(encoding="utf-8"))
        self.assertEqual(result["route"], "stop")
        self.assertEqual(decision["decision"], "stop")
        self.assertEqual(decision["anomaly_level"], "critical")
        self.assertEqual(decision["reason"], "all candidates failed assembly: missing_valid_subagent_output")
        self.assertEqual(set(decision["candidate_ids"]), set(result["candidate_ids"]))
        self.assertEqual(decision["next_batch_strategy"], "Stop until subagent output generation is fixed.")
        self.assertEqual(batch_error["error_class"], "batch_agent_output_failure")
        self.assertEqual(batch_error["reason"], "all candidates failed assembly: missing_valid_subagent_output")
        self.assertEqual(set(item["candidate_id"] for item in batch_error["candidates"]), set(result["candidate_ids"]))
        self.assertTrue(all(item["output_path"].endswith("\\output") or item["output_path"].endswith("/output") for item in batch_error["candidates"]))

    def test_all_agent_execution_failures_write_batch_error_and_stop_next_batch_route(self):
        repo_root = scratch_root("execution_failure_stop_batch")
        config_path = write_workflow_fixture(repo_root, batch_size=2)
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        StateStore(paths, repo_root / "docs" / "contract.md").initialize()
        fake_runner = ExecutionFailureAgentRunner()

        with patch("langgraph_runner.graph.AgentRunner", return_value=fake_runner):
            result = build_graph().invoke(
                {
                    "repo_root": str(repo_root),
                    "run_id": "manual",
                    "config_path": str(config_path),
                    "state_path": str(paths.state_json),
                    "route": "next_batch",
                },
                config={"recursion_limit": 20},
            )

        run_dir = paths.run_dir("manual")
        decision = json.loads((run_dir / "top_decision.json").read_text(encoding="utf-8"))
        batch_error = json.loads((run_dir / "batch_error.json").read_text(encoding="utf-8"))
        self.assertEqual(len(fake_runner.calls), len(result["candidate_ids"]))
        self.assertEqual(result["route"], "stop")
        self.assertEqual(decision["decision"], "stop")
        self.assertEqual(decision["reason"], "all candidates failed agent execution")
        self.assertEqual(decision["next_batch_strategy"], "Stop until Codex CLI launch is fixed.")
        self.assertEqual(batch_error["error_class"], "batch_agent_execution_failure")
        self.assertEqual(batch_error["reason"], "all candidates failed agent execution")
        self.assertEqual(set(item["candidate_id"] for item in batch_error["candidates"]), set(result["candidate_ids"]))
        self.assertTrue(all(item["error_class"] == "agent_execution_failed" for item in batch_error["candidates"]))
        self.assertTrue(all(item["context_paths"] for item in batch_error["candidates"]))
        self.assertTrue(all(item["output_paths"] for item in batch_error["candidates"]))
        self.assertTrue(all(any(path.endswith("agent_run.json") for path in item["log_paths"]) for item in batch_error["candidates"]))

    def test_collect_subagent_requests_does_not_parse_repository_root_for_missing_output_path(self):
        repo_root = scratch_root("missing_output_path_not_dot")
        candidate_id = "p1-b001-c01-arch-20260605-120000"

        result = graph_module.collect_subagent_requests_node(
            {
                "repo_root": str(repo_root),
                "run_id": "manual",
                "agent_calls": [
                    {
                        "candidate_id": candidate_id,
                        "agent_call_id": "call-1",
                        "status": "completed",
                    }
                ],
            }
        )

        self.assertEqual(result["subagent_outputs"][0]["errors"], ["agent_output_path_missing"])
        self.assertEqual(result["subagent_outputs"][0]["output_dir"], "")

    def test_collect_subagent_requests_falls_back_to_output_dir_when_context_path_is_stale(self):
        repo_root = scratch_root("stale_context_output_dir_fallback")
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        output = repo_root / "actual-output"
        proposal = {
            "candidate_id": candidate_id,
            "phase": "phase1_performance",
            "agent": "architecture",
            "hypothesis": "Use a larger feedback resistor in the fixture circuit.",
            "primary_objective": "performance",
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
        output.mkdir(parents=True, exist_ok=True)
        (output / "proposal.json").write_text(json.dumps(proposal), encoding="utf-8")
        (output / "patch.diff").write_text(valid_patch_text(), encoding="utf-8")
        (output / "notes.md").write_text("Fixture candidate.\n", encoding="utf-8")

        result = graph_module.collect_subagent_requests_node(
            {
                "repo_root": str(repo_root),
                "run_id": "manual",
                "agent_calls": [
                    {
                        "candidate_id": candidate_id,
                        "agent_call_id": "call-1",
                        "context_path": str(repo_root / "missing-context"),
                        "output_dir": str(output),
                        "status": "completed",
                    }
                ],
            }
        )

        self.assertTrue(result["subagent_outputs"][0]["valid"])
        self.assertEqual(result["subagent_outputs"][0]["output_dir"], str(output))

    def test_collect_subagent_requests_prefers_valid_output_dir_when_context_output_is_missing(self):
        repo_root = scratch_root("explicit_output_dir_precedence")
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        context_path = repo_root / "existing-context"
        output = repo_root / "resumed-artifact-output"
        proposal = {
            "candidate_id": candidate_id,
            "phase": "phase1_performance",
            "agent": "architecture",
            "hypothesis": "Use a larger feedback resistor in the fixture circuit.",
            "primary_objective": "performance",
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
        context_path.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)
        (output / "proposal.json").write_text(json.dumps(proposal), encoding="utf-8")
        (output / "patch.diff").write_text(valid_patch_text(), encoding="utf-8")
        (output / "notes.md").write_text("Fixture candidate.\n", encoding="utf-8")

        result = graph_module.collect_subagent_requests_node(
            {
                "repo_root": str(repo_root),
                "run_id": "manual",
                "agent_calls": [
                    {
                        "candidate_id": candidate_id,
                        "agent_call_id": "call-1",
                        "context_path": str(context_path),
                        "output_dir": str(output),
                        "status": "completed",
                    }
                ],
            }
        )

        self.assertTrue(result["subagent_outputs"][0]["valid"])
        self.assertEqual(result["subagent_outputs"][0]["output_dir"], str(output))

    def test_collect_subagent_requests_ignores_log_output_dir_when_context_output_has_artifacts(self):
        repo_root = scratch_root("log_output_dir_context_artifact_fallback")
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        context_output = repo_root / "context" / "output"
        log_output = repo_root / "runs" / "manual" / "agent_outputs" / "call-1"
        proposal = {
            "candidate_id": candidate_id,
            "phase": "phase1_performance",
            "agent": "architecture",
            "hypothesis": "Use a larger feedback resistor in the fixture circuit.",
            "primary_objective": "performance",
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
        context_output.mkdir(parents=True, exist_ok=True)
        log_output.mkdir(parents=True, exist_ok=True)
        (context_output / "proposal.json").write_text(json.dumps(proposal), encoding="utf-8")
        (context_output / "patch.diff").write_text(valid_patch_text(), encoding="utf-8")
        (context_output / "notes.md").write_text("Fixture candidate.\n", encoding="utf-8")
        (log_output / "stdout.log").write_text("stdout\n", encoding="utf-8")
        (log_output / "stderr.log").write_text("stderr\n", encoding="utf-8")
        (log_output / "agent_run.json").write_text("{}\n", encoding="utf-8")

        result = graph_module.collect_subagent_requests_node(
            {
                "repo_root": str(repo_root),
                "run_id": "manual",
                "agent_calls": [
                    {
                        "candidate_id": candidate_id,
                        "agent_call_id": "call-1",
                        "context_path": str(context_output.parent),
                        "output_dir": str(log_output),
                        "status": "completed",
                    }
                ],
            }
        )

        self.assertTrue(result["subagent_outputs"][0]["valid"])
        self.assertEqual(result["subagent_outputs"][0]["output_dir"], str(context_output))

    def test_collect_subagent_requests_rejects_repo_root_or_cwd_output_dir_fallback(self):
        repo_root = scratch_root("reject_repo_root_output_dir_fallback")
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        cases = [repo_root, Path.cwd()]

        for output_dir in cases:
            with self.subTest(output_dir=str(output_dir)):
                with patch("langgraph_runner.graph.parse_subagent_output", side_effect=AssertionError("should not parse unsafe output dir")):
                    result = graph_module.collect_subagent_requests_node(
                        {
                            "repo_root": str(repo_root),
                            "run_id": "manual",
                            "agent_calls": [
                                {
                                    "candidate_id": candidate_id,
                                    "agent_call_id": "call-1",
                                    "context_path": str(repo_root / "missing-context"),
                                    "output_dir": str(output_dir),
                                    "status": "completed",
                                }
                            ],
                        }
                    )

                self.assertEqual(result["subagent_outputs"][0]["errors"], ["agent_output_path_missing"])
                self.assertEqual(result["subagent_outputs"][0]["output_dir"], "")

    def test_collect_subagent_requests_rejects_empty_output_dir_fallback(self):
        repo_root = scratch_root("reject_empty_output_dir_fallback")
        candidate_id = "p1-b001-c01-arch-20260605-120000"
        output = repo_root / "empty-output"
        output.mkdir(parents=True, exist_ok=True)

        with patch("langgraph_runner.graph.parse_subagent_output", side_effect=AssertionError("should not parse empty output dir")):
            result = graph_module.collect_subagent_requests_node(
                {
                    "repo_root": str(repo_root),
                    "run_id": "manual",
                    "agent_calls": [
                        {
                            "candidate_id": candidate_id,
                            "agent_call_id": "call-1",
                            "context_path": str(repo_root / "missing-context"),
                            "output_dir": str(output),
                            "status": "completed",
                        }
                    ],
                }
            )

        self.assertEqual(result["subagent_outputs"][0]["errors"], ["agent_output_path_missing"])
        self.assertEqual(result["subagent_outputs"][0]["output_dir"], "")

    def test_missing_output_batch_stop_uses_structured_assembly_error_class(self):
        repo_root = scratch_root("missing_output_structured_detection")
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        candidate_id = "p1-b001-c01-arch-20260605-120000"

        result = graph_module.top_anomaly_check_node(
            {
                "repo_root": str(repo_root),
                "run_id": "manual",
                "artifact_root": str(paths.artifact_root),
                "candidate_ids": [candidate_id],
                "candidate_artifacts": [
                    {
                        "candidate_id": candidate_id,
                        "status": "error",
                        "errors": ["missing_valid_subagent_output"],
                        "error_class": "agent_output_missing",
                        "reason": "agent_output_missing",
                    }
                ],
                "candidate_evaluations": [
                    {
                        "candidate_id": candidate_id,
                        "status": "error",
                        "reason": "structured assembly error",
                        "metrics": {},
                    }
                ],
            }
        )

        self.assertEqual(result["top_decision"]["decision"], "stop")
        self.assertEqual(result["top_decision"]["candidate_ids"], [candidate_id])

    def test_missing_output_substring_in_evaluation_reason_alone_does_not_stop_batch(self):
        repo_root = scratch_root("missing_output_reason_text_not_detection")
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        candidate_id = "p1-b001-c01-arch-20260605-120000"

        result = graph_module.top_anomaly_check_node(
            {
                "repo_root": str(repo_root),
                "run_id": "manual",
                "artifact_root": str(paths.artifact_root),
                "candidate_ids": [candidate_id],
                "candidate_evaluations": [
                    {
                        "candidate_id": candidate_id,
                        "status": "error",
                        "reason": "verifier stderr mentioned missing_valid_subagent_output but assembly was not classified",
                        "metrics": {},
                    }
                ],
            }
        )

        self.assertEqual(result["top_decision"]["decision"], "continue")
        self.assertFalse((paths.run_dir("manual") / "batch_error.json").exists())

    def test_all_verifier_infrastructure_errors_write_batch_error_and_stop_batch(self):
        repo_root = scratch_root("verifier_infrastructure_error_stop")
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        candidate_ids = [
            "p1-b034-c01-arch-20260605-120000",
            "p1-b034-c02-diag-20260605-120000",
            "p1-b034-c03-opt-20260605-120000",
        ]
        reasons = {
            candidate_id: f"verifier command exited with status {index + 1}"
            for index, candidate_id in enumerate(candidate_ids)
        }
        run_dir = paths.run_dir("manual")
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "batch_error.json").write_text(
            json.dumps(
                {
                    "error_class": "batch_agent_execution_failure",
                    "candidate_ids": ["stale-candidate"],
                    "candidates": [{"candidate_id": "stale-candidate"}],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = graph_module.top_anomaly_check_node(
            {
                "repo_root": str(repo_root),
                "run_id": "manual",
                "artifact_root": str(paths.artifact_root),
                "candidate_ids": candidate_ids,
                "candidate_evaluations": [
                    {
                        "candidate_id": candidate_id,
                        "status": "error",
                        "reason": reasons[candidate_id],
                        "metrics": {},
                    }
                    for candidate_id in candidate_ids
                ],
                "verification_results": [
                    {
                        "candidate_id": candidate_id,
                        "status": "error",
                        "metrics_path": str(paths.candidate_dir(candidate_id) / "ppa_metrics.json"),
                        "report_path": str(paths.candidate_dir(candidate_id) / "ppa_report.log"),
                        "spectre_logs": [str(paths.candidate_dir(candidate_id) / "spectre_ac.log")],
                        "performance_nrmse_combined": 1.0,
                        "area_total_p": 0.0,
                        "power_score_basis_w": 0.0,
                        "errors": [reasons[candidate_id]],
                    }
                    for candidate_id in candidate_ids
                ],
            }
        )

        decision_path = run_dir / "top_decision.json"
        written = json.loads(decision_path.read_text(encoding="utf-8"))
        batch_error = json.loads((run_dir / "batch_error.json").read_text(encoding="utf-8"))
        self.assertEqual(result["top_decision"]["decision"], "stop")
        self.assertEqual(result["top_decision"]["anomaly_level"], "critical")
        self.assertEqual(result["top_decision"]["candidate_ids"], candidate_ids)
        self.assertEqual(result["top_decision"]["reason"], "all candidates failed verifier infrastructure")
        self.assertEqual(result["top_decision"]["next_batch_strategy"], "Stop until verifier infrastructure is fixed.")
        self.assertEqual(written, result["top_decision"])
        self.assertEqual(batch_error["error_class"], "batch_verifier_infrastructure_failure")
        self.assertEqual(batch_error["reason"], "all candidates failed verifier infrastructure")
        self.assertEqual(batch_error["candidate_ids"], candidate_ids)
        self.assertEqual({item["candidate_id"] for item in batch_error["candidates"]}, set(candidate_ids))
        for item in batch_error["candidates"]:
            candidate_id = item["candidate_id"]
            self.assertEqual(item["verifier_reason"], reasons[candidate_id])
            self.assertIn(str(paths.candidate_dir(candidate_id) / "verification.json"), item["artifact_paths"])
            self.assertIn(str(paths.candidate_dir(candidate_id) / "verdict.json"), item["artifact_paths"])
            self.assertIn(str(paths.candidate_dir(candidate_id) / "ppa_report.log"), item["artifact_paths"])
            self.assertIn(str(paths.candidate_dir(candidate_id) / STDOUT_LOG), item["log_paths"])
            self.assertIn(str(paths.candidate_dir(candidate_id) / STDERR_LOG), item["log_paths"])
            self.assertIn(str(paths.candidate_dir(candidate_id) / "spectre_ac.log"), item["log_paths"])

    def test_all_verifier_stale_and_missing_verification_outputs_stop_batch(self):
        repo_root = scratch_root("verifier_stale_missing_outputs_stop")
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        candidate_ids = [
            "p1-b035-c01-arch-20260605-120000",
            "p1-b035-c02-diag-20260605-120000",
        ]

        result = graph_module.top_anomaly_check_node(
            {
                "repo_root": str(repo_root),
                "run_id": "manual",
                "artifact_root": str(paths.artifact_root),
                "candidate_ids": candidate_ids,
                "candidate_evaluations": [
                    {
                        "candidate_id": candidate_ids[0],
                        "status": "error",
                        "reason": "required output not updated by current run: verification.json",
                        "metrics": {},
                    },
                    {
                        "candidate_id": candidate_ids[1],
                        "status": "error",
                        "reason": "missing verification.json",
                        "metrics": {},
                    },
                ],
            }
        )

        self.assertEqual(result["top_decision"]["decision"], "stop")
        self.assertEqual(result["top_decision"]["anomaly_level"], "critical")
        self.assertEqual(result["top_decision"]["candidate_ids"], candidate_ids)
        self.assertEqual(result["top_decision"]["reason"], "all candidates failed verifier infrastructure")
        self.assertEqual(result["top_decision"]["next_batch_strategy"], "Stop until verifier infrastructure is fixed.")

    def test_verifier_infrastructure_detection_requires_error_status_and_prefix(self):
        cases = [
            ("non_error_candidate", "rejected", "missing verification.json"),
            (
                "ordinary_reason_text",
                "error",
                "candidate note mentioned required output not updated by current run: verification.json",
            ),
            (
                "ordinary_missing_verification_text",
                "error",
                "missing verification.json was mentioned in candidate notes",
            ),
        ]

        for case, status, reason in cases:
            with self.subTest(case=case):
                repo_root = scratch_root(f"verifier_infrastructure_detection_{case}")
                paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
                candidate_id = "p1-b036-c01-arch-20260605-120000"

                result = graph_module.top_anomaly_check_node(
                    {
                        "repo_root": str(repo_root),
                        "run_id": "manual",
                        "artifact_root": str(paths.artifact_root),
                        "candidate_ids": [candidate_id],
                        "candidate_evaluations": [
                            {
                                "candidate_id": candidate_id,
                                "status": status,
                                "reason": reason,
                                "metrics": {},
                            }
                        ],
                    }
                )

                self.assertEqual(result["top_decision"]["decision"], "continue")
                self.assertFalse((paths.run_dir("manual") / "batch_error.json").exists())

    def test_verifier_infrastructure_guardrail_does_not_overwrite_existing_batch_error(self):
        repo_root = scratch_root("verifier_infrastructure_guardrail_keeps_existing_batch_error")
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        candidate_id = "p1-b037-c01-arch-20260605-120000"
        run_dir = paths.run_dir("manual")
        run_dir.mkdir(parents=True, exist_ok=True)
        stale_payload = {
            "error_class": "batch_agent_execution_failure",
            "candidate_ids": ["stale-candidate"],
            "candidates": [{"candidate_id": "stale-candidate"}],
        }
        (run_dir / "batch_error.json").write_text(json.dumps(stale_payload, indent=2) + "\n", encoding="utf-8")

        result = graph_module.top_anomaly_check_node(
            {
                "repo_root": str(repo_root),
                "run_id": "manual",
                "artifact_root": str(paths.artifact_root),
                "candidate_ids": [candidate_id],
                "candidate_evaluations": [
                    {
                        "candidate_id": candidate_id,
                        "status": "error",
                        "reason": "candidate note mentioned verifier command exited with status 1",
                        "metrics": {},
                    }
                ],
            }
        )

        self.assertEqual(result["top_decision"]["decision"], "continue")
        self.assertEqual(json.loads((run_dir / "batch_error.json").read_text(encoding="utf-8")), stale_payload)

    def test_next_batch_route_hits_recursion_limit(self):
        graph = build_graph()

        with self.assertRaises(Exception) as context:
            graph.invoke(
                {"repo_root": ".", "run_id": "test", "route": "next_batch"},
                config={"recursion_limit": 20},
            )

        self.assertIn("recursion", str(context.exception).lower())

    def test_counted_run_decrements_next_batch_and_stops_when_exhausted(self):
        continued = graph_module._route_next(
            {
                "route": "next_batch",
                "counted_run_total": 3,
                "counted_run_remaining": 3,
            }
        )

        stopped = graph_module._route_next(
            {
                "route": "next_batch",
                "counted_run_total": 3,
                "counted_run_remaining": 1,
            }
        )

        self.assertEqual(continued["route"], "next_batch")
        self.assertEqual(continued["counted_run_remaining"], 2)
        self.assertEqual(stopped["route"], "stop")
        self.assertEqual(stopped["counted_run_remaining"], 0)

    def test_top_decision_wins_over_counted_run_remaining(self):
        rerun = graph_module._route_next(
            {
                "route": "next_batch",
                "counted_run_total": 3,
                "counted_run_remaining": 3,
                "top_decision": {"decision": "rerun_verification"},
            }
        )
        interrupt = graph_module._route_next(
            {
                "route": "next_batch",
                "counted_run_total": 3,
                "counted_run_remaining": 3,
                "top_decision": {"decision": "human_interrupt"},
            }
        )
        stopped = graph_module._route_next(
            {
                "route": "next_batch",
                "counted_run_total": 3,
                "counted_run_remaining": 3,
                "top_decision": {"decision": "stop"},
            }
        )

        self.assertEqual(rerun["route"], "rerun_verification")
        self.assertEqual(rerun["counted_run_remaining"], 3)
        self.assertEqual(interrupt["route"], "human_interrupt")
        self.assertEqual(interrupt["counted_run_remaining"], 3)
        self.assertEqual(stopped["route"], "stop")
        self.assertEqual(stopped["counted_run_remaining"], 3)

    def test_record_batch_error_stops_counted_next_batch_without_decrement(self):
        routed = graph_module._route_next(
            {
                "route": "next_batch",
                "errors": ["record_batch: could not append ledger"],
                "counted_run_total": 2,
                "counted_run_remaining": 2,
            }
        )

        self.assertEqual(routed["route"], "stop")
        self.assertEqual(routed["counted_run_remaining"], 2)

    def test_legacy_next_batch_can_stop_after_current_pass_for_direct_graph_call(self):
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
