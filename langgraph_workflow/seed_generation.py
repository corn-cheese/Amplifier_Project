from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, cast

from .state import CircuitSeed, WorkflowState
from .validation import validate_seed


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass
class CodexExecSeedProvider:
    max_seeds: int
    attempts: int = 2
    run_root: Path = Path("runs")
    cwd: Path = Path(".")
    model: str | None = None
    profile: str | None = None
    timeout_s: int = 1800
    sandbox: str = "read-only"
    command: list[str] = field(default_factory=lambda: [_resolve_codex_executable(), "exec"])
    runner: Runner = subprocess.run

    def __call__(self, state: WorkflowState) -> list[CircuitSeed]:
        if self.max_seeds < 1:
            raise ValueError("max_seeds must be positive")
        if self.attempts < 1:
            raise ValueError("attempts must be positive")

        audit_dir = self.run_root / "seed_generation"
        audit_dir.mkdir(parents=True, exist_ok=True)
        schema_path = audit_dir / "codex_seed_schema.json"
        schema_path.write_text(json.dumps(_seed_output_schema(self.max_seeds), indent=2, sort_keys=True) + "\n")

        validation_feedback: list[dict[str, Any]] = []
        parse_errors: list[str] = []
        for attempt in range(1, self.attempts + 1):
            stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
            output_path = audit_dir / f"codex_seed_output_{stamp}_attempt_{attempt}.json"
            prompt_path = audit_dir / f"codex_seed_prompt_{stamp}_attempt_{attempt}.txt"
            prompt = build_codex_seed_prompt(
                state,
                max_seeds=self.max_seeds,
                validation_feedback=validation_feedback,
                parse_errors=parse_errors,
            )
            prompt_path.write_text(prompt)

            result = self.runner(
                self._command(schema_path, output_path),
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_s,
                cwd=self.cwd,
            )
            if result.returncode != 0:
                raise RuntimeError(f"codex exec seed generation failed: {result.stderr.strip()}")

            try:
                payload = _load_json_payload(output_path, result)
            except ValueError as exc:
                parse_errors.append(str(exc))
                continue

            valid_seeds, validation_feedback = _valid_seeds(payload)
            if valid_seeds:
                return valid_seeds[: self.max_seeds]

        details = {
            "validation_feedback": validation_feedback,
            "parse_errors": parse_errors,
        }
        raise RuntimeError(f"codex exec produced no valid seeds: {json.dumps(details, sort_keys=True)}")

    def _command(self, schema_path: Path, output_path: Path) -> list[str]:
        cmd = list(self.command)
        if self.model:
            cmd.extend(["--model", self.model])
        if self.profile:
            cmd.extend(["--profile", self.profile])
        cmd.extend(
            [
                "--ephemeral",
                "--cd",
                str(self.cwd),
                "--sandbox",
                self.sandbox,
                "-o",
                str(output_path),
                "-",
            ]
        )
        return cmd


@dataclass
class CodexExecSeedRepairProvider:
    attempts: int = 1
    run_root: Path = Path("runs")
    cwd: Path = Path(".")
    model: str | None = "gpt-5.5"
    profile: str | None = None
    timeout_s: int = 1800
    sandbox: str = "read-only"
    log_excerpt_chars: int = 12000
    command: list[str] = field(default_factory=lambda: [_resolve_codex_executable(), "exec"])
    runner: Runner = subprocess.run

    def __call__(self, state: WorkflowState) -> CircuitSeed:
        if self.attempts < 1:
            raise ValueError("attempts must be positive")

        audit_dir = self.run_root / "seed_generation"
        audit_dir.mkdir(parents=True, exist_ok=True)
        schema_path = audit_dir / "codex_seed_repair_schema.json"
        schema_path.write_text(json.dumps(_seed_output_schema(1), indent=2, sort_keys=True) + "\n")

        validation_feedback: list[dict[str, Any]] = []
        parse_errors: list[str] = []
        for attempt in range(1, self.attempts + 1):
            stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
            output_path = audit_dir / f"codex_seed_repair_output_{stamp}_attempt_{attempt}.json"
            prompt_path = audit_dir / f"codex_seed_repair_prompt_{stamp}_attempt_{attempt}.txt"
            prompt = build_codex_seed_repair_prompt(
                state,
                repair_attempt=attempt,
                log_excerpt_chars=self.log_excerpt_chars,
                validation_feedback=validation_feedback,
                parse_errors=parse_errors,
            )
            prompt_path.write_text(prompt)

            result = self.runner(
                self._command(schema_path, output_path),
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_s,
                cwd=self.cwd,
            )
            if result.returncode != 0:
                raise RuntimeError(f"codex exec seed repair failed: {result.stderr.strip()}")

            try:
                payload = _load_json_payload(output_path, result)
            except ValueError as exc:
                parse_errors.append(str(exc))
                continue

            valid_seeds, validation_feedback = _valid_seeds(payload)
            if valid_seeds:
                return valid_seeds[0]

        details = {
            "validation_feedback": validation_feedback,
            "parse_errors": parse_errors,
        }
        raise RuntimeError(f"codex exec produced no valid repaired seed: {json.dumps(details, sort_keys=True)}")

    def _command(self, schema_path: Path, output_path: Path) -> list[str]:
        cmd = list(self.command)
        if self.model:
            cmd.extend(["--model", self.model])
        if self.profile:
            cmd.extend(["--profile", self.profile])
        cmd.extend(
            [
                "--ephemeral",
                "--cd",
                str(self.cwd),
                "--sandbox",
                self.sandbox,
                "-o",
                str(output_path),
                "-",
            ]
        )
        return cmd


def build_codex_seed_prompt(
    state: WorkflowState,
    *,
    max_seeds: int,
    validation_feedback: list[dict[str, Any]] | None = None,
    parse_errors: list[str] | None = None,
) -> str:
    summary = _state_summary(state)
    feedback = {
        "validation feedback": validation_feedback or [],
        "parse_errors": parse_errors or [],
    }
    return f"""Generate {max_seeds} new seed topology JSON object for the BJT neural amplifier LangGraph workflow.

Read these project files if present:
- neural_signal_amplifier_project.md
- amptest/config.json
- amptest/README.md
- LANGGRAPH_WORKFLOW_PLAN.md

Return only JSON matching the supplied schema:
{{"seeds": [CircuitSeed, ...]}}

Hard requirements:
- Use exactly these pins in this order: ["VIN", "VREF", "VDD", "GND", "VOUT"].
- Do not use opamp, MOS/nfet/pfet, Verilog-A behavioral sources, ahdl_include, bsource, laplace, or controlled-source shortcuts.
- Allowed active cells: sky130_fd_pr__npn_05v5, sky130_fd_pr__pnp_05v5.
- Allowed passive cells: sky130_fd_pr__res_high_po_5p73, sky130_fd_pr__cap_vpp_11p5x11p7_m1m4_noshield, sky130_fd_pr__diode_pd2nw_05v5.
- netlist_template must be a Spectre subckt template using Python format placeholders for every tunable parameter.
- Keep approved SKY130 cell names in device_manifest.cell, but do not instantiate those manifest cell names directly in netlist_template.
- In netlist_template use Spectre-simulatable names: resistor primitives as `R1 (N1 N2) resistor r={{r}}`, capacitor primitives as `C1 (N1 N2) capacitor c={{c}}`, NPN BJTs as `XQ1 (C B E S) npn_05v5_W1p00L1p00 mult={{m}}`, and PNP BJTs as `XQ2 (C B E S) pnp_05v5_W0p68L0p68 mult={{m}}`.
- For BJT subcircuit instances, use an `X...` instance name in netlist_template and use the same name without the leading `X` in device_manifest.name, for example `XQ1` in the netlist and `"Q1"` in device_manifest.
- Every template placeholder must exist in param_ranges and initial_params.
- initial_params must be inside the declared ranges.
- device_manifest must include every active BJT instance and every PPA-relevant passive instance.
- Prefer a topology that could plausibly improve over the previous trial history.

Existing workflow state summary:
{json.dumps(summary, indent=2, sort_keys=True, default=str)}

Previous validation feedback:
{json.dumps(feedback, indent=2, sort_keys=True, default=str)}
"""


def build_codex_seed_repair_prompt(
    state: WorkflowState,
    *,
    repair_attempt: int,
    log_excerpt_chars: int,
    validation_feedback: list[dict[str, Any]] | None = None,
    parse_errors: list[str] | None = None,
) -> str:
    failed_seed = state.get("active_seed")
    trial = _latest_trial(state)
    artifact_dir = Path(str(trial.get("artifact_dir", ""))) if trial else None
    repair_context = {
        "failed_seed": failed_seed,
        "failed_trial": _compact_trial(trial) if trial else None,
        "remote_run": state.get("remote_run", {}),
        "failure_reasons": state.get("failure_reasons", []),
        "validation_feedback": validation_feedback or [],
        "parse_errors": parse_errors or [],
        "artifacts": _artifact_excerpt_bundle(artifact_dir, log_excerpt_chars) if artifact_dir else {},
    }
    return f"""Repair the failed BJT neural amplifier seed and return one replacement seed JSON.

Return only JSON matching the supplied schema:
{{"seeds": [CircuitSeed]}}

The replacement must preserve the same DUT interface and project device policy:
- Pins must be exactly ["VIN", "VREF", "VDD", "GND", "VOUT"].
- Do not use opamp, MOS/nfet/pfet, Verilog-A behavioral sources, ahdl_include, bsource, laplace, or controlled-source shortcuts.
- Use only approved SKY130 BJT/passive cells.
- Keep approved SKY130 cell names in device_manifest.cell, but use Spectre-simulatable names in netlist_template: `resistor`, `capacitor`, `npn_05v5_W1p00L1p00`, and `pnp_05v5_W0p68L0p68`.
- For BJT subcircuit instances, use an `X...` instance name in netlist_template and use the same name without the leading `X` in device_manifest.name.
- Keep all Python format placeholders declared in param_ranges and initial_params.
- The repaired seed should address the concrete Spectre/amptest failure shown below.
- Use a new seed_id version such as <old_seed_id>_repair{repair_attempt}.

Repair context:
{json.dumps(repair_context, indent=2, sort_keys=True, default=str)}
"""


def _state_summary(state: WorkflowState) -> dict[str, Any]:
    best = state.get("best_result")
    trials = list(state.get("trial_results", []))
    return {
        "existing_seed_ids": [seed.get("seed_id") for seed in state.get("seeds", [])],
        "best_result": _compact_trial(best) if best else None,
        "recent_trials": [_compact_trial(trial) for trial in trials[-5:]],
        "failure_reasons": state.get("failure_reasons", []),
        "interrupt": state.get("interrupt"),
        "spec": state.get("spec", {}),
    }


def _latest_trial(state: WorkflowState) -> dict[str, Any] | None:
    trials = list(state.get("trial_results", []))
    return trials[-1] if trials else None


def _compact_trial(trial: dict[str, Any]) -> dict[str, Any]:
    return {
        "trial_id": trial.get("trial_id"),
        "seed_id": trial.get("seed_id"),
        "status": trial.get("status"),
        "objective": trial.get("objective"),
        "error": trial.get("error"),
        "params": trial.get("params"),
    }


def _artifact_excerpt_bundle(artifact_dir: Path, limit: int) -> dict[str, str]:
    bundle: dict[str, str] = {}
    for filename in (
        "candidate.scs",
        "devices.csv",
        "config.json",
        "trial_metadata.json",
        "ppa_report.log",
        "spectre_ac.log",
        "spectre_tran.log",
        "remote_stdout.log",
        "remote_stderr.log",
    ):
        path = artifact_dir / filename
        if path.exists():
            bundle[filename] = _excerpt(path.read_text(errors="replace"), limit)
    return bundle


def _excerpt(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]\n"


def _load_json_payload(output_path: Path, result: subprocess.CompletedProcess[str]) -> Any:
    text = output_path.read_text() if output_path.exists() and output_path.read_text().strip() else result.stdout
    return _extract_json(text)


def _extract_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        raise ValueError("codex exec returned empty output")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    starts = [idx for idx in (stripped.find("{"), stripped.find("[")) if idx >= 0]
    for start in sorted(starts):
        try:
            payload, _ = decoder.raw_decode(stripped[start:])
            return payload
        except json.JSONDecodeError:
            continue
    raise ValueError("codex exec output did not contain valid JSON")


def _valid_seeds(payload: Any) -> tuple[list[CircuitSeed], list[dict[str, Any]]]:
    seeds = _coerce_seed_list(payload)
    valid: list[CircuitSeed] = []
    feedback: list[dict[str, Any]] = []
    for index, seed in enumerate(seeds):
        if not isinstance(seed, dict):
            feedback.append({"index": index, "errors": ["seed entry is not an object"]})
            continue
        result = validate_seed(seed)
        if result.valid:
            valid.append(cast(CircuitSeed, seed))
        else:
            feedback.append({"index": index, "seed_id": seed.get("seed_id"), "errors": result.errors})
    return valid, feedback


def _coerce_seed_list(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("seeds"), list):
        return list(payload["seeds"])
    raise ValueError("codex seed payload must be a list or {'seeds': [...]}")


def _seed_output_schema(max_seeds: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["seeds"],
        "properties": {
            "seeds": {
                "type": "array",
                "minItems": 1,
                "maxItems": max_seeds,
                "items": {
                    "type": "object",
                    "additionalProperties": True,
                    "required": [
                        "seed_id",
                        "topology_name",
                        "subckt_name",
                        "pins",
                        "netlist_template",
                        "param_ranges",
                        "initial_params",
                        "device_manifest",
                    ],
                    "properties": {
                        "seed_id": {"type": "string"},
                        "topology_name": {"type": "string"},
                        "subckt_name": {"type": "string"},
                        "pins": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 5,
                            "maxItems": 5,
                        },
                        "netlist_template": {"type": "string"},
                        "param_ranges": {"type": "object"},
                        "initial_params": {"type": "object"},
                        "device_manifest": {"type": "array", "items": {"type": "object"}},
                        "rationale": {"type": "string"},
                    },
                },
            }
        },
    }


def _resolve_codex_executable() -> str:
    if os.name == "nt":
        for name in ("codex.cmd", "codex.exe"):
            path = shutil.which(name)
            if path:
                return path
    return shutil.which("codex") or "codex"
