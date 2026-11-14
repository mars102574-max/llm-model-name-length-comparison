import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from name_length_benchmark.core import score


class RuleTests(unittest.TestCase):
    def evaluate(self, *args):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "benchmark.py"), *args, "--format", "json"], capture_output=True, text=True)
        return result.returncode, json.loads(result.stdout)

    def test_original_unicode_codepoint_counts(self):
        expected = {"model-agi": 9, "model v1.0": 9, "模型-v1": 5, "模型-🚀": 4, "e\u0301": 2}
        for name, value in expected.items():
            self.assertEqual(score(name), value)

    def test_unicode_normalization_is_explicit(self):
        _, original = self.evaluate("e\u0301")
        _, normalized = self.evaluate("e\u0301", "--normalization", "NFC")
        self.assertEqual(original["results"][0]["score"], 2)
        self.assertEqual(normalized["results"][0]["score"], 1)
        self.assertEqual(normalized["rules"]["profile"], "custom")

    def test_space_policies(self):
        for option, expected in [("ascii", 3), ("all", 2), ("none", 4)]:
            code, report = self.evaluate("a \u00a0b", "--spaces", option)
            self.assertEqual(code, 0)
            self.assertEqual(report["results"][0]["score"], expected)

    def test_bytes_and_characters_are_distinguished(self):
        code, report = self.evaluate("模型", "--count", "utf8-bytes")
        self.assertEqual(code, 0)
        self.assertEqual(report["results"][0]["score"], 6)
        self.assertEqual(report["results"][0]["unit"], "bytes")
        self.assertEqual(report["results"][0]["details"]["codepoints"], 2)

    def test_packaged_config_and_cli_precedence(self):
        code, report = self.evaluate("e\u0301", "--config", str(ROOT / "examples" / "rules.json"), "--normalization", "none", "--spaces", "ascii")
        self.assertEqual(code, 0)
        self.assertEqual(report["rules"]["profile"], "standard")
        self.assertEqual(report["results"][0]["score"], 2)


if __name__ == "__main__":
    unittest.main()
