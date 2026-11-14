"""Shared command conventions; this project runs independently of its sibling."""

import argparse
import json
import platform
import sys
import unicodedata

from . import __version__, core
from .errors import BenchmarkError
from .execution_log import MAX_LOG_BYTES, RunLogger, verify_log_bundle
from .i18n import argument_message, translator
from .inputs import INPUT_POLICY, normalize_name, read_config, read_json, read_names, read_utf8, validate_batch
from .integrity import VERIFICATION_ERRORS, seal_document, verify_document

SCHEMA_VERSION = "1.0"


class LocalizedParser(argparse.ArgumentParser):
    def __init__(self, language, tr):
        self.language = language
        self.tr = tr
        super().__init__(description=tr(core.DESCRIPTION_KEY), add_help=False, allow_abbrev=False)
        self._positionals.title = tr("positionals")
        self._optionals.title = tr("options")

    def format_help(self):
        return super().format_help().replace("usage: ", self.tr("usage"), 1)

    def error(self, message):
        raise BenchmarkError("invalid_arguments", message=argument_message(message, self.language))


def _option_hint(argv, option, allowed, default):
    value = default
    for index, arg in enumerate(argv):
        if arg == "--":
            break
        candidate = None
        if arg == option and index + 1 < len(argv):
            candidate = argv[index + 1]
        elif arg.startswith(option + "="):
            candidate = arg.split("=", 1)[1]
        if candidate in allowed:
            value = candidate
    return value


def build_parser(language):
    tr = translator(language)
    parser = LocalizedParser(language, tr)
    parser.add_argument("-h", "--help", action="help", help=tr("help"))
    parser.add_argument("--version", action="version", version=f"{core.BENCHMARK_ID} {__version__}", help=tr("version"))
    parser.add_argument("api_names", nargs="*", metavar="API_NAME", help=tr("names"))
    parser.add_argument("--lang", choices=("zh", "en"), default="zh", help=tr("language"))
    parser.add_argument("--format", choices=("text", "json"), default="text", help=tr("format"))
    parser.add_argument("--input", metavar="PATH", help=tr("input"))
    parser.add_argument("--input-format", choices=("auto", "text", "json"), default="auto", help=tr("input_format"))
    parser.add_argument("--config", metavar="PATH", help=tr("config"))
    parser.add_argument("--hash", dest="show_hash", action="store_true", help=tr("show_hash"))
    parser.add_argument("--log-dir", metavar="PATH", help=tr("log_dir"))
    verify = parser.add_mutually_exclusive_group()
    verify.add_argument("--verify", metavar="FILE", help=tr("verify"))
    verify.add_argument("--verify-logs", metavar="RUN_DIR", help=tr("verify_logs"))
    core.add_arguments(parser, tr)
    return parser


def _write_json(document):
    # Keep multilingual text readable and escape invalid surrogates in rejected inputs.
    text = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
    print(text.encode("utf-8", errors="backslashreplace").decode("utf-8"))


def _identity(language):
    return {"schema_version": SCHEMA_VERSION, "benchmark": core.BENCHMARK_ID, "version": __version__, "language": language}


def _print_hashes(report, tr):
    print(tr("hash_label", digest=report["integrity"]["payload_sha256"]))
    print(tr("hash_hash_label", digest=report["integrity"]["hash_sha256"]))


def _verification(args):
    if args.api_names or args.input is not None or args.config is not None or args.log_dir is not None or args.input_format != "auto" or core.overrides(args):
        raise BenchmarkError("verification_conflict")
    if args.verify is not None:
        report = read_json(read_utf8(args.verify, limit=MAX_LOG_BYTES), args.verify)
        integrity = verify_document(report)
        if report.get("benchmark") != core.BENCHMARK_ID:
            raise BenchmarkError("verification_benchmark", expected=core.BENCHMARK_ID)
        if not isinstance(report.get("results"), list) or not isinstance(report.get("summary"), dict):
            raise BenchmarkError("invalid_report")
        return {"target": "result", "valid": True, **integrity}
    return verify_log_bundle(args.verify_logs, core.BENCHMARK_ID)


def _finish_failed_log(logger, error, exit_code):
    if logger is not None and not logger.failed and not logger.closed:
        try:
            logger.record("run_failed", error)
            logger.finish(exit_code)
        except BenchmarkError as exc:
            return exc
    return None


def _emit_error(error, language, output_format, tr):
    if output_format == "json":
        _write_json(seal_document({**_identity(language), "error": error}))
    else:
        print(tr("error_prefix", message=error["message"]), file=sys.stderr)
        if "log_error" in error:
            print(tr("error_prefix", message=error["log_error"]["message"]), file=sys.stderr)


def run(argv):
    language = _option_hint(argv, "--lang", ("zh", "en"), "zh")
    output_format = _option_hint(argv, "--format", ("text", "json"), "text")
    tr = translator(language)
    logger = None
    try:
        args = build_parser(language).parse_intermixed_args(argv)
        if args.verify is not None or args.verify_logs is not None:
            verification = _verification(args)
            report = seal_document({**_identity(language), "verification": verification})
            if output_format == "json":
                _write_json(report)
            else:
                if verification["target"] == "result":
                    print(tr("verification_result"))
                else:
                    print(tr("verification_logs", execution=verification["execution_records"], audit=verification["audit_records"]))
                if args.show_hash:
                    _print_hashes(report, tr)
            return 0
        if args.log_dir is not None:
            logger = RunLogger(args.log_dir, core.BENCHMARK_ID, __version__)
        if args.api_names and args.input is not None:
            raise BenchmarkError("source_conflict")
        configured = read_config(args.config, core.BENCHMARK_ID) if args.config else {}
        # Validate the file itself before applying explicit command-line overrides.
        rules = core.validate_rules(configured)
        rules = core.validate_rules({**rules, **core.overrides(args)})
        if logger:
            logger.record("rules_resolved", rules)
        if args.input is not None:
            names = read_names(args.input, args.input_format)
        elif args.api_names:
            names = args.api_names
        elif not sys.stdin.isatty():
            names = read_names("-", args.input_format)
        else:
            # Prompts use stderr so stdout remains parseable, including in JSON mode.
            print(tr("prompt"), end="", flush=True, file=sys.stderr)
            try:
                names = [input()]
            except EOFError as exc:
                raise BenchmarkError("empty_name") from exc
        validate_batch(names)
        if logger:
            logger.record("inputs_loaded", {"count": len(names)})
        results = []
        for index, raw in enumerate(names, start=1):
            record = {"index": index, "input": raw}
            try:
                name = normalize_name(raw)
                record.update(name=name, status="ok", **core.measure(name, rules))
            except BenchmarkError as exc:
                record.update(status="error", error={"code": exc.code, "message": tr(exc.code, **exc.context)})
            results.append(record)
            if logger:
                logger.record("record_evaluated", record)
        failed = sum(record["status"] == "error" for record in results)
        profile = "standard" if rules == core.DEFAULT_RULES else "custom"
        report = seal_document({
            **_identity(language),
            "rules": {"profile": profile, **rules},
            "input_policy": INPUT_POLICY,
            "runtime": {"python": platform.python_version(), "unicode": unicodedata.unidata_version},
            "summary": {"total": len(results), "succeeded": len(results) - failed, "failed": failed},
            "results": results,
        })
        exit_code = 1 if failed else 0
        if logger:
            logger.record("integrity_verified", report["integrity"])
            logger.finish(exit_code, report["integrity"]["payload_sha256"])
            print(tr("logs_written", path=str(logger.directory)), file=sys.stderr)
        if output_format == "json":
            _write_json(report)
        else:
            for record in results:
                if record["status"] == "error":
                    message = tr("row_error", index=record["index"], message=record["error"]["message"])
                    print(tr("error_prefix", message=message), file=sys.stderr)
                    continue
                prefix = ""
                if len(results) > 1:
                    prefix = f'{record["index"]}. {json.dumps(record["name"], ensure_ascii=False)} — '
                suffix = tr("custom") if profile == "custom" else ""
                print(prefix + core.render_score(record, rules, tr) + suffix)
            if args.show_hash:
                _print_hashes(report, tr)
        return exit_code
    except BenchmarkError as exc:
        log_error = _finish_failed_log(logger, {"code": exc.code, "message": tr(exc.code, **exc.context)}, 2)
        if log_error is not None:
            exc = log_error
        message = tr(exc.code, **exc.context)
        _emit_error({"code": exc.code, "message": message}, language, output_format, tr)
        return 1 if exc.code in VERIFICATION_ERRORS else 2
    except KeyboardInterrupt:
        error = {"code": "interrupted", "message": tr("interrupted")}
        log_error = _finish_failed_log(logger, error, 130)
        if log_error is not None:
            error["log_error"] = {"code": log_error.code, "message": tr(log_error.code, **log_error.context)}
        _emit_error(error, language, output_format, tr)
        return 2 if log_error is not None else 130
    finally:
        if logger is not None:
            logger.close()


def main(argv=None):
    try:
        return run(list(sys.argv[1:] if argv is None else argv))
    except BrokenPipeError:
        return 0
