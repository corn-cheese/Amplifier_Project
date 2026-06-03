from __future__ import annotations

import csv
import io
import json
import re
import string
from dataclasses import dataclass
from typing import Any

from .state import PIN_ORDER, CircuitSeed


ALLOWED_CELLS = {
    "sky130_fd_pr__npn_05v5",
    "sky130_fd_pr__pnp_05v5",
    "sky130_fd_pr__res_high_po_5p73",
    "sky130_fd_pr__cap_vpp_11p5x11p7_m1m4_noshield",
    "sky130_fd_pr__diode_pd2nw_05v5",
}
ACTIVE_TYPES = {"npn", "pnp"}
PASSIVE_TYPES = {"resistor", "capacitor", "diode"}
FORBIDDEN_TOKENS = (
    "opamp",
    "ahdl_include",
    "bsource",
    "laplace",
    "vcvs",
    "vccs",
    "cccs",
    "ccvs",
    "nfet",
    "pfet",
    "nmos",
    "pmos",
)
UNRENDERABLE_NETLIST_CELLS = {
    "sky130_fd_pr__npn_05v5": "npn_05v5_W1p00L1p00",
    "sky130_fd_pr__pnp_05v5": "pnp_05v5_W0p68L0p68",
    "sky130_fd_pr__res_high_po_5p73": "resistor",
    "sky130_fd_pr__cap_vpp_11p5x11p7_m1m4_noshield": "capacitor",
}
ACTIVE_SIM_MODEL_TOKENS = (
    "npn_05v5_W1p00L1p00",
    "npn_05v5_W1p00L2p00",
    "sky130_fd_pr__npn_05v5_W1p00L1p00",
    "sky130_fd_pr__npn_05v5_W1p00L2p00",
    "pnp_05v5_W0p68L0p68",
    "pnp_05v5_W3p40L3p40",
    "sky130_fd_pr__pnp_05v5_W0p68L0p68",
    "sky130_fd_pr__pnp_05v5_W3p40L3p40",
)


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: list[str]


def validate_seed(seed: CircuitSeed | dict[str, Any], devices_csv_text: str | None = None) -> ValidationResult:
    errors: list[str] = []

    pins = list(seed.get("pins", []))
    if pins != PIN_ORDER:
        errors.append(f"pins must equal {PIN_ORDER!r}")

    template = str(seed.get("netlist_template", ""))
    errors.extend(_validate_forbidden_tokens(template, context="netlist template"))
    errors.extend(_validate_netlist_model_references(template))
    errors.extend(_validate_forbidden_tokens(_seed_metadata_text(seed), context="seed metadata"))

    param_ranges = seed.get("param_ranges", {})
    initial_params = seed.get("initial_params", {})
    if not isinstance(param_ranges, dict):
        errors.append("param_ranges must be a mapping")
        param_ranges = {}
    if not isinstance(initial_params, dict):
        errors.append("initial_params must be a mapping")
        initial_params = {}

    placeholders = _template_placeholders(template)
    for placeholder in sorted(placeholders):
        if placeholder not in param_ranges:
            errors.append(f"placeholder '{placeholder}' is not declared in param_ranges")
        if placeholder not in initial_params:
            errors.append(f"placeholder '{placeholder}' is missing from initial_params")
    for param in sorted(initial_params):
        if param not in param_ranges:
            errors.append(f"initial param '{param}' is not declared in param_ranges")
            continue
        errors.extend(_validate_initial_value(param, initial_params[param], param_ranges[param]))

    manifest = seed.get("device_manifest", [])
    if not isinstance(manifest, list):
        errors.append("device_manifest must be a list")
        manifest = []
    manifest_names: set[str] = set()
    active_names: set[str] = set()
    for entry in manifest:
        if not isinstance(entry, dict):
            errors.append("device_manifest entries must be mappings")
            continue
        name = str(entry.get("name", "")).strip()
        device_type = str(entry.get("type", "")).strip().lower()
        cell = str(entry.get("cell", "")).strip()
        if not name:
            errors.append("device manifest entry is missing name")
        else:
            manifest_names.add(name)
        if device_type in ACTIVE_TYPES:
            active_names.add(name)
        elif device_type not in PASSIVE_TYPES:
            errors.append(f"device '{name}' has unsupported type '{device_type}'")
        normalized_cell = cell.lower()
        if normalized_cell not in ALLOWED_CELLS:
            errors.append(f"device '{name}' uses unapproved cell '{cell}'")
        if device_type == "npn" and normalized_cell != "sky130_fd_pr__npn_05v5":
            errors.append(f"NPN device '{name}' uses wrong cell '{cell}'")
        if device_type == "pnp" and normalized_cell != "sky130_fd_pr__pnp_05v5":
            errors.append(f"PNP device '{name}' uses wrong cell '{cell}'")

    for instance_name in _active_instances_from_template(template):
        if instance_name not in manifest_names:
            errors.append(f"active instance '{instance_name}' is missing from device_manifest")

    if devices_csv_text is not None:
        csv_names = _device_csv_names(devices_csv_text)
        for active_name in sorted(active_names):
            if active_name not in csv_names:
                errors.append(f"active device '{active_name}' is missing from devices.csv")

    return ValidationResult(valid=not errors, errors=errors)


def _validate_forbidden_tokens(text: str, *, context: str) -> list[str]:
    lowered = text.lower()
    errors: list[str] = []
    for token in FORBIDDEN_TOKENS:
        if re.search(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", lowered):
            errors.append(f"forbidden token '{token}' found in {context}")
    if re.search(r"sky130_fd_pr__.*(?:nfet|pfet)", lowered):
        errors.append(f"forbidden MOS model name found in {context}")
    return errors


def _validate_netlist_model_references(template: str) -> list[str]:
    errors: list[str] = []
    lowered = template.lower()
    for manifest_cell, simulation_name in UNRENDERABLE_NETLIST_CELLS.items():
        if re.search(rf"(?<![a-z0-9_]){re.escape(manifest_cell)}(?![a-z0-9_])", lowered):
            errors.append(
                "netlist template uses manifest cell "
                f"'{manifest_cell}' as a Spectre model; use '{simulation_name}' in netlist_template"
            )
    return errors


def _seed_metadata_text(seed: CircuitSeed | dict[str, Any]) -> str:
    metadata = {
        "seed_id": seed.get("seed_id", ""),
        "topology_name": seed.get("topology_name", ""),
        "subckt_name": seed.get("subckt_name", ""),
        "rationale": seed.get("rationale", ""),
        "device_manifest": seed.get("device_manifest", []),
    }
    return json_dumps(metadata)


def json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def _template_placeholders(template: str) -> set[str]:
    placeholders: set[str] = set()
    formatter = string.Formatter()
    for _, field_name, _, _ in formatter.parse(template):
        if field_name:
            placeholders.add(field_name.split(".", 1)[0].split("[", 1)[0])
    return placeholders


def _validate_initial_value(name: str, value: Any, param_range: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    range_type = str(param_range.get("type", "float"))
    if range_type in {"float", "log_float", "int"}:
        low = param_range.get("low")
        high = param_range.get("high")
        if low is None or high is None:
            return [f"param range '{name}' must include low and high"]
        try:
            numeric = float(value)
            low_f = float(low)
            high_f = float(high)
        except (TypeError, ValueError):
            return [f"initial param '{name}'={value!r} is not numeric"]
        if range_type == "int" and int(value) != value:
            errors.append(f"initial param '{name}'={value!r} must be an int")
        if numeric < low_f or numeric > high_f:
            errors.append(f"initial param '{name}'={value} is outside [{_fmt(low)}, {_fmt(high)}]")
    elif range_type == "categorical":
        choices = list(param_range.get("choices", []))
        if value not in choices:
            errors.append(f"initial param '{name}'={value!r} is not in choices {choices!r}")
    else:
        errors.append(f"param range '{name}' has unsupported type '{range_type}'")
    return errors


def _fmt(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _active_instances_from_template(template: str) -> set[str]:
    active: set[str] = set()
    for raw_line in template.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("//", "*")):
            continue
        parts = line.split()
        if not parts:
            continue
        instance = parts[0]
        lowered = line.lower()
        if any(token.lower() in lowered for token in ACTIVE_SIM_MODEL_TOKENS):
            active.add(instance[1:] if instance.startswith("X") and len(instance) > 1 else instance)
    return active


def _device_csv_names(text: str) -> set[str]:
    reader = csv.DictReader(io.StringIO(text))
    return {str(row.get("name", "")).strip() for row in reader if row.get("name")}
