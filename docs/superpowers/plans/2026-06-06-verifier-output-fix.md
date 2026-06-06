# Verifier Output Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent verifier runs with missing simulation evidence from being recorded as successful verification results.

**Architecture:** Keep the existing graph and verifier wrapper boundaries. Fix the verifier normalization layer so `ppa_metrics.json` with missing or non-finite required metrics becomes a structured verifier error, then change the repository default verifier command to generate fresh simulation evidence instead of only analyzing an empty candidate workspace.

**Tech Stack:** Python `unittest`, Pydantic models in `langgraph_runner.schemas`, existing `amptest/ppa_wrapper.py` command contract, JSON artifact files under `automation_artifacts/`.

---

## Current Diagnosis

The observed artifact shape is:

- `automation_artifacts/candidates/.../verifier_stdout.log` prints `performance_nrmse_combined: None`.
- The same candidate's `verification.json` records `"status": "passed"` and `"performance_nrmse_combined": 1.0`.
- The candidate workspace contains `run/ppa_metrics.json`, `run/ppa_report.log`, and `run/ppa_summary.log`, but no `run/ac.csv`, `run/tran.csv`, `run/spectre_ac.log`, or `run/spectre_tran.log`.

Root cause:

1. `runner_config.json` runs `python {repo_root}/amptest/ppa_wrapper.py analyze --config {local_candidate_dir}/config.json`.
2. `analyze` does not run Spectre/Ocean. It only analyzes existing `run/ac.csv` and `run/tran.csv`.
3. Candidate workspaces are created without AC/transient output files.
4. `amptest/ppa_wrapper_core.py analyze()` writes `performance_nrmse_combined: null` when there are no AC/transient results.
5. `langgraph_runner/verifier.py` converts missing/non-finite metrics to defaults via `_finite_metric(..., default)` and synthesizes a passed `verification.json`.

The fix is not to tune candidate designs. This is an infrastructure verifier correctness bug.

## File Structure

- Modify `langgraph_runner/verifier.py`
  - Validate required normalized metrics before writing a passed `VerificationResult`.
  - Preserve existing timeout, nonzero exit, missing output, stale output, and invalid JSON behavior.

- Modify `runner_config.json`
  - Change the repository default verifier command from `analyze` to `all`.
  - Require Spectre logs as default required outputs, because the default workflow must produce fresh simulation evidence.

- Modify `tests/langgraph_runner/test_verifier.py`
  - Add a failing regression for `performance_nrmse_combined: null`.
  - Update the repository config expectation from `analyze` to `all`.

- Modify `tests/langgraph_runner/test_graph.py`
  - Add an integration regression proving a graph run does not promote a candidate when verifier metrics are missing.
  - Update the local deterministic default-shape override test so it remains a fast fake-wrapper test.

- Modify `docs/langgraph-runner.md`
  - Document that default verification runs full PPA generation.
  - Document that `performance_nrmse_combined: null` is a verifier error, not a candidate pass.

---

### Task 1: Add Verifier Regression For Null Performance Metrics

**Files:**
- Modify: `tests/langgraph_runner/test_verifier.py`

- [ ] **Step 1: Add the failing test**

Add this test after `test_ppa_wrapper_run_outputs_overwrite_stale_verification_json`:

```python
    def test_ppa_wrapper_outputs_without_finite_performance_are_error(self):
        root = scratch_case("ppa_wrapper_outputs_without_finite_performance_are_error")
        candidate_dir = root / "candidate"
        workspace = root / "workspace"
        run_dir = workspace / "run"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        command = python_command(
            "from pathlib import Path; import json; "
            f"run = Path(r'{run_dir}'); "
            "run.mkdir(parents=True, exist_ok=True); "
            "metrics = dict(performance_nrmse_combined=None, "
            "area_power=dict(area_total_p=1443.1374, power_score_basis_w=0.0)); "
            "(run / 'ppa_metrics.json').write_text(json.dumps(metrics), encoding='utf-8'); "
            "(run / 'ppa_report.log').write_text('performance_nrmse_combined: None\\n', encoding='utf-8')"
        )
        verifier = Verifier(
            command=command,
            timeout_seconds=10,
            min_interval_seconds=0,
            required_outputs=["verification.json", "ppa_metrics.json", "ppa_report.log"],
        )

        result = verifier.run("cid", root, workspace, candidate_dir)

        self.assertEqual(result.status, "error")
        self.assertIn("performance_nrmse_combined", result.errors[0])
        self.assertNotEqual(result.performance_nrmse_combined, 1.0)
        written = VerificationResult.model_validate_json(
            (candidate_dir / "verification.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written.status, "error")
        self.assertIn("performance_nrmse_combined", written.errors[0])
```

- [ ] **Step 2: Run the focused failing test**

Run:

```bash
python -m unittest tests.langgraph_runner.test_verifier.TestVerifier.test_ppa_wrapper_outputs_without_finite_performance_are_error -v
```

Expected: FAIL because current verifier returns `status == "passed"` and substitutes `1.0`.

---

### Task 2: Validate Required PPA Metrics Before Synthesizing Passed Verification

**Files:**
- Modify: `langgraph_runner/verifier.py`
- Test: `tests/langgraph_runner/test_verifier.py`

- [ ] **Step 1: Add strict metric parsing helpers**

Add these helpers near `_finite_metric`:

```python
def _required_finite_metric(metrics: dict, key: str, errors: list[str]) -> float:
    if key not in metrics:
        errors.append(f"missing finite metric: {key}")
        return 0.0
    return _parse_required_finite_metric(metrics.get(key), key, errors)


def _required_area_power_metric(area_power: dict, key: str, errors: list[str]) -> float:
    if key not in area_power:
        errors.append(f"missing finite metric: area_power.{key}")
        return 0.0
    return _parse_required_finite_metric(area_power.get(key), f"area_power.{key}", errors)


def _parse_required_finite_metric(value: object, label: str, errors: list[str]) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        errors.append(f"missing finite metric: {label}")
        return 0.0
    if not math.isfinite(parsed):
        errors.append(f"missing finite metric: {label}")
        return 0.0
    return parsed
```

- [ ] **Step 2: Use the helpers in `_normalize_ppa_outputs`**

Replace the current `VerificationResult(... _finite_metric(...))` construction block with:

```python
        metric_errors: list[str] = []
        performance_nrmse_combined = _required_finite_metric(
            metrics,
            "performance_nrmse_combined",
            metric_errors,
        )
        area_total_p = _required_area_power_metric(area_power, "area_total_p", metric_errors)
        power_score_basis_w = _required_area_power_metric(area_power, "power_score_basis_w", metric_errors)
        if metric_errors:
            self._error(
                candidate_id,
                output_dir,
                "invalid ppa_metrics.json: " + "; ".join(metric_errors),
            )
            return
        result = VerificationResult(
            candidate_id=candidate_id,
            status="passed",
            metrics_path=str(metrics_path),
            report_path=str(report_path),
            spectre_logs=copied_logs,
            performance_nrmse_combined=performance_nrmse_combined,
            area_total_p=area_total_p,
            power_score_basis_w=power_score_basis_w,
            errors=[],
        )
```

Keep `_finite_metric` only if another test or helper still uses it. If no references remain, remove `_finite_metric`.

- [ ] **Step 3: Run verifier tests**

Run:

```bash
python -m unittest tests.langgraph_runner.test_verifier -v
```

Expected: PASS, including the new null-performance regression.

---

### Task 3: Change Repository Default Verifier To Produce Fresh Evidence

**Files:**
- Modify: `runner_config.json`
- Modify: `tests/langgraph_runner/test_verifier.py`
- Test: `tests/langgraph_runner/test_config.py`

- [ ] **Step 1: Update `runner_config.json`**

Change:

```json
"command": "python {repo_root}/amptest/ppa_wrapper.py analyze --config {local_candidate_dir}/config.json",
```

to:

```json
"command": "python {repo_root}/amptest/ppa_wrapper.py all --config {local_candidate_dir}/config.json",
```

Change `required_outputs` to:

```json
"required_outputs": [
  "verification.json",
  "ppa_metrics.json",
  "ppa_report.log",
  "spectre_ac.log",
  "spectre_tran.log"
]
```

- [ ] **Step 2: Update the repository config expectation test**

Rename `test_default_runner_config_analyze_outputs_do_not_require_spectre_logs` to:

```python
    def test_default_runner_config_runs_full_ppa_flow_and_requires_spectre_logs(self):
        config = json.loads(Path("runner_config.json").read_text(encoding="utf-8"))

        self.assertIn("ppa_wrapper.py all", config["verifier"]["command"])
        self.assertEqual(
            config["verifier"]["required_outputs"],
            ["verification.json", "ppa_metrics.json", "ppa_report.log", "spectre_ac.log", "spectre_tran.log"],
        )
```

- [ ] **Step 3: Run config and verifier tests**

Run:

```bash
python -m unittest tests.langgraph_runner.test_config tests.langgraph_runner.test_verifier -v
```

Expected: PASS.

---

### Task 4: Add Graph Regression For Missing Verifier Metrics

**Files:**
- Modify: `tests/langgraph_runner/test_graph.py`
- Test: `tests/langgraph_runner/test_graph.py`

- [ ] **Step 1: Add a fake wrapper helper that reproduces the live failure**

Add near `write_default_shape_ppa_wrapper`:

```python
def write_null_performance_ppa_wrapper(root: Path) -> None:
    wrapper = root / "amptest" / "ppa_wrapper.py"
    wrapper.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "\n"
        "def main():\n"
        "    if len(sys.argv) != 4 or sys.argv[1] != 'analyze' or sys.argv[2] != '--config':\n"
        "        print('unexpected argv: ' + repr(sys.argv[1:]), file=sys.stderr)\n"
        "        return 2\n"
        "    config_path = Path(sys.argv[3])\n"
        "    run_dir = config_path.parent / 'run'\n"
        "    run_dir.mkdir(parents=True, exist_ok=True)\n"
        "    metrics = {\n"
        "        'performance_nrmse_combined': None,\n"
        "        'area_power': {'area_total_p': 1443.1374, 'power_score_basis_w': 0.0},\n"
        "    }\n"
        "    (run_dir / 'ppa_metrics.json').write_text(json.dumps(metrics), encoding='utf-8')\n"
        "    (run_dir / 'ppa_report.log').write_text('performance_nrmse_combined: None\\n', encoding='utf-8')\n"
        "    return 0\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )
```

- [ ] **Step 2: Add the integration test**

Add after `test_local_deterministic_backend_runs_default_ppa_wrapper_command_shape`:

```python
    def test_missing_performance_metrics_from_verifier_are_not_promoted(self):
        repo_root = scratch_root("missing_performance_metrics_not_promoted")
        config_path = write_workflow_fixture(repo_root, batch_size=1)
        write_null_performance_ppa_wrapper(repo_root)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["agent_backend"] = {"mode": "local_deterministic"}
        config["verifier"] = {
            "command": "python {repo_root}/amptest/ppa_wrapper.py analyze --config {local_candidate_dir}/config.json",
            "timeout_seconds": 10,
            "min_interval_seconds": 0,
            "required_outputs": ["verification.json", "ppa_metrics.json", "ppa_report.log"],
        }
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        paths = graph_module.ArtifactPaths(repo_root=repo_root, artifact_root=repo_root / "automation_artifacts")
        StateStore(paths, repo_root / "docs" / "contract.md").initialize()

        with patch("langgraph_runner.graph.AgentRunner", side_effect=AssertionError("codex backend should not be used")):
            result = build_graph().invoke(
                {
                    "repo_root": str(repo_root),
                    "run_id": "manual",
                    "config_path": str(config_path),
                    "state_path": str(paths.state_json),
                    "route": "stop",
                },
                config={"recursion_limit": 20},
            )

        candidate_id = result["candidate_ids"][0]
        candidate_dir = paths.candidate_dir(candidate_id)
        verification = json.loads((candidate_dir / "verification.json").read_text(encoding="utf-8"))
        verdict = json.loads((candidate_dir / "verdict.json").read_text(encoding="utf-8"))
        self.assertEqual(verification["status"], "error")
        self.assertIn("performance_nrmse_combined", verification["errors"][0])
        self.assertEqual(verdict["status"], "error")
        self.assertIsNone(result["promoted_candidate_id"])
        self.assertNotIn("R1 VDD VOUT 20k", (repo_root / "amptest" / "dummy_neural_amp.scs").read_text(encoding="utf-8"))
```

- [ ] **Step 3: Run the focused graph test**

Run:

```bash
python -m unittest tests.langgraph_runner.test_graph.TestGraph.test_missing_performance_metrics_from_verifier_are_not_promoted -v
```

Expected: PASS after Task 2.

---

### Task 5: Update Operator Documentation

**Files:**
- Modify: `docs/langgraph-runner.md`

- [ ] **Step 1: Update the verification description**

In the verification/artifact section, add:

```markdown
The repository default verifier command runs `ppa_wrapper.py all`, not
`ppa_wrapper.py analyze`, so each accepted verification must generate fresh
AC/transient evidence for the candidate workspace before metrics are normalized.
`analyze` is only appropriate for tests or operator-managed commands that have
already generated candidate-local `run/ac.csv` and `run/tran.csv`.
```

- [ ] **Step 2: Update failure triage**

Under "Infrastructure verifier failure", add:

```markdown
Treat `performance_nrmse_combined: null`, missing AC/transient CSVs, or missing
Spectre logs as verifier infrastructure failure evidence. The runner must not
record those candidates as `passed`, and operators should inspect
`verifier_stdout.log`, `verifier_stderr.log`, and the candidate workspace
`run/` directory before generating more candidates.
```

- [ ] **Step 3: Do not rewrite old artifacts**

Historical `automation_artifacts/candidates/*/verification.json` files should
not be edited in place. Use the fixed verifier on new batches or explicit
reruns so ledger and candidate evidence remain auditable.

---

### Task 6: Run Full Verification

**Files:**
- All modified files

- [ ] **Step 1: Run the package test suite**

Run:

```bash
python -m unittest discover -s tests/langgraph_runner -p "test_*.py" -v
```

Expected: PASS.

- [ ] **Step 2: Inspect git diff**

Run:

```bash
git diff -- langgraph_runner/verifier.py runner_config.json tests/langgraph_runner/test_verifier.py tests/langgraph_runner/test_graph.py docs/langgraph-runner.md
```

Expected:

- `Verifier` rejects missing/non-finite PPA metrics.
- Default verifier command is `ppa_wrapper.py all`.
- Tests cover the live null-performance failure.
- Documentation explains `analyze` versus `all`.

- [ ] **Step 3: Operational canary**

Run only when the EDA environment is available:

```bash
python -m langgraph_runner --repo-root . --config runner_config.json production-run --artifact-root automation_artifacts/prod --eda-smoke-command "<approved EDA smoke command>"
```

Expected:

- Candidate verifier artifacts include `ppa_metrics.json`, `ppa_report.log`, `spectre_ac.log`, `spectre_tran.log`, `verification.json`, `verifier_stdout.log`, and `verifier_stderr.log`.
- `verification.json.status` is `passed` only when all required metrics are finite.
- If EDA is unavailable or simulation fails, the candidate records a verifier error and is not promoted.

---

## Self-Review

Spec coverage:

- Review-passing candidates still enter serialized verification through `Verifier`.
- Missing, stale, invalid, timeout, and nonzero verifier outputs remain structured errors.
- Missing simulation evidence now becomes an error instead of `passed`.
- Default repository config now produces fresh candidate-local simulation evidence.

Placeholder scan:

- No `TBD`, `TODO`, or unspecified test steps remain.
- Every code-changing task includes exact snippets and commands.

Type consistency:

- `VerificationResult.status` remains one of `passed`, `failed`, or `error`.
- Metrics remain finite floats before Pydantic validation.
- Graph tests continue reading JSON-compatible state and artifact files.
