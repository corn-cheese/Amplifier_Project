# 5BJT Workflow Candidate Plan

## Context

The current fixed-topology sweep is still running. Treat its final output as the 4BJT **best trial** baseline for any 5BJT workflow.

Do not hard-code an intermediate trial number unless the sweep has completed and `best_trial_summary.json` or an equivalent final artifact exists. The 5BJT flow should resolve the final best trial workspace or explicitly receive it through a command-line argument.

Current known structure:

- `tools/optuna_q4_sweep.py` adds one Q4 device to the pinned 3BJT baseline.
- `tools/optuna_best_topology_sweep.py` retunes passive values on the current best 4BJT topology.
- A 5BJT workflow should be a new Q5-oriented sweep, not a direct replacement of either script.

Recommended new script:

```text
tools/optuna_q5_sweep.py
```

The script should use the 4BJT **best trial** as the baseline and generate 5BJT candidates by adding exactly one new BJT named `Q5`.

## Baseline Selection

Use this priority order:

1. Explicit `--baseline-workspace <path>` argument.
2. Completed fixed-topology sweep `best_candidate` or `best_trial_summary.json`.
3. Fallback to the current best 4BJT workspace only when no running sweep result is available.

The baseline must contain exactly:

```text
Q1, Q2, Q3, Q4
```

The generated 5BJT candidate must contain:

```text
Q1, Q2, Q3, Q4, Q5
```

Preserve the Q1/Q2/Q3/Q4 signal path from the best trial unless a candidate family explicitly states otherwise.

## Main Diagnosis

The recent fixed 4BJT sweep suggests the limiting error is AC shape, not transient behavior.

The best 4BJT topology already places a feedback-biased PNP active load around `NDRV` through Q4. Passive retuning can move the cutoffs and gain, but it does not add enough independent pole/zero control. Q5 should therefore target rolloff shape, bias stability, output centering, or local feedback rather than simply adding gain.

## Candidate Families

### 1. Q5 Low-Frequency Servo

Intent:

Use Q5 to add low-frequency suppression or servo-like feedback while preserving midband gain.

Likely placement:

```text
VOUT -> Q5 bias/control -> BQ4, E1B, E2B, or VREF-side bias node
```

Possible device:

```text
Q5 <collector> <base> <emitter> <substrate> npn_05v5_W1p00L1p00
```

Parameters to sweep:

- Q5 base bias divider lengths.
- Q5 emitter degeneration resistor.
- Feedback resistor from `VOUT` to Q5 base or reference node.
- Optional small compensation capacitor around Q5 control node.
- Existing `CE1`, `CE2`, `CP1`, `CP2`, `CP3` ranges, but narrower than the current passive-only sweep.

Expected upside:

- Best chance to reduce AC NRMSE by improving low-frequency rolloff.
- Can preserve the already good transient response if bias is mild.

Main risk:

- Can move output DC or Q4 bias enough to worsen transient NRMSE.
- Can collapse `NDRV` if Q5 feedback is too strong.

### 2. Q5 Output Active Sink

Intent:

Use Q5 as an active output sink or current helper below the Q3 emitter-follower output.

Likely placement:

```text
Q3: VDD NDRV VOUT GND npn_05v5_W1p00L1p00
Q5: VOUT BQ5 EQ5 GND npn_05v5_W1p00L1p00
```

Q5 may supplement or partially replace `RBUF`, but a safe first version should keep `RBUF` and make Q5 weak.

Parameters to sweep:

- Q5 base bias divider lengths.
- Q5 emitter resistor.
- `RBUF_l`.
- `CP3_m`.
- `RQ4FB_l`, because output feedback into Q4 bias already exists.

Expected upside:

- Improves output DC centering.
- Adds output pole/load control without disturbing Q1/Q2 too much.
- May improve high-frequency rolloff and passband ripple.

Main risk:

- Excess sink current can reduce swing or shift `VOUT` too low.
- Extra output loading can increase transient error.

### 3. Q5 Q4 Reference / Mirror Helper

Intent:

Use Q5 as a diode-connected or reference BJT that stabilizes Q4 active-load bias.

Likely placement:

```text
Q5 BQ5 BQ5 EQ5 VDD pnp_05v5_W3p40L3p40
Q4 NDRV BQ4 Q4E VDD pnp_05v5_W3p40L3p40
```

or use the smaller PNP if accounting and model availability are acceptable:

```text
pnp_05v5_W0p68L0p68
```

Parameters to sweep:

- Q5 reference branch resistor lengths.
- Coupling or divider between Q5 reference node and `BQ4`.
- `REQ4_l`, `RQ4U_l`, `RQ4R_l`, `RQ4FB_l`.
- `RC2_l` and `CP2_m`.

Expected upside:

- Conservative extension of the current best topology.
- Stabilizes `NDRV` active load behavior.
- Lower risk of topology collapse than servo or output sink candidates.

Main risk:

- May not add enough new transfer-function freedom to break the current AC NRMSE plateau.
- PNP area is larger than NPN area, especially with `pnp_05v5_W3p40L3p40`.

### 4. Q5 Q2 Emitter Current Helper

Intent:

Use Q5 to shape Q2 emitter current or effective degeneration, giving more independent control over the second gain stage.

Likely placement:

```text
Q2 NDRV N1 E2 GND npn_05v5_W1p00L2p00
Q5 E2 BQ5 EQ5 GND npn_05v5_W1p00L1p00
```

Parameters to sweep:

- Q5 base bias divider.
- Q5 emitter resistor.
- `RE2U_l`, `RE2B_l`, `CE2_m`.
- `RC2_l`, `CP2_m`.

Expected upside:

- Can tune gm and the Q2 pole more directly than passive retuning.
- Keeps the existing NDRV/Q4/Q3 topology intact.

Main risk:

- Strong Q5 action can reduce Q2 headroom or collapse gain.
- It may improve transient behavior while leaving AC stopband shape mostly unchanged.

## Lower Priority Families

Avoid these as first-pass 5BJT families:

- A new standalone gain stage after Q3.
- A wholesale differential-pair rewrite of Q1/Q2.
- Duplicating Q4 as a second active load without a distinct bias or feedback role.
- Large passive-only retunes under a 5BJT label.

These are less aligned with the observed limit because gain and transient response are already near target. The remaining gap is mostly AC transfer-function shape.

## Files To Change For A 5BJT Sweep

Create a new script:

```text
tools/optuna_q5_sweep.py
```

Use these existing scripts as references:

- `tools/optuna_best_topology_sweep.py` for loading a 4BJT baseline workspace and retuning existing passives.
- `tools/optuna_q4_sweep.py` for adding a new BJT, writing patches, writing `devices.csv`, reviewing candidates, and running verification.

Expected changes in the new script:

- Add `Q5_MODEL` or per-family model selection.
- Add a `--family` argument, for example:
  - `lf-servo`
  - `output-active-sink`
  - `q4-reference`
  - `q2-emitter-helper`
- Add `--baseline-workspace`.
- Add `--trials`, defaulting to a value appropriate for the first 5BJT run.
- Add family-specific parameter dictionaries.
- Add family-specific netlist builders.
- Add family-specific `devices.csv` builders.
- Add candidate IDs such as:

```text
q5-lf-servo-trial-0001
q5-output-sink-trial-0001
q5-q4-reference-trial-0001
q5-q2-emitter-helper-trial-0001
```

Write sweep artifacts under:

```text
automation_artifacts/sweeps/q5-<family>/<timestamp>/
```

## Netlist Builder Requirements

The builder should:

- Verify the baseline has Q1/Q2/Q3/Q4 before adding Q5.
- Preserve baseline Q lines unless the family explicitly requires a local rewrite.
- Insert Q5 and its support passives near the relevant circuit block.
- Keep every `res_high_po_5p73` instance explicit with positive `l=`, `w=5.73u`, and `m=1`.
- Keep all new capacitors accounted in `devices.csv`.
- Keep `devices.csv` synchronized with the netlist.
- Reject candidates that accidentally add Q6 or omit Q5.

## Suggested First Sweep Matrix

Run families independently rather than mixing them in one Optuna search.

Recommended first order:

1. `lf-servo`
2. `output-active-sink`
3. `q4-reference`
4. `q2-emitter-helper`

Suggested trial count:

```text
25 to 50 trials per family for the first pass
```

Use the best completed 4BJT **best trial** as the baseline for all four families, then compare by `performance_nrmse_combined`.

## Acceptance Criteria

Primary:

```text
performance_nrmse_combined decreases versus the 4BJT best trial
```

Secondary:

- AC NRMSE decreases materially.
- Midband gain remains near 40 dB.
- Output swing does not collapse.
- `VOUT` DC remains in a reasonable headroom region.
- Power and area increases are acceptable for Phase 1 performance exploration.

Target for first 5BJT pass:

```text
Move combined performance below the current 0.13-class plateau.
```

The stretch goal remains:

```text
performance_nrmse_combined <= 0.04
```

but the first realistic milestone is to prove that Q5 can reduce AC shape error beyond the passive-retuned 4BJT best trial.
