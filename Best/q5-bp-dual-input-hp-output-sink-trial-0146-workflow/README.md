# Global Accepted Best Workflow Clone

This folder clones the workflow and artifacts for the accepted best run:

- sweep: `q5-bandpass-dual-input-highpass-output-sink/q5-dual-input-hp-cp1hi3600-ce2hi90m-rq4fbhi32k-2000-rerun1`
- trial: `trial_0146`
- candidate: `q5-bp-dual-input-hp-output-sink-trial-0146`
- performance: `0.06252490847952057`
- status: `passed`
- rejected: `false`

## Layout

- `best_run/trial_0146/`: full copied trial artifact directory, including logs, metrics, summary, verifier output, and workspace.
- `baseline/`: baseline source metadata plus the baseline workspace used by this sweep.
- `topology/`: final accepted topology files copied from the best trial.
- `workflow/`: sweep generation script, runner config, original batch file, and a run-specific reproduce command.

## Reproduce Sweep Command

From repo root:

```bat
python "Best\q5-bp-dual-input-hp-output-sink-trial-0146-workflow\workflow\optuna_q5_bandpass_sweep.py" --repo-root . --config "Best\q5-bp-dual-input-hp-output-sink-trial-0146-workflow\workflow\runner_config.json" --family dual-input-highpass-output-sink --baseline-workspace "Best\q5-bp-dual-input-hp-output-sink-trial-0146-workflow\best_run\trial_0146\workspace" --trials 2000 --timestamp q5-dual-input-hp-diode-pr-trial0146-2000
```

This modified workflow sweeps only the trial_0146 input section. `RBIN1` and `RBIN2` are removed, and `B1`/`B2` return to `VREF` through the selected diode pseudo-resistor topology (`b2b-cc`, `b2b-ca`, `dual-b2b`, `series2-cc`, or `reverse-antiparallel`).

The exact accepted trial parameters are in `topology/params.json` and `best_run/trial_0146/trial_summary.json`.
