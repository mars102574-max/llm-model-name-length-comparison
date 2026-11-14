"""Tamper detection, audit correspondence, and failure behavior through the CLI."""

import hashlib
import importlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "name_length_benchmark"
sys.path.insert(0, str(ROOT))
integrity = importlib.import_module(PACKAGE + ".integrity")
logging_module = importlib.import_module(PACKAGE + ".execution_log")
cli = importlib.import_module(PACKAGE + ".cli")


class IntegrityLogTests(unittest.TestCase):
    def run_cli(self, *args, cwd=None):
        completed = subprocess.run(
            [sys.executable, "-B", str(ROOT / "benchmark.py"), *args],
            input="", capture_output=True, text=True, encoding="utf-8", cwd=cwd or ROOT,
        )
        self.assertNotIn("Traceback", completed.stderr)
        return completed

    def result(self, *args):
        completed = self.run_cli(*args, "--format", "json", "--lang", "en")
        return completed, json.loads(completed.stdout)

    def save_result(self, directory):
        completed, document = self.result("model-agi")
        self.assertEqual(completed.returncode, 0)
        path = Path(directory) / "result.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path, document

    def logged_run(self, directory, *names):
        base = Path(directory) / "logs"
        completed, document = self.result(*(names or ("model-agi",)), "--log-dir", str(base))
        bundle = next(base.iterdir())
        return completed, document, bundle

    def rewrite_manifest_hashes(self, bundle):
        path = bundle / "manifest.json"
        manifest = json.loads(path.read_text())
        for filename, meta in manifest["files"].items():
            data = (bundle / filename).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            meta.update(bytes=len(data), records=len(data.splitlines()), sha256=digest,
                        hash_sha256=hashlib.sha256(digest.encode("ascii")).hexdigest())
        del manifest["integrity"]
        path.write_bytes(integrity.canonical_bytes(integrity.seal_document(manifest)) + b"\n")

    def test_result_hashes_match_independent_sha256_calculation(self):
        _, report = self.result("model-agi", "模型-v1")
        metadata = report.pop("integrity")
        canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
        first = hashlib.sha256(canonical).hexdigest()
        self.assertEqual(metadata["payload_sha256"], first)
        self.assertEqual(metadata["hash_sha256"], hashlib.sha256(first.encode("ascii")).hexdigest())

    def test_result_verification_accepts_formatting_and_key_order_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            path, report = self.save_result(folder)
            path.write_text(json.dumps(dict(reversed(list(report.items()))), ensure_ascii=False, indent=4), encoding="utf-8")
            completed, verified = self.result("--verify", str(path))
            self.assertEqual(completed.returncode, 0)
            self.assertTrue(verified["verification"]["valid"])
            integrity.verify_document(verified)

    def test_changed_score_fails_content_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            path, report = self.save_result(folder)
            report["results"][0]["score"] += 1
            path.write_text(json.dumps(report), encoding="utf-8")
            completed, verified = self.result("--verify", str(path))
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(verified["error"]["code"], "payload_hash_mismatch")

    def test_changed_digest_and_second_digest_are_detected(self):
        for field in ("payload_sha256", "hash_sha256"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as folder:
                path, report = self.save_result(folder)
                report["integrity"][field] = "0" * 64
                path.write_text(json.dumps(report), encoding="utf-8")
                completed, verified = self.result("--verify", str(path))
                self.assertEqual(completed.returncode, 1)
                self.assertEqual(verified["error"]["code"], "hash_hash_mismatch")

    def test_missing_or_unsupported_integrity_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            path, report = self.save_result(folder)
            report["integrity"]["canonicalization"] = "unknown-v9"
            path.write_text(json.dumps(report), encoding="utf-8")
            completed, verified = self.result("--verify", str(path))
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(verified["error"]["code"], "invalid_integrity")
            del report["integrity"]
            path.write_text(json.dumps(report), encoding="utf-8")
            self.assertEqual(self.result("--verify", str(path))[0].returncode, 1)

    def test_hash_display_and_default_stdout(self):
        normal = self.run_cli("model-agi", "--lang", "en")
        shown = self.run_cli("model-agi", "--hash", "--lang", "en")
        self.assertEqual(len(normal.stdout.splitlines()), 1)
        self.assertEqual(len(shown.stdout.splitlines()), 3)
        self.assertIn("Result SHA-256: ", shown.stdout)
        self.assertIn("Hash SHA-256: ", shown.stdout)

    def test_default_does_not_create_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.run_cli("model-agi", cwd=folder).returncode, 0)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_log_bundle_receipts_and_report_link(self):
        with tempfile.TemporaryDirectory() as folder:
            completed, report, bundle = self.logged_run(folder, "model-a", "model-agi")
            self.assertEqual(completed.returncode, 0)
            self.assertIn(str(bundle), completed.stderr)
            self.assertEqual({p.name for p in bundle.iterdir()}, {"execution.jsonl", "audit.jsonl", "manifest.json"})
            manifest = json.loads((bundle / "manifest.json").read_text())
            integrity.verify_document(manifest)
            self.assertEqual(manifest["report_sha256"], report["integrity"]["payload_sha256"])
            execution = (bundle / "execution.jsonl").read_bytes().splitlines(keepends=True)
            audit = [json.loads(line) for line in (bundle / "audit.jsonl").read_bytes().splitlines()]
            self.assertEqual(len(execution), 7)
            self.assertEqual(len(execution), len(audit))
            for line, receipt in zip(execution, audit):
                integrity.verify_document(receipt)
                self.assertEqual(receipt["data"]["line_sha256"], hashlib.sha256(line).hexdigest())
                self.assertEqual(receipt["data"]["bytes_written"], len(line))
            verified, document = self.result("--verify-logs", str(bundle))
            self.assertEqual(verified.returncode, 0)
            self.assertEqual(document["verification"]["execution_records"], 7)

    def test_logging_does_not_change_deterministic_result(self):
        with tempfile.TemporaryDirectory() as folder:
            _, normal = self.result("model-agi")
            _, logged, _ = self.logged_run(folder)
            self.assertEqual(normal, logged)

    def test_each_run_uses_a_new_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder) / "logs"
            for _ in range(2):
                completed, _ = self.result("model-agi", "--log-dir", str(base))
                self.assertEqual(completed.returncode, 0)
            bundles = list(base.iterdir())
            self.assertEqual(len(bundles), 2)
            for bundle in bundles:
                self.assertEqual(self.result("--verify-logs", str(bundle))[0].returncode, 0)

    def test_partial_failure_is_recorded_but_logs_verify(self):
        with tempfile.TemporaryDirectory() as folder:
            completed, _, bundle = self.logged_run(folder, "model-agi", "")
            self.assertEqual(completed.returncode, 1)
            verified, document = self.result("--verify-logs", str(bundle))
            self.assertEqual(verified.returncode, 0)
            self.assertEqual(document["verification"]["outcome"], "partial")
            self.assertEqual(document["verification"]["original_exit_code"], 1)

    def test_fatal_input_failure_is_logged(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder) / "logs"
            completed, _ = self.result("--input", str(Path(folder) / "missing.txt"), "--log-dir", str(base))
            self.assertEqual(completed.returncode, 2)
            verified, document = self.result("--verify-logs", str(next(base.iterdir())))
            self.assertEqual(verified.returncode, 0)
            self.assertEqual(document["verification"]["outcome"], "failed")

    def test_log_content_changes_fail_file_hash(self):
        for filename in ("execution.jsonl", "audit.jsonl"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as folder:
                _, _, bundle = self.logged_run(folder)
                path = bundle / filename
                path.write_bytes(path.read_bytes() + b"\n")
                verified, document = self.result("--verify-logs", str(bundle))
                self.assertEqual(verified.returncode, 1)
                self.assertEqual(document["error"]["code"], "payload_hash_mismatch")

    def test_log_reordering_is_detected_even_with_updated_file_checksums(self):
        with tempfile.TemporaryDirectory() as folder:
            _, _, bundle = self.logged_run(folder)
            path = bundle / "execution.jsonl"
            lines = path.read_bytes().splitlines(keepends=True)
            lines[1], lines[2] = lines[2], lines[1]
            path.write_bytes(b"".join(lines))
            self.rewrite_manifest_hashes(bundle)
            verified, document = self.result("--verify-logs", str(bundle))
            self.assertEqual(verified.returncode, 1)
            self.assertEqual(document["error"]["code"], "log_chain_mismatch")

    def test_audit_receipt_mismatch_is_detected_independently(self):
        with tempfile.TemporaryDirectory() as folder:
            _, _, bundle = self.logged_run(folder)
            path = bundle / "audit.jsonl"
            lines = path.read_bytes().splitlines(keepends=True)
            last = json.loads(lines[-1])
            last["data"]["bytes_written"] += 1
            del last["integrity"]
            lines[-1] = integrity.canonical_bytes(integrity.seal_document(last)) + b"\n"
            path.write_bytes(b"".join(lines))
            self.rewrite_manifest_hashes(bundle)
            verified, document = self.result("--verify-logs", str(bundle))
            self.assertEqual(verified.returncode, 1)
            self.assertEqual(document["error"]["code"], "log_audit_mismatch")

    def test_missing_audit_tail_is_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            _, _, bundle = self.logged_run(folder)
            path = bundle / "audit.jsonl"
            path.write_bytes(b"".join(path.read_bytes().splitlines(keepends=True)[:-1]))
            self.rewrite_manifest_hashes(bundle)
            verified, document = self.result("--verify-logs", str(bundle))
            self.assertEqual(verified.returncode, 1)
            self.assertEqual(document["error"]["code"], "log_audit_mismatch")

    def test_both_log_tails_removed_cannot_look_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            _, _, bundle = self.logged_run(folder)
            for filename in ("execution.jsonl", "audit.jsonl"):
                path = bundle / filename
                path.write_bytes(b"".join(path.read_bytes().splitlines(keepends=True)[:-1]))
            self.rewrite_manifest_hashes(bundle)
            verified, document = self.result("--verify-logs", str(bundle))
            self.assertEqual(verified.returncode, 1)
            self.assertEqual(document["error"]["code"], "invalid_log_bundle")

    def test_audit_write_failure_is_fatal_and_does_not_recurse(self):
        append = logging_module.RunLogger._append
        calls = []

        def failing_append(logger, stream, event, data):
            calls.append((stream, event))
            if stream == "audit" and data.get("execution_event") == "record_evaluated":
                raise OSError("simulated audit disk full")
            return append(logger, stream, event, data)

        with tempfile.TemporaryDirectory() as folder:
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(logging_module.RunLogger, "_append", failing_append), redirect_stdout(output), redirect_stderr(errors):
                code = cli.main(["model-agi", "--log-dir", folder, "--format", "json", "--lang", "en"])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())["error"]["code"], "log_io")
            self.assertEqual(errors.getvalue(), "")
            self.assertEqual(len(calls), 8)
            self.assertFalse((next(Path(folder).iterdir()) / "manifest.json").exists())

    def test_log_directory_failure_returns_structured_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "regular-file"
            path.write_text("keep me")
            completed, document = self.result("model-agi", "--log-dir", str(path))
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(document["error"]["code"], "log_io")
            self.assertEqual(path.read_text(), "keep me")

    def test_interruption_is_recorded_with_its_original_exit_status(self):
        with tempfile.TemporaryDirectory() as folder:
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(cli, "normalize_name", side_effect=KeyboardInterrupt), redirect_stdout(output), redirect_stderr(errors):
                code = cli.main(["model-agi", "--log-dir", folder, "--format", "json"])
            self.assertEqual(code, 130)
            self.assertEqual(json.loads(output.getvalue())["error"]["code"], "interrupted")
            verified, document = self.result("--verify-logs", str(next(Path(folder).iterdir())))
            self.assertEqual(verified.returncode, 0)
            self.assertEqual(document["verification"]["outcome"], "interrupted")
            self.assertEqual(document["verification"]["original_exit_code"], 130)

    def test_incomplete_bundle_without_manifest_cannot_verify(self):
        with tempfile.TemporaryDirectory() as folder:
            _, _, bundle = self.logged_run(folder)
            (bundle / "manifest.json").unlink()
            verified, document = self.result("--verify-logs", str(bundle))
            self.assertEqual(verified.returncode, 2)
            self.assertEqual(document["error"]["code"], "read_error")

    def test_verification_rejects_evaluation_options(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.save_result(folder)
            completed, document = self.result("model-agi", "--verify", str(path))
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(document["error"]["code"], "verification_conflict")

    def test_new_verification_messages_are_localized(self):
        with tempfile.TemporaryDirectory() as folder:
            path, _ = self.save_result(folder)
            for language, expected in (("zh", "结果校验通过"), ("en", "Result verification passed")):
                completed = self.run_cli("--verify", str(path), "--lang", language)
                self.assertEqual(completed.returncode, 0)
                self.assertIn(expected, completed.stdout)


if __name__ == "__main__":
    unittest.main()
