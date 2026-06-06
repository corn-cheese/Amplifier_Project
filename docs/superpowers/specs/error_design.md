# LangGraph Runner Agent Execution Error Design

Date: 2026-06-05

## Purpose

This document replaces the previous error design with the current verified root
cause for the live workflow failure observed from `docs/langgraph-runner.md`.

The normal runner commands create candidate assignments, but no candidate reaches
deterministic review because the runner cannot launch `codex exec` from Python on
Windows when the command begins with `["codex", "exec"]` and is launched with
`shell=False`. The failure is
currently collapsed into `missing_valid_subagent_output`, which hides the real
agent execution failure and causes empty candidate artifacts.

This design fixes agent execution and error classification. It does not change
the circuit, evaluator, scoring, Top Coordinator contract, or EDA behavior.

## Source Documents And Evidence

- `docs/langgraph-runner.md`: documented commands and artifact layout.
- `docs/top-coordinator-contract.md`: hard invariants and candidate protocol.
- `docs/superpowers/specs/2026-06-05-langgraph-runner-production-run-design.md`:
  production preflight and canary expectations.
- `automation_artifacts/runs/manual/batch_error.json`: latest failed live batch.
- `automation_artifacts/candidates/p1-b006-c01-arch-20260605-032220/assembly.json`:
  candidate-level assembly failure.

Verified command behavior from `D:\Codex\Amplifier`:

```text
codex exec --help
```

works when launched by PowerShell.

```text
python -c "import subprocess; subprocess.run(['codex','exec','--help'])"
```

fails with:

```text
PermissionError: [WinError 5] access is denied
```

```text
python -c "import subprocess; subprocess.run(['codex.cmd','exec','--help'], encoding='utf-8', errors='replace')"
```

exits `0` and prints the Codex help text.

## Observed Failures

### 1. Agent execution fails before logs are written

The runner created `agent_calls/<agent_call_id>/context.md` and
`agent_calls/<agent_call_id>/output/`, but `agent_outputs/<agent_call_id>/`
contains no `stdout.log` or `stderr.log`.

This means the failure occurred before `AgentRunner.run()` reached its log write
step. The current exception path in `spawn_subagents_node` records empty
`context_path` and `output_dir`, so later parsing falls back to `"."`.

### 2. Execution failure is misclassified as missing output

The latest candidate assembly records:

```json
{
  "status": "error",
  "errors": [
    "missing_proposal",
    "missing_patch",
    "missing_notes",
    "retry_failed: [WinError 5] access is denied",
    "missing_valid_subagent_output"
  ],
  "error_class": "agent_output_missing",
  "reason": "agent_output_missing"
}
```

This is inaccurate. Missing files are a downstream symptom. The first failure is
that the runner did not launch the agent process.

### 3. Batch-level stop is correct but too generic

The latest `top_decision.json` correctly stops:

```json
{
  "decision": "stop",
  "reason": "all candidates failed assembly: missing_valid_subagent_output",
  "anomaly_level": "critical"
}
```

The stop behavior is good, but the reason should identify launcher failure when
all candidates failed because `codex exec` could not start.

### 4. Production preflight has an additional blocker

Production commands also fail before graph execution because the current
baseline violates the Top Coordinator contract:

- `amptest/dummy_neural_amp.scs` includes `ahdl_include "dummy_neural_amp.va"`.
- `amptest/devices.csv` contains
  `XVA_OPAMP_EQUIV,opamp,1,,,,,,,100meg,,true`.

That failure is real and should remain strict. After the baseline is repaired,
production preflight will also need the same Windows-safe Codex command
resolution for `codex exec --help`.

## Design Goals

- Launch `codex exec` reliably from Python on Windows and non-Windows systems.
- Preserve the existing trusted artifact contract:
  `output/proposal.json`, `output/patch.diff`, and `output/notes.md`.
- Classify process launch failures separately from malformed or missing agent
  output.
- Never parse `"."` as an agent output directory.
- Preserve enough run evidence to diagnose launcher, auth, timeout, and schema
  failures without rerunning.
- Keep production preflight strict for baseline contract violations.
- Share Codex command resolution between normal agent execution and production
  preflight.

## Non-Goals

- Do not weaken OPAMP, behavioral shortcut, or allowed-device validation.
- Do not modify `amptest` analyzer, wrapper, config, or generated testbench.
- Do not repair the current OPAMP/AHDL baseline in this design.
- Do not accept natural-language stdout as a candidate artifact.
- Do not add continuous unattended looping.
- Do not make rejected candidates the base for future candidates.

## Considered Approaches

### Approach A: Use `shell=True` for all `codex exec` calls

This matches PowerShell behavior and avoids direct launcher resolution.

Trade-off: it makes quoting a large runtime prompt riskier and increases the
chance that paths or prompt text are interpreted by the shell. This is not the
recommended approach.

### Approach B: Resolve the Codex executable explicitly

On Windows, prefer `codex.cmd` from `shutil.which("codex.cmd")`. On other
platforms, use `shutil.which("codex")` or `codex`.

Trade-off: this adds a small platform helper, but it keeps `shell=False` and
preserves argument boundaries. This is the recommended approach.

### Approach C: Pass the runtime prompt through stdin

Keep explicit executable resolution, but avoid placing a long prompt on the
command line by invoking `codex exec -C <context_path> -` and passing the prompt
via stdin.

Trade-off: this changes `AgentRunner` more than Approach B but avoids Windows
command-line length and quoting risks. This should be implemented with Approach
B because the prompt includes full contract excerpts and recent ledger context.

Recommended design: combine Approach B and Approach C.

## Architecture Changes

### 1. Add a Codex command resolver

Create a helper in `langgraph_runner/agent_io.py` or a small shared module such
as `langgraph_runner/codex_cli.py`.

Required behavior:

- On Windows (`os.name == "nt"`), return `[<path-to-codex.cmd>]` when
  `shutil.which("codex.cmd")` succeeds.
- If `codex.cmd` is unavailable, try `shutil.which("codex.exe")`.
- If neither exists, return `["codex"]` and let the launch failure be recorded.
- On non-Windows systems, use `shutil.which("codex") or "codex"`.
- Expose the resolved command in artifacts for audit.

Example interface:

```python
def resolve_codex_command() -> list[str]:
    return [r"C:\Users\maize\AppData\Roaming\npm\codex.cmd"]
```

### 2. Pass runtime prompt through stdin

Change `AgentRunner._subprocess_executor` to accept an optional `stdin_text`.
The command becomes:

```text
<resolved-codex-command> exec -C <context_path> -
```

The runtime prompt is passed through `input=runtime_prompt`.

This avoids command-line quoting and length problems while keeping the current
working directory and output contract unchanged.

### 3. Always write execution logs and metadata

`AgentRunner.run()` must write the following files even when launch fails:

```text
<log_dir>/stdout.log
<log_dir>/stderr.log
<log_dir>/agent_run.json
```

`agent_run.json` must include:

```json
{
  "agent_call_id": "string",
  "role": "architecture",
  "context_path": "path",
  "artifact_output_dir": "path",
  "log_dir": "path",
  "command": [
    "C:\\Users\\maize\\AppData\\Roaming\\npm\\codex.cmd",
    "exec",
    "-C",
    "D:\\Codex\\Amplifier\\automation_artifacts\\runs\\manual\\agent_calls\\p1-b006-c01-arch-20260605-032220-subagent-a1",
    "-"
  ],
  "exit_code": 1,
  "status": "error",
  "error_class": "agent_execution_failed",
  "error": "[WinError 5] access is denied"
}
```

For a timeout, use:

```text
error_class = "agent_timeout"
exit_code = 124
```

For a nonzero process exit, use:

```text
error_class = "agent_process_failed"
exit_code = <process return code>
```

For a clean process exit with missing or malformed files, do not use an
execution error class. Let output parsing classify the problem.

### 4. Preserve call paths on exception

`_run_agent_for_assignment()` must return a call state with real paths even when
the agent process cannot start. The context path and output path are known before
the process is launched, so they must not be replaced with empty strings.

Required call state fields:

```json
{
  "agent_call_id": "string",
  "candidate_id": "string",
  "attempt": 1,
  "context_path": "automation_artifacts/runs/manual/agent_calls/p1-b006-c01-arch-20260605-032220-subagent-a1",
  "output_dir": "automation_artifacts/runs/manual/agent_calls/p1-b006-c01-arch-20260605-032220-subagent-a1/output",
  "log_dir": "automation_artifacts/runs/manual/agent_outputs/p1-b006-c01-arch-20260605-032220-subagent-a1",
  "stdout_path": "automation_artifacts/runs/manual/agent_outputs/p1-b006-c01-arch-20260605-032220-subagent-a1/stdout.log",
  "stderr_path": "automation_artifacts/runs/manual/agent_outputs/p1-b006-c01-arch-20260605-032220-subagent-a1/stderr.log",
  "agent_run_path": "automation_artifacts/runs/manual/agent_outputs/p1-b006-c01-arch-20260605-032220-subagent-a1/agent_run.json",
  "exit_code": 1,
  "status": "error",
  "error_class": "agent_execution_failed"
}
```

### 5. Never parse `"."` as agent output

`_subagent_output_dir_for_call()` currently falls back to `"."` when call state
has no paths. Replace that behavior.

Required behavior:

- If `context_path` exists, parse `<context_path>/output`.
- Else if `output_dir` exists and is non-empty, parse `output_dir`.
- Else return no parseable output and record `agent_output_path_missing`.
- No code path should call `parse_subagent_output(Path("."), ...)`.

### 6. Retry only schema/output failures after a successful launch

Do not retry when the first attempt has:

- `agent_execution_failed`
- `agent_timeout`
- `agent_process_failed`

These are execution failures, not schema failures. A blind retry normally
produces the same failure and hides the root cause.

Retry once only when the process launched cleanly and output parsing found:

- `missing_proposal`
- `missing_patch`
- `missing_notes`
- `invalid_proposal`
- `empty_patch`
- `empty_notes`
- invalid `prime_requests.json`

### 7. Add execution-aware assembly classifications

`CandidateAssembler` should map errors into distinct classes:

| Condition | Candidate `error_class` | Candidate `reason` |
| --- | --- | --- |
| agent process could not start | `agent_execution_failed` | `agent_execution_failed` |
| agent process timed out | `agent_timeout` | `agent_timeout` |
| agent process exited nonzero | `agent_process_failed` | `agent_process_failed` |
| agent launched but required files missing after retry | `agent_output_missing` | `agent_output_missing` |
| proposal JSON invalid after retry | `agent_output_invalid` | `agent_output_invalid` |
| patch cannot be applied | `candidate_patch_failed` | `candidate_patch_failed` |

The `missing_valid_subagent_output` marker may remain as a compatibility detail,
but it should no longer be the primary class for execution failures.

### 8. Add execution-aware batch stop reasons

`top_anomaly_check_node()` should write a more specific batch error:

```json
{
  "error_class": "batch_agent_execution_failure",
  "reason": "all candidates failed agent execution",
  "candidate_ids": ["p1-b006-c01-arch-20260605-032220"],
  "candidates": [
    {
      "candidate_id": "p1-b006-c01-arch-20260605-032220",
      "error_class": "agent_execution_failed",
      "context_paths": [
        "automation_artifacts/runs/manual/agent_calls/p1-b006-c01-arch-20260605-032220-subagent-a1"
      ],
      "output_paths": [
        "automation_artifacts/runs/manual/agent_calls/p1-b006-c01-arch-20260605-032220-subagent-a1/output"
      ],
      "log_paths": [
        "automation_artifacts/runs/manual/agent_outputs/p1-b006-c01-arch-20260605-032220-subagent-a1/stdout.log",
        "automation_artifacts/runs/manual/agent_outputs/p1-b006-c01-arch-20260605-032220-subagent-a1/stderr.log",
        "automation_artifacts/runs/manual/agent_outputs/p1-b006-c01-arch-20260605-032220-subagent-a1/agent_run.json"
      ]
    }
  ]
}
```

The corresponding `top_decision.json` should be:

```json
{
  "decision": "stop",
  "reason": "all candidates failed agent execution",
  "anomaly_level": "critical",
  "candidate_ids": ["p1-b006-c01-arch-20260605-032220"],
  "next_batch_strategy": "Stop until Codex CLI launch is fixed.",
  "human_interrupt": {
    "required": false,
    "question": null,
    "recommended_action": null,
    "evidence_paths": []
  }
}
```

Keep the existing `batch_agent_output_failure` stop path for cases where agents
launch successfully but all fail to produce valid required files.

### 9. Reuse Codex command resolution in production preflight

Production preflight currently runs:

```python
runner(["codex", "exec", "--help"], cwd=repo_root)
```

Change it to use the same resolver:

```python
runner([*resolve_codex_command(), "exec", "--help"], cwd=repo_root)
```

This should run after contract validation. The current repository baseline will
still fail before this step until the OPAMP/AHDL baseline is repaired.

## Artifact Expectations

For an agent launcher failure:

```text
automation_artifacts/
  runs/
    manual/
      agent_calls/
        <agent_call_id>/
          context.md
          state_summary.json
          recent_ledger.jsonl
          output/
      agent_outputs/
        <agent_call_id>/
          stdout.log
          stderr.log
          agent_run.json
      batch_error.json
      top_decision.json
  candidates/
    <candidate_id>/
      assembly.json
      review.json
      verdict.json
```

For a clean agent launch with missing files:

```text
agent_run.json status = "completed"
assembly.json error_class = "agent_output_missing"
batch_error.json error_class = "batch_agent_output_failure"
```

For a launch failure:

```text
agent_run.json error_class = "agent_execution_failed"
assembly.json error_class = "agent_execution_failed"
batch_error.json error_class = "batch_agent_execution_failure"
```

## Testing Strategy

Use `python -m unittest` only.

### Agent IO tests

Add tests in `tests/langgraph_runner/test_agent_io.py`:

1. Windows resolver test:
   - Patch `os.name` or helper platform probe to simulate Windows.
   - Patch `shutil.which` to return `C:\Users\maize\AppData\Roaming\npm\codex.cmd`.
   - Assert `resolve_codex_command()` returns that path, not `codex`.

2. stdin prompt test:
   - Use a fake executor that captures command and stdin text.
   - Assert command ends with `["exec", "-C", <context_path>, "-"]`.
   - Assert the runtime prompt contains `Required artifact output directory`.

3. launch failure log test:
   - Fake executor raises `PermissionError("[WinError 5] access is denied")`.
   - Assert `stdout.log`, `stderr.log`, and `agent_run.json` are written.
   - Assert `agent_run.json.error_class == "agent_execution_failed"`.

4. timeout classification test:
   - Fake executor raises or returns timeout behavior.
   - Assert exit code `124` and `error_class == "agent_timeout"`.

### Graph tests

Add tests in `tests/langgraph_runner/test_graph.py`:

1. Agent execution failure does not parse `"."`:
   - Provide a call state with no output path.
   - Assert collection records `agent_output_path_missing` or
     `agent_execution_failed`, not `missing_proposal` from the repository root.

2. Whole-batch execution failure stops:
   - Fake every subagent launch as `PermissionError`.
   - Assert `batch_error.json.error_class == "batch_agent_execution_failure"`.
   - Assert `top_decision.decision == "stop"`.
   - Assert each candidate verdict has `status: error`.

3. Clean launch with missing artifacts remains output failure:
   - Fake executor exits `0` and writes no required files.
   - Assert `batch_error.json.error_class == "batch_agent_output_failure"`.
   - Assert `assembly.json.error_class == "agent_output_missing"`.

4. Valid fake output reaches deterministic review:
   - Fake executor writes `output/proposal.json`, `output/patch.diff`, and
     `output/notes.md`.
   - Assert `candidate_artifacts[0].status == "assembled"` or the candidate
     reaches deterministic review with a review artifact.

### Production tests

Add tests in `tests/langgraph_runner/test_production.py`:

1. Codex preflight uses resolver:
   - Use a contract-compliant fixture baseline.
   - Patch resolver to return `["codex.cmd"]`.
   - Assert the command runner receives `codex.cmd exec --help`.

2. Baseline violation remains strict:
   - Baseline with `ahdl_include` or `opamp`.
   - Assert `production_run_failure.json.error_class ==
     "baseline_contract_violation"`.
   - Assert graph is not invoked.

## Acceptance Criteria

The implementation is complete when:

- `python -c "import subprocess; subprocess.run(['codex','exec','--help'])"`
  may still fail on Windows, but the runner no longer uses that command shape.
- `AgentRunner` launches Codex through `codex.cmd` on Windows when available.
- `AgentRunner` passes the runtime prompt via stdin using `codex exec -C <dir> -`.
- Agent launch failure writes `stdout.log`, `stderr.log`, and `agent_run.json`.
- Agent launch failure is classified as `agent_execution_failed`, not
  `agent_output_missing`.
- No artifact records `output_path` as `"."` for a failed agent call.
- A clean launch with missing required files is still classified as
  `agent_output_missing`.
- A whole batch of launch failures writes `batch_agent_execution_failure` and
  stops.
- A whole batch of clean launches with missing files writes
  `batch_agent_output_failure` and stops.
- `python -m unittest discover -s tests/langgraph_runner -p "test_*.py" -v`
  passes.
- `git diff -- amptest/dummy_neural_amp.scs amptest/devices.csv` remains empty
  for this error-handling work.

## Production Baseline Follow-Up

The current production failure is also correct:

```text
baseline_contract_violation
```

This design does not repair the circuit baseline. A separate design should
replace the OPAMP/AHDL dummy baseline with a contract-compliant primitive
baseline before expecting `production-run` to reach unit tests, Codex CLI
preflight, EDA smoke, backup, and graph invocation.

Until that baseline repair exists, production commands should continue to fail
fast before candidate generation.
