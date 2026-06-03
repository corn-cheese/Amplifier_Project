import json
import shutil
import unittest
import uuid
from pathlib import Path

from langgraph_workflow.config import load_workflow_config
from langgraph_workflow.rendering import render_candidate
from langgraph_workflow.validation import validate_seed


class WorkspaceTempDir:
    def __enter__(self):
        self.path = Path.cwd() / ".test_tmp" / uuid.uuid4().hex
        self.path.mkdir(parents=True, exist_ok=False)
        return str(self.path)

    def __exit__(self, exc_type, exc, tb):
        shutil.rmtree(self.path, ignore_errors=True)


class CurrentSshSeedTests(unittest.TestCase):
    def test_workflow_ssh_uses_three_valid_renderable_seeds(self):
        cfg = load_workflow_config("workflow.ssh.yaml")

        self.assertIsNotNone(cfg.seed_file)
        seed_path = Path(cfg.seed_file)
        self.assertTrue(seed_path.exists())

        payload = json.loads(seed_path.read_text())
        seeds = payload["seeds"]
        self.assertEqual(["seed_0", "seed_1", "seed_2"], [seed["seed_id"].split("_", 2)[0] + "_" + seed["seed_id"].split("_", 2)[1] for seed in seeds])

        base_config = json.loads((cfg.amptest_local_dir / "config.json").read_text())
        with WorkspaceTempDir() as tmp:
            for seed in seeds:
                validation = validate_seed(seed)
                self.assertTrue(validation.valid, validation.errors)

                artifact_dir = Path(tmp) / "runs" / seed["seed_id"] / "trial_0"
                render_candidate(
                    seed=seed,
                    params=seed["initial_params"],
                    base_amptest_config=base_config,
                    artifact_dir=artifact_dir,
                    trial_id="trial_0",
                    backend_name="eda_ssh",
                )
                candidate = (artifact_dir / "candidate.scs").read_text()

                self.assertNotIn("sky130_fd_pr__cap_vpp_11p5x11p7_m1m4_noshield", candidate)
                self.assertNotIn("sky130_fd_pr__res_high_po_5p73", candidate)
                self.assertNotIn(" sky130_fd_pr__npn_05v5 ", candidate)
                self.assertNotIn(" sky130_fd_pr__pnp_05v5 ", candidate)


if __name__ == "__main__":
    unittest.main()
