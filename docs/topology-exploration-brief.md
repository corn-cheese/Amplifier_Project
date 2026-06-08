# Topology Exploration Brief

strategy family: q4_tail_current_sink
fixed baseline candidate: `p1-b028-c03-arch-20260606-135953`
current best observed candidate: p1-b066-c03-arch-20260607-082159 (0.1353140369451384)

Run this family for 3 batch before the strategy rotator updates this file again.

Common constraints:

- Keep the b028 three-BJT signal path recognizable unless this family explicitly says otherwise.
- Add exactly one BJT named `Q4`; do not add Q5 or any other extra BJT.
- Do not use OPAMPs, Verilog-A behavioral amplifiers, ideal gain blocks, or controlled sources as amplifiers.
- Keep patches directly applicable against the configured candidate base workspace.
- Every `res_high_po_5p73` instance must include explicit positive `l=`, `w=`, and `m=` parameters.
- Keep `devices.csv` synchronized with the netlist.

Family focus:

Use Q4 as a tail or current-sink helper for a Q1/Q2 pair-like variant.

Family instructions:

- Q4 may be an NPN tail/current sink if the candidate keeps a clear single-ended output path.
- Avoid wholesale rewrites unless needed for the tail-current topology.
- Reject local value-only retunes; this family must test whether current-tail control helps shape response.

Acceptance target:

- Stop the rotation if `performance_nrmse_combined <= 0.04` is reached.
- Otherwise, record verifier artifacts and let the rotator move to the next 4BJT family.
