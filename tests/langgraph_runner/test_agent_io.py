import json
import unittest
from pathlib import Path

from langgraph_runner.agent_io import AgentCall, AgentRunner, write_context_package
from langgraph_runner.batch import CandidateAssignment


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
            recent_ledger=[],
            base_dut=root / "dummy_neural_amp.scs",
            base_devices=root / "devices.csv",
        )

        self.assertTrue((package / "context.md").exists())
        self.assertTrue((package / "state_summary.json").exists())
        self.assertIn("p1-b001-c01-arch-20260604-231500", (package / "context.md").read_text(encoding="utf-8"))
        self.assertEqual(json.loads((package / "state_summary.json").read_text(encoding="utf-8"))["batch_no"], 0)

    def test_agent_runner_invokes_codex_exec_and_logs_streams(self):
        root = scratch_case("agent_runner")
        executor = FakeExecutor()
        runner = AgentRunner(executor=executor)
        call = AgentCall(
            role="architecture",
            context_path=root,
            output_dir=root / "out",
            timeout_seconds=1200,
        )

        result = runner.run(call)

        self.assertEqual(result.exit_code, 0)
        self.assertIn("codex", executor.calls[0][0][0])
        self.assertTrue((root / "out" / "stdout.log").exists())
        self.assertTrue((root / "out" / "stderr.log").exists())


if __name__ == "__main__":
    unittest.main()
