from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BASE_CANDIDATE_ID = "p1-b028-c03-arch-20260606-135953"
DEFAULT_TARGET_PERFORMANCE = 0.04
DEFAULT_BATCHES_PER_FAMILY = 3
STATE_FILE_NAME = "strategy_rotation.json"
BRIEF_PATH = Path("docs/topology-exploration-brief.md")


@dataclass(frozen=True)
class StrategyFamily:
    name: str
    focus: str
    instructions: tuple[str, ...]


STRATEGY_FAMILIES = (
    StrategyFamily(
        name="q4_compensation_shaping",
        focus="Keep Q4 placement conservative and use pole-zero shaping to move the AC target response.",
        instructions=(
            "Keep the b028 Q1/Q2/Q3 signal path recognizable.",
            "Add exactly one BJT named `Q4`, preferably as a mild load or bias helper near NDRV.",
            "Prioritize CP1/CP2/CP3, emitter bypass, and interstage pole-zero changes over large topology rewrites.",
        ),
    ),
    StrategyFamily(
        name="q4_pnp_ndrv_active_load",
        focus="Use Q4 as a PNP active load at the Q2 collector / Q3 base-drive node.",
        instructions=(
            "Place Q4 around NDRV as an active pull-up or current-source-style load.",
            "Use explicit passive biasing for Q4 and keep resistor l/w/m parameters on every resistor line.",
            "Avoid collapsing Q2/Q3 headroom; preserve nonzero swing and amplifier-like bias.",
        ),
    ),
    StrategyFamily(
        name="q4_diode_reference_load",
        focus="Use Q4 as a diode-connected or reference device for active-load bias generation.",
        instructions=(
            "Use Q4 to generate or stabilize a device-like bias reference.",
            "Do not add more than one BJT; bias support must be passive R/C only.",
            "Keep the signal path mostly b028-like unless the reference requires a small local load change.",
        ),
    ),
    StrategyFamily(
        name="q4_bias_current_helper",
        focus="Use Q4 mainly to stabilize operating point or current sourcing rather than as a direct gain element.",
        instructions=(
            "Use Q4 as a bias/current helper for Q1/Q2/Q3.",
            "Target VOUT centering and NDRV bias stability before area or power.",
            "Do not introduce an extra high-gain signal stage.",
        ),
    ),
    StrategyFamily(
        name="q4_tail_current_sink",
        focus="Use Q4 as a tail or current-sink helper for a Q1/Q2 pair-like variant.",
        instructions=(
            "Q4 may be an NPN tail/current sink if the candidate keeps a clear single-ended output path.",
            "Avoid wholesale rewrites unless needed for the tail-current topology.",
            "Reject local value-only retunes; this family must test whether current-tail control helps shape response.",
        ),
    ),
)


def analyze_ledger_lines(lines: list[str], *, target_performance: float = DEFAULT_TARGET_PERFORMANCE) -> dict[str, Any]:
    best_candidate_id = None
    best_performance = None
    patch_corrupt_count = 0
    verifier_error_count = 0
    rows_seen = 0

    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows_seen += 1
        reason = str(row.get("reason") or "")
        if "corrupt patch" in reason or "patch_apply_failed" in reason:
            patch_corrupt_count += 1
        if "verifier" in reason.lower() or "ssh" in reason.lower() or "network" in reason.lower():
            verifier_error_count += 1

        metrics = row.get("metrics")
        if not isinstance(metrics, dict):
            continue
        value = _finite_float(metrics.get("performance_nrmse_combined"))
        if value is None:
            continue
        if best_performance is None or value < best_performance:
            best_candidate_id = str(row.get("candidate_id") or "")
            best_performance = value

    return {
        "rows_seen": rows_seen,
        "best_candidate_id": best_candidate_id,
        "best_performance": best_performance,
        "target_hit": best_performance is not None and best_performance <= target_performance,
        "patch_corrupt_count": patch_corrupt_count,
        "verifier_error_count": verifier_error_count,
    }


def next_family_index(current_index: int) -> int:
    return (current_index + 1) % len(STRATEGY_FAMILIES)


def render_strategy_brief(
    *,
    family: StrategyFamily,
    base_candidate_id: str,
    best_candidate_id: str | None,
    best_performance: float | None,
    batches_per_family: int,
) -> str:
    best_text = "none yet" if best_candidate_id is None else f"{best_candidate_id} ({best_performance})"
    lines = [
        "# Topology Exploration Brief",
        "",
        f"strategy family: {family.name}",
        f"fixed baseline candidate: `{base_candidate_id}`",
        f"current best observed candidate: {best_text}",
        "",
        f"Run this family for {batches_per_family} batch before the strategy rotator updates this file again.",
        "",
        "Common constraints:",
        "",
        "- Keep the b028 three-BJT signal path recognizable unless this family explicitly says otherwise.",
        "- Add exactly one BJT named `Q4`; do not add Q5 or any other extra BJT.",
        "- Do not use OPAMPs, Verilog-A behavioral amplifiers, ideal gain blocks, or controlled sources as amplifiers.",
        "- Keep patches directly applicable against the configured candidate base workspace.",
        "- Every `res_high_po_5p73` instance must include explicit positive `l=`, `w=`, and `m=` parameters.",
        "- Keep `devices.csv` synchronized with the netlist.",
        "",
        "Family focus:",
        "",
        family.focus,
        "",
        "Family instructions:",
        "",
    ]
    lines.extend(f"- {item}" for item in family.instructions)
    lines.extend(
        [
            "",
            "Acceptance target:",
            "",
            "- Stop the rotation if `performance_nrmse_combined <= 0.04` is reached.",
            "- Otherwise, record verifier artifacts and let the rotator move to the next 4BJT family.",
            "",
        ]
    )
    return "\n".join(lines)


def run_rotation(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    config_path = _resolve_under_repo(repo_root, args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    artifact_root = _resolve_under_repo(repo_root, str(config["artifact_root"]))
    state_path = artifact_root / STATE_FILE_NAME
    ledger_path = artifact_root / "ledger.jsonl"
    state = _read_state(state_path)
    family_index = int(state.get("family_index", 0)) % len(STRATEGY_FAMILIES)
    base_candidate_id = str(args.base_candidate_id or _candidate_id_from_base_workspace(config) or DEFAULT_BASE_CANDIDATE_ID)

    for _cycle in range(args.cycles):
        before = _read_ledger_lines(ledger_path)
        before_analysis = analyze_ledger_lines(before, target_performance=args.target_performance)
        if before_analysis["target_hit"]:
            _write_state(state_path, {**state, **before_analysis, "family_index": family_index, "stopped": "target_hit"})
            return 0

        family = STRATEGY_FAMILIES[family_index]
        brief = render_strategy_brief(
            family=family,
            base_candidate_id=base_candidate_id,
            best_candidate_id=before_analysis["best_candidate_id"],
            best_performance=before_analysis["best_performance"],
            batches_per_family=args.batches_per_family,
        )
        brief_path = _resolve_under_repo(repo_root, str(args.brief_path))
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(brief, encoding="utf-8")

        command = [
            sys.executable,
            "-m",
            "langgraph_runner",
            "--repo-root",
            str(repo_root),
            "--config",
            str(config_path),
            "run",
            "--count",
            str(args.batches_per_family),
        ]
        completed = subprocess.run(command, cwd=repo_root)
        after = _read_ledger_lines(ledger_path)
        after_analysis = analyze_ledger_lines(after, target_performance=args.target_performance)
        state = {
            "family_index": next_family_index(family_index),
            "last_family": family.name,
            "last_command_returncode": completed.returncode,
            "last_rows_before": len(before),
            "last_rows_after": len(after),
            **after_analysis,
        }
        _write_state(state_path, state)
        if completed.returncode != 0 or after_analysis["target_hit"]:
            return completed.returncode
        family_index = next_family_index(family_index)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rotate bounded 4BJT strategy briefs around the existing runner.")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--config", default="runner_config.json")
    parser.add_argument("--cycles", type=_positive_int, default=1)
    parser.add_argument("--batches-per-family", type=_positive_int, default=DEFAULT_BATCHES_PER_FAMILY)
    parser.add_argument("--target-performance", type=float, default=DEFAULT_TARGET_PERFORMANCE)
    parser.add_argument("--base-candidate-id")
    parser.add_argument("--brief-path", type=Path, default=BRIEF_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    return run_rotation(build_parser().parse_args(argv))


def _read_ledger_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _candidate_id_from_base_workspace(config: dict[str, Any]) -> str | None:
    value = str(config.get("candidate_base_workspace") or "").strip()
    if not value:
        return None
    return Path(value).name


def _resolve_under_repo(repo_root: Path, value: str) -> Path:
    repo = repo_root.resolve()
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (repo / path).resolve()
    try:
        resolved.relative_to(repo)
    except ValueError as exc:
        raise ValueError(f"path_outside_repo: {value}") from exc
    return resolved


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
