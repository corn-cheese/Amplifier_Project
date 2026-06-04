# LangGraph Runner Implementation Design

Date: 2026-06-04

## Purpose

This document specifies the first implementation of the neural amplifier
automation runner. The runner orchestrates a tree of Codex agents while
enforcing the top-level project contract in `docs/top-coordinator-contract.md`.

The runner uses LangGraph for execution state, routing, checkpointing, and
human interrupts. It uses `codex exec` for LLM agent calls. It uses file
artifacts as the only trusted interface between the runner, the Top
Coordinator, subagents, prime agents, reviewers, and verifiers.

The goal is to improve maintainability, robustness, and traceability without
letting repeated Top Coordinator calls become an unbounded conversational loop.
The deterministic runner owns state transitions, hard validation, queueing,
rollback, and artifact recording. LLM agents propose candidates, summarize
diagnosis, and flag ambiguous cases.

## Source Documents

- `docs/top-coordinator-contract.md`: global workflow contract, phase rules,
  agent roles, candidate protocol, verification protocol, acceptance rules, and
  artifact ledger requirements.
- `amptest/README.md` and `amptest/README_KO.md`: evaluator usage references.
- `amptest/config.json`: evaluator configuration reference.
- `amptest/dummy_neural_amp.scs`: canonical DUT netlist.
- `amptest/devices.csv`: canonical device accounting file.

## Design Goals

- Keep the runner deterministic where correctness matters.
- Make every LLM decision auditable through schema-validated files.
- Support an explicit tree structure:
  `Top Coordinator -> Subagent -> Prime Agent`.
- Prevent unbounded agent expansion.
- Run candidate generation in batches while serializing verification.
- Preserve all rejected work as artifacts without promoting it.
- Allow restart and resume without losing the canonical project state.
- Trigger human interrupt only when the Top Coordinator detects an anomaly that
  should not be handled automatically.

## Non-Goals

- The first runner does not implement a full LangGraph subgraph for each agent.
- The first runner does not let failed candidates become the base for
  refinement.
- The first runner does not hard-code SSH, Cadence, Spectre, OCEAN, or
  MobaXterm details.
- The first runner does not trust natural-language stdout from agents.
- The first runner does not modify `amptest` evaluator logic.

## Architecture Decision

The approved approach is:

```text
LangGraph core runner
  -> codex exec Top Coordinator calls
  -> codex exec subagent calls
  -> codex exec prime agent calls
  -> deterministic review, evaluation, routing, and recording nodes
  -> automation_artifacts/ as the canonical automation record
```

This approach gives LangGraph responsibility for orchestration and recovery,
while keeping the project ledger in ordinary files. The graph checkpoint is a
resume aid, not the canonical source of truth.

## Agent Tree Model

The maximum tree depth is fixed:

```text
depth 0: Top Coordinator / LangGraph runner
depth 1: Subagent
depth 2: Prime Agent
```

Rules:

- The runner and Top Coordinator occupy depth 0.
- Subagents may request prime agents.
- Prime agents may not request or spawn child agents.
- The runner enforces all depth and concurrency limits.
- Agent stdout and stderr are logged but never used for acceptance decisions.

Allowed subagent roles:

- `spec`
- `architecture`
- `diagnosis`
- `optimizer`
- `reviewer`

Allowed prime roles:

- `bias-prime`
- `R-prime`
- `C-prime`
- `LOW-prime`
- `HIGH-prime`
- `gain-stage-prime`
- `output-stage-prime`

## Prime Agent Limits

Prime spawning is represented as an explicit LangGraph node, not hidden inside
subagent execution.

Per subagent:

```text
max_active_primes = 2
max_total_primes = 4
```

Rules:

- A subagent may have at most two active prime agents at any time.
- When a prime finishes, fails, or is cancelled, one active slot is freed.
- The subagent may request another prime after a slot is freed.
- Total prime count never decreases.
- Once `max_total_primes` is reached, additional prime requests are rejected.
- Prime outputs are never sent directly to verification.
- Prime outputs are assembled into a candidate proposal by the parent subagent
  or by the `assemble_candidate_proposals` node.

## LangGraph Nodes

The first implementation uses this graph shape:

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

### `load_context`

Loads:

- `docs/top-coordinator-contract.md`
- `runner_config.json`
- `automation_artifacts/state.json`
- `automation_artifacts/ledger.jsonl`
- recent candidate artifacts
- current canonical DUT and device files

The node calculates a contract hash and records it in the run snapshot.

### `plan_batch`

Builds a batch plan from the current phase, counters, stagnation flags, recent
ledger summaries, and last Top Coordinator strategy.

The default batch size is three candidates.

### `spawn_subagents`

Launches subagents through `codex exec`. Each subagent receives a constrained
context package and an assigned output directory.

### `collect_subagent_requests`

Validates subagent outputs and collects candidate proposals and prime requests.
Invalid schemas get one retry with validation errors.

### `spawn_prime_agents`

Launches approved prime requests while enforcing active and total prime limits.

### `collect_prime_outputs`

Validates prime outputs. Failed prime outputs are logged and ignored unless the
parent subagent cannot assemble a proposal without them.

### `assemble_candidate_proposals`

Combines subagent and prime outputs into final candidate artifacts:

- `proposal.json`
- `patch.diff`
- `notes.md`

The runner rejects a proposal if the assigned `candidate_id` does not match.

### `deterministic_review`

Performs hard checks before any verification run:

- required artifact presence
- JSON schema validity
- patch scope
- forbidden file changes
- forbidden device or OPAMP use
- behavioral amplifier shortcut use
- DUT pin contract
- `devices.csv` consistency
- candidate ID consistency

Candidates that fail deterministic hard checks are rejected without
verification.

### `verify_queue`

Runs accepted-for-verification candidates one at a time. Candidate generation
may be parallel, but verification is serialized.

The verifier command is externalized in `runner_config.json`. The runner checks
required output files rather than trusting stdout.

### `evaluate_candidates`

Reads `ppa_metrics.json`, calculates phase-specific acceptance state, records
best-so-far metrics, and applies deterministic acceptance gates.

### `top_anomaly_check`

Runs after deterministic evaluation. Hard anomalies are already handled by code.
The LLM Top Coordinator handles soft anomalies and writes `top_decision.json`.

The Top Coordinator may choose:

- `continue`
- `reject_batch`
- `rerun_verification`
- `human_interrupt`
- `stop`

### `record_batch`

Writes state, ledger entries, run logs, batch summaries, and verdict files.

### `route_next`

Selects the next graph route:

- next batch
- phase transition
- fallback exploration
- human interrupt
- stop

## Canonical Files and Artifact Root

`amptest/run/` remains evaluator scratch output. It is not the automation
ledger.

The runner stores all automation records under:

```text
automation_artifacts/
  state.json
  ledger.jsonl
  runs/
  candidates/
  workspaces/
```

Suggested run layout:

```text
automation_artifacts/
  runs/
    <run_id>/
      graph_events.jsonl
      config.snapshot.json
      contract.snapshot.md
      top_decisions/
      agent_calls/
```

Suggested candidate layout:

```text
automation_artifacts/
  candidates/
    <candidate_id>/
      proposal.json
      patch.diff
      notes.md
      review.json
      verification.json
      ppa_metrics.json
      ppa_report.log
      spectre_ac.log
      spectre_tran.log
      verdict.json
      primes/
```

Suggested workspace layout:

```text
automation_artifacts/
  workspaces/
    <candidate_id>/
      dummy_neural_amp.scs
      devices.csv
```

## Source of Truth

Canonical source of truth:

- `automation_artifacts/state.json`
- `automation_artifacts/ledger.jsonl`
- `automation_artifacts/candidates/<candidate_id>/`

LangGraph checkpoint:

- node-level resume
- interrupt and resume
- crash recovery during an active run
- execution snapshot only

The LangGraph checkpoint never overrides `state.json` or `ledger.jsonl`.

Startup order:

1. Read `docs/top-coordinator-contract.md`.
2. Read `runner_config.json`.
3. Read `automation_artifacts/state.json`.
4. Read `automation_artifacts/ledger.jsonl`.
5. Reconcile `automation_artifacts/candidates/`.
6. Resume or start LangGraph execution.

Conflict rules:

- `state.json` and `ledger.jsonl` are canonical over checkpoint state.
- Checkpoint/state conflict is resolved in favor of `state.json`.
- Accepted candidate missing required artifacts triggers human interrupt.
- Ledger/state contradiction triggers deterministic reconciliation before any
  new candidate generation.

## Candidate ID Policy

The runner assigns every candidate ID before launching a subagent.

Format:

```text
<phase_prefix>-b<batch_no>-c<candidate_no>-<agent>-<timestamp>
```

Examples:

```text
p1-b003-c02-arch-20260604-231500
p2a-b014-c01-opt-20260605-004210
p2b-b002-c03-power-20260605-021122
```

Rules:

- Agents may not invent candidate IDs.
- `proposal.json` must echo the assigned `candidate_id`.
- Candidate ID mismatch causes rejection.
- Prime artifacts are nested under the parent candidate or parent subagent run.

## Batch Policy

Default batch size:

```text
candidate_generation_batch_size = 3
```

Batch flow:

1. Generate three candidate proposals.
2. Run deterministic review.
3. Queue review-passing candidates for verification.
4. Run verification one candidate at a time.
5. Evaluate the whole batch.
6. Promote only accepted candidates.
7. If no candidate satisfies acceptance rules, discard the batch as a base and
   generate the next batch.

Rejected candidates remain recorded. Their patches are not used as the base for
future candidates. Their metrics, logs, and diagnosis summaries may be included
in the next batch context to avoid repeating failures.

Batch rejection conditions include:

- all candidates fail deterministic review
- all candidates fail simulation
- all candidates fail the current phase acceptance gate
- the Top Coordinator flags a soft anomaly and requests batch rejection

## Phase 1 Policy

Phase 1 is performance-first.

Promotion gate:

```text
performance_nrmse_combined <= 0.04
```

Rules:

- A candidate is accepted in Phase 1 only if it satisfies the performance gate,
  passes deterministic review, completes verification, and passes safety gates.
- Candidates above `0.04` are never promoted.
- Failed candidates still contribute metrics to best-so-far tracking,
  diagnosis, stagnation detection, and next-batch context.
- The promotion gate remains `0.04`; it is not relaxed for convenience.

3BJT policy:

- Phase 1 starts with 3BJT-only candidates.
- At least 12 distinct 3BJT candidates must be verified before fallback is
  allowed.
- Before 12 verified 3BJT candidates, 4-6BJT fallback is forbidden.
- After 12 verified 3BJT candidates, fallback is allowed only if the recent 5
  verified 3BJT candidates improve best `performance_nrmse_combined` by less
  than `0.005`.
- Fallback candidates may use at most 6 BJTs.
- OPAMP and behavioral shortcuts remain forbidden in all phases.

## Candidate Workspace and Promotion

Every candidate uses an isolated workspace:

```text
automation_artifacts/workspaces/<candidate_id>/
```

Rules:

- Candidate patches are applied only inside the isolated workspace.
- Rejected workspaces are preserved as artifacts but never promoted.
- Accepted candidates are promoted to canonical files:
  `amptest/dummy_neural_amp.scs` and `amptest/devices.csv`.
- The Top Coordinator must never stack unaccepted patches.
- Rollback is implemented by retaining the previous accepted canonical files
  and refusing to promote rejected workspaces.

## Agent Context Package

Each `codex exec` call receives a constrained context package rather than an
open-ended instruction to inspect the whole repository.

Suggested context package:

```text
automation_artifacts/runs/<run_id>/agent_calls/<agent_call_id>/
  context.md
  state_summary.json
  recent_ledger.jsonl
  base_files/
    dummy_neural_amp.scs
    devices.csv
```

The package includes:

- assigned role
- assigned candidate ID or parent agent call ID
- phase and objective
- allowed files
- relevant sections from `docs/top-coordinator-contract.md`
- current state summary
- recent ledger summary
- batch failure summary
- base DUT and device paths
- required output schema

Repo-wide exploration is allowed only when explicitly permitted in the role
prompt. The agent should rely on the package first.

## Trusted Output Policy

The runner trusts only declared artifact files. Natural-language stdout and
stderr are logged but ignored for decisions.

Subagent required outputs:

```text
proposal.json
patch.diff
notes.md
```

Reviewer output:

```text
review.json
```

Verifier outputs:

```text
verification.json
ppa_metrics.json
ppa_report.log
spectre_ac.log
spectre_tran.log
```

Top Coordinator output:

```text
top_decision.json
```

General rules:

- Missing required file means agent failure.
- Invalid JSON or schema mismatch means agent failure.
- Schema failure gets one retry with validation errors.
- Second schema failure is final.
- `patch.diff` without `proposal.json` is rejected.
- `proposal.json` without `patch.diff` is rejected unless the role explicitly
  permits notes-only output.

## LLM Top Coordinator

The LLM Top Coordinator is called through `codex exec`. It is not allowed to
directly mutate canonical files.

It receives:

- relevant contract sections
- state summary
- batch summary
- review evidence
- verification evidence
- deterministic anomaly report

It writes:

```json
{
  "decision": "continue",
  "reason": "No soft anomaly requires interruption.",
  "anomaly_level": "none",
  "candidate_ids": [],
  "next_batch_strategy": "Continue 3BJT performance exploration.",
  "human_interrupt": {
    "required": false,
    "question": null,
    "recommended_action": null,
    "evidence_paths": []
  }
}
```

Allowed `decision` values:

- `continue`
- `reject_batch`
- `rerun_verification`
- `human_interrupt`
- `stop`

The runner validates `top_decision.json` before acting on it.

## Human Interrupt Policy

The runner is automatic by default. Human interrupt is anomaly-driven.

Process:

1. Deterministic checker handles hard anomalies.
2. LLM Top Coordinator reviews soft anomalies.
3. If Top marks human input as required, LangGraph interrupts and persists the
   current execution state.
4. The user response resumes the graph through LangGraph.

Hard anomalies are handled without LLM discretion:

- missing artifact
- invalid JSON schema
- forbidden file modification
- forbidden device, OPAMP, or behavioral shortcut
- invalid DUT pin contract
- `devices.csv` inconsistency
- missing required metrics
- NaN, infinity, or impossible metric values
- verification command failure

Soft anomalies are reviewed by the LLM Top Coordinator:

- safety gate ambiguity
- suspicious waveform or log pattern
- topology repetition
- phase transition concern
- near-threshold candidate judgment
- weak or contradictory diagnosis summary
- uncertain need for human intervention

Interrupt request file:

```json
{
  "reason": "string",
  "severity": "warning",
  "candidate_id": "string",
  "evidence": {
    "metrics_path": "string",
    "review_path": "string",
    "diff_path": "string",
    "logs": []
  },
  "recommended_action": "continue"
}
```

Allowed `recommended_action` values:

- `continue`
- `reject`
- `rerun_verification`
- `stop`

## Verifier Configuration

The verifier command is externalized in `runner_config.json`.

The runner core does not hard-code SSH, Cadence, Spectre, OCEAN, or MobaXterm
details.

Example:

```json
{
  "verifier": {
    "command": "ssh user@host 'cd {remote_candidate_dir} && ./runtest.sh'",
    "timeout_seconds": 1800,
    "min_interval_seconds": 30,
    "required_outputs": [
      "ppa_metrics.json",
      "ppa_report.log",
      "spectre_ac.log",
      "spectre_tran.log"
    ]
  }
}
```

The runner may pass variables such as:

- `{candidate_id}`
- `{local_candidate_dir}`
- `{remote_candidate_dir}`
- `{output_dir}`

Verifier success is determined by `verification.json`, required output files,
and metric validation.

## Timeouts

Default timeouts:

```text
Subagent codex exec: 20 minutes
Prime codex exec: 10 minutes
Reviewer codex exec: 5 minutes
Top anomaly codex exec: 5 minutes
Deterministic checks: fail fast
Verifier: runner_config.json
```

Timeout handling:

- Subagent timeout causes candidate generation failure.
- Prime timeout causes prime output to be ignored.
- Reviewer timeout causes candidate rejection as unreviewed.
- Top anomaly timeout causes conservative human interrupt.
- Verifier timeout causes verification error unless classified as an
  infrastructure failure eligible for retry.

## Retry Policy

LLM and `codex exec` agents:

- Invalid or missing required schema gets one retry with validation errors.
- Timeout has no automatic retry.
- Second schema failure marks the agent failed.
- Natural-language-only output is treated as schema failure.

Verifier:

- Infrastructure-looking failure gets one retry without changing the candidate.
- Candidate-caused simulation failure gets no retry.
- Missing metrics after an otherwise successful command gets one retry only if
  the failure looks infrastructural.
- Repeated verifier failure rejects the candidate or triggers human interrupt
  depending on evidence.

## State Schema

`automation_artifacts/state.json` should include:

```json
{
  "current_phase": "phase1_performance",
  "baseline_candidate_id": null,
  "accepted_candidate_id": null,
  "accepted_metrics": null,
  "accepted_ppa_surrogate_score": null,
  "best_failed_candidate_id": null,
  "best_failed_metrics": null,
  "batch_no": 0,
  "three_bjt_verified_count": 0,
  "three_bjt_stagnated": false,
  "phase2a_verified_count": 0,
  "phase2a_stagnated": false,
  "last_verification_at": null,
  "last_top_decision_path": null,
  "contract_hash": "string"
}
```

The implementation may add fields if they are documented and written
consistently.

## Ledger Schema

`automation_artifacts/ledger.jsonl` records one JSON object per candidate
attempt:

```json
{
  "candidate_id": "string",
  "batch_id": "string",
  "phase": "phase1_performance",
  "agent": "architecture",
  "status": "accepted",
  "reason": "string",
  "metrics": {},
  "ppa_surrogate_score": null,
  "artifact_dir": "automation_artifacts/candidates/<candidate_id>",
  "workspace_dir": "automation_artifacts/workspaces/<candidate_id>",
  "created_at": "2026-06-04T00:00:00Z",
  "contract_hash": "string"
}
```

Allowed status values:

- `accepted`
- `rejected`
- `error`
- `interrupted`

## Acceptance Summary

Phase 1 acceptance:

- `amptest` completed successfully.
- deterministic review passed.
- no hard invariant violation was found.
- `performance_nrmse_combined <= 0.04`.
- safety gates passed.

Phase 2 acceptance follows `docs/top-coordinator-contract.md`:

- `amptest` completed successfully.
- deterministic review passed.
- `performance_nrmse_combined <= 0.10`.
- PPA surrogate score improves over current accepted best.
- safety gates do not indicate invalid amplifier behavior.

Rejected candidates are always recorded when possible.

## Implementation Test Strategy

The implementation should be tested without requiring Cadence first.

Minimum tests:

- candidate ID generation
- schema validation for all agent output files
- deterministic patch scope checks
- forbidden device and OPAMP detection
- prime active and total limit enforcement
- batch rejection when no candidate passes
- Phase 1 gate enforcement
- 3BJT minimum count and fallback gating
- source-of-truth reconciliation
- verifier command templating
- retry and timeout state transitions
- human interrupt request routing

Verifier integration tests can use fixture `ppa_metrics.json` and log files
before connecting to the real remote execution environment.

## Completion Criteria

The runner implementation is complete when:

- it can initialize `automation_artifacts/`;
- it can create a batch of three candidate assignments;
- it can call `codex exec` agents and validate their file outputs;
- it can spawn and limit prime agents through a separate graph node;
- it can reject invalid candidates deterministically;
- it can run a configured verifier command serially with a 30-second minimum
  interval;
- it can evaluate Phase 1 acceptance rules;
- it can record state, ledger, and candidate artifacts;
- it can trigger and resume a LangGraph human interrupt;
- it can restart without treating checkpoint state as canonical over
  `state.json` and `ledger.jsonl`.
