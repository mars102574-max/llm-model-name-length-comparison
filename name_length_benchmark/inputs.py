"""Identical normalization for arguments, interactive input, files and pipes."""

import json
import math
import sys
import unicodedata
from pathlib import Path

from .errors import BenchmarkError

MAX_NAME_LENGTH = 4096
MAX_BATCH_SIZE = 10_000
MAX_INPUT_BYTES = 8 * 1024 * 1024
INPUT_POLICY = {
    "version": "1.0",
    "trim_outer_whitespace": True,
    "unwrap_one_quote_pair": True,
    "reject_unicode_categories": ["Cc", "Cf", "Cs"],
    "max_name_codepoints": MAX_NAME_LENGTH,
}


def normalize_name(raw: object) -> str:
    if not isinstance(raw, str):
        raise BenchmarkError("name_type")
    name = raw.strip()
    if name and (name[0] in "\"'" or name[-1] in "\"'"):
        if len(name) < 2 or name[0] != name[-1] or name[0] not in "\"'":
            raise BenchmarkError("unbalanced_quotes")
        name = name[1:-1].strip()
        if name and (name[0] in "\"'" or name[-1] in "\"'"):
            raise BenchmarkError("nested_quotes")
    if not name:
        raise BenchmarkError("empty_name")
    if len(name) > MAX_NAME_LENGTH:
        raise BenchmarkError("name_too_long", limit=MAX_NAME_LENGTH)
    for char in name:
        if unicodedata.category(char) in ("Cc", "Cf", "Cs"):
            raise BenchmarkError("control_character", codepoint=f"U+{ord(char):04X}")
    return name


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkError("duplicate_key", key=key)
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(value)


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(value)
    return number


def read_json(text: str, source: str):
    try:
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant, parse_float=_finite_float)
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, BenchmarkError):
            raise
        raise BenchmarkError("invalid_json", path=source) from exc


def read_utf8(path: str, limit: int = MAX_INPUT_BYTES) -> str:
    try:
        if path == "-":
            if hasattr(sys.stdin, "buffer"):
                data = sys.stdin.buffer.read(limit + 1)
            else:
                data = sys.stdin.read(limit + 1).encode("utf-8")
        else:
            with Path(path).open("rb") as handle:
                data = handle.read(limit + 1)
        if len(data) > limit:
            raise BenchmarkError("file_too_large", path=path, limit=limit)
        return data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise BenchmarkError("invalid_utf8", path=path) from exc
    except OSError as exc:
        raise BenchmarkError("read_error", path=path, reason=str(exc)) from exc


def read_names(path: str, input_format: str) -> list:
    text = read_utf8(path)
    if input_format == "auto":
        input_format = "json" if path.lower().endswith(".json") or text.lstrip().startswith("[") else "text"
    if input_format == "json":
        names = read_json(text, path)
        if not isinstance(names, list):
            raise BenchmarkError("json_array")
    else:
        names = [line for line in text.split("\n") if line.strip()]
    return names


def validate_batch(names: list) -> None:
    if not names:
        raise BenchmarkError("empty_batch")
    if len(names) > MAX_BATCH_SIZE:
        raise BenchmarkError("batch_too_large", limit=MAX_BATCH_SIZE)


def read_config(path: str, benchmark: str) -> dict:
    document = read_json(read_utf8(path, limit=65536), path)
    if not isinstance(document, dict):
        raise BenchmarkError("config_object")
    unknown = set(document) - {"schema_version", "benchmark", "rules"}
    if unknown:
        raise BenchmarkError("config_keys", keys=", ".join(sorted(unknown)))
    if document.get("benchmark") != benchmark:
        raise BenchmarkError("config_benchmark", expected=benchmark)
    if document.get("schema_version", "1.0") != "1.0":
        raise BenchmarkError("config_schema")
    if not isinstance(document.get("rules"), dict):
        raise BenchmarkError("config_rules")
    return document["rules"]
