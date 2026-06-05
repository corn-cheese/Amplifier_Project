# LangGraph Runner Error Handling Design

Date: 2026-06-05

## Purpose

This document specifies fixes for three workflow failures observed while
running the documented LangGraph runner commands in `docs/langgraph-runner.md`.
The goal is to make runner failures explicit, bounded, and contract-aligned
without changing circuit topology, verifier scoring, or EDA behavior.

## Source Documents

- `docs/langgraph-runner.md`: documented runner commands and artifact layout.
- `docs/top-coordinator-contract.md`: hard invariants, agent roles, artifact
  protocol, OPAMP prohibition, and production operating rules.
- `docs/superpowers/specs/2026-06-04-langgraph-runner-implementation-design.md`:
  original runner implementation design.
- `docs/superpowers/specs/2026-06-05-langgraph-runner-remaining-workflow-design.md`:
  real workflow wiring design.
- `docs/superpowers/specs/2026-06-05-langgraph-runner-production-run-design.md`:
  production preflight and canary design.

## Observed Failures

The following one-line workflow checks were run through subagents:

- `python -m langgraph_runner --repo-root . --config runner_config.json init`
- `python -m langgraph_runner --repo-root . --config runner_config.json run-one-batch`
- `python -m langgraph_runner --repo-root . --config runner_config.json run`
- `python -m langgraph_runner --repo-root . --config runner_config.json resume --human-response "continue"`
- `python -m langgraph_runner --repo-root . --config runner_config.json production-run --artifact-root automation_artifacts/prod --eda-signoff "..."`
- `python -m langgraph_runner --repo-root . --config runner_config.json production-run --artifact-root automation_artifacts/prod --eda-smoke-command "..."`
- `git diff -- amptest/dummy_neural_amp.scs amptest/devices.csv`

The unit test suite passed, but live workflow runs exposed these failures:

1. Candidate subagents did not produce valid required artifacts.
2. The Top decision remained `continue` even when every candidate failed
   assembly.
3. Production preflight failed because the current `devices.csv` violates the
   OPAMP prohibition.

## Non-Goals

- Do not change `amptest` evaluator logic.
- Do not change `docs/top-coordinator-contract.md` invariants.
- Do not permit OPAMP, OPAMP-equivalent macros, behavioral amplifiers, ideal
  gain blocks, or controlled-source amplifier shortcuts.
- Do not change candidate topology or attempt circuit repair in this work.
- Do not add unattended continuous looping.
- Do not treat LangGraph checkpoints as canonical state.

## Problem 1: Missing Valid Subagent Output

### Evidence

Across `run-one-batch`, `run`, and `resume`, the runner created three batches
and nine candidate attempts. Every candidate failed before verification with:

```text
assembly_failed; missing_valid_subagent_output
```

The retry context recorded these validation errors:

```text
missing_proposal
missing_patch
missing_notes
```

The required files were not found in the expected subagent output directories:

- `proposal.json`
- `patch.diff`
- `notes.md`

### Root Cause Hypothesis

The runner executes subagents with:

```text
codex exec -C <context_path> <runtime_prompt>
```

but instructs them to write required artifacts to a sibling run path such as:

```text
automation_artifacts/runs/<run_id>/agent_outputs/<agent_call_id>/
```

Because the Codex execution root is `<context_path>`, the sibling
`agent_outputs` path may not be writable or may be outside the effective
workspace expected by the subagent. Re-running produces more empty or invalid
output directories rather than valid candidate artifacts.

### Design Decision

Move raw subagent output into the execution context:

```text
automation_artifacts/runs/<run_id>/agent_calls/<agent_call_id>/output/
```

The subagent prompt must require exactly these files:

```text
output/proposal.json
output/patch.diff
output/notes.md
```

The runner then reads from `<context_path>/output/`, validates the required
files, and copies validated artifacts into the canonical candidate directory:

```text
automation_artifacts/candidates/<candidate_id>/
```

This changes the raw agent-output layout only. It does not change the canonical
candidate artifact contract from `docs/top-coordinator-contract.md`.

### Required Behavior

For each subagent attempt:

1. `write_context_package` creates `<context_path>/output/`.
2. `context.md` states that required files must be written under `output/`
   relative to the current context directory.
3. `AgentRunner` may still log stdout and stderr for the call.
4. `collect_subagent_requests` parses `<context_path>/output/`.
5. Retry attempts receive validation errors and use their own
   `<retry_context_path>/output/`.
6. Valid artifacts are copied into
   `automation_artifacts/candidates/<candidate_id>/`.
7. Missing files after retry become a structured candidate error.

### Compatibility

The old `automation_artifacts/runs/<run_id>/agent_outputs/<agent_call_id>/`
directory may be kept for stdout/stderr logs if useful, but it must no longer
be the trusted source of required candidate artifacts.

## Problem 2: Invalid Continue Decision

### Evidence

When every candidate in a batch failed assembly with
`missing_valid_subagent_output`, the runner wrote `top_decision.json` with:

```json
{
  "decision": "continue",
  "anomaly_level": "none",
  "candidate_ids": []
}
```

This let `run`, and `resume` with no pending interrupt, continue producing more
failed batches.

### Design Decision

A batch where every candidate fails assembly because no valid subagent output
exists is not a normal candidate rejection. It is a runner or agent execution
failure. The runner must stop instead of continuing.

### Required Behavior

If all candidates in the current batch have assembly errors caused by
`missing_valid_subagent_output`, the runner must:

1. Write `batch_error.json` under:

   ```text
   automation_artifacts/runs/<run_id>/batch_error.json
   ```

2. Write or update `top_decision.json` with:

   ```json
   {
     "decision": "stop",
     "reason": "all candidates failed assembly: missing_valid_subagent_output",
     "anomaly_level": "critical",
     "candidate_ids": ["..."],
     "next_batch_strategy": "Stop until subagent output generation is fixed.",
     "human_interrupt": {
       "required": false,
       "question": null,
       "recommended_action": null,
       "evidence_paths": []
     }
   }
   ```

3. Include every failed candidate ID in `candidate_ids`.
4. Include evidence paths in `batch_error.json`, including:
   - candidate IDs
   - agent call IDs
   - context paths
   - output paths
   - validation errors
   - assembly paths
   - review paths
5. Set the final route to `stop`.
6. Avoid generating another batch automatically from `run` or `resume`.

### Ledger and State Behavior

Rejected or errored candidate attempts should still be recorded when enough
artifact context exists. However, recording those attempts must not cause the
runner to treat the batch as healthy.

`RunnerState.batch_no` may increment for an attempted batch if ledger entries
are appended, but `last_top_decision_path` must point to the stop decision and
the route must stop.

## Problem 3: OPAMP Baseline Contract Violation

### Evidence

Production preflight failed before graph execution because
`amptest/devices.csv` contained an OPAMP accounting row:

```text
XVA_OPAMP_EQUIV,opamp,...
```

The Top Coordinator contract forbids OPAMP and OPAMP-equivalent shortcuts in
all phases, including fallback phases.

### Design Decision

Production preflight must remain strict. The validator should not be weakened
to permit OPAMP in the baseline. Instead, the failure should be classified more
clearly as a baseline contract violation.

### Required Behavior

When production preflight detects OPAMP, OPAMP-equivalent, behavioral
amplifier, ideal gain block, controlled-source amplifier shortcut, forbidden
device class, or invalid accounting in the repository baseline, it must:

1. Fail before production graph execution.
2. Write `production_run_failure.json`.
3. Classify the failure as:

   ```text
   baseline_contract_violation
   ```

4. Include the specific violated invariant.
5. Include the exact file path and row or pattern when available.
6. Avoid creating candidate batches, verifier runs, promotions, or ledger
   entries under the production artifact root.

The production workflow should not proceed until the repository baseline is
made contract-compliant in a separate task.

## Error Classification

The following classes should be used in artifacts:

| Class | Meaning | Normal Route |
| --- | --- | --- |
| `agent_output_missing` | Required subagent files are absent after retry. | Candidate error |
| `batch_agent_output_failure` | Every candidate in a batch has missing valid subagent output. | Stop |
| `baseline_contract_violation` | Repository baseline violates hard contract before production. | Production preflight fail |
| `operator_command_error` | Documented operator command cannot be parsed or launched correctly. | Fail command, preserve stderr |

## Artifact Expectations

For Problem 1 and 2, a failed batch should leave:

```text
automation_artifacts/
  runs/
    <run_id>/
      agent_calls/
        <agent_call_id>/
          context.md
          validation_errors.json
          output/
      batch_error.json
      top_decision.json
  candidates/
    <candidate_id>/
      assembly.json
      review.json
      verdict.json
```

For Problem 3, a production preflight failure should leave:

```text
automation_artifacts/
  operator_configs/
    prod-run-YYYYMMDD-HHMMSS.json
  prod/
    runs/
      manual/
        production_run_failure.json
```

## Testing Strategy

Use `python -m unittest` only.

Add regression tests for:

1. Context-local subagent output parsing:
   - fake agent writes `output/proposal.json`, `output/patch.diff`, and
     `output/notes.md`
   - runner assembles candidate artifacts successfully
2. Missing output after retry:
   - fake agent writes nothing twice
   - runner records `agent_output_missing`
3. Whole-batch missing output failure:
   - every candidate lacks valid output
   - runner writes `batch_error.json`
   - `top_decision.decision == "stop"`
   - route is `stop`
4. Production baseline OPAMP violation:
   - baseline `devices.csv` contains an `opamp` row
   - `production_run_failure.json` records
     `baseline_contract_violation`
   - graph is not invoked
5. Smoke command quoting:
   - CLI tests document the accepted PowerShell-safe invocation format for
     `--eda-smoke-command`
   - parser failures are recorded as operator command errors

## Acceptance Criteria

The error-handling implementation is complete when:

- A subagent that writes required files under `<context_path>/output/` can
  produce a candidate that reaches deterministic review.
- Missing required subagent artifacts after retry produce a structured
  `agent_output_missing` candidate error.
- A batch where all candidates are missing valid subagent output writes
  `batch_error.json`.
- That same batch writes `top_decision.json` with `decision: "stop"` and
  `anomaly_level: "critical"`.
- `run` and `resume` do not automatically continue after the whole-batch
  missing-output failure.
- Production preflight rejects OPAMP baseline state before graph execution.
- The production failure artifact uses `baseline_contract_violation` rather
  than only a generic `invalid devices.csv` message.
- `git diff -- amptest/dummy_neural_amp.scs amptest/devices.csv` remains empty
  unless a separate accepted candidate promotion or explicit baseline repair
  task changes those files.
- `python -m unittest discover -s tests/langgraph_runner -p "test_*.py" -v`
  passes.

## Open Follow-Up Work

The repository baseline currently violates the OPAMP prohibition. Making the
baseline contract-compliant is intentionally outside this error-handling design
and should be handled as a separate design and implementation task.
