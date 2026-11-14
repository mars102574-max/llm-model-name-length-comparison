"""Pure length rules, independent of input sources and translations."""

import unicodedata

from .errors import BenchmarkError

BENCHMARK_ID = "llm-model-name-length-comparison"
DESCRIPTION_KEY = "description_length"
DEFAULT_RULES = {"spaces": "ascii", "normalization": "none", "count": "codepoints"}


def add_arguments(parser, tr):
    parser.add_argument("--spaces", choices=("ascii", "all", "none"), help=tr("spaces"))
    parser.add_argument("--normalization", choices=("none", "NFC", "NFKC"), help=tr("normalization"))
    parser.add_argument("--count", choices=("codepoints", "utf8-bytes"), help=tr("count"))


def overrides(args):
    return {key: getattr(args, key) for key in DEFAULT_RULES if getattr(args, key) is not None}


def validate_rules(values):
    unknown = set(values) - set(DEFAULT_RULES)
    if unknown:
        raise BenchmarkError("unknown_rules", keys=", ".join(sorted(unknown)))
    rules = {**DEFAULT_RULES, **values}
    if rules["spaces"] not in ("ascii", "all", "none"):
        raise BenchmarkError("invalid_spaces")
    if rules["normalization"] not in ("none", "NFC", "NFKC"):
        raise BenchmarkError("invalid_normalization")
    if rules["count"] not in ("codepoints", "utf8-bytes"):
        raise BenchmarkError("invalid_count")
    return rules


def measure(name, rules):
    normalized = name if rules["normalization"] == "none" else unicodedata.normalize(rules["normalization"], name)
    if rules["spaces"] == "ascii":
        counted = normalized.replace(" ", "")
    elif rules["spaces"] == "all":
        counted = "".join(char for char in normalized if not char.isspace())
    else:
        counted = normalized
    codepoints = len(counted)
    byte_count = len(counted.encode("utf-8"))
    return {
        "score": codepoints if rules["count"] == "codepoints" else byte_count,
        "unit": "characters" if rules["count"] == "codepoints" else "bytes",
        "details": {
            "normalized_name": normalized,
            "counted_name": counted,
            "removed_spaces": len(normalized) - codepoints,
            "codepoints": codepoints,
            "utf8_bytes": byte_count,
        },
    }


def render_score(result, rules, tr):
    text = tr("score_length", score=result["score"])
    return text + (tr("bytes") if result["unit"] == "bytes" else "")


def score(api_name: str) -> int:
    """Compatibility helper: original code-point count, excluding U+0020."""
    return len(api_name.replace(" ", ""))
