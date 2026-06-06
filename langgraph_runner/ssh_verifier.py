from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


WORKSPACE_FILES = ("dummy_neural_amp.scs", "devices.csv", "config.json")
AMPTEST_FILES = ("ppa_wrapper.py", "ppa_wrapper_core.py", "runtest.sh")
RUN_OUTPUTS = ("ppa_metrics.json", "ppa_report.log", "spectre_ac.log", "spectre_tran.log")
REMOTE_ROOT_RE = re.compile(r"^/[A-Za-z0-9_./=-]+$")
CANDIDATE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def run_ssh_verifier(
    *,
    ssh_target: str,
    remote_root: str,
    candidate_id: str,
    repo_root: Path,
    local_candidate_dir: Path,
    identity_file: Path | None = None,
) -> int:
    repo_root = Path(repo_root).resolve()
    local_candidate_dir = Path(local_candidate_dir).resolve()
    identity_file = Path(identity_file).expanduser().resolve() if identity_file is not None else None
    remote_dir = _remote_candidate_dir(remote_root, candidate_id)
    local_run_dir = local_candidate_dir / "run"

    local_sources = [
        *(local_candidate_dir / name for name in WORKSPACE_FILES),
        *(repo_root / "amptest" / name for name in AMPTEST_FILES),
    ]
    if identity_file is not None:
        local_sources.append(identity_file)
    missing = [str(path) for path in local_sources if not path.exists()]
    if missing:
        print("missing SSH verifier input: " + ", ".join(missing), file=sys.stderr)
        return 2
    if identity_file is not None:
        local_sources.pop()

    local_run_dir.mkdir(parents=True, exist_ok=True)
    ssh_options = _ssh_options(identity_file)

    prepare_command = [
        "ssh",
        *ssh_options,
        ssh_target,
        f"set -eu; rm -rf {remote_dir}; mkdir -p {remote_dir}",
    ]
    upload_command = [
        "scp",
        *ssh_options,
        "-q",
        *(str(path) for path in local_sources),
        f"{ssh_target}:{remote_dir}/",
    ]
    run_command = [
        "ssh",
        *ssh_options,
        ssh_target,
        f"set -eu; cd {remote_dir}; chmod +x runtest.sh; ./runtest.sh",
    ]
    download_command = [
        "scp",
        *ssh_options,
        "-q",
        *(f"{ssh_target}:{remote_dir}/run/{name}" for name in RUN_OUTPUTS),
        str(local_run_dir),
    ]

    for command in (prepare_command, upload_command):
        returncode = _run(command)
        if returncode != 0:
            return returncode

    run_returncode = _run(run_command)
    download_returncode = _run(download_command)
    return run_returncode or download_returncode


def _remote_candidate_dir(remote_root: str, candidate_id: str) -> str:
    root = remote_root.rstrip("/")
    if not REMOTE_ROOT_RE.fullmatch(root):
        raise ValueError("remote_root must be an absolute POSIX path using only safe path characters")
    if not CANDIDATE_ID_RE.fullmatch(candidate_id) or ".." in candidate_id:
        raise ValueError("candidate_id contains unsafe characters")
    return f"{root}/{candidate_id}"


def _run(command: list[str]) -> int:
    completed = subprocess.run(command)
    return int(completed.returncode)


def _ssh_options(identity_file: Path | None) -> list[str]:
    options = ["-o", "BatchMode=yes"]
    if identity_file is not None:
        options.extend(["-i", str(identity_file), "-o", "IdentitiesOnly=yes"])
    return options


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ssh-verifier")
    parser.add_argument("--ssh-target", required=True)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--identity-file", type=Path)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--local-candidate-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_ssh_verifier(
            ssh_target=args.ssh_target,
            remote_root=args.remote_root,
            candidate_id=args.candidate_id,
            repo_root=args.repo_root,
            local_candidate_dir=args.local_candidate_dir,
            identity_file=args.identity_file,
        )
    except ValueError as exc:
        print(f"invalid SSH verifier configuration: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
