# BJT Neural Amplifier LangGraph Workflow Plan

## Summary

This document describes a Python LangGraph workflow for the single-ended neural
signal amplifier project. The workflow generates non-OPAMP amplifier seed
topologies using project-approved devices, uses Optuna to tune numeric
parameters, evaluates every candidate with the professor-provided `amptest`
program, and stores reproducible artifacts for each trial.

LLM calls are used only to obtain candidate topology seeds and parameter ranges.
The LLM must not tune trial-by-trial values after the seed is accepted. Optuna is
the only optimizer. `amptest` is the source of truth for AC response, transient
response, area, power, and PPA metrics.

## Project Constraints

- DUT interface must expose exactly five pins: `VIN VREF VDD GND VOUT`.
- Testbench conditions are inherited from `amptest/config.json`:
  - `VDD = 5.0 V`
  - `VREF = 0.5 * VDD = 2.5 V`
  - `VIN` DC common mode is `2.5 V`
  - `VIN` AC amplitude is `1 mV`
  - `CLOAD = 10 pF`
  - AC sweep is `0.1 Hz` to `10 MHz`
  - default transient input is `1 kHz`, `1 mV` sine
- Target response:
  - midband gain near `40 dB` or `100 V/V`
  - target bandwidth `10 Hz` to `20 kHz`
  - roughly `80 dB/decade` rolloff on both sides
- v1 optimizes amplifier PPA only. Bonus AC-DC converter and clamp blocks are
  out of scope for this workflow.
- The workflow must never reuse the shared `amptest/run` directory because
  existing outputs may be stale or mixed with prior candidates.

## Device Policy

The workflow runs a static project-device validation gate before any simulation.
OPAMP is forbidden, but the other devices listed in
`neural_signal_amplifier_project.md` may be used.

Allowed active devices:

- `sky130_fd_pr__npn_05v5`
- `sky130_fd_pr__pnp_05v5`

Allowed passive/project devices:

- `sky130_fd_pr__res_high_po_5p73`
- `sky130_fd_pr__cap_vpp_11p5x11p7_m1m4_noshield`
- `sky130_fd_pr__diode_pd2nw_05v5`

Forbidden constructs:

- `opamp`
- MOS devices
- Verilog-A behavioral sources
- `ahdl_include`
- `bsource`
- any `laplace`, controlled-source, or black-box behavioral filter shortcut
- any cell not declared in the seed device manifest

The validator must reject a seed if the netlist text, seed metadata, device
manifest, or `devices.csv` implies forbidden devices or if any active instance
is missing from `devices.csv`.

## Architecture

Use Python LangGraph `StateGraph` as the top-level orchestrator. Compile the
graph with a durable checkpointer so long-running EDA simulations can be resumed
after failures, SSH disconnects, or human review interrupts.

Recommended production checkpointer:

- `SqliteSaver` for local development and single-user runs.
- Postgres-backed checkpointer if the workflow is later shared by multiple
  workers.

Default execution backend:

- `EDA SSH`
- Local Windows prepares files.
- The workflow uploads one isolated candidate directory to the EDA/Linux server.
- The server runs `python3 ppa_wrapper.py all --config config.json`.
- The workflow downloads `ppa_metrics.json`, `ppa_report.log`, Spectre logs, CSV
  outputs, and PNG plots.

Local/WSL execution can be added later through the same backend interface, but
`EDA SSH` is the v1 default because `spectre` and `ocean` are not expected to be
available on the Windows PATH.

## State Interfaces

### CircuitSeed

```python
class CircuitSeed(TypedDict):
    seed_id: str
    topology_name: str
    subckt_name: str
    pins: list[str]
    netlist_template: str
    param_ranges: dict[str, dict[str, float | int | str]]
    initial_params: dict[str, float | int | str]
    device_manifest: list[dict[str, str | int | float | bool]]
```

Required seed rules:

- `pins` must equal `["VIN", "VREF", "VDD", "GND", "VOUT"]`.
- `netlist_template` must render to a Spectre subckt named by `subckt_name`.
- Every tunable placeholder in the template must be declared in
  `param_ranges`.
- `initial_params` must contain valid values inside the declared ranges.
- `device_manifest` must include every active/passive instance used for PPA.

### TrialResult

```python
class TrialResult(TypedDict):
    trial_id: str
    seed_id: str
    params: dict[str, float | int | str]
    status: Literal["queued", "rendered", "simulated", "failed", "scored"]
    metrics: dict[str, Any] | None
    objective: float | None
    artifact_dir: str
    error: str | None
```

### WorkflowState

```python
class WorkflowState(TypedDict):
    spec: dict[str, Any]
    seeds: list[CircuitSeed]
    active_seed: CircuitSeed | None
    study_name: str | None
    trial_results: list[TrialResult]
    best_result: TrialResult | None
    remote_run: dict[str, Any]
    failure_reasons: list[str]
```

Persist large artifacts by path, not by storing file contents in state.

## LangGraph Nodes

### `load_project_spec`

Responsibilities:

- Read `neural_signal_amplifier_project.md`.
- Read `amptest/config.json` and `amptest/README.md`.
- Construct normalized constraints for pins, supply, target gain, bandwidth,
  allowed devices, expected metrics, and artifact layout.

Output state updates:

- `spec`
- empty `failure_reasons`

### `llm_seed_topologies`

Responsibilities:

- Ask the LLM for strict JSON only.
- Request non-OPAMP topologies using only project-approved devices and numeric
  parameter ranges.
- Include the project constraints directly in the prompt.
- Require each seed to explain the intended gain/filter/bias mechanism in a
  short `rationale` field, but do not use rationale in simulation.

The LLM output must be parsed as JSON and converted into `CircuitSeed` objects.
Invalid JSON routes to reseeding or human review.

### `validate_seed`

Responsibilities:

- Validate exact pin order.
- Parse the netlist template for forbidden tokens.
- Verify every placeholder has a parameter range and initial value.
- Verify all `initial_params` are inside range.
- Verify device manifest entries map to project-approved cells.
- Verify `devices.csv` can be rendered from the manifest.
- Reject OPAMP, MOS, Verilog-A, and behavioral shortcuts before simulation.

Routing:

- valid seed -> `init_optuna_study`
- invalid seed with recoverable errors -> `llm_seed_topologies`
- repeated invalid seeds -> human interrupt for seed review

### `init_optuna_study`

Responsibilities:

- Create or resume one Optuna study per accepted seed.
- Use deterministic study names such as
  `bjt_amp_<seed_id>_<YYYYMMDD_HHMMSS>`.
- Enqueue `initial_params` so the first evaluated trial is the LLM seed.
- Store seed metadata as study user attributes.

Use Optuna ask/tell style because LangGraph owns trial orchestration and needs
to route each trial through rendering, SSH execution, and scoring.

### `sample_trial`

Responsibilities:

- Ask Optuna for the next trial.
- Suggest every parameter from `active_seed.param_ranges`.
- Support range types:
  - `float`: `trial.suggest_float(name, low, high, log=False)`
  - `log_float`: `trial.suggest_float(name, low, high, log=True)`
  - `int`: `trial.suggest_int(name, low, high)`
  - `categorical`: `trial.suggest_categorical(name, choices)`
- Create a new `TrialResult` with status `queued`.

### `render_candidate`

Responsibilities:

- Render `candidate.scs` from the seed template and sampled parameters.
- Render `devices.csv` from the seed manifest and sampled multiplicities.
- Render a candidate-specific `config.json`.
- Set `work_dir` to a unique per-trial run directory.
- Keep `dut_subckt` and `dut_pins_order` aligned with the rendered subckt.

Artifact layout:

```text
runs/
  <seed_id>/
    <trial_id>/
      candidate.scs
      devices.csv
      config.json
      remote_stdout.log
      remote_stderr.log
      ppa_metrics.json
      ppa_report.log
      ppa_summary.log
      spectre_ac.log
      spectre_tran.log
      ac.csv
      tran.csv
      ac_response.png
      transient_response.png
```

### `run_amptest_ssh`

Responsibilities:

- Upload the per-trial directory and a clean copy of `amptest` to the EDA
  server or to a configured remote workspace.
- Run:

```sh
python3 ppa_wrapper.py all --config ./config.json
```

- Capture stdout/stderr and remote exit code.
- Download expected outputs if the command exits successfully.
- Download logs even on failure when possible.
- Mark the trial failed if SSH, Spectre, OCEAN, export, or analysis fails.

The node must be idempotent. If a trial artifact directory already contains a
complete `ppa_metrics.json` and matching parameter metadata, it may skip rerun
and proceed to scoring.

### `score_trial`

Responsibilities:

- Load `ppa_metrics.json`.
- Extract:
  - `performance_nrmse_combined`
  - `area_power.area_total_p`
  - `area_power.power_score_basis_w`
  - `ac.midband_gain_db`
  - `ac.lower_3db_hz`
  - `ac.upper_3db_hz`
  - `tran.vout_ac_peak_to_peak_v`
  - `tran.vout_mean_v`
  - `tran.thd_db`
- Compute objective.
- Tell Optuna the objective value or mark the trial failed/pruned.
- Update `best_result` if the trial improves the current best objective.

Objective:

```python
objective = (
    performance_nrmse_combined
    + 0.15 * math.log10(1.0 + area_total_p / 100.0)
    + 0.15 * math.log10(1.0 + power_score_basis_w / 1e-3)
    + hard_penalties
)
```

Hard penalties:

- `+1000` for simulation failure or missing `ppa_metrics.json`
- `+1000` for non-BJT or forbidden device discovered after render
- `+100` for invalid pin order
- `+25` for missing AC or transient metrics
- `+10` if midband gain is outside `40 dB +/- 6 dB`
- `+10` if lower 3 dB point is above `20 Hz`
- `+10` if upper 3 dB point is below `10 kHz`
- `+10` if transient output clips or mean output is outside `2.5 V +/- 0.5 V`

### `route_next`

Responsibilities:

- Continue sampling while trial budget and wall-clock budget remain.
- Reseed if all trials for a topology fail validation or simulation.
- Stop when:
  - max trials reached
  - timeout reached
  - objective target reached
  - user interrupt requests stop

### `final_report`

Responsibilities:

- Produce a Markdown summary in the run root.
- Include:
  - best seed and trial ID
  - best parameters
  - objective and PPA metrics
  - artifact paths
  - failed seed/trial reasons
  - recommended next manual review points
- Copy or reference best candidate files for final Cadence/schematic work.

## Routing Sketch

```text
START
  -> load_project_spec
  -> llm_seed_topologies
  -> validate_seed
  -> init_optuna_study
  -> sample_trial
  -> render_candidate
  -> run_amptest_ssh
  -> score_trial
  -> route_next

route_next:
  continue trial -> sample_trial
  reseed -> llm_seed_topologies
  stop -> final_report -> END
```

## Error Handling

- LLM JSON parse failure: retry seed generation with the parser error included.
- Validation failure: do not simulate; either reseed or interrupt for review.
- SSH failure: retry the remote execution node with exponential backoff.
- Spectre/OCEAN failure: store logs, apply hard penalty, tell Optuna failure.
- Missing metrics: apply hard penalty and keep artifacts for inspection.
- Repeated failure for one seed: stop that seed and request a new topology.

## Human Review Points

Use LangGraph interrupts only when a decision cannot be made safely:

- repeated invalid LLM seeds
- all BJT seeds fail simulation
- best candidate trades much lower area/power for visibly poor waveform shape
- EDA SSH configuration is missing or authentication fails

Interrupt payloads must be JSON-serializable and include artifact paths rather
than large file contents.

## Test Plan

### Unit Tests

- Validator rejects:
  - `opamp`
  - MOS model names
  - `ahdl_include`
  - `bsource`
  - wrong pin order
  - missing `devices.csv` rows for active devices
- Renderer produces:
  - `candidate.scs`
  - `devices.csv`
  - `config.json`
  - isolated `work_dir`
- Objective calculation matches known values from a fixture
  `ppa_metrics.json`.

### Integration Tests

- Mock SSH runner returns fixture `ppa_metrics.json` and verifies the LangGraph
  loop can run without Cadence.
- Mock simulation failure verifies hard penalties and Optuna failure handling.
- Resume test verifies checkpointed state can continue after `run_amptest_ssh`.

### EDA Smoke Test

Run one valid BJT seed on the EDA server and confirm the artifact directory
contains:

- `ppa_metrics.json`
- `ppa_report.log`
- `ppa_summary.log`
- `spectre_ac.log`
- `spectre_tran.log`
- `ac_response.png`
- `transient_response.png`

The smoke test passes only if `ppa_metrics.json` includes non-null
`performance_nrmse_combined`, `area_power.area_total_p`, and
`area_power.power_score_basis_w`.

## Implementation Defaults

- Trial budget: `5` trials per accepted seed for initial testing.
- Seed budget: `3` accepted non-OPAMP seeds.
- Parallelism: `1`; Cadence/Spectre trials must never run in parallel.
- Minimum interval: `60` seconds between trial starts.
- Remote timeout: `20` minutes (`1200` seconds) per trial.
- Daily maximum: `20` Cadence/Spectre trial starts.
- Study direction: minimize.
- Checkpoint database: `runs/langgraph_checkpoints.sqlite`.
- Optuna storage: `sqlite:///runs/optuna_studies.sqlite3`.
- Run root: `runs/`.
- Default backend: `eda_ssh`.

## Configuration Shape

```yaml
backend: eda_ssh
run_root: runs
amptest_local_dir: amptest
checkpoint_db: runs/langgraph_checkpoints.sqlite
optuna_storage: sqlite:///runs/optuna_studies.sqlite3
max_seeds: 3
max_trials: 5
max_trials_per_seed: 5
parallelism: 1
min_interval_s: 60
timeout_s: 1200
daily_max_trials: 20
objective_target: 0.25
remote:
  host: eda.example.edu
  user: your_user
  base_dir: /home/your_user/bjt_amp_langgraph
  python: python3
  command: python3 ppa_wrapper.py all --config ./config.json
```

## Notes For Implementation

- Do not mutate `amptest/config.json` or use `amptest/run` directly.
- Copy only the clean evaluator files needed for each run.
- Record the exact rendered netlist and parameter JSON for each trial.
- Treat `ppa_metrics.json` as immutable once scored.
- Keep paths relative inside reports when possible so the run directory can be
  moved or archived.
- Prefer deterministic IDs:
  - `seed_id = seed_<index>_<short_hash>`
  - `trial_id = trial_<optuna_trial_number>`
- Keep LLM prompts and raw LLM responses in the run root for auditability.
