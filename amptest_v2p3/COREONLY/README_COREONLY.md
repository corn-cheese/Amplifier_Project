# COREONLY Test

This folder preserves the existing core-only neural amplifier PPA test.

Run from this folder:

```sh
python3 ppa_wrapper.py analyze --config ./config.json
```

For a full Spectre/OCEAN run:

```sh
python3 ppa_wrapper.py all --config ./config.json
```

Generated outputs go under `run/`.

