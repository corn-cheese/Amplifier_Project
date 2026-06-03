import unittest
import sys
import types
from unittest import mock

from langgraph_workflow.optimizer import create_study, sample_trial_params, tell_trial
from tests.test_validation import VALID_SEED


class OptimizerTests(unittest.TestCase):
    def test_fallback_sampler_evaluates_initial_params_first(self):
        study = create_study(VALID_SEED, storage=None)

        trial_number, params = sample_trial_params(study, VALID_SEED)

        self.assertEqual(0, trial_number)
        self.assertEqual(VALID_SEED["initial_params"], params)

    def test_fallback_sampler_supports_plan_range_types_after_initial_trial(self):
        seed = {
            **VALID_SEED,
            "param_ranges": {
                "float_param": {"type": "float", "low": 1.0, "high": 3.0},
                "log_param": {"type": "log_float", "low": 1e-3, "high": 1e3},
                "int_param": {"type": "int", "low": 1, "high": 5},
                "cat_param": {"type": "categorical", "choices": ["npn", "pnp"]},
            },
            "initial_params": {
                "float_param": 1.5,
                "log_param": 1.0,
                "int_param": 2,
                "cat_param": "npn",
            },
        }
        study = create_study(seed, storage=None)
        sample_trial_params(study, seed)

        trial_number, params = sample_trial_params(study, seed)

        self.assertEqual(1, trial_number)
        self.assertGreaterEqual(params["float_param"], 1.0)
        self.assertLessEqual(params["float_param"], 3.0)
        self.assertGreaterEqual(params["log_param"], 1e-3)
        self.assertLessEqual(params["log_param"], 1e3)
        self.assertIsInstance(params["int_param"], int)
        self.assertIn(params["cat_param"], ["npn", "pnp"])

    def test_fallback_tell_tracks_best_objective(self):
        study = create_study(VALID_SEED, storage=None)
        number_0, _ = sample_trial_params(study, VALID_SEED)
        tell_trial(study, number_0, 2.0)
        number_1, _ = sample_trial_params(study, VALID_SEED)
        tell_trial(study, number_1, 1.0)

        self.assertEqual(1.0, study.best_value)
        self.assertEqual(number_1, study.best_trial_number)

    def test_optuna_installed_path_uses_enqueue_ask_and_tell(self):
        fake_study = FakeOptunaStudy()
        fake_optuna = types.SimpleNamespace(
            create_study=lambda **kwargs: fake_study.created_with(kwargs),
            trial=types.SimpleNamespace(TrialState=types.SimpleNamespace(COMPLETE="complete", FAIL="fail")),
        )

        with mock.patch.dict(sys.modules, {"optuna": fake_optuna}):
            study = create_study(VALID_SEED, storage="sqlite:///runs/optuna.sqlite3")
            number, params = sample_trial_params(study, VALID_SEED)
            tell_trial(study, number, 0.5)
            _, second_params = sample_trial_params(study, VALID_SEED)
            tell_trial(study, 1, None, failed=True)

        self.assertEqual("sqlite:///runs/optuna.sqlite3", fake_study.kwargs["storage"])
        self.assertEqual(True, fake_study.kwargs["load_if_exists"])
        self.assertEqual([VALID_SEED["initial_params"]], fake_study.enqueued)
        self.assertEqual(0, number)
        self.assertEqual(VALID_SEED["initial_params"], params)
        self.assertEqual(VALID_SEED["initial_params"], second_params)
        self.assertEqual([(0, 0.5, "complete"), (1, None, "fail")], fake_study.told)


class FakeOptunaTrial:
    def __init__(self, number, fixed_params=None):
        self.number = number
        self.fixed_params = fixed_params or {}

    def suggest_float(self, name, low, high, log=False):
        if name in self.fixed_params:
            return self.fixed_params[name]
        return low if log else (low + high) / 2.0

    def suggest_int(self, name, low, high):
        if name in self.fixed_params:
            return self.fixed_params[name]
        return low

    def suggest_categorical(self, name, choices):
        if name in self.fixed_params:
            return self.fixed_params[name]
        return choices[0]


class FakeOptunaStudy:
    def __init__(self):
        self.study_name = "fake"
        self.trials = []
        self.enqueued = []
        self.attrs = {}
        self.told = []
        self.kwargs = {}

    def created_with(self, kwargs):
        self.kwargs = kwargs
        self.study_name = kwargs["study_name"]
        return self

    def enqueue_trial(self, params):
        self.enqueued.append(params)

    def set_user_attr(self, key, value):
        self.attrs[key] = value

    def ask(self):
        if self.enqueued:
            params = self.enqueued[0]
        else:
            params = {}
        trial = FakeOptunaTrial(len(self.trials), params)
        self.trials.append(trial)
        return trial

    def tell(self, trial_number, values=None, state=None):
        self.told.append((trial_number, values, state))


if __name__ == "__main__":
    unittest.main()
