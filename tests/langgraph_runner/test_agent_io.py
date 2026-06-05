import json
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from langgraph_runner.agent_io import AgentCall, AgentRunner, write_context_package
from langgraph_runner.batch import CandidateAssignment, plan_batch
from langgraph_runner.schemas import RunnerState


SCRATCH = Path(".test_tmp_langgraph_runner") / "agent_io"


def scratch_case(name: str) -> Path:
    path = SCRATCH / name
    path.mkdir(parents=True, exist_ok=True)
    return path


class FakeExecutor:
    def __init__(self, exit_code=0):
        self.calls = []
        self.exit_code = exit_code

    def __call__(self, command, cwd, timeout):
        self.calls.append((command, cwd, timeout))
        return self.exit_code, "stdout text", "stderr text"


class TestAgentIO(unittest.TestCase):
    def test_context_package_contains_assignment_and_required_schema(self):
        root = scratch_case("context_package")
        base_dut = root / "dummy_neural_amp.scs"
        base_devices = root / "devices.csv"
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
            base_dut=base_dut,
            base_devices=base_devices,
        )

        context_text = (package / "context.md").read_text(encoding="utf-8")
        self.assertTrue((package / "state_summary.json").exists())
        self.assertIn("p1-b001-c01-arch-20260604-231500", context_text)
        self.assertIn("batch_id: p1-b001", context_text)
        self.assertIn("phase: phase1_performance", context_text)
        self.assertIn("primary_objective: performance", context_text)
        self.assertIn("- proposal.json", context_text)
        self.assertIn("- patch.diff", context_text)
        self.assertIn("- notes.md", context_text)
        self.assertEqual(json.loads((package / "state_summary.json").read_text(encoding="utf-8"))["batch_no"], 0)
        ledger_lines = (package / "recent_ledger.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line) for line in ledger_lines], recent_ledger)
        self.assertEqual((package / "base_files" / "dummy_neural_amp.scs").read_text(encoding="utf-8"), "simulator lang=spectre\n")
        self.assertEqual((package / "base_files" / "devices.csv").read_text(encoding="utf-8"), "name,type\nM1,nmos\n")

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
            base_dut=root / "dummy_neural_amp.scs",
            base_devices=root / "devices.csv",
        )

        context_text = (package / "context.md").read_text(encoding="utf-8")
        self.assertNotIn("Phase.PHASE1_PERFORMANCE", context_text)
        self.assertIn("phase: phase1_performance", context_text)

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
        runtime_prompt = context_text + "\nAssigned output directory: " + str(call.output_dir) + "\n"

        result = runner.run(call)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            executor.calls[0][0],
            ["codex", "exec", "-C", str(call.context_path), runtime_prompt],
        )
        self.assertIn(context_text, executor.calls[0][0][-1])
        self.assertIn(str(call.output_dir), executor.calls[0][0][-1])
        self.assertNotEqual(executor.calls[0][0][-1], str(call.context_path / "context.md"))
        self.assertEqual(executor.calls[0][1], call.context_path)
        self.assertEqual(executor.calls[0][2], call.timeout_seconds)
        self.assertTrue((root / "out" / "stdout.log").exists())
        self.assertTrue((root / "out" / "stderr.log").exists())
        self.assertEqual((root / "out" / "stdout.log").read_text(encoding="utf-8"), "stdout text")
        self.assertEqual((root / "out" / "stderr.log").read_text(encoding="utf-8"), "stderr text")

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
                base_dut=root / "dummy_neural_amp.scs",
                base_devices=root / "devices.csv",
            )

    def test_subprocess_executor_uses_utf8_capture_with_replacement_errors(self):
        runner = AgentRunner()
        completed = Mock(returncode=7, stdout="stdout text", stderr="stderr text")

        with patch("langgraph_runner.agent_io.subprocess.run", return_value=completed) as run:
            result = runner._subprocess_executor(["codex", "exec", "-C", "ctx", "prompt"], Path("."), 30)

        self.assertEqual(result, (7, "stdout text", "stderr text"))
        kwargs = run.call_args.kwargs
        self.assertIn("encoding", kwargs)
        self.assertIn("errors", kwargs)
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    def test_subprocess_executor_returns_structured_timeout_result(self):
        runner = AgentRunner()

        with patch(
            "langgraph_runner.agent_io.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["codex", "exec"], timeout=30, output="partial", stderr="late"),
        ):
            result = runner._subprocess_executor(["codex", "exec"], Path("."), 30)

        self.assertEqual(result[0], 124)
        self.assertEqual(result[1], "partial")
        self.assertIn("timed out after 30 seconds", result[2])


if __name__ == "__main__":
    unittest.main()
