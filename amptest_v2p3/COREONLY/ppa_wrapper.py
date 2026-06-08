#!/usr/bin/env python
"""Python-version-safe launcher for ppa_wrapper_core.py."""

from __future__ import print_function

import os
import sys


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    core = os.path.join(here, "ppa_wrapper_core.py")
    candidates = [
        os.environ.get("PPA_PYTHON"),
        "python3",
        "python3.11",
        "python3.9",
    ]
    for exe in candidates:
        if not exe:
            continue
        try:
            os.execvp(exe, [exe, core] + sys.argv[1:])
        except OSError:
            pass
    print("ERROR: Python 3 is required. Try: python3 ppa_wrapper.py ...", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
