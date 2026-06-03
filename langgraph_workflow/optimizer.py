from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from typing import Any

from .state import CircuitSeed


@dataclass
class FallbackStudy:
    study_name: str
    next_number: int = 0
    trials: list[dict[str, Any]] = field(default_factory=list)
    best_value: float | None = None
    best_trial_number: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "fallback",
            "study_name": self.study_name,
            "next_number": self.next_number,
            "trials": self.trials,
            "best_value": self.best_value,
            "best_trial_number": self.best_trial_number,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FallbackStudy":
        return cls(
            study_name=str(data["study_name"]),
            next_number=int(data.get("next_number", 0)),
            trials=list(data.get("trials", [])),
            best_value=data.get("best_value"),
            best_trial_number=data.get("best_trial_number"),
        )


class OptunaStudyAdapter:
    def __init__(self, study: Any):
        self.study = study
        self.study_name = study.study_name


def create_study(
    seed: CircuitSeed | dict[str, Any],
    *,
    storage: str | None,
    study_name: str | None = None,
    state: dict[str, Any] | None = None,
) -> FallbackStudy | OptunaStudyAdapter:
    if state and state.get("kind") == "fallback":
        return FallbackStudy.from_dict(state)
    name = study_name or f"bjt_amp_{seed['seed_id']}"
    if storage:
        try:
            import optuna  # type: ignore
        except ImportError:
            return FallbackStudy(study_name=name)
        study = optuna.create_study(
            study_name=name,
            storage=storage,
            direction="minimize",
            load_if_exists=True,
        )
        if len(study.trials) == 0:
            study.enqueue_trial(dict(seed.get("initial_params", {})))
        study.set_user_attr("seed_id", seed["seed_id"])
        study.set_user_attr("topology_name", seed.get("topology_name", ""))
        return OptunaStudyAdapter(study)
    return FallbackStudy(study_name=name)


def sample_trial_params(study: FallbackStudy | OptunaStudyAdapter, seed: CircuitSeed | dict[str, Any]) -> tuple[int, dict[str, Any]]:
    if isinstance(study, OptunaStudyAdapter):
        trial = study.study.ask()
        params = _suggest_optuna_params(trial, seed.get("param_ranges", {}))
        return int(trial.number), params

    trial_number = study.next_number
    if trial_number == 0:
        params = dict(seed.get("initial_params", {}))
    else:
        params = _fallback_params(seed, trial_number)
    study.next_number += 1
    study.trials.append({"number": trial_number, "params": params, "value": None, "state": "running"})
    return trial_number, params


def tell_trial(study: FallbackStudy | OptunaStudyAdapter, trial_number: int, value: float | None, *, failed: bool = False) -> None:
    if isinstance(study, OptunaStudyAdapter):
        import optuna  # type: ignore

        state = optuna.trial.TrialState.FAIL if failed or value is None else optuna.trial.TrialState.COMPLETE
        study.study.tell(trial_number, values=None if value is None else float(value), state=state)
        return

    for trial in study.trials:
        if trial["number"] == trial_number:
            trial["value"] = value
            trial["state"] = "failed" if failed else "complete"
            break
    if not failed and value is not None:
        if study.best_value is None or float(value) < study.best_value:
            study.best_value = float(value)
            study.best_trial_number = trial_number


def serialize_study(study: FallbackStudy | OptunaStudyAdapter) -> dict[str, Any] | None:
    if isinstance(study, FallbackStudy):
        return study.to_dict()
    return None


def _suggest_optuna_params(trial: Any, param_ranges: dict[str, dict[str, Any]]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for name, spec in param_ranges.items():
        range_type = spec.get("type", "float")
        if range_type == "float":
            params[name] = trial.suggest_float(name, float(spec["low"]), float(spec["high"]), log=False)
        elif range_type == "log_float":
            params[name] = trial.suggest_float(name, float(spec["low"]), float(spec["high"]), log=True)
        elif range_type == "int":
            params[name] = trial.suggest_int(name, int(spec["low"]), int(spec["high"]))
        elif range_type == "categorical":
            params[name] = trial.suggest_categorical(name, list(spec["choices"]))
        else:
            raise ValueError(f"unsupported parameter range type: {range_type}")
    return params


def _fallback_params(seed: CircuitSeed | dict[str, Any], trial_number: int) -> dict[str, Any]:
    param_ranges = seed.get("param_ranges", {})
    rng = random.Random(_seed_int(seed["seed_id"], trial_number))
    params: dict[str, Any] = {}
    for name, spec in param_ranges.items():
        range_type = spec.get("type", "float")
        if range_type == "float":
            params[name] = rng.uniform(float(spec["low"]), float(spec["high"]))
        elif range_type == "log_float":
            low = math.log10(float(spec["low"]))
            high = math.log10(float(spec["high"]))
            params[name] = 10 ** rng.uniform(low, high)
        elif range_type == "int":
            params[name] = rng.randint(int(spec["low"]), int(spec["high"]))
        elif range_type == "categorical":
            choices = list(spec["choices"])
            params[name] = choices[rng.randrange(len(choices))]
        else:
            raise ValueError(f"unsupported parameter range type: {range_type}")
    return params


def _seed_int(seed_id: str, trial_number: int) -> int:
    payload = json.dumps({"seed_id": seed_id, "trial_number": trial_number}, sort_keys=True)
    return int(hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16], 16)
