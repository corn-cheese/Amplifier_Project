from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from .state import WorkflowConfig


def load_workflow_config(path: str | Path) -> WorkflowConfig:
    """Load workflow config from JSON or simple YAML and apply plan defaults."""

    cfg_path = Path(path)
    raw = _load_mapping(cfg_path)
    base = WorkflowConfig()

    data = {
        "backend": raw.get("backend", base.backend),
        "run_root": Path(raw.get("run_root", base.run_root)),
        "amptest_local_dir": Path(raw.get("amptest_local_dir", base.amptest_local_dir)),
        "checkpoint_db": Path(raw.get("checkpoint_db", base.checkpoint_db)),
        "optuna_storage": raw.get("optuna_storage", base.optuna_storage),
        "max_seeds": int(raw.get("max_seeds", base.max_seeds)),
        "max_trials_per_seed": int(raw.get("max_trials_per_seed", raw.get("max_trials", base.max_trials_per_seed))),
        "objective_target": float(raw.get("objective_target", base.objective_target)),
        "parallelism": int(raw.get("parallelism", base.parallelism)),
        "min_interval_s": int(raw.get("min_interval_s", base.min_interval_s)),
        "remote_timeout_s": int(raw.get("remote_timeout_s", raw.get("timeout_s", base.remote_timeout_s))),
        "daily_max_trials": int(raw.get("daily_max_trials", base.daily_max_trials)),
        "max_seed_repair_attempts": int(raw.get("max_seed_repair_attempts", base.max_seed_repair_attempts)),
        "max_consecutive_smoke_failures": int(
            raw.get("max_consecutive_smoke_failures", base.max_consecutive_smoke_failures)
        ),
        "smoke_log_excerpt_chars": int(raw.get("smoke_log_excerpt_chars", base.smoke_log_excerpt_chars)),
        "llm_seed_batch_size": int(raw.get("llm_seed_batch_size", base.llm_seed_batch_size)),
        "llm_seed_attempts": int(raw.get("llm_seed_attempts", base.llm_seed_attempts)),
        "codex_exec_model": raw.get("codex_exec_model", base.codex_exec_model),
        "codex_exec_profile": raw.get("codex_exec_profile", base.codex_exec_profile),
        "codex_exec_timeout_s": int(raw.get("codex_exec_timeout_s", base.codex_exec_timeout_s)),
        "codex_exec_sandbox": raw.get("codex_exec_sandbox", base.codex_exec_sandbox),
        "remote": dict(raw.get("remote", base.remote)),
    }
    if data["parallelism"] != 1:
        raise ValueError("parallelism must be 1; Cadence/Spectre trials must never run in parallel")
    if data["max_trials_per_seed"] > data["daily_max_trials"]:
        data["max_trials_per_seed"] = data["daily_max_trials"]
    if data["min_interval_s"] < 0:
        raise ValueError("min_interval_s must be non-negative")
    if data["remote_timeout_s"] <= 0:
        raise ValueError("timeout_s must be positive")
    if data["max_seed_repair_attempts"] < 0:
        raise ValueError("max_seed_repair_attempts must be non-negative")
    if data["max_consecutive_smoke_failures"] < 1:
        raise ValueError("max_consecutive_smoke_failures must be positive")
    if data["smoke_log_excerpt_chars"] < 0:
        raise ValueError("smoke_log_excerpt_chars must be non-negative")
    if data["llm_seed_batch_size"] < 1:
        raise ValueError("llm_seed_batch_size must be positive")
    if data["llm_seed_attempts"] < 1:
        raise ValueError("llm_seed_attempts must be positive")
    if data["codex_exec_timeout_s"] <= 0:
        raise ValueError("codex_exec_timeout_s must be positive")
    if data["llm_seed_batch_size"] > data["max_seeds"]:
        data["llm_seed_batch_size"] = data["max_seeds"]
    if raw.get("seed_file"):
        data["seed_file"] = Path(raw["seed_file"])
    if raw.get("mock_fixture_dir"):
        data["mock_fixture_dir"] = Path(raw["mock_fixture_dir"])
    return replace(base, **data)


def _load_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    text = path.read_text()
    if not text.strip():
        return {}
    if path.suffix.lower() == ".json":
        loaded = json.loads(text)
    elif path.suffix.lower() in {".yaml", ".yml"}:
        loaded = _load_yaml(text)
    else:
        raise ValueError(f"Unsupported config extension: {path.suffix}")
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError("Workflow config must be a mapping")
    return loaded


def _load_yaml(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError:
        return _parse_simple_yaml(text)
    loaded = yaml.safe_load(text)
    return {} if loaded is None else loaded


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the small YAML shape documented in LANGGRAPH_WORKFLOW_PLAN.md.

    This fallback intentionally supports only top-level keys and one nested
    mapping level, which is enough for `remote:`.
    """

    root: dict[str, Any] = {}
    current_map: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if ":" not in stripped:
            raise ValueError(f"Unsupported YAML line: {raw_line}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if indent == 0:
            if value == "":
                current_map = {}
                root[key] = current_map
            else:
                current_map = None
                root[key] = _parse_scalar(value)
        elif indent == 2 and current_map is not None:
            current_map[key] = _parse_scalar(value)
        else:
            raise ValueError(f"Unsupported YAML indentation: {raw_line}")
    return root


def _parse_scalar(value: str) -> Any:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
