from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path


WORKFLOW_DIR = Path(__file__).resolve().parent
REPO_ROOT = WORKFLOW_DIR.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from langgraph_runner.ssh_verifier import run_ssh_verifier


IMPL_NETLIST = "dummy_neural_amp_amptest_v2p3_impl.va"
WRAPPER_INSTANCE = "XAMPTEST_V2P3_INTERPRETED"
CAP_MODEL_RE = re.compile(r"\b(cap_[^\s]+)\b", re.IGNORECASE)
M_PARAM_RE = re.compile(r"(?i)(?:^|\s)(?:m|mult|multi|multiplier)\s*=\s*([^\s]+)")


def prepare_workspace_for_amptest_v2p3(candidate_dir: Path) -> None:
    candidate_dir = Path(candidate_dir).resolve()
    config_path = candidate_dir / "config.json"
    netlist_path = candidate_dir / "dummy_neural_amp.scs"
    devices_path = candidate_dir / "devices.csv"
    impl_path = candidate_dir / IMPL_NETLIST

    config = json.loads(config_path.read_text(encoding="utf-8"))
    subckt = str(config.get("dut_subckt", "dummy_neural_amp"))
    pins = [str(pin) for pin in config.get("dut_pins_order", ["GND", "VDD", "VIN", "VOUT", "VREF"])]
    impl_subckt = f"{subckt}_amptest_v2p3_impl"

    original_netlist = _implementation_netlist(netlist_path, impl_path)
    impl_text = _rename_subckt(original_netlist, subckt, impl_subckt)
    impl_path.write_text(impl_text, encoding="utf-8")

    wrapper = _wrapper_netlist(subckt, pins, impl_subckt)
    netlist_path.write_text(wrapper, encoding="utf-8")

    config["dut_netlist"] = "dummy_neural_amp.scs"
    include_files = [str(value) for value in config.get("include_files", [])]
    include_files = [value for value in include_files if value != IMPL_NETLIST]
    config["include_files"] = [IMPL_NETLIST, *include_files]
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    cap_multipliers = _netlist_cap_multipliers(impl_text)
    _normalize_devices_for_netlist_m(devices_path, cap_multipliers)


def _implementation_netlist(netlist_path: Path, impl_path: Path) -> str:
    text = netlist_path.read_text(encoding="utf-8")
    if WRAPPER_INSTANCE not in text:
        return text
    if not impl_path.exists():
        raise FileNotFoundError(f"preflight wrapper exists but implementation netlist is missing: {impl_path}")
    return impl_path.read_text(encoding="utf-8")


def _rename_subckt(netlist: str, old_name: str, new_name: str) -> str:
    lines = []
    for line in netlist.splitlines():
        if re.match(rf"^\s*subckt\s+{re.escape(old_name)}(\s|$)", line, flags=re.IGNORECASE):
            line = re.sub(rf"(^\s*subckt\s+){re.escape(old_name)}(\s|$)", rf"\1{new_name}\2", line, flags=re.IGNORECASE)
        elif re.match(rf"^\s*ends\s+{re.escape(old_name)}\s*$", line, flags=re.IGNORECASE):
            line = re.sub(rf"{re.escape(old_name)}\s*$", new_name, line, flags=re.IGNORECASE)
        lines.append(line)
    return "\n".join(lines).rstrip() + "\n"


def _wrapper_netlist(subckt: str, pins: list[str], impl_subckt: str) -> str:
    pin_text = " ".join(pins)
    return (
        "simulator lang=spectre\n\n"
        f"subckt {subckt} {pin_text}\n"
        f"{WRAPPER_INSTANCE} {pin_text} {impl_subckt}\n"
        f"ends {subckt}\n"
    )


def _netlist_cap_multipliers(netlist: str) -> dict[str, float]:
    multipliers: dict[str, float] = {}
    for raw in netlist.splitlines():
        line = raw.split("//", 1)[0].split(";", 1)[0].strip()
        if not line or line.startswith("*"):
            continue
        tokens = line.split()
        if not tokens:
            continue
        inst = tokens[0]
        if not inst or not inst[0].lower() == "c":
            continue
        if not CAP_MODEL_RE.search(line):
            continue
        multiplier = 1.0
        match = None
        for match in M_PARAM_RE.finditer(line):
            pass
        if match is not None:
            multiplier = _parse_number(match.group(1), 1.0)
        multipliers[inst.lower()] = multiplier
    return multipliers


def _normalize_devices_for_netlist_m(devices_path: Path, cap_multipliers: dict[str, float]) -> None:
    with devices_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if not fieldnames:
        return
    for row in rows:
        name = str(row.get("name", "")).strip().lower()
        dtype = str(row.get("type", "")).strip().lower()
        if dtype == "capacitor" and cap_multipliers.get(name, 1.0) != 1.0:
            row["multiplier"] = "1"
    with devices_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _parse_number(value: str, default: float) -> float:
    try:
        return float(value)
    except ValueError:
        return default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="amptest-v2p3-preflight")
    parser.add_argument("--ssh-target", required=True)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--identity-file", type=Path)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--local-candidate-dir", required=True, type=Path)
    parser.add_argument("--amptest-dir", default=Path("amptest_v2p3/COREONLY"), type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    prepare_workspace_for_amptest_v2p3(args.local_candidate_dir)
    return run_ssh_verifier(
        ssh_target=args.ssh_target,
        remote_root=args.remote_root,
        candidate_id=args.candidate_id,
        repo_root=args.repo_root,
        local_candidate_dir=args.local_candidate_dir,
        identity_file=args.identity_file,
        amptest_dir=args.amptest_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
