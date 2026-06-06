# LangGraph Runner

This document covers the LangGraph runner for the amplifier automation workflow.
The runner package is `langgraph_runner`, the repository default config is
`runner_config.json`, and canonical artifacts live under the configured
`artifact_root`.

## Commands

Initialize runner artifacts and canonical state:

```sh
python -m langgraph_runner --repo-root . --config runner_config.json init
```

Run one single-batch pass and stop:

```sh
python -m langgraph_runner --repo-root . --config runner_config.json run-one-batch
```

`run-one-batch` is equivalent to a counted run with count 1 for a single
candidate batch, but it enters the graph on the one-batch `stop` route.

Run the counted `run` route. The default count is 1:

```sh
python -m langgraph_runner --repo-root . --config runner_config.json run
```

Run more than one candidate batch/pass explicitly:

```sh
python -m langgraph_runner --repo-root . --config runner_config.json run --count 3
```

`--count` must be a positive integer. The count unit is a candidate batch/pass,
not an individual candidate. There is no unbounded `run`; the graph receives
`counted_run_total` and `counted_run_remaining` and stops when the count is
exhausted unless Top routes to `stop`, `human_interrupt`, or
`rerun_verification` first.

Pass a human response into a pending manual interrupt:

```sh
python -m langgraph_runner --repo-root . --config runner_config.json resume --human-response "continue"
```

`resume --human-response` is meaningful only when
`<artifact_root>/runs/manual/human_interrupt.json` exists. If there is no
pending interrupt, the graph records the condition and continues at most one
counted pass unless an explicit count already exists in persisted graph state.

## Source Of Truth

The runner treats these files and directories as canonical:

- `<artifact_root>/state.json`
- `<artifact_root>/ledger.jsonl`
- `<artifact_root>/candidates/<candidate_id>/`
- `<artifact_root>/workspaces/<candidate_id>/`
- `<artifact_root>/runs/<run_id>/`

`state.json` stores workflow summary state. `ledger.jsonl` records candidate
attempts as append-only JSON lines. Candidate artifact directories hold
proposal, patch, notes, review, verification, metrics, logs, verdicts, and any
partial failure evidence. Candidate workspace directories hold isolated design
snapshots used for patch application, verification, and promotion.

LangGraph checkpoint state never overrides canonical state or ledger files.
Checkpoints are resume aids only; if checkpoint data disagrees with
`state.json` or `ledger.jsonl`, the canonical files win.

Operators should not hand-edit `state.json` or `ledger.jsonl` except as part of
a full documented backup restoration.

## Current Graph

The graph implements nodes for context load, batch planning, subagent execution,
subagent output parsing and retry, prime request handling, candidate assembly,
deterministic review, serialized verification, evaluation, Top decision
defaulting, batch recording, and route selection.

Candidate and prime agent calls both use Codex CLI with the runtime prompt
passed on stdin:

```text
codex exec -C <context_path> -
```

For the `codex_exec` backend, the child process inherits the operator's existing
Codex CLI environment and authentication, but the runner overrides write-heavy
paths for each agent call. In the normal run artifact layout, `CODEX_HOME` is
set to `.codex_home/` under the run directory, outside the individual agent
context directory, and `TMP`/`TEMP` are set to `.codex_tmp/` under that same run
directory. The runner seeds `auth.json` and `config.toml` from the operator's
Codex home when present, so Codex state and temporary files stay inside the
configured artifact tree without placing auth files in the agent workdir. Model,
provider, profile, authentication, and project trust settings must still work
for non-interactive `codex exec` in the shell that launches the runner.

The subagent context package is the candidate-production handoff. It assigns a concrete
`candidate_id`, `phase`, and `primary_objective`, and the agent owns producing
the three candidate artifacts under `output/` in that context directory:

- `output/proposal.json`
- `output/patch.diff`
- `output/notes.md`

Candidate subagents produce proposal.json, patch.diff, and notes.md.
Prime agents in the current runner-level advisory path are helper calls
requested by a candidate subagent. Those advisory calls write `notes.md`
directly in their assigned prime output directory, which is copied under the
candidate's `primes/` artifacts for reference; they do not submit independent
candidate `proposal.json` or `patch.diff` artifacts.

This runner-level advisory path is narrower than the contract-level prime agent
role in `docs/top-coordinator-contract.md`. Any prime agent that submits a
candidate must follow the full candidate protocol: `proposal.json`,
`patch.diff`, and `notes.md`, with the same verification protocol, device
rules, and file-editing constraints as ordinary candidate agents.

Prose-only answers are invalid candidate output. `proposal.json` must echo the
assigned candidate ID, phase, agent role, and primary objective, and must include
the hypothesis, changed blocks, files touched, expected metric effects, risk,
and the same patch text written to `patch.diff`. `patch.diff` must be a unified
diff. `notes.md` must explain the hypothesis, changed blocks, expected metric
effects, risk, and reviewer/verifier notes. Natural-language claims are not
evidence; only verifier artifacts and recorded metrics can prove outcomes.

The runner owns parsing and retry handling, candidate assembly, deterministic
review, serialized verification, evaluation, and promotion.
Candidate assembly applies patches to isolated workspaces before deterministic_review.
deterministic_review validates the assembled artifacts and patched workspace before verification.
Candidate and prime agents must not mutate repository files
directly; only candidate patches are applied, and only to isolated candidate
workspaces.

Run-scoped agent calls, agent outputs, Top decisions, human interrupts, batch
errors, and production summaries are stored under:

```text
<artifact_root>/runs/<run_id>/
```

The current graph handles deterministic review and default Top decisions in
process. It does not launch separate reviewer or Top Coordinator `codex exec`
calls. The `run-one-batch` route executes at most one candidate batch. The
counted `run` route uses `counted_run_total` and `counted_run_remaining`; count
1 is the old one-pass behavior, and larger counts are explicit.

## DUT Pin Contract

The runner validates the DUT subckt name and pin order from
`amptest/config.json`. Candidate workspaces must preserve that DUT contract when
editing or replacing the DUT netlist.

The current repository DUT is:

```text
dummy_neural_amp GND VDD VIN VOUT VREF
```

Only the configured `dut_netlist` and `devices_csv` paths may be promoted back
into the repository.

## Artifact Layout

```text
<artifact_root>/
  state.json
  ledger.jsonl
  candidates/
    <candidate_id>/
      proposal.json
      notes.md
      patch.diff
      review.json
      verifier_stdout.log
      verifier_stderr.log
      verification.json
      ppa_metrics.json
      ppa_report.log
      spectre_ac.log
      spectre_tran.log
      verdict.json
  workspaces/
    <candidate_id>/
      dummy_neural_amp.scs
      devices.csv
      config.json
  runs/
    <run_id>/
      agent_calls/
      agent_outputs/
      top_decision.json
      human_interrupt.json
      batch_error.json
      production_run_start.json
      production_run_summary.json
```

`verifier_stdout.log` and `verifier_stderr.log` are runner-produced logs for the
configured verifier process. `ppa_report.log`, `spectre_ac.log`, and
`spectre_tran.log` are verifier outputs when the EDA backend produces them.
The repository default verifier command invokes
`python -m langgraph_runner.ssh_verifier`, which copies the candidate workspace
and professor-provided `amptest` runner files to
`me59@163.180.160.78:/home/me59/amplifier_runner/{candidate_id}`, executes
`./runtest.sh` over SSH, and copies the generated PPA outputs back into the
local candidate workspace `run/` directory before metrics are normalized. The
default command uses `%USERPROFILE%\.ssh\eda_langgraph` with `BatchMode=yes`;
password prompts are not supported for unattended runner execution. The runner
core still treats this as an external verifier command and does not hard-code
SSH, Cadence, Spectre, OCEAN, or MobaXterm behavior.

Candidate subagents must submit exactly three required files before runner
assembly:

```json
{
  "candidate_id": "p1-b001-c01-arch-20260605-120000",
  "phase": "phase1_performance",
  "agent": "architecture",
  "hypothesis": "Increase feedback resistance to reduce output error.",
  "primary_objective": "performance",
  "changed_blocks": ["feedback"],
  "files_touched": ["amptest/dummy_neural_amp.scs", "amptest/devices.csv"],
  "expected_effect": {
    "performance_nrmse_combined": "decrease",
    "area_total_p": "increase",
    "power_score_basis_w": "no_major_change"
  },
  "risk": "May increase area.",
  "patch": "same unified diff text as output/patch.diff"
}
```

Allowed expected_effect values for each metric: decrease, increase, no_major_change, unknown. Use one literal per metric.

`files_touched` and `output/patch.diff` may touch only the configured
`runner_config.dut_netlist` and `runner_config.devices_csv` path strings. For
the repository default config, those paths are `amptest/dummy_neural_amp.scs`
and `amptest/devices.csv`. Candidate patches must not modify `amptest` config,
wrappers, analyzer or scoring logic, generated testbenches, AC or transient
input conditions, supplies, references, inputs, loads, or metric calculations.
OPAMPs, OPAMP-equivalent macros, Verilog-A behavioral amplifiers, ideal gain
blocks, and controlled sources used as amplifiers are forbidden in every phase.
Candidate netlists must use the exact Spectre-valid SKY130 names in
`docs/top-coordinator-contract.md`. `sky130_fd_pr_main__...` names are stale
project-statement labels and must not appear in candidate patches.

The repository `amptest/config.json` uses `area.resistor_source = "netlist"`.
Therefore every `res_high_po_5p73` resistor instance must include explicit
positive `l=`, `w=`, and `m=` values on the DUT netlist line. Resistor rows in
`devices.csv` alone are not enough for area accounting. An abnormally small
`area_total_p` can indicate missing resistor geometry rather than a good area
result.

## Production Run Readiness

The full design is
`docs/superpowers/specs/2026-06-05-langgraph-runner-production-run-design.md`.

Production should not mutate the repository default `runner_config.json`.
Instead, use a copied production config with an isolated artifact root:

```sh
python -m langgraph_runner --repo-root . --config runner_config.json production-run --artifact-root automation_artifacts/prod --eda-signoff "<verifier-owner signoff>"
```

Or run an approved EDA smoke command:

```sh
python -m langgraph_runner --repo-root . --config runner_config.json production-run --artifact-root automation_artifacts/prod --eda-smoke-command "<approved-smoke-command>"
```

`production-run` creates:

- `automation_artifacts/operator_configs/prod-run-YYYYMMDD-HHMMSS.json`
- `candidate_generation_batch_size=1`
- `production_run_start.json` records `counted_run_total=1` and
  `counted_run_remaining=1`; `production_run_summary.json` records explicit
  `initial_counted_run_total`, `initial_counted_run_remaining`, and
  `final_counted_run_remaining`
- `verifier.min_interval_seconds >= 30`
- a timestamped backup under `<artifact_root>/backups/YYYYMMDD-HHMMSS/`
- `production_run_start.json` and `production_run_summary.json` under
  `<artifact_root>/runs/manual/`

Before invoking the graph, `production-run` performs these gates:

- records `git status --short` for operator review
- validates contract, `amptest/config.json`, DUT netlist pins, and `devices.csv`
- initializes the production artifact root after contract validation succeeds
- runs `python -m unittest discover -s tests/langgraph_runner -p "test_*.py" -v`
- runs `codex exec --help` in the inherited operator CLI environment
- requires either `--eda-smoke-command` success or `--eda-signoff`
- backs up DUT files, state, ledger, candidates, workspaces, runs, and the exact
  production config

After the gates pass, `production-run` invokes the graph with route
`next_batch`, `counted_run_total=1`, and `counted_run_remaining=1`, so the
canary is one candidate batch at most.

## One-Batch Canary

The first production run must use `candidate_generation_batch_size=1` through
`production-run`. After it completes, inspect:

```text
<artifact_root>/state.json
<artifact_root>/ledger.jsonl
<artifact_root>/runs/manual/
<artifact_root>/candidates/<candidate_id>/
<artifact_root>/workspaces/<candidate_id>/
```

Every candidate that reaches evaluation should have `verdict.json`. Candidates
that pass deterministic review should also have verifier logs and normalized
`verification.json`. Accepted candidates should promote only the DUT netlist and
`devices.csv`.

Increase batch size only after canary artifacts, ledger entries, state mutation,
and rollback expectations have been reviewed.

## Backup And Rollback

Before production graph execution, `production-run` copies these files and
directories when present:

- `amptest/dummy_neural_amp.scs`
- `amptest/devices.csv`
- `<artifact_root>/state.json`
- `<artifact_root>/ledger.jsonl`
- `<artifact_root>/candidates/`
- `<artifact_root>/workspaces/`
- `<artifact_root>/runs/`
- the exact production config

If no candidate was accepted, repository rollback is normally unnecessary.
Check:

```sh
git diff -- amptest/dummy_neural_amp.scs amptest/devices.csv
```

If an accepted promotion must be reverted, stop all runner invocations, restore
the DUT files from the timestamped backup, preserve the candidate artifacts and
ledger entry, and write an operator incident note under:

```text
<artifact_root>/runs/manual/operator_rollback.json
```

Restore `state.json` from backup only when the accepted promotion is fully
withdrawn from canonical workflow state.

## Failure Classes

Use these classes when triaging a production run:

- Candidate rejection: forbidden file touch, invalid DUT pins, OPAMP or
  behavioral shortcut, invalid `devices.csv`, or failed acceptance gates.
  Preserve artifacts and do not retry unless a new candidate patch is generated.
- Candidate error: missing or malformed proposal artifacts, patch application
  failure, invalid verification JSON, or repeated malformed agent output.
  Preserve stdout/stderr and partial artifacts; escalate to the runner owner if
  the same error repeats across candidates.
- Infrastructure verifier failure: EDA executable, license, timeout, missing
  evidence, or stale verifier outputs. Stop additional production batches,
  preserve verifier logs, and escalate to the Verifier Agent or EDA owner. Do
  not change candidate design to fix infrastructure failures. Treat
  `performance_nrmse_combined: null`, missing AC/transient CSVs, or missing
  Spectre logs as verifier infrastructure failure evidence. The runner must not
  record those candidates as `passed`; operators should inspect verifier logs
  and the candidate workspace `run/` directory before generating more
  candidates.
- Agent execution failure: `codex exec` missing, inherited Codex CLI
  authentication/configuration inaccessible, timed out, returned nonzero, or
  wrote malformed artifacts twice. Preserve run-scoped stdout/stderr, escalate
  CLI/auth/timeouts to the runner/operator owner, and repeated malformed output
  to the prompt/agent owner.
- Runner programming failure: Python exception, failed state or ledger write,
  failed promotion, or repository mutation without an accepted candidate. Stop
  production runs, preserve the whole artifact root, restore DUT files from
  backup if needed, and open a runner defect with paths to `batch_error.json`,
  `state.json`, `ledger.jsonl`, and relevant candidate directories.
- Human escalation: Top decision requests interrupt, critical anomaly, multiple
  accepted candidates needing review, rollback needs state restore, or contract
  hash changes between backup and run start.
