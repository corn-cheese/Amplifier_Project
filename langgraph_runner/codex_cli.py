from __future__ import annotations

import os
import shutil


def resolve_codex_command() -> list[str]:
    if os.name == "nt":
        codex_cmd = shutil.which("codex.cmd")
        if codex_cmd:
            return [codex_cmd]
        codex_exe = shutil.which("codex.exe")
        if codex_exe:
            return [codex_exe]
        return ["codex"]
    return [shutil.which("codex") or "codex"]
