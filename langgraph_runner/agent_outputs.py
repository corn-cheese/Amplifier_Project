from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .schemas import PrimeRequest, Proposal


@dataclass(frozen=True)
class ParsedSubagentOutput:
    candidate_id: str
    agent_call_id: str
    output_dir: Path
    valid: bool
    errors: list[str]
    proposal: Proposal | None
    proposal_path: Path
    patch_path: Path
    notes_path: Path
    prime_requests: list[dict[str, Any]]

    def to_state(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "agent_call_id": self.agent_call_id,
            "output_dir": str(self.output_dir),
            "valid": self.valid,
            "status": "valid" if self.valid else "error",
            "errors": list(self.errors),
            "proposal": self.proposal.model_dump(mode="json") if self.proposal is not None else None,
            "proposal_path": str(self.proposal_path),
            "patch_path": str(self.patch_path),
            "notes_path": str(self.notes_path),
            "prime_requests": list(self.prime_requests),
        }


@dataclass(frozen=True)
class PrimeOutput:
    candidate_id: str
    prime_call_id: str
    output_dir: Path
    valid: bool
    errors: list[str]
    notes_path: Path

    def to_state(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "prime_call_id": self.prime_call_id,
            "output_dir": str(self.output_dir),
            "valid": self.valid,
            "status": "valid" if self.valid else "error",
            "errors": list(self.errors),
            "notes_path": str(self.notes_path),
        }


def parse_subagent_output(output_dir: Path, candidate_id: str, *, agent_call_id: str) -> ParsedSubagentOutput:
    proposal_path = output_dir / "proposal.json"
    patch_path = output_dir / "patch.diff"
    notes_path = output_dir / "notes.md"
    errors: list[str] = []
    proposal: Proposal | None = None

    if not proposal_path.exists():
        errors.append("missing_proposal")
    else:
        try:
            proposal = Proposal.model_validate(json.loads(proposal_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValidationError, OSError) as exc:
            errors.append(f"invalid_proposal: {exc}")

    if not patch_path.exists():
        errors.append("missing_patch")
    elif not patch_path.read_text(encoding="utf-8", errors="replace").strip():
        errors.append("empty_patch")

    if not notes_path.exists():
        errors.append("missing_notes")
    elif not notes_path.read_text(encoding="utf-8", errors="replace").strip():
        errors.append("empty_notes")

    prime_requests = _parse_prime_requests(output_dir, candidate_id, agent_call_id, errors)

    return ParsedSubagentOutput(
        candidate_id=candidate_id,
        agent_call_id=agent_call_id,
        output_dir=output_dir,
        valid=not errors,
        errors=errors,
        proposal=proposal,
        proposal_path=proposal_path,
        patch_path=patch_path,
        notes_path=notes_path,
        prime_requests=prime_requests if not errors else [],
    )


def parse_prime_output(output_dir: Path, candidate_id: str, *, prime_call_id: str) -> PrimeOutput:
    notes_path = output_dir / "notes.md"
    errors = []
    if not notes_path.exists():
        errors.append("missing_prime_notes")
    elif not notes_path.read_text(encoding="utf-8", errors="replace").strip():
        errors.append("empty_prime_notes")
    return PrimeOutput(
        candidate_id=candidate_id,
        prime_call_id=prime_call_id,
        output_dir=output_dir,
        valid=not errors,
        errors=errors,
        notes_path=notes_path,
    )


def copy_prime_output(prime_output: PrimeOutput, candidate_prime_dir: Path) -> None:
    candidate_prime_dir.mkdir(parents=True, exist_ok=True)
    if prime_output.valid:
        shutil.copy2(prime_output.notes_path, candidate_prime_dir / "notes.md")
    (candidate_prime_dir / "prime_output.json").write_text(
        json.dumps(prime_output.to_state(), indent=2) + "\n",
        encoding="utf-8",
    )


def _parse_prime_requests(
    output_dir: Path,
    candidate_id: str,
    parent_agent_call_id: str,
    errors: list[str],
) -> list[dict[str, Any]]:
    path = output_dir / "prime_requests.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        errors.append(f"invalid_prime_requests: {exc}")
        return []
    if not isinstance(raw, list):
        errors.append("invalid_prime_requests: expected list")
        return []

    requests = []
    for index, item in enumerate(raw):
        try:
            request = PrimeRequest.model_validate(item)
        except ValidationError as exc:
            errors.append(f"invalid_prime_request[{index}]: {exc}")
            continue
        request_dict = request.model_dump(mode="json")
        request_dict["candidate_id"] = candidate_id
        request_dict["parent_agent_call_id"] = parent_agent_call_id
        request_dict["request_index"] = index
        requests.append(request_dict)
    return requests
