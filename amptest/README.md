# Neural Amplifier PPA Evaluator

This folder contains a Cadence/Spectre wrapper plus a Python analyzer for the project spec. Use Python 3. `ppa_wrapper.py` is a small launcher that also works when `python` points to Python 2, as long as `python3` is available in `PATH`.

Typical use:

```sh
python3 ppa_eval/ppa_wrapper.py all --config ppa_eval/config.example.json
```

If simulation is provided externally, export:

- `ac.csv`: frequency plus either `vin_real,vin_imag,vout_real,vout_imag`, or `freq_hz,mag_db`, or `freq_hz,vout_db`.
- `tran.csv`: `time_s,vin_v,vout_v`, optionally `idd_a`.
- `devices.csv`: device list using the example columns.

Then run:

```sh
python3 ppa_eval/ppa_wrapper.py analyze --config your_config.json
```

The generated Spectre testbench assumes a black-box subckt with pins ordered as:

```text
VIN VREF VDD GND VOUT
```

`VDD=5 V`, `VREF=0.5*VDD`, and `VIN` DC common-mode is also `0.5*VDD`. The AC source magnitude is `1 mV`, and the load is `10 pF`.
