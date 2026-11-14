# LLM Model Name Length Comparison

[简体中文](README.md)

Measure the length of API request names. A command-line tool with batches, configurable rules and Chinese/English messages. Version **0.3.0**.

## Quick start

Requires **Python 3.10+**. Runtime uses only the standard library. From this project's directory:

```console
python3 benchmark.py "model-agi" --lang en
LLM Model Name Length: 9
```

Without names, a terminal prompts for one name. Piped input is read as a batch. The default language is Chinese (`--lang zh`); use `--lang en` for English help, prompts, errors and results. Language does not depend on the OS locale.

## Consistent input

Quote each request name in command examples. The shell removes command-line quotes. Interactive input, text rows and JSON string values also support a single matching pair of ASCII single or double outer quotes.

The input policy strips outer whitespace, unwraps one matching quote pair and strips whitespace inside that wrapper. Empty names, unmatched/nested outer quotes, controls, invisible format characters and surrogate code points are rejected. The complete remaining name, including its prefix, version and suffix, is evaluated.

`model-agi`, `"model-agi"` and `'model-agi'` therefore evaluate the same name. JSON preserves both original `input` and evaluated `name`. Put names starting with `-` after the end-of-options marker:

```bash
python3 benchmark.py --lang en -- "-model-agi"
```

## Batches and JSON

```bash
python3 benchmark.py "model-a" "model-gi" "model-agi" --lang en
python3 benchmark.py --input examples/models.txt --lang en
python3 benchmark.py --input examples/models.json --format json --lang en
python3 benchmark.py --input - --input-format json --format json --lang en
```

Text input contains one name per line. Blank lines are skipped; UTF-8 BOM and Windows line endings are supported. JSON input is an array of strings. Use positional names or `--input`, not both. Order and duplicates are preserved. Invalid records get individual errors while valid records continue to be evaluated.

Text or JSON results go to stdout. Text errors and interactive prompts go to stderr; JSON errors remain in the JSON document. Evaluation results are not saved automatically; shell redirection can save stdout. Logs are explicitly enabled with `--log-dir`.

## Rules

The standard rules exclude ASCII space U+0020 and count Unicode code points, without Unicode normalization. Hyphens, digits, dots, prefixes, versions and suffixes count. `model-agi` scores 9, `模型-v1` scores 5, and `e` followed by a combining accent scores 2.

| Option / configuration key | Default | Values |
| --- | --- | --- |
| `--spaces` / `spaces` | `ascii` | `ascii`: exclude U+0020; `all`: exclude all Unicode whitespace; `none`: keep spaces |
| `--normalization` / `normalization` | `none` | `none`, `NFC`, `NFKC` |
| `--count` / `count` | `codepoints` | `codepoints` or `utf8-bytes` |

```bash
python3 benchmark.py "model v1.0" --spaces none --lang en
python3 benchmark.py "模型-v1" --count utf8-bytes --lang en
```

Outer whitespace and quote pairs are input wrappers, removed before scoring. `--spaces none` preserves internal spaces. Controls such as internal tabs are always rejected; `--spaces all` does not weaken validation. JSON includes the counted string, removed spaces, code points and UTF-8 byte count.

## Configuration

```bash
python3 benchmark.py "model-gi" --config examples/rules.json --format json --lang en
```

The shape is `{"schema_version":"1.0","benchmark":"llm-model-name-length-comparison","rules":{...}}`. `schema_version` is optional and defaults to `1.0`. The file must be valid on its own; explicit command-line rules then override it, with defaults filling any remaining fields. Unknown fields, duplicate JSON keys and invalid types are rejected.

Non-default rules are marked `[custom rules]` in text and `rules.profile: "custom"` in JSON. The standard profile is `standard`.

## Results and reproducibility

JSON records the schema version, benchmark ID, software version, language, effective rules, versioned input policy, Python/Unicode runtime versions, batch summary and per-record inputs, names, scores, units, details or stable error codes.

Identical inputs, rules, software and runtime produce identical JSON; no timestamps or randomness are added. Language changes messages, not scores, rules or error codes. Unicode behavior can differ across runtimes, which is why runtime versions are recorded.

Limits: 4096 code points per name, 10000 names per batch, 8 MiB per input file, 64 KiB per configuration file. File decoding, malformed JSON and conflicting input sources are fatal to the batch.

| Exit code | Meaning |
| --- | --- |
| `0` | All records succeeded |
| `1` | At least one invalid record, or failed integrity verification |
| `2` | Invalid arguments/configuration, file-reading errors or log-writing errors |
| `130` | Interrupted |

## Two-layer hash verification (0.3.0)

Every JSON evaluation result automatically contains an `integrity` envelope, verified immediately after generation. Use `--hash` to display both digests with text output:

```bash
python3 benchmark.py "model-agi" --hash --lang en
python3 benchmark.py "model-agi" --format json --lang en > result.json
python3 benchmark.py --verify "result.json" --lang en
python3 benchmark.py --verify "result.json" --lang en --format json
```

1. `payload_sha256` hashes the canonical JSON bytes of the result, excluding its top-level `integrity` field. Inputs, scores, rules, language and software/runtime versions are all covered.
2. `hash_sha256` hashes the **64 lowercase hexadecimal ASCII characters** of the first digest, rather than its 32 raw bytes.

The versioned encoding `json-sorted-ascii-v1` uses Python `json.dumps(sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)`, then UTF-8. This is not RFC 8785/JCS. The protocol and Python version are recorded. Object key ordering and display indentation do not affect verification; array ordering, numeric representations and content changes can.

`--verify` checks both layers, integrity metadata and the benchmark identity. It verifies content consistency, without rerunning scoring. Changing translated messages changes the document hash, even though scores stay the same. These are unsigned checksums: changing the content and recomputing every checksum will pass. A second layer does not replace a trusted signature or an externally retained checksum.

## Execution logs and logs of logs (0.3.0)

Enable logging explicitly with `--log-dir`. Each invocation creates a separate directory named with a timestamp and random run ID:

```bash
python3 benchmark.py "model-a" "model-agi" --hash --log-dir "logs" --lang en
python3 benchmark.py --verify-logs "logs/RUN_ID" --lang en
python3 benchmark.py --verify-logs "logs/RUN_ID" --lang en --format json
```

Replace `RUN_ID` with the directory name reported on stderr at the end of the run.

| File | Contents |
| --- | --- |
| `execution.jsonl` | Run start, resolved rules, input count, each evaluated record or error, result integrity check and run completion |
| `audit.jsonl` | One receipt per successful execution-log write: sequence, event, byte count and SHA-256 of the written line |
| `manifest.json` | Completion marker with both log files' record/byte counts and two-layer checksums, run outcome and result digest; also protected by its own two hash layers |

Each execution-log write is flushed, fsynced and read back before its audit receipt is written. Every record in both streams carries two hashes and the previous line's SHA-256, creating two ordered chains. Audit writes do not recursively create a third log stream.

`--verify-logs` checks the completion manifest, file hashes, record hashes, sequences, chain links, one-to-one audit correspondence, and start/end records. This detects content changes without updated checksums, reordered/deleted records and missing audit receipts. **Valid logs do not imply a successful original evaluation**: `outcome` and `original_exit_code` report the original run status.

Write or read-back failures stop the run with exit code 2 rather than publishing a normal success result. Logging failures are not logged recursively. Incomplete directories can remain on disk and cannot pass verification without a valid completion manifest. Logging starts only after successful argument parsing; help, argument-parsing errors and verification operations do not produce logs. Each stream is limited to 128 MiB.

Logs contain request names, rules, errors, scores, UTC timestamps and a run ID. Dynamic logging fields stay outside the result document, so enabling logs does not change deterministic results or their hashes. Logs stay local. Results go to stdout; the log location is reported on stderr.

Verification can be combined only with `--lang`, `--format` and `--hash`, not evaluation inputs, rules or `--log-dir`. Exit codes: 0 for a verified document/bundle, 1 for integrity/consistency failure, 2 for argument or file-reading errors.

## Installation and development

Run the script directly, or install the console command in your own virtual environment:

```bash
python3 -m pip install .
name-length "model-agi" --lang en
python3 -m name_length_benchmark "model-agi" --lang en
python3 -m unittest discover -s tests -v
```

Building uses setuptools; there are no third-party runtime dependencies. Each project includes all its code and runs independently. Modules separate scoring (`core.py`), input/configuration (`inputs.py`), translation (`i18n.py`) the command line (`cli.py`), two-layer integrity (`integrity.py`) and execution/audit logging (`execution_log.py`). Tests check externally observable behavior. To add a language, add its catalog, update the `--lang` support lists and verify matching translation keys/placeholders.

Version 0.2.0 introduces input normalization and Chinese default result labels. The importable `benchmark.score(name)` compatibility helper retains the original default scoring on the unmodified string; CLI input first follows the normalization policy above.

## License

This project is licensed under the [MIT License](LICENSE).
