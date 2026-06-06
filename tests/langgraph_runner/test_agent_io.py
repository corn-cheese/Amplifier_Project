import json
import subprocess
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from langgraph_runner.agent_io import AgentCall, AgentRunner, resolve_codex_command, write_context_package
from langgraph_runner.batch import CandidateAssignment, plan_batch
from langgraph_runner.schemas import Proposal, RunnerState


SCRATCH = Path(".test_tmp_langgraph_runner") / "agent_io"


def scratch_case(name: str) -> Path:
    path = SCRATCH / name
    path.mkdir(parents=True, exist_ok=True)
    return path


class FakeExecutor:
    def __init__(self, exit_code=0):
        self.calls = []
        self.exit_code = exit_code

    def __call__(self, command, cwd, timeout, stdin_text=None, env=None):
        self.calls.append((command, cwd, timeout, stdin_text, env))
        return self.exit_code, "stdout text", "stderr text"


class TestAgentIO(unittest.TestCase):
    def test_resolve_codex_command_prefers_cmd_on_windows(self):
        def which(name):
            return {
                "codex.cmd": r"C:\Users\maize\AppData\Roaming\npm\codex.cmd",
                "codex.exe": r"C:\Tools\codex.exe",
            }.get(name)

        with patch("langgraph_runner.codex_cli.os.name", "nt"), patch("langgraph_runner.codex_cli.shutil.which", side_effect=which):
            self.assertEqual(resolve_codex_command(), [r"C:\Users\maize\AppData\Roaming\npm\codex.cmd"])

    def test_context_package_contains_assignment_and_required_schema(self):
        root = scratch_case("context_package")
        base_dut = root / "custom_amp.scs"
        base_devices = root / "accounting_devices.csv"
        base_dut.write_text("simulator lang=spectre\n", encoding="utf-8")
        base_devices.write_text("name,type\nM1,nmos\n", encoding="utf-8")
        recent_ledger = [
            {"candidate_id": "p1-b001-c00-seed-20260604-231400", "status": "accepted"},
            {"candidate_id": "p1-b001-c01-arch-20260604-231500", "status": "proposed"},
        ]
        assignment = CandidateAssignment(
            candidate_id="p1-b001-c01-arch-20260604-231500",
            batch_id="p1-b001",
            role="architecture",
            phase="phase1_performance",
            primary_objective="performance",
        )

        package = write_context_package(
            run_dir=root / "runs" / "run-1",
            agent_call_id="call-1",
            assignment=assignment,
            contract_excerpt="Only DUT and devices.csv may change.",
            state_summary={"batch_no": 0},
            recent_ledger=recent_ledger,
            dut_netlist_path="circuits/custom_amp.scs",
            devices_csv_path="accounting/devices.csv",
            base_dut=base_dut,
            base_devices=base_devices,
        )

        context_text = (package / "context.md").read_text(encoding="utf-8")
        self.assertTrue((package / "state_summary.json").exists())
        self.assertIn("p1-b001-c01-arch-20260604-231500", context_text)
        self.assertIn("batch_id: p1-b001", context_text)
        self.assertIn("phase: phase1_performance", context_text)
        self.assertIn("primary_objective: performance", context_text)
        self.assertTrue((package / "output").is_dir())
        self.assertIn("- output/proposal.json", context_text)
        self.assertIn("- output/patch.diff", context_text)
        self.assertIn("- output/notes.md", context_text)
        self.assertIn("under output/ relative to the current context directory", context_text)
        required_context_fragments = [
            "You must produce a real candidate by writing all three required files.",
            "output/patch.diff must be a unified diff",
            "patches apply only in isolated candidate snapshots",
            "output/notes.md must explain",
            "Allowed file changes: circuits/custom_amp.scs and accounting/devices.csv only.",
            "Allowed expected_effect values for each metric: decrease, increase, no_major_change, unknown.",
            "Do not modify amptest config, analyzer, scoring logic, generated testbenches, AC/transient input conditions, supplies, references, inputs, loads, or metric calculations.",
            "Forbidden: OPAMP, OPAMP-equivalent macro, Verilog-A behavioral amplifier, ideal gain block, controlled source used as an amplifier.",
            "Natural-language claims are not evidence.",
            "Prose-only answers are invalid",
            "Codex CLI execution inherits the operator's existing CLI environment and authentication.",
            "Do not ask for or require an OpenAI API key.",
        ]
        for fragment in required_context_fragments:
            self.assertIn(fragment, context_text)
        proposal_marker = "```json\n"
        proposal_start = context_text.index(proposal_marker) + len(proposal_marker)
        proposal_end = context_text.index("\n```", proposal_start)
        proposal_contract = json.loads(context_text[proposal_start:proposal_end])
        self.assertEqual(proposal_contract["candidate_id"], "p1-b001-c01-arch-20260604-231500")
        self.assertEqual(proposal_contract["phase"], "phase1_performance")
        self.assertEqual(proposal_contract["agent"], "architecture")
        self.assertEqual(proposal_contract["primary_objective"], "performance")
        self.assertEqual(proposal_contract["hypothesis"], "string")
        self.assertEqual(proposal_contract["changed_blocks"], ["bias"])
        self.assertEqual(proposal_contract["files_touched"], ["circuits/custom_amp.scs", "accounting/devices.csv"])
        self.assertEqual(
            proposal_contract["expected_effect"],
            {
                "performance_nrmse_combined": "unknown",
                "area_total_p": "unknown",
                "power_score_basis_w": "unknown",
            },
        )
        for effect in proposal_contract["expected_effect"].values():
            self.assertNotIn("|", effect)
        Proposal.model_validate(proposal_contract)
        self.assertEqual(proposal_contract["risk"], "string")
        self.assertEqual(proposal_contract["patch"], "same unified diff text as output/patch.diff")
        self.assertEqual(json.loads((package / "state_summary.json").read_text(encoding="utf-8"))["batch_no"], 0)
        ledger_lines = (package / "recent_ledger.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line) for line in ledger_lines], recent_ledger)
        self.assertEqual((package / "base_files" / "custom_amp.scs").read_text(encoding="utf-8"), "simulator lang=spectre\n")
        self.assertEqual((package / "base_files" / "accounting_devices.csv").read_text(encoding="utf-8"), "name,type\nM1,nmos\n")

    def test_context_package_serializes_plan_batch_phase_as_schema_value(self):
        root = scratch_case("context_package_plan_batch_phase")
        state = RunnerState.initial(contract_hash="contract-hash")
        assignment = plan_batch(
            state=state,
            batch_size=1,
            now=datetime(2026, 6, 4, 23, 15, 0, tzinfo=timezone.utc),
        )[0]

        package = write_context_package(
            run_dir=root / "runs" / "run-1",
            agent_call_id="call-1",
            assignment=assignment,
            contract_excerpt="Only DUT and devices.csv may change.",
            state_summary={"batch_no": state.batch_no},
            recent_ledger=[],
            dut_netlist_path="amptest/dummy_neural_amp.scs",
            devices_csv_path="amptest/devices.csv",
            base_dut=root / "dummy_neural_amp.scs",
            base_devices=root / "devices.csv",
        )

        context_text = (package / "context.md").read_text(encoding="utf-8")
        self.assertNotIn("Phase.PHASE1_PERFORMANCE", context_text)
        self.assertIn("phase: phase1_performance", context_text)

    def test_context_package_includes_actionable_recent_failure_feedback(self):
        root = scratch_case("context_package_recent_failure_feedback")
        assignment = CandidateAssignment(
            candidate_id="p1-b030-c01-arch-20260605-170000",
            batch_id="p1-b030",
            role="architecture",
            phase="phase1_performance",
            primary_objective="performance",
        )
        recent_ledger = [
            {
                "candidate_id": "p1-b029-c01-arch-20260605-161926",
                "status": "rejected",
                "reason": "acceptance_gate_failed",
                "metrics": {
                    "performance_nrmse_combined": 1.0,
                    "area_total_p": 4359433.555299999,
                    "power_score_basis_w": 0.0,
                },
            },
            {
                "candidate_id": "p1-b029-c03-opt-20260605-161926",
                "status": "error",
                "reason": "assembly_failed; patch_apply_failed: git apply failed: error: corrupt patch at line 19",
                "metrics": {},
            },
        ]

        package = write_context_package(
            run_dir=root / "runs" / "run-1",
            agent_call_id="call-1",
            assignment=assignment,
            contract_excerpt="Only DUT and devices.csv may change.",
            state_summary={
                "current_phase": "phase1_performance",
                "best_failed_candidate_id": "p1-b029-c01-arch-20260605-161926",
                "best_failed_metrics": {
                    "performance_nrmse_combined": 1.0,
                    "area_total_p": 4359433.555299999,
                    "power_score_basis_w": 0.0,
                },
            },
            recent_ledger=recent_ledger,
            dut_netlist_path="amptest/dummy_neural_amp.scs",
            devices_csv_path="amptest/devices.csv",
            base_dut=root / "dummy_neural_amp.scs",
            base_devices=root / "devices.csv",
        )

        context_text = (package / "context.md").read_text(encoding="utf-8")
        self.assertIn("## Feedback From Recent Verifications", context_text)
        self.assertIn("p1-b029-c01-arch-20260605-161926", context_text)
        self.assertIn("performance_nrmse_combined=1.0", context_text)
        self.assertIn("power_score_basis_w=0.0", context_text)
        self.assertIn("create a DC-biased amplifier with nonzero supply current", context_text)
        self.assertIn("ensure AC and transient metrics can be produced", context_text)
        self.assertIn("do not optimize area until performance metrics are non-null", context_text)
        self.assertIn("abnormally small area_total_p can indicate invalid resistor area accounting", context_text)
        self.assertIn("res_high_po_5p73 resistor instances must include explicit l=, w=, and m=", context_text)
        self.assertIn("avoid corrupt patches", context_text)

    def test_agent_runner_invokes_codex_exec_and_logs_streams(self):
        root = scratch_case("agent_runner")
        context_text = "Review the assigned circuit and write proposal artifacts.\n"
        (root / "context.md").write_text(context_text, encoding="utf-8")
        executor = FakeExecutor()
        runner = AgentRunner(executor=executor)
        call = AgentCall(
            role="architecture",
            context_path=root,
            output_dir=root / "out",
            timeout_seconds=1200,
        )
        runtime_prompt = context_text + "\nRequired artifact output directory: " + str(call.output_dir) + "\n"
        resolved_context = call.context_path.resolve()
        resolved_output = call.output_dir.resolve()
        resolved_runtime_prompt = context_text + "\nRequired artifact output directory: " + str(resolved_output) + "\n"

        with patch("langgraph_runner.agent_io.resolve_codex_command", return_value=["codex.cmd"]):
            result = runner.run(call)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            executor.calls[0][0],
            ["codex.cmd", "exec", "--sandbox", "workspace-write", "-C", str(resolved_context), "-"],
        )
        self.assertEqual(executor.calls[0][3], resolved_runtime_prompt)
        self.assertIn(context_text, executor.calls[0][3])
        self.assertIn(str(resolved_output), executor.calls[0][3])
        self.assertNotIn(runtime_prompt, executor.calls[0][0])
        self.assertEqual(executor.calls[0][1], resolved_context)
        self.assertEqual(executor.calls[0][2], call.timeout_seconds)
        self.assertTrue((root / "out" / "stdout.log").exists())
        self.assertTrue((root / "out" / "stderr.log").exists())
        self.assertTrue((root / "out" / "agent_run.json").exists())
        self.assertEqual((root / "out" / "stdout.log").read_text(encoding="utf-8"), "stdout text")
        self.assertEqual((root / "out" / "stderr.log").read_text(encoding="utf-8"), "stderr text")
        run_data = json.loads((root / "out" / "agent_run.json").read_text(encoding="utf-8"))
        self.assertEqual(run_data["command"], ["codex.cmd", "exec", "--sandbox", "workspace-write", "-C", str(resolved_context), "-"])
        self.assertEqual(run_data["context_path"], str(resolved_context))
        self.assertEqual(run_data["artifact_output_dir"], str(resolved_output))
        self.assertEqual(run_data["log_dir"], str(resolved_output))
        self.assertEqual(run_data["status"], "completed")
        self.assertIsNone(run_data["error_class"])
        self.assertIsNone(run_data["error"])

    def test_agent_runner_can_separate_required_output_from_stream_logs(self):
        root = scratch_case("agent_runner_artifact_output")
        context_text = "Review the assigned circuit and write proposal artifacts under output/.\n"
        (root / "context.md").write_text(context_text, encoding="utf-8")
        artifact_output = root / "output"
        log_output = root / "agent_outputs" / "call-1"
        executor = FakeExecutor()
        runner = AgentRunner(executor=executor)
        call = AgentCall(
            role="architecture",
            context_path=root,
            output_dir=log_output,
            timeout_seconds=1200,
            artifact_output_dir=artifact_output,
        )

        with patch("langgraph_runner.agent_io.resolve_codex_command", return_value=["codex.cmd"]):
            result = runner.run(call)

        self.assertEqual(result.exit_code, 0)
        self.assertTrue(artifact_output.is_dir())
        self.assertIn("Required artifact output directory: " + str(artifact_output.resolve()), executor.calls[0][3])
        self.assertTrue((log_output / "stdout.log").exists())
        self.assertTrue((log_output / "stderr.log").exists())
        self.assertFalse((artifact_output / "stdout.log").exists())
        self.assertFalse((artifact_output / "stderr.log").exists())

    def test_agent_runner_resolves_relative_context_paths_for_codex_cwd_and_prompt(self):
        root = scratch_case("agent_runner_relative_paths")
        context_path = root / "relative_context"
        context_path.mkdir(parents=True, exist_ok=True)
        context_text = "Use stdin as the runtime prompt.\n"
        (context_path / "context.md").write_text(context_text, encoding="utf-8")
        artifact_output = root / "relative_output"
        log_output = root / "relative_logs"
        executor = FakeExecutor()
        runner = AgentRunner(executor=executor)
        call = AgentCall(
            role="architecture",
            context_path=context_path,
            output_dir=log_output,
            timeout_seconds=1200,
            artifact_output_dir=artifact_output,
        )

        with patch("langgraph_runner.agent_io.resolve_codex_command", return_value=["codex.cmd"]):
            result = runner.run(call)

        resolved_context = context_path.resolve()
        resolved_artifact_output = artifact_output.resolve()
        resolved_log_output = log_output.resolve()
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(executor.calls[0][0], ["codex.cmd", "exec", "--sandbox", "workspace-write", "-C", str(resolved_context), "-"])
        self.assertEqual(executor.calls[0][1], resolved_context)
        self.assertEqual(
            executor.calls[0][3],
            context_text + "\nRequired artifact output directory: " + str(resolved_artifact_output) + "\n",
        )
        run_data = json.loads((log_output / "agent_run.json").read_text(encoding="utf-8"))
        self.assertEqual(run_data["context_path"], str(resolved_context))
        self.assertEqual(run_data["artifact_output_dir"], str(resolved_artifact_output))
        self.assertEqual(run_data["log_dir"], str(resolved_log_output))

    def test_agent_runner_launch_failure_writes_logs_and_run_metadata(self):
        root = scratch_case("agent_runner_launch_failure")
        (root / "context.md").write_text("Launch the agent.\n", encoding="utf-8")

        def failing_executor(command, cwd, timeout, stdin_text=None):
            raise PermissionError("[WinError 5] access is denied")

        runner = AgentRunner(executor=failing_executor)
        call = AgentCall(
            role="architecture",
            context_path=root,
            output_dir=root / "logs",
            timeout_seconds=1200,
            artifact_output_dir=root / "output",
        )

        with patch("langgraph_runner.agent_io.resolve_codex_command", return_value=["codex.cmd"]):
            result = runner.run(call)

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_class, "agent_execution_failed")
        self.assertIn("WinError 5", result.error)
        self.assertTrue((root / "logs" / "stdout.log").exists())
        self.assertTrue((root / "logs" / "stderr.log").exists())
        self.assertTrue((root / "logs" / "agent_run.json").exists())
        run_data = json.loads((root / "logs" / "agent_run.json").read_text(encoding="utf-8"))
        self.assertEqual(run_data["command"], ["codex.cmd", "exec", "--sandbox", "workspace-write", "-C", str(root.resolve()), "-"])
        self.assertEqual(run_data["status"], "error")
        self.assertEqual(run_data["error_class"], "agent_execution_failed")
        self.assertEqual(run_data["exit_code"], 1)
        self.assertEqual(run_data["context_path"], str(root.resolve()))
        self.assertEqual(run_data["artifact_output_dir"], str((root / "output").resolve()))
        self.assertEqual(run_data["log_dir"], str((root / "logs").resolve()))
        self.assertIn("WinError 5", run_data["error"])

    def test_agent_runner_missing_context_is_execution_failure_and_writes_metadata(self):
        root = scratch_case("agent_runner_missing_context")

        def should_not_execute(command, cwd, timeout, stdin_text=None):
            raise AssertionError("missing context should fail before executor")

        runner = AgentRunner(executor=should_not_execute)
        call = AgentCall(
            role="architecture",
            context_path=root,
            output_dir=root / "logs",
            timeout_seconds=1200,
            artifact_output_dir=root / "output",
        )

        with patch("langgraph_runner.agent_io.resolve_codex_command", return_value=["codex.cmd"]):
            result = runner.run(call)

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_class, "agent_execution_failed")
        self.assertIn("context.md", result.error)
        self.assertTrue((root / "logs" / "stdout.log").exists())
        self.assertTrue((root / "logs" / "stderr.log").exists())
        self.assertTrue((root / "logs" / "agent_run.json").exists())
        run_data = json.loads((root / "logs" / "agent_run.json").read_text(encoding="utf-8"))
        self.assertEqual(run_data["error_class"], "agent_execution_failed")
        self.assertIn("context.md", run_data["error"])

    def test_agent_runner_log_path_collision_returns_execution_failure_without_metadata(self):
        root = scratch_case("agent_runner_log_path_collision")
        (root / "context.md").write_text("Launch the agent.\n", encoding="utf-8")
        log_path = root / "logs"
        log_path.write_text("not a directory\n", encoding="utf-8")

        def should_not_execute(command, cwd, timeout, stdin_text=None):
            raise AssertionError("log path collision should fail before executor")

        runner = AgentRunner(executor=should_not_execute)
        call = AgentCall(
            role="architecture",
            context_path=root,
            output_dir=log_path,
            timeout_seconds=1200,
            artifact_output_dir=root / "output",
        )

        with patch("langgraph_runner.agent_io.resolve_codex_command", return_value=["codex.cmd"]):
            result = runner.run(call)

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_class, "agent_execution_failed")
        self.assertIn("logs", result.error)
        self.assertIsNone(result.agent_run_path)

    def test_agent_runner_timeout_writes_agent_timeout_metadata(self):
        root = scratch_case("agent_runner_timeout")
        (root / "context.md").write_text("Launch the agent.\n", encoding="utf-8")

        def timeout_executor(command, cwd, timeout, stdin_text=None):
            raise subprocess.TimeoutExpired(cmd=command, timeout=timeout, output="partial stdout", stderr="partial stderr")

        runner = AgentRunner(executor=timeout_executor)
        call = AgentCall(
            role="architecture",
            context_path=root,
            output_dir=root / "logs",
            timeout_seconds=30,
        )

        with patch("langgraph_runner.agent_io.resolve_codex_command", return_value=["codex.cmd"]):
            result = runner.run(call)

        self.assertEqual(result.exit_code, 124)
        self.assertEqual(result.error_class, "agent_timeout")
        run_data = json.loads((root / "logs" / "agent_run.json").read_text(encoding="utf-8"))
        self.assertEqual(run_data["exit_code"], 124)
        self.assertEqual(run_data["error_class"], "agent_timeout")
        self.assertIn("timed out after 30 seconds", run_data["error"])
        self.assertEqual((root / "logs" / "stdout.log").read_text(encoding="utf-8"), "partial stdout")
        self.assertIn("partial stderr", (root / "logs" / "stderr.log").read_text(encoding="utf-8"))

    def test_agent_runner_nonzero_process_result_is_process_failure(self):
        root = scratch_case("agent_runner_process_failure")
        (root / "context.md").write_text("Launch the agent.\n", encoding="utf-8")
        executor = FakeExecutor(exit_code=2)
        runner = AgentRunner(executor=executor)
        call = AgentCall(
            role="architecture",
            context_path=root,
            output_dir=root / "logs",
            timeout_seconds=1200,
        )

        with patch("langgraph_runner.agent_io.resolve_codex_command", return_value=["codex.cmd"]):
            result = runner.run(call)

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_class, "agent_process_failed")
        run_data = json.loads((root / "logs" / "agent_run.json").read_text(encoding="utf-8"))
        self.assertEqual(run_data["error_class"], "agent_process_failed")
        self.assertIn("process exited with code 2", run_data["error"])

    def test_agent_runner_inherits_parent_codex_cli_environment_except_local_write_paths(self):
        root = scratch_case(f"agent_runner_inherited_codex_env_{uuid.uuid4().hex}")
        (root / "context.md").write_text("Launch the agent.\n", encoding="utf-8")
        parent_codex_home = root / "parent_codex_home"
        parent_codex_home.mkdir(parents=True, exist_ok=True)
        (parent_codex_home / "auth.json").write_text('{"token":"secret"}\n', encoding="utf-8")
        (parent_codex_home / "config.toml").write_text('model = "gpt-5.5"\n', encoding="utf-8")
        executor = FakeExecutor()
        runner = AgentRunner(executor=executor)
        call = AgentCall(
            role="architecture",
            context_path=root,
            output_dir=root / "logs",
            timeout_seconds=1200,
            artifact_output_dir=root / "output",
            agent_call_id="call-1",
        )
        original_env = {
            "CODEX_HOME": str(parent_codex_home),
            "TMP": "C:\\Users\\maize\\AppData\\Local\\Temp",
            "TEMP": "C:\\Users\\maize\\AppData\\Local\\Temp",
            "PATH": "C:\\Tools",
            "USERPROFILE": "C:\\Users\\maize",
            "APPDATA": "C:\\Users\\maize\\AppData\\Roaming",
            "LOCALAPPDATA": "C:\\Users\\maize\\AppData\\Local",
            "OPENAI_API_KEY": "secret",
            "AZURE_OPENAI_API_KEY": "secret",
            "KEEP_ME": "inherited",
        }

        with patch.dict("os.environ", original_env, clear=True):
            with patch("langgraph_runner.agent_io.resolve_codex_command", return_value=["codex.cmd"]):
                result = runner.run(call)
            current_env = dict(__import__("os").environ)

        child_env = executor.calls[0][4]
        self.assertEqual(result.exit_code, 0)
        expected_codex_home = str((root / ".codex_home").resolve())
        expected_codex_tmp = str((root / ".codex_tmp").resolve())
        self.assertEqual(child_env["CODEX_HOME"], expected_codex_home)
        self.assertEqual(child_env["TMP"], expected_codex_tmp)
        self.assertEqual(child_env["TEMP"], expected_codex_tmp)
        self.assertEqual(child_env["PATH"], "C:\\Tools")
        self.assertEqual(child_env["USERPROFILE"], "C:\\Users\\maize")
        self.assertEqual(child_env["APPDATA"], "C:\\Users\\maize\\AppData\\Roaming")
        self.assertEqual(child_env["LOCALAPPDATA"], "C:\\Users\\maize\\AppData\\Local")
        self.assertEqual(child_env["OPENAI_API_KEY"], "secret")
        self.assertEqual(child_env["AZURE_OPENAI_API_KEY"], "secret")
        self.assertEqual(child_env["KEEP_ME"], "inherited")
        self.assertTrue((root / ".codex_home").is_dir())
        self.assertTrue((root / ".codex_tmp").is_dir())
        self.assertEqual((root / ".codex_home" / "auth.json").read_text(encoding="utf-8"), '{"token":"secret"}\n')
        self.assertEqual((root / ".codex_home" / "config.toml").read_text(encoding="utf-8"), 'model = "gpt-5.5"\n')
        self.assertEqual(current_env, original_env)
        run_data = json.loads((root / "logs" / "agent_run.json").read_text(encoding="utf-8"))
        self.assertEqual(run_data["environment"]["codex_cli_environment"], "context_local_codex_home")
        self.assertEqual(run_data["environment"]["CODEX_HOME"], expected_codex_home)
        self.assertEqual(run_data["environment"]["TMP"], expected_codex_tmp)
        self.assertEqual(run_data["environment"]["TEMP"], expected_codex_tmp)
        self.assertNotIn("OPENAI_API_KEY", run_data["environment"])
        self.assertNotIn("secret", json.dumps(run_data))

    def test_agent_runner_uses_run_local_codex_home_outside_agent_context(self):
        run_dir = scratch_case(f"agent_runner_run_codex_home_{uuid.uuid4().hex}") / "runs" / "manual"
        context_path = run_dir / "agent_calls" / "call-1"
        context_path.mkdir(parents=True, exist_ok=True)
        (context_path / "context.md").write_text("Launch the agent.\n", encoding="utf-8")
        parent_codex_home = run_dir / "parent_codex_home"
        parent_codex_home.mkdir(parents=True, exist_ok=True)
        (parent_codex_home / "auth.json").write_text('{"token":"secret"}\n', encoding="utf-8")
        executor = FakeExecutor()
        runner = AgentRunner(executor=executor)
        call = AgentCall(
            role="architecture",
            context_path=context_path,
            output_dir=run_dir / "agent_outputs" / "call-1",
            timeout_seconds=1200,
        )
        original_env = {
            "CODEX_HOME": str(parent_codex_home),
            "TMP": r"C:\Users\maize\AppData\Local\Temp",
            "TEMP": r"C:\Users\maize\AppData\Local\Temp",
            "PATH": r"C:\Tools",
        }

        with patch.dict("os.environ", original_env, clear=True):
            with patch("langgraph_runner.agent_io.resolve_codex_command", return_value=["codex.cmd"]):
                result = runner.run(call)

        expected_codex_home_path = run_dir / ".codex_home"
        expected_codex_home = str(expected_codex_home_path.resolve())
        child_env = executor.calls[0][4]
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(child_env["CODEX_HOME"], expected_codex_home)
        self.assertTrue(expected_codex_home_path.is_dir())
        self.assertFalse((context_path / ".codex_home").exists())
        self.assertEqual((expected_codex_home_path / "auth.json").read_text(encoding="utf-8"), '{"token":"secret"}\n')
        run_data = json.loads((run_dir / "agent_outputs" / "call-1" / "agent_run.json").read_text(encoding="utf-8"))
        self.assertEqual(run_data["environment"]["codex_cli_environment"], "run_local_codex_home")
        self.assertEqual(run_data["environment"]["CODEX_HOME"], expected_codex_home)
        self.assertNotIn(r"C:\Users\maize\.codex", json.dumps(run_data))
        self.assertNotIn("secret", json.dumps(run_data))

    def test_agent_runner_does_not_hide_executor_type_errors_when_env_is_supported(self):
        root = scratch_case("agent_runner_executor_type_error")
        (root / "context.md").write_text("Launch the agent.\n", encoding="utf-8")

        def executor_with_internal_type_error(command, cwd, timeout, stdin_text=None, env=None):
            if env is not None:
                raise TypeError("internal env parsing failed")
            return 0, "fallback stdout", ""

        runner = AgentRunner(executor=executor_with_internal_type_error)
        call = AgentCall(
            role="architecture",
            context_path=root,
            output_dir=root / "logs",
            timeout_seconds=1200,
        )

        with patch("langgraph_runner.agent_io.resolve_codex_command", return_value=["codex.cmd"]):
            with self.assertRaises(TypeError) as context:
                runner.run(call)

        self.assertIn("internal env parsing failed", str(context.exception))

    def test_context_package_rejects_agent_call_id_path_escape(self):
        root = scratch_case("context_package_escape")
        assignment = CandidateAssignment(
            candidate_id="p1-b001-c01-arch-20260604-231500",
            batch_id="p1-b001",
            role="architecture",
            phase="phase1_performance",
            primary_objective="performance",
        )

        with self.assertRaises(ValueError):
            write_context_package(
                run_dir=root / "runs" / "run-escape",
                agent_call_id="../escape",
                assignment=assignment,
                contract_excerpt="Only DUT and devices.csv may change.",
                state_summary={"batch_no": 0},
                recent_ledger=[],
                dut_netlist_path="amptest/dummy_neural_amp.scs",
                devices_csv_path="amptest/devices.csv",
                base_dut=root / "dummy_neural_amp.scs",
                base_devices=root / "devices.csv",
            )

    def test_langgraph_runner_docs_keep_candidate_and_prime_contracts_separate(self):
        docs = Path("docs/langgraph-runner.md").read_text(encoding="utf-8")
        proposal_marker = "```json\n"
        proposal_start = docs.index(proposal_marker) + len(proposal_marker)
        proposal_end = docs.index("\n```", proposal_start)
        proposal_contract = json.loads(docs[proposal_start:proposal_end])

        Proposal.model_validate(proposal_contract)
        self.assertLessEqual(
            {
                "candidate_id",
                "phase",
                "agent",
                "hypothesis",
                "primary_objective",
                "changed_blocks",
                "files_touched",
                "expected_effect",
                "risk",
                "patch",
            },
            set(proposal_contract),
        )
        for effect in proposal_contract["expected_effect"].values():
            self.assertNotIn("|", effect)
        self.assertIn("Allowed expected_effect values for each metric: decrease, increase, no_major_change, unknown.", docs)
        self.assertIn("- `output/proposal.json`", docs)
        self.assertIn("- `output/patch.diff`", docs)
        self.assertIn("- `output/notes.md`", docs)
        self.assertIn("under `output/` in that context directory", docs)
        prime_start = docs.index("Prime agents")
        prime_end = docs.index("\n\n", prime_start)
        prime_contract = docs[prime_start:prime_end]
        self.assertIn("notes.md", prime_contract)
        self.assertIn("assigned prime output directory", prime_contract)
        self.assertNotIn("output/notes.md", prime_contract)
        self.assertIn("do not", prime_contract.lower())
        self.assertIn("candidate `proposal.json`", prime_contract)
        self.assertIn("`patch.diff`", prime_contract)
        self.assertIn("Candidate assembly applies patches to isolated workspaces before deterministic_review.", docs)
        self.assertIn("deterministic_review validates the assembled artifacts and patched workspace before verification.", docs)
        self.assertIn("runner_config.dut_netlist", docs)
        self.assertIn("runner_config.devices_csv", docs)
        self.assertIn("path strings", docs)
        self.assertIn("inherits the operator's existing", docs)
        self.assertIn("Codex CLI environment and authentication", docs)
        self.assertIn("overrides write-heavy", docs)
        self.assertIn("`CODEX_HOME` is", docs)
        self.assertIn("under the run directory", docs)
        self.assertIn("`TMP`/`TEMP` are set to `.codex_tmp/`", docs)
        self.assertIn("seeds `auth.json` and `config.toml`", docs)
        self.assertIn("without placing auth files in the agent workdir", docs)
        self.assertNotIn("Candidate and prime agents must not mutate repository files directly, and their patches", docs)
        self.assertNotIn("snapshots after review", docs)

    def test_subprocess_executor_uses_utf8_capture_with_replacement_errors(self):
        runner = AgentRunner()
        completed = Mock(returncode=7, stdout="stdout text", stderr="stderr text")

        with patch("langgraph_runner.agent_io.subprocess.run", return_value=completed) as run:
            result = runner._subprocess_executor(["codex", "exec", "-C", "ctx", "-"], Path("."), 30, "prompt", env={"X": "Y"})

        self.assertEqual(result, (7, "stdout text", "stderr text"))
        kwargs = run.call_args.kwargs
        self.assertIn("encoding", kwargs)
        self.assertIn("errors", kwargs)
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")
        self.assertEqual(kwargs["input"], "prompt")
        self.assertEqual(kwargs["env"], {"X": "Y"})

    def test_local_deterministic_runner_writes_candidate_artifacts_without_executor(self):
        from langgraph_runner.agent_outputs import parse_subagent_output
        from langgraph_runner.local_agent import LocalDeterministicAgentRunner

        root = scratch_case("local_deterministic_candidate")
        base_dut = root / "dummy_neural_amp.scs"
        base_devices = root / "devices.csv"
        base_dut.write_text(
            "simulator lang=spectre\n"
            "subckt dummy_neural_amp GND VDD VIN VOUT VREF\n"
            "R1 VDD VOUT 10k\n"
            "ends dummy_neural_amp\n",
            encoding="utf-8",
        )
        base_devices.write_text(
            "name,type,count,include_in_ppa\n"
            "R1,resistor,1,true\n",
            encoding="utf-8",
        )
        assignment = CandidateAssignment(
            candidate_id="p1-b001-c01-arch-20260604-231500",
            batch_id="p1-b001",
            role="architecture",
            phase="phase1_performance",
            primary_objective="performance",
        )
        context_path = write_context_package(
            run_dir=root / "runs" / "manual",
            agent_call_id="call-1",
            assignment=assignment,
            contract_excerpt="Only DUT and devices.csv may change.",
            state_summary={"batch_no": 0},
            recent_ledger=[],
            dut_netlist_path="amptest/dummy_neural_amp.scs",
            devices_csv_path="amptest/devices.csv",
            base_dut=base_dut,
            base_devices=base_devices,
        )

        result = LocalDeterministicAgentRunner().run(
            AgentCall(
                role="architecture",
                context_path=context_path,
                output_dir=root / "logs",
                artifact_output_dir=context_path / "output",
                timeout_seconds=60,
                agent_call_id="call-1",
            )
        )

        output = context_path / "output"
        parsed = parse_subagent_output(output, assignment.candidate_id, agent_call_id="call-1")
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(parsed.valid, parsed.errors)
        self.assertTrue((output / "proposal.json").exists())
        self.assertTrue((output / "patch.diff").exists())
        self.assertTrue((output / "notes.md").exists())
        self.assertIn("local_deterministic_agent", result.command)
        self.assertNotIn("codex", " ".join(result.command or []).lower())
        patch_text = (output / "patch.diff").read_text(encoding="utf-8")
        proposal = json.loads((output / "proposal.json").read_text(encoding="utf-8"))
        self.assertIn("npn_05v5_W1p00L1p00", patch_text)
        self.assertIn("pnp_05v5_W3p40L3p40", patch_text)
        self.assertIn("res_high_po_5p73 l=5u w=5.73u m=40", patch_text)
        self.assertNotIn("sky130_fd_pr_main__", patch_text)
        self.assertIn("QIN,npn,1", patch_text)
        self.assertIn("QLOAD,pnp,1", patch_text)
        self.assertIn("QOUT,npn,1", patch_text)
        self.assertNotIn("RLOCAL", patch_text)
        self.assertEqual(proposal["changed_blocks"], ["bias", "gain_stage", "output_stage", "device_accounting"])

    def test_local_deterministic_runner_uses_six_bjt_fallback_after_three_bjt_stagnation(self):
        from langgraph_runner.agent_outputs import parse_subagent_output
        from langgraph_runner.local_agent import LocalDeterministicAgentRunner

        root = scratch_case("local_deterministic_fallback_candidate")
        base_dut = root / "dummy_neural_amp.scs"
        base_devices = root / "devices.csv"
        base_dut.write_text(
            "simulator lang=spectre\n"
            "subckt dummy_neural_amp GND VDD VIN VOUT VREF\n"
            "R1 VDD VOUT 10k\n"
            "ends dummy_neural_amp\n",
            encoding="utf-8",
        )
        base_devices.write_text(
            "name,type,count,include_in_ppa\n"
            "R1,resistor,1,true\n",
            encoding="utf-8",
        )
        assignment = CandidateAssignment(
            candidate_id="p1-b001-c03-opt-20260604-231500",
            batch_id="p1-b001",
            role="optimizer",
            phase="phase1_performance",
            primary_objective="performance",
        )
        context_path = write_context_package(
            run_dir=root / "runs" / "manual",
            agent_call_id="call-1",
            assignment=assignment,
            contract_excerpt="Only DUT and devices.csv may change.",
            state_summary={"batch_no": 1, "three_bjt_verified_count": 12, "three_bjt_stagnated": True},
            recent_ledger=[],
            dut_netlist_path="amptest/dummy_neural_amp.scs",
            devices_csv_path="amptest/devices.csv",
            base_dut=base_dut,
            base_devices=base_devices,
        )

        result = LocalDeterministicAgentRunner().run(
            AgentCall(
                role="optimizer",
                context_path=context_path,
                output_dir=root / "logs",
                artifact_output_dir=context_path / "output",
                timeout_seconds=60,
                agent_call_id="call-1",
            )
        )

        output = context_path / "output"
        parsed = parse_subagent_output(output, assignment.candidate_id, agent_call_id="call-1")
        patch_text = (output / "patch.diff").read_text(encoding="utf-8")

        self.assertEqual(result.exit_code, 0)
        self.assertTrue(parsed.valid, parsed.errors)
        self.assertIn("QINP,npn,1", patch_text)
        self.assertIn("QTAIL,npn,1", patch_text)
        self.assertIn("QLOADP,pnp,1", patch_text)
        self.assertIn("QOUT,npn,1", patch_text)
        self.assertEqual(patch_text.count(",npn,1"), 4)
        self.assertEqual(patch_text.count(",pnp,1"), 2)

    def test_local_deterministic_runner_writes_prime_notes_without_executor(self):
        from langgraph_runner.agent_outputs import parse_prime_output
        from langgraph_runner.local_agent import LocalDeterministicAgentRunner

        root = scratch_case("local_deterministic_prime")
        context_path = root / "prime_context"
        context_path.mkdir(parents=True, exist_ok=True)
        (context_path / "context.md").write_text(
            "# Prime Agent Context\n\n"
            "candidate_id: p1-b001-c01-arch-20260604-231500\n"
            "prime_role: R-prime\n\n"
            "Review implementation risk.\n",
            encoding="utf-8",
        )

        result = LocalDeterministicAgentRunner().run(
            AgentCall(
                role="R-prime",
                context_path=context_path,
                output_dir=root / "prime_logs",
                timeout_seconds=30,
                agent_call_id="prime-1",
            )
        )

        parsed = parse_prime_output(root / "prime_logs", "p1-b001-c01-arch-20260604-231500", prime_call_id="prime-1")
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(parsed.valid, parsed.errors)
        self.assertTrue((root / "prime_logs" / "agent_run.json").exists())


if __name__ == "__main__":
    unittest.main()
