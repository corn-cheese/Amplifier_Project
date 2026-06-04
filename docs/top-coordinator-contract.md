# Top Coordinator Contract

This document defines the top-level operating contract for the neural signal
amplifier automation workflow. The Top Coordinator does not directly design the
circuit. It defines the rules that subagents must follow while exploring,
verifying, accepting, rejecting, and recording amplifier candidates.

## 1. Purpose

The workflow is a performance-first, verification-driven search for a
single-ended neural signal amplifier under the project constraints.

The Top Coordinator owns:

- phase transitions
- global constraints
- agent role assignment
- candidate comparison
- verification queue control
- acceptance and rollback decisions
- state and artifact recording

The Top Coordinator must not accept natural-language claims as evidence. Only
validated `amptest` metrics, reviewer checks, and recorded artifacts can be used
for acceptance decisions.

## 2. Evaluation Target

The DUT must be a black-box Spectre subcircuit with the standard project pin
contract:

```spectre
subckt neural_amp VIN VREF VDD GND VOUT
...
ends neural_amp
```

The evaluator conditions are fixed:

- `VDD = 5 V`
- `VREF = 0.5 * VDD = 2.5 V`
- `VIN DC = 0.5 * VDD = 2.5 V`
- `VIN AC amplitude = 1 mV`
- `CLOAD = 10 pF`
- AC and transient tests are defined by `amptest`

## 3. Hard Invariants

Only the DUT implementation and its device accounting may change.

Editable files:

- DUT netlist
- `devices.csv`

Forbidden edits:

- `amptest` wrapper scripts
- `amptest` analyzer or scoring logic
- `amptest` config
- generated testbench files
- AC or transient input conditions
- supply, reference, input, or load conditions
- metric calculation logic

Forbidden design shortcuts:

- `ahdLib` OPAMP
- any OPAMP-equivalent macro
- Verilog-A behavioral amplifier
- ideal gain block
- controlled source used as an amplifier
- testbench or metric manipulation

Allowed device classes:

- `sky130_fd_pr_main` `npn_05v5`
- `sky130_fd_pr_main` `pnp_05v5`
- `sky130_fd_pr_main` `res_high_po_5p73`
- `sky130_fd_pr_main` `cap_vpp_11p5x11p7_m1m4_noshield`
- `sky130_fd_pr_main` `diode_pd2nw_05v5`

OPAMP use is forbidden in every phase, including fallback phases.

## 4. Phase Strategy

### Phase 1: Performance First

The first objective is to find a circuit that satisfies the initial performance
gate:

```text
performance_nrmse_combined <= 0.04
```

Safety gates must also reject candidates with obviously broken amplifier
behavior, even if the combined score appears acceptable. Safety checks include:

- midband gain should remain close to the 40 dB target
- lower cutoff should remain near the 10 Hz target region
- upper cutoff should remain near or above the 20 kHz target region
- transient response must remain amplifier-like and centered near the expected
  common-mode behavior
- passband ripple and distortion must not indicate a broken response

The exact safety numbers may be refined by the Spec Agent, but they must be
recorded before being used and must not depend on a single candidate.

### Phase 1 Architecture Policy

The initial architecture search is 3BJT-first.

Rules:

- Search 3BJT candidates first.
- Verify at least 12 distinct 3BJT candidates with `amptest`.
- If the recent 5 verified 3BJT candidates improve the best
  `performance_nrmse_combined` by less than `0.005`, mark 3BJT exploration as
  stagnated.
- After stagnation, allow BJT-based fallback candidates.
- Fallback candidates may use at most 6 BJTs.
- OPAMP remains forbidden during fallback.

### Phase 2A: Area First

Phase 2A begins after Phase 1 first reaches
`performance_nrmse_combined <= 0.04`.

Primary generation objective:

```text
reduce area_total_p
```

Acceptance is based on the PPA surrogate score defined in this document, not on
area alone. Area-first means candidate generators should primarily target area
reduction, while the Top Coordinator still accepts only candidates that improve
the total surrogate score.

Area stagnation rule:

- Verify at least 15 Phase 2A area candidates.
- If the accepted best area improves by less than 2% over the recent 6 area
  attempts, mark area optimization as stagnated.
- After area stagnation, transition to Phase 2B.

### Phase 2B: Power Optimization

Primary generation objective:

```text
reduce power_score_basis_w
```

Acceptance is based on the same PPA surrogate score. Power-first means candidate
generators primarily target power reduction, while the Top Coordinator still
accepts only candidates that improve the total surrogate score.

## 5. PPA Surrogate Score

The real project grading uses relative evaluation for performance, power, and
area. Because external team results are unavailable, the workflow uses an
internal surrogate score.

The Phase 1 first-passing candidate is the fixed baseline for Phase 2:

```text
perf_ref  = baseline.performance_nrmse_combined
power_ref = baseline.power_score_basis_w
area_ref  = baseline.area_total_p
```

Each candidate receives log-scaled component scores:

```text
score_perf  = log(1 + performance_nrmse_combined / perf_ref)
score_power = log(1 + power_score_basis_w / power_ref)
score_area  = log(1 + area_total_p / area_ref)
```

The total surrogate score is:

```text
ppa_surrogate_score =
    0.50 * score_perf
  + 0.25 * score_power
  + 0.25 * score_area
```

Lower score is better.

Phase 2 candidates may relax performance if the total PPA surrogate score
improves. However, every Phase 2 candidate must satisfy the performance safety
floor:

```text
performance_nrmse_combined <= 0.10
```

Candidates above this floor are rejected regardless of area or power.

## 6. Agent Roles

### Top Coordinator

The Top Coordinator manages the global state and makes phase-level decisions.
It does not run Cadence directly unless acting through the Verifier Agent role.

Responsibilities:

- create agent tasks
- enforce hard invariants
- select candidates for verification
- maintain the verification queue
- compare metrics
- accept or reject candidates
- update the state ledger
- trigger phase transitions

### Spec Agent

The Spec Agent extracts operational requirements from the project statement,
`amptest` configuration, previous logs, and available documentation.

Responsibilities:

- define metric names and their meaning
- refine safety gates without changing evaluator behavior
- identify ambiguous requirements
- produce clarifying notes for other agents

### Architecture Agent

The Architecture Agent proposes new topology-level candidates.

Responsibilities:

- explore 3BJT-first architectures
- propose fallback BJT architectures after stagnation
- keep candidates within the allowed device set
- submit candidate JSON metadata and patches

### Diagnosis Agent

The Diagnosis Agent interprets failed or weak verification results.

Responsibilities:

- classify metric failures
- identify likely causes
- recommend which block should be modified next
- explain whether a candidate should be refined or abandoned

### Optimizer Agent

The Optimizer Agent proposes local changes to accepted or promising candidates.

Allowed local blocks:

- bias
- resistor network
- capacitor network
- low-frequency shaping
- high-frequency shaping
- gain stage
- output stage

In one optimization cycle, the Optimizer Agent must name one primary block and
one primary objective.

### Verifier Agent

The Verifier Agent is the only agent allowed to execute SSH, Cadence, Spectre,
OCEAN, or `amptest`.

Responsibilities:

- enforce the 30-second global verification rate limit
- run `amptest`
- collect `ppa_metrics.json`
- collect `ppa_report.log`
- collect raw Spectre and transient logs
- report verification status using structured output

### Reviewer Agent

The Reviewer Agent checks candidate validity before or after verification.

Responsibilities:

- detect forbidden devices
- detect OPAMP or behavioral amplifier use
- detect illegal file changes
- verify DUT pin contract
- check `devices.csv` consistency with the netlist
- flag metric gaming or testbench manipulation

## 7. Prime Agents

Architecture and Optimizer Agents may create more focused prime agents when the
search space benefits from decomposition.

Examples:

- bias-prime
- R-prime
- C-prime
- LOW-prime
- HIGH-prime
- gain-stage-prime
- output-stage-prime

Prime agents must follow the same candidate protocol, verification protocol,
device rules, and file-editing constraints as ordinary agents.

## 8. Candidate Proposal Protocol

Agents other than the Verifier submit candidates as JSON metadata plus a patch.
They do not directly claim verified success.

Required candidate fields:

```json
{
  "candidate_id": "string",
  "phase": "phase1_performance | phase2a_area | phase2b_power",
  "agent": "spec | architecture | diagnosis | optimizer | reviewer | prime",
  "hypothesis": "string",
  "primary_objective": "performance | area | power",
  "changed_blocks": ["bias"],
  "files_touched": ["dut.scs", "devices.csv"],
  "expected_effect": {
    "performance_nrmse_combined": "decrease | increase | no_major_change | unknown",
    "area_total_p": "decrease | increase | no_major_change | unknown",
    "power_score_basis_w": "decrease | increase | no_major_change | unknown"
  },
  "risk": "string",
  "patch": "unified diff or equivalent patch text"
}
```

The Top Coordinator or executor applies candidate patches only after checking
that they touch allowed files.

## 9. Verification Protocol

Verification flow:

1. Top Coordinator receives a candidate proposal.
2. Reviewer checks hard invariants and patch scope.
3. Top Coordinator applies the patch to a candidate workspace or snapshot.
4. Verifier runs `amptest` no sooner than 30 seconds after the previous
   verification run.
5. Verifier records all metrics and logs.
6. Top Coordinator evaluates acceptance rules.
7. Top Coordinator updates the ledger and state.

Verifier output must include:

```json
{
  "candidate_id": "string",
  "status": "passed | failed | error",
  "metrics_path": "string",
  "report_path": "string",
  "spectre_logs": ["string"],
  "performance_nrmse_combined": 0.0,
  "area_total_p": 0.0,
  "power_score_basis_w": 0.0,
  "errors": []
}
```

If simulation fails, the candidate is rejected unless the failure is caused by a
known infrastructure issue. Infrastructure failures must be retried without
changing the candidate.

## 10. Acceptance Rules

### Phase 1 Acceptance

Accept a candidate only if all conditions hold:

- `amptest` completed successfully
- Reviewer found no hard invariant violation
- `performance_nrmse_combined <= 0.04`
- safety gates are not violated

The first accepted Phase 1 candidate becomes the fixed Phase 2 baseline.

### Phase 2 Acceptance

Accept a candidate only if all conditions hold:

- `amptest` completed successfully
- Reviewer found no hard invariant violation
- `performance_nrmse_combined <= 0.10`
- `ppa_surrogate_score` is lower than the current accepted best score
- safety gates do not indicate a non-amplifier or invalid response

Phase 2 does not require performance to remain at or below `0.04`.

### Rejection

Reject a candidate if any of the following occur:

- forbidden device or OPAMP use
- illegal file modification
- invalid DUT pin contract
- missing or inconsistent `devices.csv`
- simulation failure caused by candidate design
- Phase 1 performance gate failure
- Phase 2 performance floor failure
- worse PPA surrogate score in Phase 2
- evidence of testbench or metric manipulation

Rejected candidates must still be recorded with reason and metrics when
available.

## 11. Rollback Rules

Only accepted candidates become the base for future cycles.

For every candidate attempt:

- apply the patch to an isolated candidate snapshot
- verify the snapshot
- accept or reject using the phase rules
- if rejected, discard the snapshot and keep the previous accepted design
- if accepted, promote the snapshot to the current accepted design

The Top Coordinator must never stack unaccepted patches.

## 12. State Ledger and Artifacts

The workflow stores both summary state and full candidate artifacts.

Summary files:

- `state.json`
- `ledger.jsonl`

Candidate artifact directory:

```text
artifacts/
  candidates/
    <candidate_id>/
      proposal.json
      patch.diff
      dut.scs
      devices.csv
      ppa_metrics.json
      ppa_report.log
      spectre_ac.log
      spectre_tran.log
      verdict.json
```

`state.json` should include:

```json
{
  "current_phase": "phase1_performance",
  "baseline_candidate_id": null,
  "accepted_candidate_id": null,
  "accepted_metrics": null,
  "accepted_ppa_surrogate_score": null,
  "three_bjt_verified_count": 0,
  "three_bjt_stagnated": false,
  "phase2a_verified_count": 0,
  "phase2a_stagnated": false,
  "last_verification_at": null
}
```

`ledger.jsonl` records one JSON object per candidate attempt:

```json
{
  "candidate_id": "string",
  "phase": "string",
  "agent": "string",
  "status": "accepted | rejected | error",
  "reason": "string",
  "metrics": {},
  "ppa_surrogate_score": null,
  "artifact_dir": "string",
  "created_at": "string"
}
```

## 13. Stop Conditions

The workflow may stop when any of the following occur:

- Phase 1 cannot progress and 6BJT fallback also stagnates.
- Phase 2A and Phase 2B both stagnate.
- Verification budget is exhausted.
- The user stops the workflow.
- A candidate reaches a strong final target agreed by the user.

Optional stretch target:

```text
performance_nrmse_combined <= 0.02
```

This stretch target is not required for Phase 1 completion.

## 14. Operating Principle

The automation is allowed to be exploratory, but every exploration must be
measurable, reversible, and comparable.

The Top Coordinator may leave circuit details to subagents, but it must never
delegate away:

- constraints
- metrics
- verification authority
- acceptance rules
- rollback control
- state recording
