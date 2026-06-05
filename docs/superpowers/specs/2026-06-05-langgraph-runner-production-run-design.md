# LangGraph Runner Production Run Readiness Design

Date: 2026-06-05

## Purpose

This document defines the production readiness and runbook design for operating
the repository LangGraph runner against the amplifier automation workflow.

The focus is operational readiness, not new feature implementation. A
production run means a bounded, auditable runner invocation that may call
`codex exec` for candidate and prime agents and may call the configured
`amptest` verifier for EDA-backed metrics, while preserving the Top Coordinator
contract,
canonical artifacts, rollback rules, and human control points.

The production operator must be able to answer four questions before every run:

- What code, config, state, and external tools are being used?
- What will be mutated, and how is it backed up?
- How will failures be classified and escalated?
- How can the workflow be stopped, resumed, or rolled back without trusting
  checkpoint state over canonical artifacts?

## Current Verified Baseline

Repository baseline:

- Runner package: `langgraph_runner`.
- CLI entry point: `python -m langgraph_runner`.
- Default config: `runner_config.json`.
- Contract source: `docs/top-coordinator-contract.md`.
- Artifact root: `automation_artifacts`.
- DUT contract source: `amptest/config.json`.
- Current DUT pin contract documented by the runner:
  `dummy_neural_amp GND VDD VIN VOUT VREF`.

Implemented runner behavior in the current package:

- `init` initializes `automation_artifacts/`, creates `ledger.jsonl` when
  missing, and creates `state.json` when missing.
- `run-one-batch` invokes the graph once with route `stop`.
- `run` invokes the graph with route `next_batch` and
  `stop_after_current_pass=True`, so the CLI remains bounded.
- `resume --human-response "<text>"` injects `human_response` into graph state.
- The graph includes implemented nodes for context load, batch planning,
  subagent execution, subagent output parsing and retry, prime request handling,
  candidate assembly, deterministic review, serialized verification,
  evaluation, top decision defaulting, batch recording, and route selection.
- Canonical state is `automation_artifacts/state.json`; canonical ledger is
  `automation_artifacts/ledger.jsonl`.
- Candidate artifacts are stored under
  `automation_artifacts/candidates/<candidate_id>/`.
- Candidate workspaces are stored under
  `automation_artifacts/workspaces/<candidate_id>/`.
- Run-scoped agent calls, outputs, top decisions, and interrupt files are stored
  under `automation_artifacts/runs/<run_id>/`.

Current production limitation:

- The repository code can orchestrate real `codex exec` and verifier commands,
  but production readiness depends on external installation, authentication,
  EDA environment access, and run-specific config validation.
- Current `codex exec` process calls are used for candidate subagents and prime
  agents. Deterministic review and Top anomaly handling are implemented in
  process; the current graph does not launch separate reviewer or Top
  Coordinator `codex exec` calls.
- The Top anomaly node persists a provided `TopDecision` or writes a
  deterministic default decision. Production human-interrupt behavior can be
  exercised through pending interrupt/resume paths, but a live Top Coordinator
  agent handoff is outside the initial bounded canary unless implemented
  separately.
- `runner_config.json` is suitable as the repository default. Production should
  use an explicit copied config and artifact root rather than mutating the
  default artifact tree directly.

## Production Goals

- Run one bounded batch at a time with complete preflight checks.
- Keep candidate generation, review, verification, evaluation, promotion, and
  ledger/state recording auditable from files.
- Preserve the Top Coordinator contract invariants:
  only the DUT netlist and `devices.csv` may change, evaluator logic may not be
  changed, and OPAMP or behavioral amplifier shortcuts remain forbidden.
- Ensure every accepted, rejected, errored, or interrupted attempt has a durable
  artifact trail.
- Serialize EDA verification and preserve the configured minimum interval.
- Treat `automation_artifacts/state.json` and `automation_artifacts/ledger.jsonl`
  as authoritative over LangGraph checkpoint state.
- Allow a human operator to stop, inspect, resume, or roll back without
  reconstructing state from stdout.

## Production Non-Goals

- Do not implement continuous unattended looping for initial production runs.
- Do not change `amptest` scoring, analyzer, wrapper, config, or generated
  testbench behavior.
- Do not make LangGraph checkpoints canonical.
- Do not let agents write directly to repository DUT files.
- Do not accept natural-language claims as verification evidence.
- Do not use rejected or errored candidates as the base for future candidate
  work.
- Do not collapse infrastructure errors and candidate design failures into the
  same escalation path.

## External Prerequisites

### `codex exec`

Production candidate generation requires:

- `codex` is installed and available on `PATH` for the same shell that launches
  `python -m langgraph_runner`.
- `codex exec` can run non-interactively from a context directory.
- Authentication and model/provider settings are already configured outside the
  runner.
- The CLI policy or sandbox used for production prevents unintended writes
  outside the assigned output directory. The runner prompts agents to write only
  there, but it does not itself sandbox the external `codex` process.
- Timeout behavior is acceptable for the active external role timeouts:
  `subagent=1200` and `prime=600` seconds in the repository default config.
  `reviewer=300` and `top=300` are configured values reserved for those roles,
  but the current graph handles review and Top decisions in process.
- The operator has confirmed that `codex exec` is allowed to read the generated
  context package under `automation_artifacts/runs/<run_id>/agent_calls/`.
- The operator has confirmed the actual command shape used by the runner:
  `codex exec -C <context_path> <runtime_prompt>`.

Preflight command:

```sh
codex exec --help
```

This confirms CLI availability only. A production dry run should also execute a
small throwaway `codex exec -C <context_path> <runtime_prompt>` smoke that
writes only to a temporary output directory. Fake-executor tests are useful for
runner logic, but they do not validate the production Codex CLI environment.

### EDA and `amptest` verifier

Production verification requires:

- Python environment can run `amptest/ppa_wrapper.py`.
- Cadence/Spectre/OCEAN or the project EDA backend required by `amptest` is
  installed, licensed, and available from the runner process environment.
- Any required process, PDK, model, license, and SSH environment variables are
  present before launching the runner.
- `amptest/config.json` is readable and matches the intended DUT contract.
- The verifier command writes all required outputs into the candidate artifact
  directory:
  `verification.json`, `ppa_metrics.json`, `ppa_report.log`,
  `spectre_ac.log`, and `spectre_tran.log`.
- `verification.json` conforms to `langgraph_runner.schemas.VerificationResult`
  and contains finite metrics for `performance_nrmse_combined`,
  `area_total_p`, and `power_score_basis_w`.
- The 30-second minimum verification interval in `runner_config.json` is
  acceptable for the EDA environment.

Repository default verifier command:

```sh
python {repo_root}/amptest/ppa_wrapper.py analyze --config {local_candidate_dir}/config.json
```

The runner executes this through `shell=True` from `repo_root` after formatting
`{repo_root}`, `{local_candidate_dir}`, `{remote_candidate_dir}`,
`{candidate_id}`, and `{output_dir}`.

## Production Config Separation

Do not use the repository default `runner_config.json` as the mutable production
config. Create a run-specific config under a local operator path inside the
repo, for example:

```text
automation_artifacts/operator_configs/prod-run-YYYYMMDD-HHMMSS.json
```

Required production config rules:

- `artifact_root` must point to a run-specific or environment-specific artifact
  root, for example `automation_artifacts/prod`.
- `contract_path` must remain `docs/top-coordinator-contract.md` unless the
  contract has been intentionally versioned and reviewed.
- `amptest_dir`, `dut_netlist`, `devices_csv`, and `amptest_config` must remain
  under the repository root.
- `candidate_generation_batch_size` should start at `1` for the first canary
  run, even though the default is `3`.
- `verifier.min_interval_seconds` must not be lower than `30` for production
  unless the Verifier Agent owner explicitly approves the EDA load.
- `verifier.timeout_seconds` must match the expected worst-case EDA runtime and
  should remain at or above the default `1800` seconds until measured data
  supports changing it.
- Any non-default verifier command must still write the same required outputs
  and must not modify evaluator logic.

The production config file itself should be archived with the run artifacts.
The operator should record the exact config path used in the run note.

## Preflight and Dry-Run Checks

Run all checks from `D:\Codex\Amplifier`.

### Repository and state preflight

```sh
git status --short
python -m langgraph_runner --repo-root . --config runner_config.json init
python -m unittest discover -s tests/langgraph_runner -p "test_*.py" -v
```

Expected result:

- `git status --short` is reviewed by the operator. Existing unrelated edits
  are not reverted.
- `init` exits `0`.
- `automation_artifacts/state.json` exists.
- `automation_artifacts/ledger.jsonl` exists.
- LangGraph runner unit tests pass before production execution. Broader
  repository test suites may also be run when their external fixtures are
  available; unrelated failures must be triaged before production.

### Config preflight

For the selected production config:

```sh
python -m langgraph_runner --repo-root . --config automation_artifacts/operator_configs/prod-run-YYYYMMDD-HHMMSS.json init
```

Expected result:

- Config loads with Pydantic validation.
- Config path resolves under the repository root.
- Artifact root is created.
- State contract hash is computed from `docs/top-coordinator-contract.md`.

### Contract preflight

Operator checks:

- `amptest/config.json` declares the intended `dut_subckt` and
  `dut_pins_order`.
- `docs/top-coordinator-contract.md` and `docs/langgraph-runner.md` agree on
  the DUT pin contract.
- The current DUT netlist preserves the configured subckt name and pin order.
- `devices.csv` exists and accounts only for allowed device classes.

### External tool dry run

Before a production candidate batch, verify the external commands separately:

```sh
codex exec --help
python amptest/ppa_wrapper.py analyze --config <approved-smoke-config>
```

Use the exact EDA smoke command approved by the Verifier Agent owner. The
runner's production verifier path normally uses the candidate workspace
`config.json`, so a repository-level `amptest/config.json` smoke is acceptable
only when the verifier owner confirms that it exercises the same Spectre/OCEAN,
license, model, and wrapper environment. The dry run must not alter evaluator
logic or candidate acceptance rules.

## One-Batch Canary Procedure

The first production run must be a one-candidate, one-batch canary.

1. Create a production config with:
   `candidate_generation_batch_size=1` and an isolated `artifact_root`.
2. Back up the current canonical files listed in the backup section below.
3. Run:

   ```sh
   python -m langgraph_runner --repo-root . --config automation_artifacts/operator_configs/prod-run-YYYYMMDD-HHMMSS.json run-one-batch
   ```

4. Inspect:

   ```text
   <artifact_root>/state.json
   <artifact_root>/ledger.jsonl
   <artifact_root>/runs/manual/
   <artifact_root>/candidates/<candidate_id>/
   <artifact_root>/workspaces/<candidate_id>/
   ```

5. Confirm every candidate has:
   `proposal.json`, `patch.diff`, `notes.md`, `review.json`,
   `verification.json` when review passed, and `verdict.json`.
6. Confirm verifier logs are present for review-passing candidates:
   `verifier_stdout.log`, `verifier_stderr.log`, `ppa_report.log`,
   `spectre_ac.log`, and `spectre_tran.log` when produced by the verifier.
7. If a candidate is accepted, confirm only
   `amptest/dummy_neural_amp.scs` and `amptest/devices.csv` changed in the repo.
8. Run the project test suite after promotion:

   ```sh
   python -m unittest discover -s tests/langgraph_runner -p "test_*.py" -v
   ```

9. Increase `candidate_generation_batch_size` only after the canary artifacts,
   ledger entries, state mutation, and rollback path have been reviewed.

## Human Interrupt and Resume Operations

Human interrupt files are run-scoped:

```text
<artifact_root>/runs/<run_id>/human_interrupt.json
```

Current CLI runs use `run_id=manual`.

When a human interrupt is pending:

- Do not promote candidate workspaces manually.
- Inspect `human_interrupt.json` and `top_decision.json`.
- Inspect evidence paths named by the top decision.
- Decide one of: continue, reject, rerun verification, or stop.

Resume command:

```sh
python -m langgraph_runner --repo-root . --config <production-config> resume --human-response "continue"
```

Operational rules:

- A response is attached only if
  `<artifact_root>/runs/manual/human_interrupt.json` exists.
- If no pending interrupt exists, the runner records a warning and continues a
  bounded graph pass.
- Canonical `state.json` and `ledger.jsonl` remain authoritative after resume.
- Do not edit `state.json`, `ledger.jsonl`, or `human_interrupt.json` by hand
  unless executing the documented rollback procedure.

## Artifact, State, Ledger Backup and Rollback

### Backup before production run

Create a timestamped backup directory under the selected artifact root:

```text
<artifact_root>/backups/YYYYMMDD-HHMMSS/
```

Copy these files and directories when present:

- `amptest/dummy_neural_amp.scs`
- `amptest/devices.csv`
- `<artifact_root>/state.json`
- `<artifact_root>/ledger.jsonl`
- `<artifact_root>/candidates/`
- `<artifact_root>/workspaces/`
- `<artifact_root>/runs/`
- the exact production config file

The backup is a production run artifact and should not be deleted during the
same operating window.

### Rollback after rejected, errored, or interrupted run

No repository rollback is normally required if no candidate was accepted,
because candidate workspaces are isolated and rejected/error candidates are not
promoted.

Operator checks:

```sh
git diff -- amptest/dummy_neural_amp.scs amptest/devices.csv
```

Expected result:

- No diff if no accepted candidate was promoted.
- If there is a diff without an accepted ledger entry, classify as
  `unexpected_repo_mutation` and escalate.

### Rollback after accepted promotion

If an accepted promotion must be reverted:

1. Stop all runner invocations for the repo.
2. Restore `amptest/dummy_neural_amp.scs` and `amptest/devices.csv` from the
   timestamped backup.
3. Preserve the candidate artifact directory and ledger entry that caused the
   promotion.
4. Append an operator incident note under:

   ```text
   <artifact_root>/runs/manual/operator_rollback.json
   ```

5. Restore `state.json` from backup only when the accepted promotion is being
   fully withdrawn from canonical workflow state. Do not edit individual fields
   by hand.
6. Re-run:

   ```sh
   python -m unittest discover -s tests/langgraph_runner -p "test_*.py" -v
   ```

Ledger rollback policy:

- `ledger.jsonl` is append-only for normal operation.
- Do not delete accepted entries during ordinary rollback.
- If a corrupted ledger must be restored from backup, preserve the corrupted
  file as `ledger.jsonl.corrupt-YYYYMMDD-HHMMSS` and record the incident.

## Failure Classification and Escalation

Use these production classes.

### Candidate rejection

Examples:

- Patch touches forbidden files.
- DUT pin contract is invalid.
- Forbidden OPAMP, behavioral amplifier, controlled-source amplifier, or
  disallowed device appears.
- `devices.csv` is missing or inconsistent.
- `amptest` completes and metrics fail phase acceptance gates.

Action:

- Preserve candidate artifacts.
- Do not retry unless a new candidate patch is generated.
- No EDA/operator escalation required unless failures cluster unexpectedly.

### Candidate error

Examples:

- Missing `proposal.json`, `patch.diff`, or `notes.md` after retry.
- Invalid proposal JSON.
- Patch cannot be applied to the isolated workspace.
- Verification output JSON is invalid.

Action:

- Preserve stdout/stderr and candidate artifacts.
- Retry only through the runner retry path or a new bounded batch.
- Escalate to runner owner if the same error repeats for multiple candidates.

### Infrastructure verifier failure

Examples:

- Verifier command exits nonzero before simulation evidence is produced.
- License checkout failure.
- EDA executable missing.
- Timeout caused by system load rather than candidate convergence.
- Required verifier outputs are missing or stale.

Action:

- Stop additional production batches.
- Preserve `verifier_stdout.log` and `verifier_stderr.log`.
- Escalate to Verifier Agent or EDA environment owner.
- Re-run the same candidate only after the infrastructure cause is corrected.
- Do not change candidate design to fix infrastructure failures.

### Agent execution failure

Examples:

- `codex exec` missing, unauthenticated, timed out, or returned nonzero.
- Agent wrote outside the assigned output directory, as observed by sandbox
  audit, git diff, or filesystem inspection.
- Agent produced malformed artifacts twice.

Action:

- Preserve run-scoped `stdout.log` and `stderr.log`.
- Escalate to the runner/operator owner for CLI/auth/timeouts.
- Escalate to prompt/agent owner for repeated malformed outputs.

### Runner programming failure

Examples:

- Python exception not represented as structured candidate error.
- State or ledger write fails.
- Promotion fails after an accepted decision.
- Repository files change without an accepted candidate.

Action:

- Stop production runs.
- Preserve the whole artifact root.
- Restore repository DUT files from backup if needed.
- Open a runner defect with paths to `batch_error.json`, `state.json`,
  `ledger.jsonl`, and relevant candidate directories.

### Human escalation

Escalate to the human operator when:

- Top decision requests `human_interrupt`.
- `anomaly_level` is `critical`.
- Multiple accepted candidates in one batch require deterministic winner review.
- Rollback requires restoring `state.json`.
- The contract hash changes between backup and run start.

## Operator Commands

Initialize default runner artifacts:

```sh
python -m langgraph_runner --repo-root . --config runner_config.json init
```

Initialize production artifacts:

```sh
python -m langgraph_runner --repo-root . --config <production-config> init
```

Run one bounded production batch:

```sh
python -m langgraph_runner --repo-root . --config <production-config> run-one-batch
```

Run the bounded `run` route:

```sh
python -m langgraph_runner --repo-root . --config <production-config> run
```

Resume with a human response:

```sh
python -m langgraph_runner --repo-root . --config <production-config> resume --human-response "continue"
```

Inspect repository mutations to DUT files:

```sh
git diff -- amptest/dummy_neural_amp.scs amptest/devices.csv
```

Inspect artifact state:

```sh
Get-Content <artifact_root>/state.json
Get-Content <artifact_root>/ledger.jsonl
```

Run verification tests:

```sh
python -m unittest discover -s tests/langgraph_runner -p "test_*.py" -v
```

## Documentation Updates Needed for `docs/langgraph-runner.md`

Update `docs/langgraph-runner.md` before declaring production readiness:

- Add a "Production Run Readiness" section linking to this spec.
- Clarify that `runner_config.json` is the repository default and production
  should use a copied config with an isolated artifact root.
- Document that current CLI `run` remains bounded by
  `stop_after_current_pass=True`.
- Document run-scoped files under `automation_artifacts/runs/<run_id>/`,
  including agent calls, agent outputs, `top_decision.json`,
  `human_interrupt.json`, and `batch_error.json`.
- Document `verifier_stdout.log` and `verifier_stderr.log` as runner-produced
  verifier logs.
- Add the one-batch canary procedure and backup expectations.
- Add the failure classification table in concise form.
- Clarify that `resume --human-response` is meaningful only when the matching
  run directory has a pending `human_interrupt.json`.
- State explicitly that production operators should not hand-edit
  `state.json` or `ledger.jsonl` except as part of full backup restoration.

## Acceptance Criteria

Production run readiness is satisfied when:

- A run-specific production config exists under the repository and validates
  with `init`.
- The selected artifact root is isolated from unrelated development artifacts.
- `python -m unittest discover -s tests/langgraph_runner -p "test_*.py" -v`
  passes before the production batch.
- `codex exec --help` succeeds in the production shell.
- The approved EDA smoke command succeeds or the Verifier Agent owner signs off
  on the exact verifier environment.
- A timestamped backup of DUT files, state, ledger, candidate artifacts,
  workspaces, runs, and production config exists before execution.
- A one-candidate `run-one-batch` canary completes with durable artifacts.
- Every candidate that reaches evaluation and batch recording has a
  `verdict.json` and a ledger entry unless the run stops before recording due
  to a documented human interrupt, rerun-verification decision, or batch-level
  runner error.
- Candidate attempts that fail before evaluation have durable run-scoped
  stdout/stderr, parser/retry errors, and any partial candidate artifacts
  preserved for diagnosis.
- Review-passing candidates have verifier logs and normalized
  `verification.json`.
- Accepted candidates promote only `amptest/dummy_neural_amp.scs` and
  `amptest/devices.csv`.
- Rejected and errored candidates remain preserved but are not promoted.
- Human interrupt resume is demonstrated against a pending interrupt file or is
  explicitly marked not exercised because no interrupt occurred.
- Rollback from the timestamped backup is rehearsed or reviewed by the operator
  before increasing batch size above one.

## Assumptions

- Production runs are launched from `D:\Codex\Amplifier`.
- The operator uses PowerShell-compatible commands unless adapting them
  deliberately for another shell.
- The current repository default config remains `runner_config.json`.
- The runner keeps canonical files under the configured `artifact_root`.
- LangGraph checkpoint state is not used as the source of truth.
- `codex exec` is the real candidate and prime agent execution interface for
  production.
- EDA-backed verification remains behind the configured verifier command.
- The initial production posture is bounded batches, not unattended continuous
  looping.
- The Top Coordinator contract and `amptest/config.json` remain the controlling
  sources for constraints and DUT pin order.
- Other contributors may have uncommitted edits in the repository; operators
  review `git status` but do not revert unrelated work as part of this runbook.
