"""Execution events and one finite audit layer for their successful writes."""

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .errors import BenchmarkError
from .inputs import read_json, read_utf8
from .integrity import canonical_bytes, check_digest, hash_of_hash, seal_document, sha256, verify_document, verify_pair

LOG_SCHEMA_VERSION = "1.0"
MAX_LOG_BYTES = 128 * 1024 * 1024
FILENAMES = {"execution": "execution.jsonl", "audit": "audit.jsonl"}
OUTCOMES = {0: "completed", 1: "partial", 2: "failed", 130: "interrupted"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class RunLogger:
    def __init__(self, base_directory: str, benchmark: str, version: str):
        self.benchmark = benchmark
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex
        self.directory = Path(base_directory) / self.run_id
        self.failed = False
        self.closed = False
        self._handles = {}
        self._state = {
            stream: {"records": 0, "bytes": 0, "previous": None, "hasher": hashlib.sha256()}
            for stream in FILENAMES
        }
        try:
            self.directory.parent.mkdir(parents=True, exist_ok=True)
            self.directory.mkdir()
            for stream, filename in FILENAMES.items():
                self._handles[stream] = (self.directory / filename).open("x+b")
            self.record("run_started", {"benchmark": benchmark, "version": version})
        except (OSError, BenchmarkError, KeyboardInterrupt) as exc:
            self.failed = True
            self.close()
            if not isinstance(exc, OSError):
                raise
            raise BenchmarkError("log_io", path=str(self.directory), reason=str(exc)) from exc

    def _append(self, stream: str, event: str, data: dict) -> tuple[dict, bytes]:
        state = self._state[stream]
        entry = seal_document({
            "schema_version": LOG_SCHEMA_VERSION,
            "run_id": self.run_id,
            "stream": stream,
            "sequence": state["records"] + 1,
            "timestamp": utc_now(),
            "previous_line_sha256": state["previous"],
            "event": event,
            "data": data,
        })
        line = canonical_bytes(entry) + b"\n"
        if state["bytes"] + len(line) > MAX_LOG_BYTES:
            raise OSError("log size limit exceeded")
        handle = self._handles[stream]
        offset = handle.tell()
        if handle.write(line) != len(line):
            raise OSError("short log write")
        handle.flush()
        os.fsync(handle.fileno())
        handle.seek(offset)
        if handle.read(len(line)) != line:
            raise OSError("log read-back mismatch")
        handle.seek(0, os.SEEK_END)
        state["records"] += 1
        state["bytes"] += len(line)
        state["previous"] = sha256(line)
        state["hasher"].update(line)
        return entry, line

    def record(self, event: str, data: dict) -> None:
        if self.failed or self.closed:
            raise BenchmarkError("log_io", path=str(self.directory), reason="logger is not writable")
        try:
            entry, line = self._append("execution", event, data)
            # The audit write does not call record(), preventing a third logging layer.
            self._append("audit", "execution_log_written", {
                "execution_sequence": entry["sequence"],
                "execution_event": event,
                "bytes_written": len(line),
                "line_sha256": sha256(line),
            })
        except (OSError, BenchmarkError, KeyboardInterrupt) as exc:
            self.failed = True
            if not isinstance(exc, OSError):
                raise
            raise BenchmarkError("log_io", path=str(self.directory), reason=str(exc)) from exc

    def finish(self, exit_code: int, report_hash: str | None = None) -> None:
        outcome = OUTCOMES[exit_code]
        self.record("run_finished", {"exit_code": exit_code, "outcome": outcome, "report_sha256": report_hash})
        files = {}
        for stream, filename in FILENAMES.items():
            state = self._state[stream]
            digest = state["hasher"].hexdigest()
            files[filename] = {
                "records": state["records"], "bytes": state["bytes"],
                "sha256": digest, "hash_sha256": hash_of_hash(digest),
            }
        manifest = seal_document({
            "schema_version": LOG_SCHEMA_VERSION, "benchmark": self.benchmark,
            "run_id": self.run_id, "outcome": outcome, "exit_code": exit_code,
            "report_sha256": report_hash, "files": files,
        })
        try:
            # A manifest is the completion marker, created only after both streams flush.
            data = canonical_bytes(manifest) + b"\n"
            with (self.directory / "manifest.json").open("x+b") as handle:
                if handle.write(data) != len(data):
                    raise OSError("short manifest write")
                handle.flush()
                os.fsync(handle.fileno())
                handle.seek(0)
                if handle.read() != data:
                    raise OSError("manifest read-back mismatch")
        except (OSError, KeyboardInterrupt) as exc:
            self.failed = True
            if isinstance(exc, KeyboardInterrupt):
                raise
            raise BenchmarkError("log_io", path=str(self.directory), reason=str(exc)) from exc
        finally:
            self.close()

    def close(self) -> None:
        for handle in self._handles.values():
            try:
                handle.close()
            except OSError:
                # Data was already flushed/fsynced in _append; preserve the primary error.
                pass
        self.closed = True


def _read_log(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_LOG_BYTES + 1)
    except OSError as exc:
        raise BenchmarkError("read_error", path=str(path), reason=str(exc)) from exc
    if len(data) > MAX_LOG_BYTES:
        raise BenchmarkError("file_too_large", path=str(path), limit=MAX_LOG_BYTES)
    return data


def verify_log_bundle(directory: str, benchmark: str) -> dict:
    root = Path(directory)
    manifest_path = root / "manifest.json"
    manifest = read_json(read_utf8(str(manifest_path), limit=65536), str(manifest_path))
    verify_document(manifest)
    if manifest.get("benchmark") != benchmark:
        raise BenchmarkError("verification_benchmark", expected=benchmark)
    files = manifest.get("files")
    exit_code = manifest.get("exit_code")
    if (
        manifest.get("schema_version") != LOG_SCHEMA_VERSION
        or not isinstance(manifest.get("run_id"), str) or not manifest["run_id"]
        or type(exit_code) is not int or exit_code not in OUTCOMES
        or manifest.get("outcome") != OUTCOMES[exit_code]
        or not isinstance(files, dict) or set(files) != set(FILENAMES.values())
    ):
        raise BenchmarkError("invalid_log_bundle")
    if manifest.get("report_sha256") is not None:
        check_digest(manifest["report_sha256"])
    streams = {}
    for stream, filename in FILENAMES.items():
        meta = files[filename]
        if (
            not isinstance(meta, dict)
            or set(meta) != {"records", "bytes", "sha256", "hash_sha256"}
            or type(meta["records"]) is not int or meta["records"] < 2
            or type(meta["bytes"]) is not int or meta["bytes"] <= 0
        ):
            raise BenchmarkError("invalid_log_bundle")
        data = _read_log(root / filename)
        verify_pair(data, meta["sha256"], meta["hash_sha256"])
        lines = data.splitlines(keepends=True)
        if len(data) != meta["bytes"] or len(lines) != meta["records"]:
            raise BenchmarkError("invalid_log_bundle")
        previous = None
        entries = []
        for sequence, line in enumerate(lines, start=1):
            try:
                entry = read_json(line.decode("utf-8"), filename)
            except UnicodeError as exc:
                raise BenchmarkError("invalid_utf8", path=filename) from exc
            verify_document(entry)
            if (
                canonical_bytes(entry) + b"\n" != line
                or entry.get("schema_version") != LOG_SCHEMA_VERSION
                or entry.get("run_id") != manifest["run_id"]
                or entry.get("stream") != stream
                or type(entry.get("sequence")) is not int or entry["sequence"] != sequence
                or entry.get("previous_line_sha256") != previous
                or not isinstance(entry.get("data"), dict)
            ):
                raise BenchmarkError("log_chain_mismatch", stream=stream, sequence=sequence)
            previous = sha256(line)
            entries.append((entry, line))
        streams[stream] = entries
    execution, audit = streams["execution"], streams["audit"]
    if len(execution) != len(audit):
        raise BenchmarkError("log_audit_mismatch", sequence=0)
    for index, ((event, line), (receipt, _)) in enumerate(zip(execution, audit), start=1):
        if receipt.get("event") != "execution_log_written" or receipt["data"] != {
            "execution_sequence": index, "execution_event": event.get("event"),
            "bytes_written": len(line), "line_sha256": sha256(line),
        }:
            raise BenchmarkError("log_audit_mismatch", sequence=index)
    first, last = execution[0][0], execution[-1][0]
    if (
        first.get("event") != "run_started" or first["data"].get("benchmark") != benchmark
        or last.get("event") != "run_finished"
        or last["data"] != {
            "exit_code": exit_code, "outcome": manifest["outcome"], "report_sha256": manifest.get("report_sha256"),
        }
    ):
        raise BenchmarkError("invalid_log_bundle")
    return {
        "target": "logs", "valid": True, "run_id": manifest["run_id"],
        "execution_records": len(execution), "audit_records": len(audit),
        "outcome": manifest["outcome"], "original_exit_code": exit_code,
        "report_sha256": manifest.get("report_sha256"),
    }
