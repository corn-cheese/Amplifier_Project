from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from pydantic import ValidationError

from .schemas import Proposal, ReviewResult


FORBIDDEN_SHORTCUT_PATTERNS = [
    re.compile(r"(?<![A-Za-z0-9])ahdlib(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])opamp(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"\bvcvs\b", re.IGNORECASE),
    re.compile(r"\bvccs\b", re.IGNORECASE),
    re.compile(r"\bccvs\b", re.IGNORECASE),
    re.compile(r"\bcccs\b", re.IGNORECASE),
    re.compile(r"\blaplace\b", re.IGNORECASE),
    re.compile(r"\bbsource\b", re.IGNORECASE),
    re.compile(r"\bahdl_include\b", re.IGNORECASE),
]

ALLOWED_DEVICE_TYPES = {"npn", "pnp", "resistor", "capacitor", "diode"}


class DeterministicReviewer:
    def __init__(self, allowed_files: set[str], dut_subckt: str, dut_pins_order: list[str]):
        self.allowed_files = allowed_files
        self.dut_subckt = dut_subckt
        self.dut_pins_order = dut_pins_order

    def review(self, candidate_dir: Path, workspace_dir: Path, assigned_candidate_id: str) -> ReviewResult:
        errors: list[str] = []
        checks: dict[str, bool] = {}
        proposal_path = candidate_dir / "proposal.json"
        patch_path = candidate_dir / "patch.diff"
        notes_path = candidate_dir / "notes.md"
        checks["required_artifacts"] = proposal_path.exists() and patch_path.exists() and notes_path.exists()
        if not checks["required_artifacts"]:
            errors.append("missing_required_artifact")
            return ReviewResult(candidate_id=assigned_candidate_id, passed=False, checks=checks, errors=errors)

        try:
            proposal = Proposal.model_validate(json.loads(proposal_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValidationError):
            checks["proposal_schema"] = False
            errors.append("proposal_schema_invalid")
            return ReviewResult(candidate_id=assigned_candidate_id, passed=False, checks=checks, errors=errors)

        checks["proposal_schema"] = True
        checks["candidate_id"] = proposal.candidate_id == assigned_candidate_id
        if not checks["candidate_id"]:
            errors.append("candidate_id_mismatch")

        patch_text = patch_path.read_text(encoding="utf-8")
        checks["patch_present"] = bool(patch_text.strip())
        if not checks["patch_present"]:
            errors.append("empty_patch")

        touched_paths = set(proposal.files_touched) | self._extract_patch_paths(patch_text)
        checks["file_scope"] = touched_paths.issubset(self.allowed_files)
        if not checks["file_scope"]:
            errors.append("illegal_file_touch")

        netlist_path = workspace_dir / "dummy_neural_amp.scs"
        checks["workspace_netlist"] = netlist_path.exists()
        if not checks["workspace_netlist"]:
            errors.append("workspace_netlist_missing")
            return ReviewResult(candidate_id=assigned_candidate_id, passed=False, checks=checks, errors=errors)

        netlist = netlist_path.read_text(encoding="utf-8", errors="ignore")
        checks["pin_contract"] = self._has_pin_contract(netlist)
        if not checks["pin_contract"]:
            errors.append("invalid_dut_pin_contract")

        checks["forbidden_shortcut"] = not any(pattern.search(netlist) for pattern in FORBIDDEN_SHORTCUT_PATTERNS)
        if not checks["forbidden_shortcut"]:
            errors.append("forbidden_shortcut")

        devices_path = workspace_dir / "devices.csv"
        checks["devices_csv"] = devices_path.exists() and self._devices_csv_is_valid(devices_path)
        if not checks["devices_csv"]:
            errors.append("devices_csv_invalid")

        return ReviewResult(candidate_id=assigned_candidate_id, passed=not errors, checks=checks, errors=errors)

    def _has_pin_contract(self, netlist: str) -> bool:
        expected = " ".join(["subckt", self.dut_subckt, *self.dut_pins_order]).lower()
        logical_lines = [" ".join(line.split()).lower() for line in netlist.splitlines()]
        return expected in logical_lines

    def _extract_patch_paths(self, patch_text: str) -> set[str]:
        paths: set[str] = set()
        for line in patch_text.splitlines():
            if line.startswith("diff --git "):
                parts = line.split()
                if len(parts) >= 4:
                    self._add_patch_path(paths, parts[2])
                    self._add_patch_path(paths, parts[3])
            elif line.startswith("--- ") or line.startswith("+++ "):
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    self._add_patch_path(paths, parts[1])
        return paths

    def _add_patch_path(self, paths: set[str], raw_path: str) -> None:
        path = raw_path.split("\t", 1)[0].split(" ", 1)[0]
        if path == "/dev/null":
            return
        if path.startswith("a/") or path.startswith("b/"):
            path = path[2:]
        paths.add(path)

    def _devices_csv_is_valid(self, path: Path) -> bool:
        required = {"name", "type", "count", "include_in_ppa"}
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
                return False
            for row in reader:
                row_text = " ".join(str(value) for value in row.values() if value is not None)
                if any(pattern.search(row_text) for pattern in FORBIDDEN_SHORTCUT_PATTERNS):
                    return False
                if str(row.get("type") or "").strip().lower() not in ALLOWED_DEVICE_TYPES:
                    return False
            return True
