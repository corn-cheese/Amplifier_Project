from __future__ import annotations

import copy
import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .state import AMPT_EST_COMMAND, PIN_ORDER, CircuitSeed


DEVICE_CSV_FIELDS = [
    "name",
    "type",
    "count",
    "width",
    "length",
    "multiplier",
    "segments",
    "seg_length",
    "seg_width",
    "ft_hz",
    "area_p",
    "include_in_ppa",
]


@dataclass(frozen=True)
class RenderedCandidate:
    artifact_dir: Path
    candidate_netlist: Path
    devices_csv: Path
    config_json: Path
    metadata_json: Path
    params_hash: str


def render_candidate(
    *,
    seed: CircuitSeed | dict[str, Any],
    params: dict[str, Any],
    base_amptest_config: dict[str, Any],
    artifact_dir: str | Path,
    trial_id: str,
    backend_name: str,
) -> RenderedCandidate:
    artifact_path = Path(artifact_dir)
    artifact_path.mkdir(parents=True, exist_ok=True)

    params_hash = hash_params(params)
    candidate_netlist = artifact_path / "candidate.scs"
    devices_csv = artifact_path / "devices.csv"
    config_json = artifact_path / "config.json"
    metadata_json = artifact_path / "trial_metadata.json"

    candidate_text = str(seed["netlist_template"]).format(**params) + "\n"
    devices_text = _devices_csv_text(seed.get("device_manifest", []), params)
    config_text = _candidate_config_text(seed, base_amptest_config, trial_id)
    render_hash = hash_render_inputs(candidate_text, devices_text, config_text)

    candidate_netlist.write_text(candidate_text)
    devices_csv.write_text(devices_text)
    config_json.write_text(config_text)
    _write_metadata(metadata_json, seed, trial_id, params, params_hash, render_hash, backend_name)

    return RenderedCandidate(
        artifact_dir=artifact_path,
        candidate_netlist=candidate_netlist,
        devices_csv=devices_csv,
        config_json=config_json,
        metadata_json=metadata_json,
        params_hash=params_hash,
    )


def artifact_complete(artifact_dir: str | Path, params: dict[str, Any]) -> bool:
    artifact_path = Path(artifact_dir)
    metrics = artifact_path / "ppa_metrics.json"
    trial_metadata = artifact_path / "trial_metadata.json"
    run_metadata = artifact_path / "run_metadata.json"
    if not metrics.exists() or not trial_metadata.exists() or not run_metadata.exists():
        return False
    try:
        trial_meta = json.loads(trial_metadata.read_text())
        run_meta = json.loads(run_metadata.read_text())
    except json.JSONDecodeError:
        return False
    return (
        trial_meta.get("params_hash") == hash_params(params)
        and trial_meta.get("params_hash") == run_meta.get("params_hash")
        and trial_meta.get("render_hash") == run_meta.get("render_hash")
    )


def hash_params(params: dict[str, Any]) -> str:
    payload = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def hash_render_inputs(candidate_text: str, devices_text: str, config_text: str) -> str:
    payload = json.dumps(
        {
            "candidate.scs": candidate_text,
            "devices.csv": devices_text,
            "config.json": config_text,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def mark_artifact_complete(artifact_dir: str | Path) -> None:
    artifact_path = Path(artifact_dir)
    trial_metadata = artifact_path / "trial_metadata.json"
    if not trial_metadata.exists():
        return
    meta = json.loads(trial_metadata.read_text())
    run_meta = {
        "seed_id": meta.get("seed_id"),
        "trial_id": meta.get("trial_id"),
        "params_hash": meta.get("params_hash"),
        "render_hash": meta.get("render_hash"),
        "completed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    (artifact_path / "run_metadata.json").write_text(json.dumps(run_meta, indent=2, sort_keys=True) + "\n")


def _devices_csv_text(manifest: list[dict[str, Any]], params: dict[str, Any]) -> str:
    from io import StringIO

    out = StringIO()
    writer = csv.DictWriter(out, fieldnames=DEVICE_CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for entry in manifest:
        row = {field: "" for field in DEVICE_CSV_FIELDS}
        for field in DEVICE_CSV_FIELDS:
            if field in entry:
                row[field] = _csv_value(_resolve_value(entry[field], params))
        row["name"] = entry.get("name", "")
        row["type"] = entry.get("type", "")
        if not row["count"]:
            row["count"] = "1"
        if "include_in_ppa" not in entry:
            row["include_in_ppa"] = "true"
        writer.writerow(row)
    return out.getvalue()


def _candidate_config_text(
    seed: CircuitSeed | dict[str, Any],
    base_amptest_config: dict[str, Any],
    trial_id: str,
) -> str:
    cfg = copy.deepcopy(base_amptest_config)
    cfg["design_name"] = f"{seed['seed_id']}_{trial_id}"
    cfg["work_dir"] = "."
    cfg["ahdl_include_files"] = []
    cfg["dut_netlist"] = "candidate.scs"
    cfg["dut_subckt"] = seed["subckt_name"]
    cfg["dut_pins_order"] = list(seed.get("pins", PIN_ORDER))
    cfg["input_files"] = {
        "devices_csv": "devices.csv",
        "ac_csv": "ac.csv",
        "tran_csv": "tran.csv",
    }
    return json.dumps(cfg, indent=2, sort_keys=True) + "\n"


def _write_metadata(
    path: Path,
    seed: CircuitSeed | dict[str, Any],
    trial_id: str,
    params: dict[str, Any],
    params_hash: str,
    render_hash: str,
    backend_name: str,
) -> None:
    metadata = {
        "seed_id": seed["seed_id"],
        "trial_id": trial_id,
        "params": params,
        "params_hash": params_hash,
        "render_hash": render_hash,
        "timestamp_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "command": AMPT_EST_COMMAND,
        "backend": backend_name,
    }
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")


def _resolve_value(value: Any, params: dict[str, Any]) -> Any:
    if isinstance(value, str) and value in params:
        return params[value]
    return value


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
