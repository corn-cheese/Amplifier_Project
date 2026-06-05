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

Run one bounded batch and stop:

```sh
python -m langgraph_runner --repo-root . --config runner_config.json run-one-batch
```

Run the bounded `run` route:

```sh
python -m langgraph_runner --repo-root . --config runner_config.json run
```

The CLI sets `stop_after_current_pass=True` for `run`, so this command completes
the current pass instead of looping indefinitely.

Pass a human response into a pending manual interrupt:

```sh
python -m langgraph_runner --repo-root . --config runner_config.json resume --human-response "continue"
```

`resume --human-response` is meaningful only when
`<artifact_root>/runs/manual/human_interrupt.json` exists. If there is no
pending interrupt, the graph records the condition and continues one bounded
pass.

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

Candidate and prime agents run through:

```text
codex exec -C <context_path> <runtime_prompt>
```

Run-scoped agent calls, agent outputs, Top decisions, human interrupts, batch
errors, and production summaries are stored under:

```text
<artifact_root>/runs/<run_id>/
```

The current graph handles deterministic review and default Top decisions in
process. It does not launch separate reviewer or Top Coordinator `codex exec`
calls.

## DUT Pin Contract

The runner validates the DUT subckt name and pin order from
`amptest/config.json`. Candidate workspaces must preserve that DUT contract when
editing or replacing the DUT netlist.

The current repository DUT is:

```text
dummy_neural_amp GND VDD VIN VOUT VREF
```

Only `amptest/dummy_neural_amp.scs` and `amptest/devices.csv` may be promoted
back into the repository.

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
- `verifier.min_interval_seconds >= 30`
- a timestamped backup under `<artifact_root>/backups/YYYYMMDD-HHMMSS/`
- `production_run_start.json` and `production_run_summary.json` under
  `<artifact_root>/runs/manual/`

Before invoking the graph, `production-run` performs these gates:

- records `git status --short` for operator review
- validates contract, `amptest/config.json`, DUT netlist pins, and `devices.csv`
- initializes the production artifact root after contract validation succeeds
- runs `python -m unittest discover -s tests/langgraph_runner -p "test_*.py" -v`
- runs `codex exec --help`
- requires either `--eda-smoke-command` success or `--eda-signoff`
- backs up DUT files, state, ledger, candidates, workspaces, runs, and the exact
  production config

After the gates pass, `production-run` invokes the graph with route `stop`, so
the canary is one candidate batch at most.

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
  not change candidate design to fix infrastructure failures.
- Agent execution failure: `codex exec` missing, unauthenticated, timed out, or
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
