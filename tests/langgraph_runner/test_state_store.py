import json
import math
import unittest
from pathlib import Path

from langgraph_runner.artifacts import ArtifactPaths
from langgraph_runner.state_store import StateStore, contract_hash


def scratch_root(name: str) -> Path:
    root = Path(__file__).resolve().parents[2] / ".test_tmp_langgraph_runner" / "state_store"
    path = root / name
    path.mkdir(mode=0o777, parents=True, exist_ok=True)
    return path


class TestStateStore(unittest.TestCase):
    def test_artifact_path_helpers_reject_unsafe_fragments(self):
        root = scratch_root("artifact_paths")
        paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
        unsafe_fragments = [
            "",
            ".",
            "..",
            "candidate..id",
            "../escape",
            r"..\escape",
            "nested/id",
            r"nested\id",
            "C:tmp",
            "C:",
            str(Path.cwd()),
        ]

        for helper_name in ("candidate_dir", "workspace_dir", "run_dir"):
            helper = getattr(paths, helper_name)
            self.assertEqual(helper("p1-b001-c01-arch-20260604-231500").name, "p1-b001-c01-arch-20260604-231500")
            for fragment in unsafe_fragments:
                with self.subTest(helper=helper_name, fragment=fragment):
                    with self.assertRaises(ValueError):
                        helper(fragment)

    def test_initialize_creates_canonical_artifacts(self):
        root = scratch_root("initialize")
        (root / "docs").mkdir(mode=0o777, exist_ok=True)
        contract = root / "docs" / "contract.md"
        contract.write_text("contract\n", encoding="utf-8")
        paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
        store = StateStore(paths=paths, contract_path=contract)

        state = store.initialize()

        self.assertTrue(paths.state_json.exists())
        self.assertTrue(paths.ledger_jsonl.exists())
        self.assertEqual(state.contract_hash, contract_hash(contract))
        self.assertTrue(paths.runs_dir.exists())
        self.assertTrue(paths.candidates_dir.exists())
        self.assertTrue(paths.workspaces_dir.exists())

    def test_state_json_wins_over_checkpoint_state(self):
        root = scratch_root("checkpoint")
        contract = root / "contract.md"
        contract.write_text("contract\n", encoding="utf-8")
        paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
        store = StateStore(paths=paths, contract_path=contract)
        state = store.initialize()
        state.batch_no = 7
        store.write_state(state)

        loaded = store.load_state(checkpoint_state={"batch_no": 99})

        self.assertEqual(loaded.batch_no, 7)

    def test_ledger_append_writes_one_json_object_per_line(self):
        root = scratch_root("ledger")
        contract = root / "contract.md"
        contract.write_text("contract\n", encoding="utf-8")
        paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
        store = StateStore(paths=paths, contract_path=contract)
        state = store.initialize()
        paths.ledger_jsonl.write_text("", encoding="utf-8")

        store.append_ledger(
            candidate_id="p1-b001-c01-arch-20260604-231500",
            batch_id="p1-b001",
            phase=state.current_phase,
            agent="architecture",
            status="rejected",
            reason="Phase 1 gate failed.",
            metrics={"performance_nrmse_combined": 0.2},
            ppa_surrogate_score=None,
            artifact_dir="automation_artifacts/candidates/p1-b001-c01-arch-20260604-231500",
            workspace_dir="automation_artifacts/workspaces/p1-b001-c01-arch-20260604-231500",
            contract_hash=state.contract_hash,
        )

        lines = paths.ledger_jsonl.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["status"], "rejected")

    def test_ledger_append_rejects_non_finite_metrics_without_writing(self):
        root = scratch_root("ledger_nonfinite")
        contract = root / "contract.md"
        contract.write_text("contract\n", encoding="utf-8")
        paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
        store = StateStore(paths=paths, contract_path=contract)
        state = store.initialize()
        paths.ledger_jsonl.write_text("", encoding="utf-8")

        with self.assertRaises(ValueError):
            store.append_ledger(
                candidate_id="p1-b001-c01-arch-20260604-231500",
                batch_id="p1-b001",
                phase=state.current_phase,
                agent="architecture",
                status="rejected",
                reason="Phase 1 gate failed.",
                metrics={"performance_nrmse_combined": math.inf},
                ppa_surrogate_score=None,
                artifact_dir="automation_artifacts/candidates/p1-b001-c01-arch-20260604-231500",
                workspace_dir="automation_artifacts/workspaces/p1-b001-c01-arch-20260604-231500",
                contract_hash=state.contract_hash,
            )

        self.assertEqual(paths.ledger_jsonl.read_text(encoding="utf-8").splitlines(), [])

    def test_ledger_append_rejects_non_finite_ppa_surrogate_score_without_writing(self):
        root = scratch_root("ledger_nonfinite_ppa")
        contract = root / "contract.md"
        contract.write_text("contract\n", encoding="utf-8")
        paths = ArtifactPaths(repo_root=root, artifact_root=root / "automation_artifacts")
        store = StateStore(paths=paths, contract_path=contract)
        state = store.initialize()
        original_ledger = '{"candidate_id":"existing"}\n'
        paths.ledger_jsonl.write_text(original_ledger, encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "ppa_surrogate_score"):
            store.append_ledger(
                candidate_id="p1-b001-c01-arch-20260604-231500",
                batch_id="p1-b001",
                phase=state.current_phase,
                agent="architecture",
                status="rejected",
                reason="Phase 1 gate failed.",
                metrics={"performance_nrmse_combined": 0.2},
                ppa_surrogate_score=math.nan,
                artifact_dir="automation_artifacts/candidates/p1-b001-c01-arch-20260604-231500",
                workspace_dir="automation_artifacts/workspaces/p1-b001-c01-arch-20260604-231500",
                contract_hash=state.contract_hash,
            )

        self.assertEqual(paths.ledger_jsonl.read_text(encoding="utf-8"), original_ledger)


if __name__ == "__main__":
    unittest.main()
