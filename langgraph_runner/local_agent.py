from __future__ import annotations

import csv
import difflib
import io
import json
import re
from pathlib import Path

from .agent_errors import AGENT_EXECUTION_FAILED
from .agent_io import AgentCall, AgentRunResult, _finalize_agent_run


LOCAL_AGENT_COMMAND = "local_deterministic_agent"

DEFAULT_DEVICE_FIELDS = [
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

THREE_BJT_BODY = [
    "RBIAS VDD NBIAS GND res_high_po_5p73 l=5u w=5.73u m=40",
    "RREF NBIAS GND GND res_high_po_5p73 l=5u w=5.73u m=20",
    "QIN NGAIN VIN NTAIL GND npn_05v5_W1p00L1p00",
    "QLOAD NGAIN VREF VDD VDD pnp_05v5_W3p40L3p40",
    "QOUT VOUT NGAIN GND GND npn_05v5_W1p00L1p00",
    "REMIT NTAIL GND GND res_high_po_5p73 l=5u w=5.73u m=5",
    "RLOAD VDD VOUT GND res_high_po_5p73 l=5u w=5.73u m=30",
    "CFB VOUT NGAIN GND cap_vpp_11p5x11p7_m1m4_noshield",
]

SIX_BJT_BODY = [
    "RBIAS VDD NBIAS GND res_high_po_5p73 l=5u w=5.73u m=35",
    "RREF NBIAS GND GND res_high_po_5p73 l=5u w=5.73u m=18",
    "QTAIL NTAIL NBIAS GND GND npn_05v5_W1p00L1p00",
    "QINP NGAIN VIN NTAIL GND npn_05v5_W1p00L1p00",
    "QINN NREF VREF NTAIL GND npn_05v5_W1p00L1p00",
    "QLOADP NGAIN NBIAS VDD VDD pnp_05v5_W3p40L3p40",
    "QLOADN NREF NBIAS VDD VDD pnp_05v5_W3p40L3p40",
    "QOUT VOUT NGAIN GND GND npn_05v5_W1p00L1p00",
    "RLOAD VDD VOUT GND res_high_po_5p73 l=5u w=5.73u m=24",
    "REMIT VOUT GND GND res_high_po_5p73 l=5u w=5.73u m=8",
    "CCOMP VOUT NGAIN GND cap_vpp_11p5x11p7_m1m4_noshield",
]

THREE_BJT_DEVICES = [
    {"name": "QIN", "type": "npn", "count": "1"},
    {"name": "QLOAD", "type": "pnp", "count": "1"},
    {"name": "QOUT", "type": "npn", "count": "1"},
    {"name": "RBIAS", "type": "resistor", "count": "1", "segments": "40"},
    {"name": "RREF", "type": "resistor", "count": "1", "segments": "20"},
    {"name": "REMIT", "type": "resistor", "count": "1", "segments": "5"},
    {"name": "RLOAD", "type": "resistor", "count": "1", "segments": "30"},
    {"name": "CFB", "type": "capacitor", "count": "1"},
]

SIX_BJT_DEVICES = [
    {"name": "QTAIL", "type": "npn", "count": "1"},
    {"name": "QINP", "type": "npn", "count": "1"},
    {"name": "QINN", "type": "npn", "count": "1"},
    {"name": "QLOADP", "type": "pnp", "count": "1"},
    {"name": "QLOADN", "type": "pnp", "count": "1"},
    {"name": "QOUT", "type": "npn", "count": "1"},
    {"name": "RBIAS", "type": "resistor", "count": "1", "segments": "35"},
    {"name": "RREF", "type": "resistor", "count": "1", "segments": "18"},
    {"name": "RLOAD", "type": "resistor", "count": "1", "segments": "24"},
    {"name": "REMIT", "type": "resistor", "count": "1", "segments": "8"},
    {"name": "CCOMP", "type": "capacitor", "count": "1"},
]


class LocalDeterministicAgentRunner:
    def run(self, call: AgentCall) -> AgentRunResult:
        context_path = call.context_path.resolve()
        output_dir = call.output_dir.resolve()
        artifact_output_dir = (call.artifact_output_dir or call.output_dir).resolve()
        resolved_call = AgentCall(
            role=call.role,
            context_path=context_path,
            output_dir=output_dir,
            timeout_seconds=call.timeout_seconds,
            artifact_output_dir=artifact_output_dir,
            agent_call_id=call.agent_call_id,
        )
        stdout_path = output_dir / "stdout.log"
        stderr_path = output_dir / "stderr.log"
        agent_run_path = output_dir / "agent_run.json"
        command = [LOCAL_AGENT_COMMAND, call.role]
        stdout = ""
        stderr = ""
        status = "completed"
        error_class: str | None = None
        error: str | None = None
        exit_code = 0

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            artifact_output_dir.mkdir(parents=True, exist_ok=True)
            context_text = (context_path / "context.md").read_text(encoding="utf-8")
            if _is_prime_call(call.role, context_text):
                _write_prime_notes(artifact_output_dir, context_text)
                stdout = "local deterministic prime completed\n"
            else:
                _write_candidate_artifacts(artifact_output_dir, context_path, context_text, call.role)
                stdout = "local deterministic candidate completed\n"
        except (OSError, ValueError) as exc:
            exit_code = 1
            status = "error"
            error_class = AGENT_EXECUTION_FAILED
            error = str(exc)
            stderr = str(exc)

        return _finalize_agent_run(
            call=resolved_call,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            agent_run_path=agent_run_path,
            artifact_output_dir=artifact_output_dir,
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            status=status,
            error_class=error_class,
            error=error,
            environment={"agent_backend": "local_deterministic"},
        )


def _is_prime_call(role: str, context_text: str) -> bool:
    return role.endswith("-prime") or "prime_role:" in context_text or context_text.startswith("# Prime Agent Context")


def _write_prime_notes(output_dir: Path, context_text: str) -> None:
    values = _context_values(context_text)
    candidate_id = values.get("candidate_id", "unknown-candidate")
    prime_role = values.get("prime_role", "prime")
    (output_dir / "notes.md").write_text(
        "\n".join(
            [
                f"Local deterministic notes for {prime_role}.",
                f"candidate_id: {candidate_id}",
                "No external agent process was invoked.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_candidate_artifacts(output_dir: Path, context_path: Path, context_text: str, role: str) -> None:
    values = _context_values(context_text)
    candidate_id = _required_value(values, "candidate_id")
    phase = _required_value(values, "phase")
    primary_objective = _required_value(values, "primary_objective")
    agent_role = values.get("agent", role)
    dut_path, devices_path = _allowed_file_paths(context_text)
    topology = _candidate_topology(context_path)
    patch_text = _candidate_patch(context_path, dut_path, devices_path, topology)
    proposal = {
        "candidate_id": candidate_id,
        "phase": phase,
        "agent": agent_role,
        "hypothesis": _topology_hypothesis(topology),
        "primary_objective": primary_objective,
        "changed_blocks": _topology_changed_blocks(topology),
        "files_touched": [dut_path, devices_path],
        "expected_effect": {
            "performance_nrmse_combined": "decrease",
            "area_total_p": "increase",
            "power_score_basis_w": "increase" if topology == "six_bjt_fallback" else "unknown",
        },
        "risk": _topology_risk(topology),
        "patch": patch_text,
    }
    (output_dir / "proposal.json").write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
    (output_dir / "patch.diff").write_text(patch_text, encoding="utf-8")
    (output_dir / "notes.md").write_text(
        "\n".join(
            [
                "Local deterministic candidate.",
                f"Topology: {topology}.",
                "Changed DUT netlist and device accounting only.",
                "No codex exec or network-backed agent process was invoked.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _context_values(context_text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in context_text.splitlines():
        if line.startswith("# Agent Context:"):
            values["agent"] = line.split(":", 1)[1].strip()
            continue
        if ": " in line:
            key, value = line.split(": ", 1)
            values[key.strip()] = value.strip()
    return values


def _required_value(values: dict[str, str], name: str) -> str:
    value = values.get(name)
    if not value:
        raise ValueError(f"missing {name} in agent context")
    return value


def _allowed_file_paths(context_text: str) -> tuple[str, str]:
    match = re.search(r"Allowed file changes:\s+(.+?)\s+and\s+(.+?)\s+only\.", context_text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "amptest/dummy_neural_amp.scs", "amptest/devices.csv"


def _candidate_topology(context_path: Path) -> str:
    state = _read_state_summary(context_path)
    if bool(state.get("three_bjt_stagnated")) and _int_or_zero(state.get("three_bjt_verified_count")) >= 12:
        return "six_bjt_fallback"
    return "three_bjt"


def _read_state_summary(context_path: Path) -> dict:
    path = context_path / "state_summary.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _int_or_zero(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _topology_hypothesis(topology: str) -> str:
    if topology == "six_bjt_fallback":
        return "Six-BJT fallback introduces a differential input pair, active loads, tail bias, and output pull-down stage for measurable small-signal gain."
    return "Three-BJT candidate replaces the behavioral placeholder with discrete BJT gain, active load, bias, and compensation devices."


def _topology_changed_blocks(topology: str) -> list[str]:
    if topology == "six_bjt_fallback":
        return ["bias", "input_pair", "active_load", "output_stage", "compensation", "device_accounting"]
    return ["bias", "gain_stage", "output_stage", "device_accounting"]


def _topology_risk(topology: str) -> str:
    if topology == "six_bjt_fallback":
        return "Fallback topology may draw more current and still needs verifier tuning for cutoff and distortion targets."
    return "Bias points are heuristic and may require verifier-guided optimization for gain and cutoff targets."


def _candidate_patch(context_path: Path, dut_path: str, devices_path: str, topology: str) -> str:
    base_files = context_path / "base_files"
    dut_source = _find_base_file(base_files, Path(dut_path).name, ".scs")
    devices_source = _find_base_file(base_files, Path(devices_path).name, ".csv")
    dut_original = dut_source.read_text(encoding="utf-8")
    devices_original = devices_source.read_text(encoding="utf-8")
    sections = [
        _diff_section(dut_path, dut_original, _modify_dut_netlist(dut_original, topology)),
        _diff_section(devices_path, devices_original, _modify_devices_csv(devices_original, topology)),
    ]
    return "".join(sections)


def _find_base_file(base_files: Path, preferred_name: str, suffix: str) -> Path:
    preferred = base_files / preferred_name
    if preferred.exists():
        return preferred
    matches = sorted(base_files.glob(f"*{suffix}"))
    if matches:
        return matches[0]
    raise ValueError(f"missing base {suffix} file in {base_files}")


def _modify_dut_netlist(text: str, topology: str) -> str:
    subckt_line, subckt_name = _subckt_signature(text)
    body = SIX_BJT_BODY if topology == "six_bjt_fallback" else THREE_BJT_BODY
    return "\n".join(["simulator lang=spectre", "", subckt_line, *body, f"ends {subckt_name}", ""])


def _subckt_signature(text: str) -> tuple[str, str]:
    for line in text.splitlines():
        tokens = line.strip().split()
        if len(tokens) >= 2 and tokens[0].lower() == "subckt":
            return " ".join(tokens), tokens[1]
    return "subckt dummy_neural_amp GND VDD VIN VOUT VREF", "dummy_neural_amp"


def _modify_devices_csv(text: str, topology: str) -> str:
    fieldnames = _device_fieldnames(text)
    specs = SIX_BJT_DEVICES if topology == "six_bjt_fallback" else THREE_BJT_DEVICES
    rows = [_device_row(fieldnames, spec) for spec in specs]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _device_fieldnames(text: str) -> list[str]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return list(DEFAULT_DEVICE_FIELDS)
    fields = list(reader.fieldnames)
    required = {"name", "type", "count", "include_in_ppa"}
    if not required.issubset(fields):
        return list(DEFAULT_DEVICE_FIELDS)
    return fields


def _device_row(fieldnames: list[str], spec: dict[str, str]) -> dict[str, str]:
    row = {field: "" for field in fieldnames}
    row["name"] = spec["name"]
    row["type"] = spec["type"]
    row["count"] = spec["count"]
    if "include_in_ppa" in row:
        row["include_in_ppa"] = "true"
    if spec["type"] == "resistor":
        _set_if_present(row, "segments", spec.get("segments", "10"))
        _set_if_present(row, "seg_length", "5.73u")
        _set_if_present(row, "seg_width", "0.35u")
    elif spec["type"] == "capacitor":
        _set_if_present(row, "width", "11.5u")
        _set_if_present(row, "length", "11.7u")
        _set_if_present(row, "multiplier", "1")
    elif spec["type"] in {"npn", "pnp"}:
        _set_if_present(row, "ft_hz", "1e9")
    return row


def _set_if_present(row: dict[str, str], field: str, value: str) -> None:
    if field in row:
        row[field] = value


def _diff_section(path: str, original: str, modified: str) -> str:
    if original == modified:
        raise ValueError(f"local deterministic backend produced no change for {path}")
    return "diff --git a/{0} b/{0}\n".format(path) + "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
