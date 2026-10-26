"""Isolated runner contracts; every project and attestation below is synthetic."""
import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch
import zipfile

import harness


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="harness-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.state = self.base / "private-state"
        self.case = {"contract_version": 1, "case_id": "synthetic-case", "project_id": "synthetic-project",
                     "source_revision": "a" * 40, "required_checks": ["regression"]}
        self.adapter = {"contract_version": 1, "project_id": "synthetic-project", "source_revision": "a" * 40,
                        "lifecycle": {"kind": "reference-only"}, "browser_fixture": "fixture.py",
                        "checks": {"regression": {"argv": ["$PYTHON", "check.py"], "timeout_seconds": 3,
                                                  "inputs": ["adapter.json", "case.json", "check.py", "fixture.py"]}},
                        "required_attestations": ["browser_form_regression"],
                        "bundle_files": ["adapter.json", "case.json", "check.py", "fixture.py"]}
        (self.root / "check.py").write_text("print('synthetic-only-output')\n", encoding="utf-8")
        (self.root / "fixture.py").write_text("# Synthetic external browser fixture\n", encoding="utf-8")
        self.write_config()

    def write_config(self):
        for name, value in (("adapter.json", self.adapter), ("case.json", self.case)):
            (self.root / name).write_text(json.dumps(value), encoding="utf-8")

    def plan(self, **kwargs):
        return harness.plan_operation("adapter.json", "case.json", self.state, "synthetic-operation", root=self.root, **kwargs)

    def run_regression(self):
        return harness.run_check(self.state, "regression")

    def client_evidence(self, outcome="passed"):
        value = harness.load_json(self.state / "operation.json")
        _, key = harness.check_key(self.root, value, "regression")
        proof = self.base / "synthetic-browser-proof.txt"
        proof.write_text("Synthetic proof for contract testing only.\n", encoding="utf-8")
        payload = {"contract_version": 1, "kind": "browser_form_regression", "case_id": self.case["case_id"],
                   "input_tree": key["input_tree"], "client": "Synthetic test client", "outcome": outcome,
                   "checks": {"broken_origin_blocked": True, "fixed_login_passed": True, "null_origin_rejected": True},
                   "fixture_source_sha256": harness.file_digest(self.root / "fixture.py"),
                   "evidence_files": [{"path": str(proof), "sha256": harness.file_digest(proof)}]}
        target = self.base / "synthetic-evidence.json"
        target.write_text(json.dumps(payload), encoding="utf-8")
        return target, payload

    def ready(self):
        self.plan(); self.run_regression()
        path, _ = self.client_evidence()
        return harness.attest(self.state, "browser_form_regression", path)

    def test_plan_idempotency_and_conflicting_operation(self):
        first = self.plan()
        second = self.plan()
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(first["plan_digest"], second["plan_digest"])
        with self.assertRaises(harness.HarnessError):
            harness.plan_operation("adapter.json", "case.json", self.state, "different-operation", root=self.root)

    def test_generic_project_needs_no_engine_copy_or_blueprint(self):
        self.adapter["required_attestations"] = []
        self.adapter.pop("browser_fixture")
        self.write_config(); self.plan()
        result = self.run_regression()
        self.assertEqual(result["outcome"], "passed")
        self.assertIn("engine_sha256", result)
        self.assertFalse((self.root / "harness.py").exists())
        status = harness.status(self.state)
        self.assertEqual(status["local_status"], "handoff_ready")
        self.assertEqual(status["lifecycle_status"], "lifecycle_reference_only")

    def test_state_in_source_or_other_git_checkout_is_rejected(self):
        with self.assertRaises(harness.HarnessError):
            harness.plan_operation("adapter.json", "case.json", self.root / "state", "operation", root=self.root)
        git_root = self.base / "other-checkout"
        (git_root / ".git").mkdir(parents=True)
        with self.assertRaises(harness.HarnessError):
            harness.plan_operation("adapter.json", "case.json", git_root / "state", "operation", root=self.root)
        with self.assertRaises(harness.HarnessError):
            harness.plan_operation("adapter.json", "case.json", self.base, "operation", root=self.root)

    def test_case_required_browser_check_cannot_be_omitted_by_adapter(self):
        self.case["required_attestations"] = ["browser_form_regression"]
        self.adapter["required_attestations"] = []
        self.write_config()
        with self.assertRaises(harness.HarnessError):
            self.plan()

    def test_dirty_pinned_upstream_is_rejected_before_execution(self):
        upstream = self.base / "synthetic-upstream"
        (upstream / "tools").mkdir(parents=True)
        (upstream / "tools/lifecycle_plan.py").write_text("raise AssertionError('must not execute')\n", encoding="utf-8")
        adapter = {**self.adapter, "lifecycle": {"kind": "blueprint-lifecycle-v1", "task": "source_release", "entrypoint": "tools/lifecycle_plan.py"}}
        head = subprocess.CompletedProcess([], 0, stdout=("a" * 40 + "\n").encode(), stderr=b"")
        dirty = subprocess.CompletedProcess([], 0, stdout=b" M tools/lifecycle_plan.py\n", stderr=b"")
        with patch.object(harness.subprocess, "run", side_effect=[head, dirty]) as execute:
            with self.assertRaises(harness.HarnessError):
                harness.readonly_lifecycle(adapter, upstream, self.state)
        self.assertEqual(execute.call_count, 2)

    def test_source_traversal_is_rejected(self):
        for name in ("../case.json", "/case.json", "sub\\case.json", ".git/config"):
            with self.assertRaises(harness.HarnessError):
                harness.source_file(self.root, name)
    def test_source_symlink_is_rejected_when_supported(self):
        target = self.root / "linked.py"
        try:
            target.symlink_to(self.root / "check.py")
        except OSError:
            self.skipTest("OS does not permit creating this synthetic symlink.")
        with self.assertRaises(harness.HarnessError):
            harness.source_file(self.root, "linked.py")

    def test_passed_checks_reuse_only_unchanged_inputs(self):
        self.plan(); first = self.run_regression(); second = self.run_regression()
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        (self.root / "check.py").write_text("print('different-synthetic-output')\n", encoding="utf-8")
        third = self.run_regression()
        self.assertFalse(third["reused"])
        self.assertNotEqual(first["validation_key"], third["validation_key"])

    def test_inputs_changed_during_execution_are_not_certified(self):
        self.plan()
        def change_during_check(*_):
            (self.root / "check.py").write_text("print('changed-during-check')\n", encoding="utf-8")
            return {"outcome": "passed", "returncode": 0}
        with patch.object(harness, "execute", side_effect=change_during_check):
            result = self.run_regression()
        self.assertEqual(result["outcome"], "inputs_changed")
        self.assertFalse(harness.status(self.state)["checks"]["regression"])

    def test_runtime_change_invalidates_passed_check_reuse(self):
        self.plan(); original = self.run_regression()
        different = {**harness.runtime(), "version": "synthetic-test-runtime"}
        with patch.object(harness, "runtime", return_value=different):
            changed = self.run_regression()
        self.assertFalse(changed["reused"])
        self.assertNotEqual(original["validation_key"], changed["validation_key"])

    def test_receipt_has_no_raw_subprocess_output(self):
        self.plan(); receipt = self.run_regression()
        saved = (self.state / "operation.json").read_text(encoding="utf-8")
        self.assertNotIn("synthetic-only-output", saved)
        self.assertGreater(receipt["stdout"]["bytes"], 0)
        self.assertRegex(receipt["stdout"]["sha256"], r"^[a-f0-9]{64}$")
        self.assertNotIn("BLUEPRINT_BACKEND_PASSWORD", receipt["runtime"])

    def test_failure_and_timeout_are_not_passed(self):
        (self.root / "check.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
        self.plan(); result = self.run_regression()
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["returncode"], 3)
        (self.root / "check.py").write_text("import time; time.sleep(5)\n", encoding="utf-8")
        # Keep this test short; the independent timeout policy is fixed before its new plan.
        self.adapter["checks"]["regression"]["timeout_seconds"] = 0.1
        self.write_config()
        other = self.base / "timeout-state"
        harness.plan_operation("adapter.json", "case.json", other, "timeout-operation", root=self.root)
        result = harness.run_check(other, "regression")
        self.assertEqual(result["outcome"], "timeout")

    def test_unknown_outcome_requires_operator_acknowledgement(self):
        self.plan()
        path = self.state / "operation.json"
        value = harness.load_json(path)
        value["checks"]["regression"] = {"outcome": "running"}
        harness.atomic_json(path, value)
        with self.assertRaises(harness.HarnessError):
            self.run_regression()
        result = harness.run_check(self.state, "regression", acknowledge_unknown=True)
        self.assertEqual(result["outcome"], "passed")

    def test_missing_or_failed_browser_evidence_blocks_bundle(self):
        self.plan(); self.run_regression()
        with self.assertRaises(harness.HarnessError):
            harness.bundle(self.state, self.base / "public.zip")
        target, _ = self.client_evidence(outcome="failed")
        harness.attest(self.state, "browser_form_regression", target)
        with self.assertRaises(harness.HarnessError):
            harness.bundle(self.state, self.base / "public.zip")

    def test_attestation_is_explicit_external_and_idempotent(self):
        receipt = self.ready()
        self.assertEqual(receipt["execution"], "external_client_attestation")
        self.assertEqual(receipt["production_status"], "not_verified")
        target, _ = self.client_evidence()
        self.assertTrue(harness.attest(self.state, "browser_form_regression", target)["reused"])
        self.assertEqual(harness.status(self.state)["local_status"], "handoff_ready")

    def test_bad_or_tampered_evidence_is_rejected(self):
        self.plan(); self.run_regression()
        target, payload = self.client_evidence()
        payload["checks"]["fixed_login_passed"] = False
        target.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(harness.HarnessError):
            harness.attest(self.state, "browser_form_regression", target)
        target, payload = self.client_evidence()
        harness.attest(self.state, "browser_form_regression", target)
        Path(payload["evidence_files"][0]["path"]).write_text("changed", encoding="utf-8")
        self.assertFalse(harness.status(self.state)["attestations"]["browser_form_regression"])

    def test_config_mutation_and_omitted_discovery_dependency_fail(self):
        self.plan()
        self.adapter["checks"]["regression"]["argv"] = ["$PYTHON", "other.py"]
        self.write_config()
        with self.assertRaises(harness.HarnessError):
            self.run_regression()
        self.adapter["checks"]["regression"]["argv"] = ["$PYTHON", "check.py"]
        self.write_config()
        (self.root / "tests").mkdir()
        (self.root / "tests/test_omitted.py").write_text("# synthetic omitted dependency\n", encoding="utf-8")
        with self.assertRaises(harness.HarnessError):
            self.run_regression()

    def test_bundle_allowlist_manifest_and_no_private_evidence(self):
        self.ready()
        target = self.base / "public.zip"
        first = harness.bundle(self.state, target)
        second = harness.bundle(self.state, target)
        self.assertTrue(second["reused"])
        self.assertEqual(first["sha256"], second["sha256"])
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            self.assertIn("MANIFEST.sha256", names)
            self.assertIn("HANDOFF_STATUS.json", names)
            self.assertNotIn("operation.json", names)
            self.assertNotIn("synthetic-browser-proof.txt", names)
            public_status = json.loads(archive.read("HANDOFF_STATUS.json"))
            self.assertFalse(public_status["private_evidence_included"])
            self.assertEqual(public_status["candidate_status"], "candidate_not_submitted")
        target.write_bytes(b"unknown previous artifact")
        with self.assertRaises(harness.HarnessError):
            harness.bundle(self.state, target)
        self.assertEqual(target.read_bytes(), b"unknown previous artifact")

    def test_bundle_rejects_private_text_even_on_allowlist(self):
        private_marker = "C:" + chr(92) + "Users" + chr(92) + "synthetic-person" + chr(92) + "private.txt"
        (self.root / "check.py").write_text("# " + private_marker + "\nprint('synthetic')\n", encoding="utf-8")
        self.ready()
        with self.assertRaises(harness.HarnessError):
            harness.bundle(self.state, self.base / "public.zip")

    def test_bundle_rechecks_changed_input_and_runtime_file_names(self):
        self.ready()
        (self.root / "check.py").write_text("print('changed')\n", encoding="utf-8")
        with self.assertRaises(harness.HarnessError):
            harness.bundle(self.state, self.base / "public.zip")
        with self.assertRaises(harness.HarnessError):
            harness.public_file(self.root, "../outside.json")

    def test_docs_only_change_invalidates_checks_and_bundle(self):
        (self.root / "README.md").write_text("Synthetic reviewed instructions.\n", encoding="utf-8")
        self.adapter["bundle_files"].append("README.md")
        self.adapter["checks"]["regression"]["inputs"].append("README.md")
        self.write_config(); self.ready()
        (self.root / "README.md").write_text("Changed instructions need review.\n", encoding="utf-8")
        self.assertFalse(harness.status(self.state)["checks"]["regression"])
        with self.assertRaises(harness.HarnessError):
            harness.bundle(self.state, self.base / "public.zip")

    def test_bundle_only_file_cannot_be_omitted_from_check_scope(self):
        (self.root / "README.md").write_text("Synthetic instructions.\n", encoding="utf-8")
        self.adapter["bundle_files"].append("README.md")
        self.write_config(); self.plan()
        with self.assertRaises(harness.HarnessError):
            self.run_regression()

    def test_external_project_cannot_store_evidence_in_shared_engine(self):
        engine = self.base / "shared-engine"
        engine.mkdir()
        with patch.object(harness, "ROOT", engine):
            with self.assertRaises(harness.HarnessError):
                harness.outside_all_sources(engine / "private-proof.json", self.root)

    def test_bundle_captured_bytes_must_match_tested_source(self):
        self.ready()
        original = harness.public_file
        def substituted(root, name):
            value = original(root, name)
            return value + b"# untested bytes\n" if name == "check.py" else value
        with patch.object(harness, "public_file", side_effect=substituted):
            with self.assertRaises(harness.HarnessError):
                harness.bundle(self.state, self.base / "public.zip")
        self.assertFalse((self.base / "public.zip").exists())

    def test_cli_inputs_changed_returns_nonzero(self):
        self.plan()
        def change_during_check(*_):
            (self.root / "check.py").write_text("print('changed-by-synthetic-check')\n", encoding="utf-8")
            return {"outcome": "passed", "returncode": 0}
        output = io.StringIO()
        with patch.object(harness, "execute", side_effect=change_during_check), redirect_stdout(output):
            result = harness.main(["run", "--state", str(self.state), "--check", "regression"])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue())["outcome"], "inputs_changed")

    def test_cli_future_nonpassed_outcome_is_not_success(self):
        with patch.object(harness, "run_check", return_value={"outcome": "synthetic_future_failure"}), redirect_stdout(io.StringIO()):
            self.assertEqual(harness.main(["run", "--state", str(self.state), "--check", "regression"]), 1)
        with patch.object(harness, "status", return_value={"local_status": "checks_or_evidence_pending"}), redirect_stdout(io.StringIO()):
            self.assertEqual(harness.main(["status", "--state", str(self.state)]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
