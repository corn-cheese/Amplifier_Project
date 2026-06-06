import json
import unittest
from pathlib import Path

from langgraph_runner.config import RunnerConfig, load_runner_config


SCRATCH = Path(".test_tmp_langgraph_runner") / "config"


def minimal_config(**updates):
    config = {
        "artifact_root": "automation_artifacts",
        "contract_path": "docs/contract.md",
        "amptest_dir": "amptest",
        "dut_netlist": "amptest/dummy_neural_amp.scs",
        "devices_csv": "amptest/devices.csv",
        "amptest_config": "amptest/config.json",
        "candidate_generation_batch_size": 1,
        "max_active_primes_per_subagent": 1,
        "max_total_primes_per_subagent": 1,
        "agent_timeouts_seconds": {"subagent": 60, "prime": 30},
        "verifier": {
            "command": "python -m unittest",
            "timeout_seconds": 60,
            "min_interval_seconds": 0,
            "required_outputs": ["verification.json"],
        },
    }
    config.update(updates)
    return config


class TestRunnerConfig(unittest.TestCase):
    def test_agent_backend_defaults_to_codex_exec(self):
        config = RunnerConfig.model_validate(minimal_config())

        self.assertEqual(config.agent_backend.mode, "codex_exec")

    def test_agent_backend_accepts_local_deterministic_mode(self):
        config = RunnerConfig.model_validate(
            minimal_config(agent_backend={"mode": "local_deterministic"})
        )

        self.assertEqual(config.agent_backend.mode, "local_deterministic")

    def test_load_runner_config_reads_agent_backend(self):
        root = SCRATCH / "load_backend"
        root.mkdir(parents=True, exist_ok=True)
        path = root / "runner_config.json"
        path.write_text(
            json.dumps(minimal_config(agent_backend={"mode": "local_deterministic"})),
            encoding="utf-8",
        )

        config = load_runner_config(path)

        self.assertEqual(config.agent_backend.mode, "local_deterministic")

    def test_repository_runner_config_uses_codex_exec_backend(self):
        config = load_runner_config(Path("runner_config.json"))

        self.assertEqual(config.agent_backend.mode, "codex_exec")


if __name__ == "__main__":
    unittest.main()
