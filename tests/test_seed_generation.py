import json
import os
import shutil
import unittest
import uuid
from pathlib import Path

from langgraph_workflow.seed_generation import CodexExecSeedProvider, build_codex_seed_repair_prompt
from tests.test_validation import VALID_SEED


class WorkspaceTempDir:
    def __enter__(self):
        self.path = Path.cwd() / ".test_tmp" / uuid.uuid4().hex
        self.path.mkdir(parents=True, exist_ok=False)
        return str(self.path)

    def __exit__(self, exc_type, exc, tb):
        shutil.rmtree(self.path, ignore_errors=True)


class FakeCompletedProcess:
    returncode = 0
    stdout = "codex completed\n"
    stderr = ""


class CodexExecSeedProviderTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows-specific codex shim behavior")
    def test_default_codex_command_uses_windows_executable_shim(self):
        provider = CodexExecSeedProvider(max_seeds=1)

        self.assertTrue(provider.command[0].lower().endswith((".cmd", ".exe")))
        self.assertEqual("exec", provider.command[1])

    def test_codex_exec_provider_reads_structured_seed_output(self):
        calls = []

        def runner(cmd, **kwargs):
            calls.append((cmd, kwargs))
            output_path = Path(cmd[cmd.index("-o") + 1])
            output_path.write_text(json.dumps({"seeds": [VALID_SEED]}))
            return FakeCompletedProcess()

        with WorkspaceTempDir() as tmp:
            provider = CodexExecSeedProvider(max_seeds=1, attempts=1, run_root=Path(tmp), runner=runner)

            seeds = provider({"spec": {"project_spec_path": "neural_signal_amplifier_project.md"}})

        self.assertEqual([VALID_SEED], seeds)
        self.assertIn(Path(calls[0][0][0]).name.lower(), {"codex", "codex.cmd", "codex.exe"})
        self.assertEqual("exec", calls[0][0][1])
        self.assertIn("--ephemeral", calls[0][0])
        self.assertNotIn("--output-schema", calls[0][0])
        self.assertIn("-o", calls[0][0])
        self.assertEqual("utf-8", calls[0][1]["encoding"])
        self.assertEqual("replace", calls[0][1]["errors"])
        self.assertIn("Generate 1 new seed", calls[0][1]["input"])

    def test_codex_exec_provider_retries_with_validation_feedback(self):
        invalid_seed = {**VALID_SEED, "topology_name": "opamp style invalid seed"}
        outputs = [{"seeds": [invalid_seed]}, {"seeds": [VALID_SEED]}]
        prompts = []

        def runner(cmd, **kwargs):
            prompts.append(kwargs["input"])
            output_path = Path(cmd[cmd.index("-o") + 1])
            output_path.write_text(json.dumps(outputs.pop(0)))
            return FakeCompletedProcess()

        with WorkspaceTempDir() as tmp:
            provider = CodexExecSeedProvider(max_seeds=1, attempts=2, run_root=Path(tmp), runner=runner)

            seeds = provider({})

        self.assertEqual([VALID_SEED], seeds)
        self.assertEqual(2, len(prompts))
        self.assertIn("validation feedback", prompts[1])
        self.assertIn("opamp", prompts[1])

    def test_repair_prompt_includes_rendered_artifacts_and_log_excerpts(self):
        with WorkspaceTempDir() as tmp:
            artifact_dir = Path(tmp) / "runs" / VALID_SEED["seed_id"] / "trial_0"
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "candidate.scs").write_text("QBAD VOUT VIN GND GND bad_cell\n")
            (artifact_dir / "devices.csv").write_text("name,type\nQBAD,npn\n")
            (artifact_dir / "config.json").write_text('{"dut_netlist":"candidate.scs"}')
            (artifact_dir / "trial_metadata.json").write_text('{"trial_id":"trial_0"}')
            (artifact_dir / "ppa_report.log").write_text("ppa report failure details\n")
            (artifact_dir / "spectre_ac.log").write_text("spectre ac error\n" + ("x" * 200))
            (artifact_dir / "spectre_tran.log").write_text("spectre tran error\n")
            state = {
                "active_seed": VALID_SEED,
                "trial_results": [
                    {
                        "trial_id": "trial_0",
                        "seed_id": VALID_SEED["seed_id"],
                        "params": VALID_SEED["initial_params"],
                        "status": "scored",
                        "metrics": None,
                        "objective": 1000.0,
                        "artifact_dir": str(artifact_dir),
                        "error": "simulation failed or missing ppa_metrics.json",
                    }
                ],
                "remote_run": {"stdout": "remote stdout text", "stderr": "remote stderr text"},
                "failure_reasons": ["smoke failed"],
            }

            prompt = build_codex_seed_repair_prompt(state, repair_attempt=1, log_excerpt_chars=80)

        self.assertIn("Repair the failed BJT neural amplifier seed", prompt)
        self.assertIn('"failed_seed"', prompt)
        self.assertIn("QBAD VOUT VIN GND GND bad_cell", prompt)
        self.assertIn("remote stderr text", prompt)
        self.assertIn("ppa report failure details", prompt)
        self.assertIn("spectre ac error", prompt)
        self.assertIn("simulation failed or missing ppa_metrics.json", prompt)

    def test_seed_prompts_use_remote_available_pnp_subckt_name(self):
        provider = CodexExecSeedProvider(max_seeds=1)

        def runner(cmd, **kwargs):
            prompt = kwargs["input"]
            self.assertIn("pnp_05v5_W0p68L0p68", prompt)
            self.assertNotIn("sky130_fd_pr__pnp_05v5_W0p68L0p68", prompt)
            output_path = Path(cmd[cmd.index("-o") + 1])
            output_path.write_text(json.dumps({"seeds": [VALID_SEED]}))
            return FakeCompletedProcess()

        with WorkspaceTempDir() as tmp:
            provider = CodexExecSeedProvider(max_seeds=1, attempts=1, run_root=Path(tmp), runner=runner)

            provider({})

        repair_prompt = build_codex_seed_repair_prompt({"active_seed": VALID_SEED}, repair_attempt=1, log_excerpt_chars=0)
        self.assertIn("pnp_05v5_W0p68L0p68", repair_prompt)
        self.assertNotIn("sky130_fd_pr__pnp_05v5_W0p68L0p68", repair_prompt)


if __name__ == "__main__":
    unittest.main()
