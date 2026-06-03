import json
import shutil
import uuid
import unittest
from pathlib import Path

from langgraph_workflow.config import load_workflow_config
from langgraph_workflow.objective import compute_objective
from langgraph_workflow.rendering import artifact_complete, mark_artifact_complete, render_candidate
from tests.test_validation import VALID_SEED


class WorkspaceTempDir:
    def __enter__(self):
        self.path = Path.cwd() / ".test_tmp" / uuid.uuid4().hex
        self.path.mkdir(parents=True, exist_ok=False)
        return str(self.path)

    def __exit__(self, exc_type, exc, tb):
        shutil.rmtree(self.path, ignore_errors=True)


class RenderingObjectiveConfigTests(unittest.TestCase):
    def test_load_workflow_config_applies_plan_defaults(self):
        with WorkspaceTempDir() as tmp:
            path = Path(tmp) / "workflow.json"
            path.write_text(json.dumps({"backend": "mock", "remote": {"host": "eda"}}))

            cfg = load_workflow_config(path)

            self.assertEqual("mock", cfg.backend)
            self.assertEqual(Path("runs"), cfg.run_root)
            self.assertEqual("sqlite:///runs/optuna_studies.sqlite3", cfg.optuna_storage)
            self.assertEqual(3, cfg.max_seeds)
            self.assertEqual(5, cfg.max_trials_per_seed)
            self.assertEqual(0.25, cfg.objective_target)
            self.assertEqual(1, cfg.parallelism)
            self.assertEqual(60, cfg.min_interval_s)
            self.assertEqual(1200, cfg.remote_timeout_s)
            self.assertEqual(20, cfg.daily_max_trials)
            self.assertEqual("gpt-5.5", cfg.codex_exec_model)
            self.assertEqual(2, cfg.max_seed_repair_attempts)
            self.assertEqual(6, cfg.max_consecutive_smoke_failures)
            self.assertEqual(12000, cfg.smoke_log_excerpt_chars)

    def test_load_workflow_config_rejects_parallelism_over_one(self):
        with WorkspaceTempDir() as tmp:
            path = Path(tmp) / "workflow.json"
            path.write_text(json.dumps({"backend": "mock", "parallelism": 2}))

            with self.assertRaises(ValueError):
                load_workflow_config(path)

    def test_load_workflow_config_accepts_user_throttle_key_names(self):
        with WorkspaceTempDir() as tmp:
            path = Path(tmp) / "workflow.json"
            path.write_text(
                json.dumps(
                    {
                        "backend": "mock",
                        "max_trials": 5,
                        "parallelism": 1,
                        "min_interval_s": 60,
                        "timeout_s": 1200,
                        "daily_max_trials": 20,
                    }
                )
            )

            cfg = load_workflow_config(path)

            self.assertEqual(5, cfg.max_trials_per_seed)
            self.assertEqual(1, cfg.parallelism)
            self.assertEqual(60, cfg.min_interval_s)
            self.assertEqual(1200, cfg.remote_timeout_s)
            self.assertEqual(20, cfg.daily_max_trials)

    def test_render_candidate_writes_isolated_artifacts_and_metadata(self):
        with WorkspaceTempDir() as tmp:
            artifact_dir = Path(tmp) / "runs" / VALID_SEED["seed_id"] / "trial_0"
            base_config = {
                "design_name": "template",
                "work_dir": "run",
                "include_files": [],
                "library_sections": [{"path": "/eda/models/sky130.lib.spice", "section": "tt"}],
                "ahdl_include_files": [],
                "dut_netlist": "old.scs",
                "dut_subckt": "old",
                "dut_pins_order": ["GND", "VDD", "VIN", "VOUT", "VREF"],
                "spec": {"vdd": 5.0, "load_cap_f": 1e-11},
                "sim": {"run_spectre": True, "run_ocean_export": True, "ac": {}, "tran": {}},
                "input_files": {"devices_csv": "old.csv", "ac_csv": "run/ac.csv", "tran_csv": "run/tran.csv"},
            }
            params = {"q_mult": 3, "r_bias": 25000.0, "c_load": 2e-11}

            rendered = render_candidate(
                seed=VALID_SEED,
                params=params,
                base_amptest_config=base_config,
                artifact_dir=artifact_dir,
                trial_id="trial_0",
                backend_name="mock",
            )

            self.assertEqual(artifact_dir, rendered.artifact_dir)
            self.assertTrue((artifact_dir / "candidate.scs").exists())
            self.assertTrue((artifact_dir / "devices.csv").exists())
            self.assertTrue((artifact_dir / "config.json").exists())
            self.assertTrue((artifact_dir / "trial_metadata.json").exists())
            self.assertIn("mult=3", (artifact_dir / "candidate.scs").read_text())

            config = json.loads((artifact_dir / "config.json").read_text())
            self.assertEqual(".", config["work_dir"])
            self.assertEqual("candidate.scs", config["dut_netlist"])
            self.assertEqual("bjt_amp", config["dut_subckt"])
            self.assertEqual(["VIN", "VREF", "VDD", "GND", "VOUT"], config["dut_pins_order"])
            self.assertEqual("devices.csv", config["input_files"]["devices_csv"])
            self.assertEqual("ac.csv", config["input_files"]["ac_csv"])

            metadata = json.loads((artifact_dir / "trial_metadata.json").read_text())
            self.assertEqual("seed_0_abcd1234", metadata["seed_id"])
            self.assertEqual("trial_0", metadata["trial_id"])
            self.assertEqual("mock", metadata["backend"])
            self.assertEqual("python3 ppa_wrapper.py all --config ./config.json", metadata["command"])
            self.assertTrue(artifact_complete(artifact_dir, params) is False)

            (artifact_dir / "ppa_metrics.json").write_text(json.dumps({"performance_nrmse_combined": 0.1}))
            mark_artifact_complete(artifact_dir)
            self.assertTrue(artifact_complete(artifact_dir, params))

    def test_artifact_complete_rejects_stale_metrics_after_candidate_changes(self):
        with WorkspaceTempDir() as tmp:
            artifact_dir = Path(tmp) / "runs" / VALID_SEED["seed_id"] / "trial_0"
            base_config = {
                "design_name": "template",
                "work_dir": "run",
                "dut_netlist": "old.scs",
                "dut_subckt": "old",
                "dut_pins_order": ["VIN", "VREF", "VDD", "GND", "VOUT"],
                "spec": {"vdd": 5.0, "load_cap_f": 1e-11},
                "sim": {"run_spectre": True, "run_ocean_export": True, "ac": {}, "tran": {}},
                "input_files": {"devices_csv": "old.csv", "ac_csv": "run/ac.csv", "tran_csv": "run/tran.csv"},
            }
            params = {"q_mult": 3, "r_bias": 25000.0, "c_load": 2e-11}
            render_candidate(
                seed=VALID_SEED,
                params=params,
                base_amptest_config=base_config,
                artifact_dir=artifact_dir,
                trial_id="trial_0",
                backend_name="mock",
            )
            (artifact_dir / "ppa_metrics.json").write_text(json.dumps({"performance_nrmse_combined": 0.1}))
            mark_artifact_complete(artifact_dir)
            self.assertTrue(artifact_complete(artifact_dir, params))

            changed_seed = {**VALID_SEED, "netlist_template": VALID_SEED["netlist_template"] + "\n// changed\n"}
            render_candidate(
                seed=changed_seed,
                params=params,
                base_amptest_config=base_config,
                artifact_dir=artifact_dir,
                trial_id="trial_0",
                backend_name="mock",
            )

            self.assertFalse(artifact_complete(artifact_dir, params))

    def test_objective_matches_formula_and_applies_hard_penalties(self):
        metrics = {
            "performance_nrmse_combined": 0.1,
            "area_power": {"area_total_p": 100.0, "power_score_basis_w": 1e-3},
            "ac": {"midband_gain_db": 40.0, "lower_3db_hz": 10.0, "upper_3db_hz": 20000.0},
            "tran": {"vout_ac_peak_to_peak_v": 0.2, "vout_mean_v": 2.5, "thd_db": -50.0},
        }

        result = compute_objective(metrics)

        self.assertAlmostEqual(0.190309, result.objective, places=5)
        self.assertEqual([], result.penalties)

        missing = compute_objective({"area_power": {}, "ac": {}, "tran": {}})
        self.assertGreaterEqual(missing.objective, 25.0)
        self.assertTrue(any("missing performance_nrmse_combined" in penalty for penalty in missing.penalties))

        failed = compute_objective(None, simulation_failed=True)
        self.assertEqual(1000.0, failed.objective)
        self.assertIn("simulation failed or missing ppa_metrics.json", failed.penalties)

        clipped = compute_objective(
            {
                "performance_nrmse_combined": 0.1,
                "area_power": {"area_total_p": 0.0, "power_score_basis_w": 0.0},
                "ac": {"midband_gain_db": 31.0, "lower_3db_hz": 21.0, "upper_3db_hz": 9000.0},
                "tran": {"vout_mean_v": 3.1, "vout_min_v": 0.05, "vout_max_v": 4.95},
            }
        )
        self.assertGreaterEqual(clipped.objective, 40.0)
        self.assertTrue(any("transient output clips" in penalty for penalty in clipped.penalties))


if __name__ == "__main__":
    unittest.main()
