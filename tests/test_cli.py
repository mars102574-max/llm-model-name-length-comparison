"""CLI contracts: input sources, localization, invalid data, and reproducibility."""

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
BENCHMARK = "llm-model-name-length-comparison"


class CommandTests(unittest.TestCase):
    def command(self, *args, stdin=""):
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "benchmark.py"), *args],
            input=stdin, capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
        )

    def json_result(self, *args, stdin=""):
        completed = self.command(*args, "--format", "json", stdin=stdin)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertEqual(completed.stderr, "")
        return completed, json.loads(completed.stdout)

    def test_sources_and_quotes_have_identical_scores(self):
        names = ["model-a", "model-gi", "model-agi"]
        _, direct = self.json_result(*names)
        expected = [(r["name"], r["score"]) for r in direct["results"]]
        with tempfile.TemporaryDirectory() as folder:
            text_path = Path(folder) / "models.txt"
            json_path = Path(folder) / "models.json"
            text_path.write_text('\ufeff"model-a"\r\n\n\'model-gi\'\nmodel-agi\n', encoding="utf-8")
            json_path.write_text(json.dumps(names), encoding="utf-8")
            inputs = [
                (("--input", str(text_path)), ""),
                (("--input", str(json_path)), ""),
                (("--input", "-"), json.dumps(names)),
                ((), '\n'.join('"' + name + '"' for name in names)),
            ]
            for arguments, stdin in inputs:
                with self.subTest(arguments=arguments):
                    completed, report = self.json_result(*arguments, stdin=stdin)
                    self.assertEqual(completed.returncode, 0)
                    self.assertEqual([(r["name"], r["score"]) for r in report["results"]], expected)

    def test_batch_retains_duplicates_and_order(self):
        _, report = self.json_result("model-agi", "model-a", "model-agi")
        self.assertEqual([r["name"] for r in report["results"]], ["model-agi", "model-a", "model-agi"])
        self.assertEqual(report["summary"], {"total": 3, "succeeded": 3, "failed": 0})

    def test_mixed_valid_and_invalid_rows(self):
        completed, report = self.json_result("--input", "-", stdin=json.dumps(["model-agi", "", 42, "x\u200by"]))
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(report["summary"], {"total": 4, "succeeded": 1, "failed": 3})
        self.assertEqual([r["error"]["code"] for r in report["results"][1:]], ["empty_name", "name_type", "control_character"])

    def test_bad_quotes_and_control_characters(self):
        invalid = ['"model', 'model"', '\'model"', '""model""', "model\tname", "model\x1bname"]
        completed, report = self.json_result("--input", "-", stdin=json.dumps(invalid))
        self.assertEqual(completed.returncode, 1)
        self.assertTrue(all(row["status"] == "error" for row in report["results"]))

    def test_rejected_surrogate_still_produces_valid_json(self):
        completed, report = self.json_result("--input", "-", stdin='["\\ud800"]')
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(report["results"][0]["error"]["code"], "control_character")

    def test_input_size_limits(self):
        completed, report = self.json_result("x" * 4097)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(report["results"][0]["error"]["code"], "name_too_long")
        completed, report = self.json_result("--input", "-", stdin=json.dumps(["x"] * 10001))
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(report["error"]["code"], "batch_too_large")

    def test_read_and_json_failures(self):
        for stdin in ("[", "{\"name\": \"model\"}", "[NaN]", "[1e999]"):
            completed, report = self.json_result("--input", "-", "--input-format", "json", stdin=stdin)
            self.assertEqual(completed.returncode, 2)
            self.assertIn(report["error"]["code"], ("invalid_json", "json_array"))
        completed, report = self.json_result("--input", str(ROOT / "does-not-exist.txt"))
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(report["error"]["code"], "read_error")

    def test_empty_source_and_source_conflict(self):
        completed, report = self.json_result()
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(report["error"]["code"], "empty_batch")
        completed, report = self.json_result("model-agi", "--input", "-")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(report["error"]["code"], "source_conflict")

    def test_language_does_not_change_data(self):
        _, zh = self.json_result("model-agi", "", "--lang", "zh")
        _, en = self.json_result("model-agi", "", "--lang", "en")
        self.assertEqual(zh["rules"], en["rules"])
        self.assertEqual(zh["results"][0], en["results"][0])
        self.assertEqual(zh["results"][1]["error"]["code"], en["results"][1]["error"]["code"])
        self.assertNotEqual(zh["results"][1]["error"]["message"], en["results"][1]["error"]["message"])

    def test_localized_help_and_argument_errors(self):
        zh = self.command("--lang", "zh", "--help")
        en = self.command("--lang", "en", "--help")
        self.assertEqual(zh.returncode, 0)
        self.assertEqual(en.returncode, 0)
        self.assertIn("位置参数", zh.stdout)
        self.assertIn("positional arguments", en.stdout)
        for language, expected in (("zh", "需要一个参数值"), ("en", "expected one argument")):
            completed, report = self.json_result("--lang", language, "--input")
            self.assertEqual(completed.returncode, 2)
            self.assertIn(expected, report["error"]["message"])
        completed, report = self.json_result("--lang", "zh", "--unknown")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("无法识别", report["error"]["message"])

    def test_interactive_prompt_does_not_pollute_json(self):
        sys.path.insert(0, str(ROOT))
        cli = importlib.import_module(PACKAGE + ".cli")
        stdin = io.StringIO('"model-agi"\n')
        stdin.isatty = lambda: True
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.stdin", stdin), redirect_stdout(output), redirect_stderr(errors):
            code = cli.main(["--lang", "en", "--format", "json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["results"][0]["name"], "model-agi")
        self.assertEqual(errors.getvalue(), "API request name: ")

    def test_wrong_benchmark_and_duplicate_config_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rules.json"
            path.write_text(json.dumps({"benchmark": "different", "rules": {}}), encoding="utf-8")
            completed, report = self.json_result("model-agi", "--config", str(path))
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(report["error"]["code"], "config_benchmark")
            path.write_text('{"rules": {}, "rules": {}}', encoding="utf-8")
            completed, report = self.json_result("model-agi", "--config", str(path))
            self.assertEqual(report["error"]["code"], "duplicate_key")

    def test_stable_json_for_same_input_and_environment(self):
        first = self.command("model-agi", "--format", "json")
        second = self.command("model-agi", "--format", "json")
        self.assertEqual(first.stdout, second.stdout)

    def test_translation_catalogs_have_identical_keys_and_placeholders(self):
        import string
        sys.path.insert(0, str(ROOT))
        messages = importlib.import_module(PACKAGE + ".i18n").MESSAGES
        self.assertEqual(set(messages["zh"]), set(messages["en"]))
        formatter = string.Formatter()
        for key in messages["zh"]:
            fields = lambda text: {name for _, name, _, _ in formatter.parse(text) if name is not None}
            self.assertEqual(fields(messages["zh"][key]), fields(messages["en"][key]), key)


if __name__ == "__main__":
    unittest.main()
