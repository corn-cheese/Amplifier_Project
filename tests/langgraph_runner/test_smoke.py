import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

from langgraph_runner.artifacts import ArtifactPaths
from langgraph_runner.state_store import StateStore
from langgraph_runner.verifier import Verifier
from langgraph_runner.workspace import CandidateWorkspace


SCRATCH = Path(__file__).resolve().parents[2] / ".test_tmp_langgraph_runner" / "smoke"
REQUIRED_OUTPUTS = [
    "verification.json",
    "ppa_metrics.json",
    "ppa_report.log",
    "spectre_ac.log",
    "spectre_tran.log",
]


def scratch_case(name: str) -> Path:
    root = SCRATCH / name / f"{os.getpid()}_{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def python_command(code: str) -> str:
    return subprocess.list2cmdline([sys.executable, "-c", code])


def write_fixture_repo(root: Path) -> tuple[Path, Path, Path, Path]:
    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    contract = docs_dir / "top-coordinator-contract.md"
    contract.write_text("# Top Coordinator Contract\n\nSmoke fixture contract.\n", encoding="utf-8")

    amptest = root / "amptest"
    amptest.mkdir(parents=True, exist_ok=True)
    dut = amptest / "dummy_neural_amp.scs"
    dut.write_text(
        "simulator lang=spectre\n"
        'ahdl_include "dummy_neural_amp.va"\n\n'
        "subckt dummy_neural_amp GND VDD VIN VOUT VREF\n"
        "ends dummy_neural_amp\n",
        encoding="utf-8",
    )
    devices = amptest / "devices.csv"
    devices.write_text(
        "name,type,count,width,length,multiplier,segments,seg_length,seg_width,ft_hz,area_p,include_in_ppa\n"
        "XVA_OPAMP_EQUIV,opamp,1,,,,,,,100meg,,true\n"
        "RREF_TOP,resistor,1,,,,100,5.73u,0.35u,,,true\n"
        "RREF_BOT,resistor,1,,,,100,5.73u,0.35u,,,true\n"
        "CINT_EQUIV,capacitor,2,11.5u,11.7u,1,,,,,,true\n",
        encoding="utf-8",
    )
    config = amptest / "config.json"
    config.write_text(json.dumps(_fixture_config(), indent=2) + "\n", encoding="utf-8")
    return contract, dut, devices, config


def _fixture_config() -> dict:
    return {
        "design_name": "veriloga_dummy_neural_amp",
        "work_dir": "run",
        "include_files": [],
        "library_sections": [
            {
                "path": "/home/eda/edk_cadence/sky130_release_0.1.0/models/sky130.lib.spice",
                "section": "tt",
            }
        ],
        "ahdl_include_files": [],
        "dut_netlist": "dummy_neural_amp.scs",
        "dut_subckt": "dummy_neural_amp",
        "dut_pins_order": ["GND", "VDD", "VIN", "VOUT", "VREF"],
        "spec": {
            "vdd": 5.0,
            "vref_ratio": 0.5,
            "vindc_ratio": 0.5,
            "input_ac_amplitude": 0.001,
            "midband_gain_vv": 100.0,
            "low_cut_hz": 10.0,
            "high_cut_hz": 20000.0,
            "rolloff_order_each_side": 4,
            "load_cap_f": 1e-11,
            "attenuation_probe_low_hz": 1.0,
            "attenuation_probe_high_hz": 200000.0,
        },
        "area": {"resistor_source": "netlist"},
        "sim": {
            "spectre_cmd": "spectre",
            "spectre_args": ["+lqtimeout", "60"],
            "ocean_cmd": "ocean",
            "run_spectre": True,
            "run_ocean_export": True,
            "ac": {
                "start_hz": 0.1,
                "stop_hz": 10000000.0,
                "points_per_dec": 50,
            },
            "tran": {
                "stop_s": 0.02,
                "maxstep_s": 1e-6,
                "strobe_s": 2e-6,
                "input": {
                    "kind": "sine",
                    "amplitude_v": 0.001,
                    "frequency_hz": 1000.0,
                },
                "settle_skip_s": 0.004,
                "fft_fundamental_hz": 1000.0,
            },
        },
        "input_files": {
            "devices_csv": "devices.csv",
            "ac_csv": "run/ac.csv",
            "tran_csv": "run/tran.csv",
        },
    }


def fixture_verifier_command() -> str:
    code = (
        "from pathlib import Path; "
        "import json; "
        "out = Path(r'{output_dir}'); "
        "out.mkdir(parents=True, exist_ok=True); "
        "metrics = dict(performance_nrmse_combined=0.031, area_total_p=42.0, power_score_basis_w=0.0025); "
        "(out / 'ppa_metrics.json').write_text(json.dumps(metrics), encoding='utf-8'); "
        "(out / 'ppa_report.log').write_text('ppa passed\\n', encoding='utf-8'); "
        "(out / 'spectre_ac.log').write_text('ac passed\\n', encoding='utf-8'); "
        "(out / 'spectre_tran.log').write_text('tran passed\\n', encoding='utf-8'); "
        "data = dict(candidate_id='{candidate_id}', status='passed', "
        "metrics_path=str(out / 'ppa_metrics.json'), report_path=str(out / 'ppa_report.log'), "
        "spectre_logs=[str(out / 'spectre_ac.log'), str(out / 'spectre_tran.log')], "
        "performance_nrmse_combined=metrics['performance_nrmse_combined'], "
        "area_total_p=metrics['area_total_p'], power_score_basis_w=metrics['power_score_basis_w'], "
        "errors=[]); "
        "(out / 'verification.json').write_text(json.dumps(data), encoding='utf-8')"
    )
    return python_command(code)


class TestLangGraphRunnerSmoke(unittest.TestCase):
    def test_init_workspace_and_fixture_verifier_flow(self):
        root = scratch_case("init_workspace_and_fixture_verifier_flow")
        contract, base_dut, base_devices, base_config = write_fixture_repo(root)
        paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
        store = StateStore(paths=paths, contract_path=contract)

        state = store.initialize()

        self.assertEqual(state.batch_no, 0)

        workspace = CandidateWorkspace(paths.workspaces_dir).create("cid", base_dut, base_devices, base_config)
        self.assertEqual(workspace, paths.workspace_dir("cid"))
        self.assertTrue((workspace / "dummy_neural_amp.scs").exists())
        self.assertTrue((workspace / "devices.csv").exists())
        self.assertTrue((workspace / "config.json").exists())

        output_dir = paths.candidate_dir("cid")
        output_dir.mkdir(parents=True, exist_ok=True)
        verifier = Verifier(
            command=fixture_verifier_command(),
            timeout_seconds=10,
            min_interval_seconds=0,
            required_outputs=REQUIRED_OUTPUTS,
        )

        result = verifier.run("cid", root, workspace, output_dir)

        self.assertEqual(result.status, "passed", result.errors)
        self.assertTrue((output_dir / "verification.json").exists())
        self.assertTrue((output_dir / "ppa_report.log").exists())
        self.assertTrue(Path(result.report_path).exists())


if __name__ == "__main__":
    unittest.main()
