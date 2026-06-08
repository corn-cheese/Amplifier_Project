from __future__ import annotations

import csv
import importlib.util
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path


WORKFLOW_DIR = Path(__file__).resolve().parent
REPO_ROOT = WORKFLOW_DIR.parents[2]


def _load_core():
    core_path = REPO_ROOT / "amptest_v2p3" / "COREONLY" / "ppa_wrapper_core.py"
    spec = importlib.util.spec_from_file_location("ppa_wrapper_core_for_test", core_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestAmptestV2p3Preflight(unittest.TestCase):
    def test_range_autotune_expands_bounds_when_top_trials_cluster_at_edges(self) -> None:
        sys.path.insert(0, str(WORKFLOW_DIR))
        from optuna_q5_bandpass_sweep import FAMILY_SPECS, adapt_param_ranges_for_batch

        spec = FAMILY_SPECS["output-active-sink-expanded"]
        batch = []
        for index in range(10):
            params = {
                name: (
                    param.low + (param.high - param.low) * 0.97
                    if name == "CE2_m" and index < 7
                    else param.low / 0.97
                    if name == "RBUF_l" and index < 7
                    else param.low + (param.high - param.low) * 0.5
                )
                for name, param in spec.params.items()
            }
            batch.append(
                {
                    "trial_no": index + 1,
                    "params": params,
                    "objective": {"objective": float(index), "rejected": False},
                }
            )

        adjusted, events = adapt_param_ranges_for_batch(spec, batch)

        self.assertEqual({event["name"] for event in events}, {"CE2_m", "RBUF_l"})
        self.assertGreater(adjusted.params["CE2_m"].high, spec.params["CE2_m"].high)
        self.assertLess(adjusted.params["RBUF_l"].low, spec.params["RBUF_l"].low)
        self.assertEqual(adjusted.params["CP1_m"], spec.params["CP1_m"])

    def test_preflight_makes_raw_amptest_area_include_capacitors_once(self) -> None:
        sys.path.insert(0, str(WORKFLOW_DIR))
        from amptest_v2p3_preflight import prepare_workspace_for_amptest_v2p3

        source = REPO_ROOT / "Best" / "q5-bp-dual-input-hp-output-sink-trial-0146-workflow" / "best_run" / "trial_0146" / "workspace"
        scratch_root = REPO_ROOT / ".tmp" / f"test_amptest_v2p3_preflight_{uuid.uuid4().hex}"
        scratch_root.mkdir(parents=True)
        try:
            workspace = scratch_root
            for name in ("dummy_neural_amp.scs", "devices.csv", "config.json"):
                (workspace / name).write_text((source / name).read_text(encoding="utf-8"), encoding="utf-8")

            prepare_workspace_for_amptest_v2p3(workspace)

            config = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
            self.assertIn("dummy_neural_amp_amptest_v2p3_impl.va", config["include_files"])
            self.assertIn("XAMPTEST_V2P3_INTERPRETED", (workspace / "dummy_neural_amp.scs").read_text(encoding="utf-8"))

            with (workspace / "devices.csv").open(newline="", encoding="utf-8") as handle:
                rows = {row["name"]: row for row in csv.DictReader(handle)}
            self.assertEqual(rows["CE1"]["multiplier"], "1")
            self.assertEqual(rows["CIN1"]["multiplier"], "1")

            core = _load_core()
            cfg = core.load_config(workspace / "config.json")
            devices_csv = core.resolve_path(cfg, cfg["input_files"]["devices_csv"])
            area_power = core.analyze_area_power(devices_csv, cfg)
            area_rows = {row["name"]: row for row in area_power["device_rows"]}

            self.assertIn("CE1", area_rows)
            self.assertIn("CIN2", area_rows)
            self.assertAlmostEqual(area_rows["CE1"]["area_p_total"], 134.55 * 62929414, delta=1.0)
            self.assertAlmostEqual(area_power["area_total_p"], 17530907160.97451, delta=1.0)
        finally:
            if scratch_root.exists():
                shutil.rmtree(scratch_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
