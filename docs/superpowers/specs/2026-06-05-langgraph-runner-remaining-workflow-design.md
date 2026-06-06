# LangGraph Runner Remaining Workflow Design

Date: 2026-06-05

## Purpose

This document specifies the remaining work required to turn the current
LangGraph runner from a shell-safe deterministic core into an actual autonomous
candidate workflow.

The existing implementation already provides the package scaffold, CLI,
canonical state and ledger, candidate IDs, agent context packaging,
deterministic review, verifier wrapper, acceptance gates, workspace
promotion/rollback, graph shell, smoke tests, and documentation. The remaining
work is to connect the pass-through graph nodes to real agent execution,
candidate assembly, serialized verification, ledger/state mutation, phase
routing, and human-interrupt handling.

## Current Baseline

Implemented and verified:

- `python -m langgraph_runner --repo-root . --config runner_config.json init`
  initializes `automation_artifacts/`.
- `run-one-batch`, counted `run --count N`, and `resume` enter the graph with
  shell-safe count-controlled behavior.
- `load_context`, `plan_batch`, `deterministic_review`,
  `evaluate_candidates`, `record_batch`, and `route_next` have deterministic
  boundary behavior.
- `spawn_subagents`, `collect_subagent_requests`, `spawn_prime_agents`,
  `collect_prime_outputs`, `assemble_candidate_proposals`, and
  `top_anomaly_check` are still pass-through nodes.
- `verify_queue` records the verification queue boundary but does not call the
  verifier.
- `record_batch` does not yet append ledger entries, write verdicts, update
  canonical state, or promote accepted workspaces.
- `resume --human-response` stores `human_response` in graph state but no node
  consumes it.

## Design Goals

- Run a complete counted batch/pass: assign candidates, call agents, assemble
  artifacts, review, verify, evaluate, record, and route.
- Keep every trusted decision based on schema-validated files, not agent stdout.
- Preserve deterministic safety boundaries around file scope, DUT contract,
  verifier outputs, and promotion.
- Keep verification serialized even when candidate generation is parallel.
- Record all accepted, rejected, errored, interrupted, and retried candidate
  attempts in canonical artifacts.
- Make restart/resume deterministic by treating `automation_artifacts/state.json`
  and `automation_artifacts/ledger.jsonl` as source of truth.
- Add real human-interrupt semantics without letting a stale checkpoint override
  canonical state.

## Non-Goals

- Do not change `amptest` evaluator logic.
- Do not hard-code SSH, Cadence, Spectre, OCEAN, or GUI automation details.
- Do not allow agents or prime agents to mutate canonical repository files
  directly.
- Do not allow failed or rejected candidates to become the base for future
  patches.
- Do not implement unbounded graph recursion. Repetition must be explicit
  counted_run control where the count unit is a candidate batch/pass.
- Do not use LangGraph checkpoint state as the canonical ledger.

## Remaining Architecture

The runner should keep the existing graph shape:

```text
load_context
-> plan_batch
-> spawn_subagents
-> collect_subagent_requests
-> spawn_prime_agents
-> collect_prime_outputs
-> assemble_candidate_proposals
-> deterministic_review
-> verify_queue
-> evaluate_candidates
-> top_anomaly_check
-> record_batch
-> route_next
```

The missing implementation is not a new graph. It is the replacement of
pass-through nodes with deterministic adapters around already-created modules.

Recommended module additions:

- `langgraph_runner/agent_outputs.py`: parse and validate subagent and prime
  output directories.
- `langgraph_runner/candidate_assembly.py`: copy validated
  `proposal.json`, `patch.diff`, and `notes.md` into canonical candidate
  directories and create candidate workspaces.
- `langgraph_runner/top_decision.py`: build Top Coordinator context, call the
  top agent, validate `top_decision.json`, and model human interrupts.
- `langgraph_runner/batch_recording.py`: write `review.json`,
  `verification.json`, `verdict.json`, ledger entries, state updates, and
  promotions.

Existing modules should remain responsible for their current boundaries:

- `agent_io.py`: create context packages and execute `codex exec`.
- `prime_limits.py`: enforce active and total prime limits.
- `workspace.py`: create candidate workspaces, apply patches, promote accepted
  candidates.
- `verifier.py`: run one verifier command, enforce timeout/rate limit, reject
  missing/stale outputs.
- `acceptance.py`: evaluate phase gates and PPA score.
- `state_store.py`: persist canonical state and ledger.

## Graph State Additions

Add these fields to `GraphState` as plain JSON-compatible values:

```text
run_dir: str
agent_calls: list[dict]
subagent_outputs: list[dict]
prime_requests: list[dict]
prime_calls: list[dict]
prime_outputs: list[dict]
candidate_artifacts: list[dict]
verification_queue: list[str]
top_decision: dict
ledger_entries: list[dict]
promoted_candidate_id: str | null
human_interrupt: dict
```

Rules:

- Graph state may cache these values for routing and resume.
- Canonical files under `automation_artifacts/` remain authoritative.
- Every dict written into graph state must also be recoverable from a canonical
  artifact or from deterministic recomputation.

## Agent Execution Design

### Subagent Spawn

`spawn_subagents` should:

1. Require `runner_config`, `runner_state`, `batch_assignments`,
   `artifact_root`, and `contract_path`.
2. Create a run directory under `automation_artifacts/runs/<run_id>/`.
3. For each assignment from `plan_batch`, call `write_context_package`.
4. Call `AgentRunner.run` with role-specific timeout from
   `runner_config.agent_timeouts_seconds`.
5. Record `agent_call_id`, `candidate_id`, role, context path, output path,
   exit code, stdout log path, stderr log path, and status in graph state.

Subagent calls may run sequentially for the first real workflow. Parallelism can
be added later once deterministic artifact handling is proven.

### Subagent Output Collection

`collect_subagent_requests` should:

1. Read each subagent output directory.
2. Validate candidate artifacts:
   `proposal.json`, `patch.diff`, and `notes.md`.
3. Validate optional prime request files if present.
4. Reject malformed candidate outputs with structured errors and one retry.
5. Preserve failed output directories for inspection.

One retry means:

- Create a second context package that includes validation errors.
- Re-run the same role with the same assigned candidate ID.
- If the second output is invalid, mark the candidate `error`.

Prime requests must be schema-validated and linked to the parent subagent call.
Prime agents may not request child agents.

## Prime Agent Design

`spawn_prime_agents` should:

1. Read validated prime requests from graph state.
2. Use `prime_limits.py` to enforce:
   `max_active_primes_per_subagent` and `max_total_primes_per_subagent`.
3. Reject over-limit requests deterministically.
4. Create context packages scoped to the parent subagent and prime role.
5. Execute prime calls through `AgentRunner`.

`collect_prime_outputs` should:

1. Validate prime output files.
2. Store accepted prime notes under:
   `automation_artifacts/candidates/<candidate_id>/primes/<prime_call_id>/`.
3. Record invalid prime outputs as warnings or errors for the parent candidate.
4. Never send prime output directly to deterministic review or verification.

The first real workflow should support prime outputs as advisory material only.
Candidate proposal authority stays with the assigned subagent or the assembly
node.

## Candidate Assembly Design

`assemble_candidate_proposals` should:

1. For each candidate assignment, choose the final valid candidate output.
2. Copy `proposal.json`, `patch.diff`, and `notes.md` into
   `automation_artifacts/candidates/<candidate_id>/`.
3. Validate `proposal.candidate_id` matches the assigned ID.
4. Validate `proposal.phase`, `proposal.agent`, and
   `proposal.primary_objective` match the assignment.
5. Create `automation_artifacts/workspaces/<candidate_id>/` from the current
   canonical DUT, devices CSV, and `amptest/config.json`.
6. Apply `patch.diff` to the isolated workspace only.
7. Mark candidates with missing artifacts, invalid schema, invalid assignment
   echo, or patch failure as `error` candidates.

The node should not promote any workspace. Promotion happens only in
`record_batch` after review, verification, evaluation, and top anomaly checks.

## Deterministic Review Integration

The current deterministic review node already checks:

- required artifacts
- proposal schema
- candidate ID
- patch presence
- file scope
- DUT pin contract from `amptest/config.json`
- forbidden shortcut patterns
- `devices.csv` required columns and allowed accounting device types

Remaining integration:

- Write `review.json` to each candidate directory.
- Add review-failing candidates to the batch record as `rejected` or `error`.
- Queue only review-passing candidates for verification.

Recommended status mapping:

- Missing candidate artifacts: `error`
- Invalid proposal schema: `error`
- Patch scope violation: `rejected`
- Invalid DUT pin contract: `rejected`
- Forbidden shortcut or disallowed `devices.csv`: `rejected`
- Workspace creation or patch application failure: `error`

## Verification Queue Design

`verify_queue` should:

1. Select candidate IDs whose `review.json` passed.
2. Run `Verifier.verify` one candidate at a time.
3. Use candidate workspace directory as `{local_candidate_dir}`.
4. Copy verifier required outputs into
   `automation_artifacts/candidates/<candidate_id>/`.
5. Write normalized `verification.json` into the candidate directory.
6. Record timeout, missing output, stale output, invalid JSON, and nonzero exit
   as structured verifier errors.

Verification must remain serialized. Candidate generation can become parallel
later, but verification must use the existing rate-limit lock and
`min_interval_seconds`.

## Evaluation Design

`evaluate_candidates` should continue to call `evaluate_candidate`, but after
real verifier integration it should:

1. Read `review.json` and `verification.json` from candidate artifacts.
2. Evaluate only candidates with review and verifier payloads.
3. Write `verdict.json` for every candidate with one of:
   `accepted`, `rejected`, `error`, or `interrupted`.
4. Preserve structured reasons from review, verification, acceptance, and top
   decision checks.

If multiple candidates are accepted in one batch, the runner should promote the
best accepted candidate by deterministic score:

- Phase 1: lowest `performance_nrmse_combined` among candidates passing
  `<= 0.04`.
- Phase 2A/2B: lowest PPA surrogate score among candidates passing the phase
  safety floor.

## Top Coordinator and Human Interrupt Design

`top_anomaly_check` should be introduced after deterministic evaluation and
before recording.

It should call the Top Coordinator only for soft decisions:

- batch-level anomaly explanation
- rerun verification request
- human interrupt recommendation
- stop recommendation
- next batch strategy summary

It should not override deterministic hard rejections.

Top output must be `top_decision.json` and validated with the existing
`TopDecision` schema.

Human interrupt behavior:

- If TopDecision is `human_interrupt`, write
  `automation_artifacts/runs/<run_id>/human_interrupt.json`.
- Set graph state route to a waiting/stop condition.
- Do not promote any candidate while waiting.
- On `resume --human-response`, load the pending interrupt file, attach the
  response, and allow the top decision node to produce a new validated
  `top_decision.json`.
- If no pending interrupt exists, `resume --human-response` records a warning
  and runs at most one counted pass unless an explicit count already exists in
  persisted graph state.

## Batch Recording Design

`record_batch` should own canonical mutation.

Responsibilities:

1. Append one ledger entry per candidate attempt.
2. Update `RunnerState.batch_no`.
3. Update accepted candidate fields when a candidate is promoted.
4. Update best failed candidate fields when rejected candidates have valid
   metrics.
5. Update phase counters and stagnation flags.
6. Record `last_verification_at` and `last_top_decision_path`.
7. Promote exactly one accepted workspace if the batch has an accepted winner.
8. Preserve rejected/error workspaces and candidate artifact directories.

Ordering:

1. Precompute all ledger entries and state updates in memory.
2. If promotion is needed, promote workspace with rollback support.
3. Write verdict files.
4. Append ledger entries.
5. Write state last.

If promotion fails, do not append accepted ledger entries and do not write the
new state. Record a batch-level error artifact instead.

## Routing Design

`route_next` should distinguish:

- `stop`: no further work.
- `next_batch`: run another batch.
- `human_interrupt`: stop and require resume.
- `rerun_verification`: return to `verify_queue` for selected candidates.

`run` should be count-controlled with `--count N`, defaulting to 1. Internally
the graph should carry clear counted_run fields such as `counted_run_total` and
`counted_run_remaining`. Count affects only `next_batch`: Top decisions for
`stop`, `human_interrupt`, and `rerun_verification` win first; otherwise
`route_next` decrements the remaining pass count and stops when the count is
exhausted. Direct graph calls without count fields should remain stable for
recursion and invalid-route tests.

Stop conditions:

- Top decision is `stop`.
- Human interrupt is pending.
- Phase 1 and fallback are both stagnated.
- Phase 2A and Phase 2B are both stagnated.
- Verification budget or configured batch limit is exhausted.
- Batch has only errors caused by infrastructure and top decision requests stop.

## Error Handling

Every node should return graph state with structured errors instead of raising
for expected workflow failures.

Expected workflow failures:

- missing agent output
- invalid JSON
- schema validation failure
- patch application failure
- deterministic review rejection
- verifier timeout
- verifier missing/stale output
- no candidates passing review
- no candidates passing acceptance
- pending human interrupt

Unexpected programming errors should still fail tests and should not be hidden
as normal candidate errors.

## Testing Strategy

Use `python -m unittest` only.

Required test layers:

- Unit tests for every new parser/schema helper.
- Node tests for each previously pass-through graph node.
- Integration test for one counted batch with fake agent outputs and fake
  verifier command.
- Integration test for retry after invalid agent output.
- Integration test for prime request limit enforcement.
- Integration test for rejected candidate not being promoted.
- Integration test for accepted candidate promotion and ledger/state update.
- Integration test for `resume --human-response` with a pending interrupt.
- Regression test that `run --count N` executes only the requested number of
  candidate batch/passes.

Fake verifier tests should avoid Cadence/Spectre and write deterministic
`verification.json`, `ppa_metrics.json`, `ppa_report.log`, `spectre_ac.log`,
and `spectre_tran.log` into the candidate directory.

## Suggested Implementation Sequence

1. Add schemas and parsers for subagent outputs and prime requests.
2. Implement `collect_subagent_requests` with invalid-output retry using a fake
   executor in tests.
3. Implement candidate assembly and workspace patch application from agent
   outputs.
4. Implement prime request spawning and collection with limit tests.
5. Wire `verify_queue` to the existing verifier wrapper.
6. Extend evaluation to write `verdict.json` for every candidate.
7. Implement `record_batch` canonical ledger/state mutation and promotion.
8. Implement `top_anomaly_check` and human interrupt/resume semantics.
9. Add counted end-to-end graph tests with fake agents and fake verifier.
10. Keep any unattended or continuous operation outside the CLI unless a future
    design adds a separate explicit command.

## Acceptance Criteria

The remaining workflow implementation is complete when:

- `run-one-batch` can execute one counted batch/pass using fake test agents and fake
  verifier in tests.
- Review-passing candidates are actually verified through `Verifier`.
- Every candidate gets `review.json`, `verification.json` when applicable, and
  `verdict.json`.
- Accepted candidates are promoted exactly once with rollback protection.
- Rejected and error candidates are preserved but never promoted.
- `automation_artifacts/ledger.jsonl` receives one entry per candidate attempt.
- `automation_artifacts/state.json` reflects the post-batch canonical state.
- `resume --human-response` has observable behavior for pending human
  interrupts.
- The runner test suite passes with no network, no Cadence, and no real Codex
  calls by using injected fake executors.

## Assumptions

- The runner continues to use `codex exec` through `AgentRunner` for real agent
  calls.
- Real EDA execution remains behind the configured verifier command.
- The first real workflow may run subagents sequentially. Parallel subagent
  execution is an optimization after deterministic artifact handling is proven.
- `docs/top-coordinator-contract.md` and `amptest/config.json` remain the
  project contract sources.
- The implementation plan should use subagent-driven development with review
  checkpoints because the remaining work touches multiple graph nodes and
  canonical state transitions.
