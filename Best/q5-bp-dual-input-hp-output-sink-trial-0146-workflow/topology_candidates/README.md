# CIN2 Series-Damped Boost Topology Candidates

Baseline: `best_run/trial_0146` keeps `CIN2 B1 B2` and `RBIN2 B2 VREF`.
Its measured lower/upper 3 dB points are about 60.58 Hz and 20.13 kHz.

Simple parallel `CIN2A B1 B2` comparison runs lowered the low cutoff, but also
pushed the upper cutoff below 20 kHz:

- `CIN2A m=2000000`: lower 33.83 Hz, upper 17.97 kHz.
- `CIN2A m=5000000`: lower 27.67 Hz, upper 17.45 kHz.

These candidates keep the accepted amplifier core unchanged and add only
series-damped CIN2 boost paths. They are intended as direct next sweep seeds.

| Candidate | Added branch topology | Starting values | Intent |
| --- | --- | --- | --- |
| `candidate_01_single_series_boost` | `B1 - RZIN2 - CIN2A - B2` | `RZIN2 l=180u`, `CIN2A m=2000000` | First direct test of the requested series-damped boost. |
| `candidate_02_dual_end_isolated_boost` | `B1 - RZIN2A - CIN2A - RZIN2B - B2` | `RZIN2A/B l=330u`, `CIN2A m=3500000` | More low-cutoff boost with isolation at both ends of the auxiliary branch. |
| `candidate_03_split_staggered_boost` | two parallel `B1 - RZIN2x - CIN2x - B2` paths | `1.8M/220u` and `2.2M/900u` | Distribute the boost over two damped paths to reduce high-frequency collapse and passband peaking. |

Run each candidate with the same COREONLY wrapper/config pattern used by the
accepted topology. If using `ppa_wrapper_core.py` directly, point `--config` at
a workspace config whose `dut_netlist` is the candidate `dummy_neural_amp.scs`
and whose `devices_csv` is the candidate `devices.csv`.

The workflow can now sweep these as the `cin2-series-boost-topology` Optuna
family. Run `workflow/run_cin2_series_boost_topology_sweep.bat` to sweep the
three candidates as the `cin2_boost_topology` categorical parameter, or pass one
candidate name as the third argument to fix a single topology.

The sweep also retunes the accepted trial_0146 input, bypass, output-shaping,
and feedback values alongside the selected boost branch:
`CIN1_m`, `CIN2_m`, `RBIN1_l`, `RBIN2_l`, `CE1_m`, `CE2_m`, `CP1_m`,
`CP2_m`, `CP3_m`, `RBUF_l`, and `RQ4FB_l`.
