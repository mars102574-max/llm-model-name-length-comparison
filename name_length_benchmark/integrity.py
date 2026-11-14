"""Versioned SHA-256 envelopes, including a second hash of the first digest."""

import hashlib
import hmac
import json
import re

from .errors import BenchmarkError

CANONICALIZATION = "json-sorted-ascii-v1"
INTEGRITY_VERSION = "1.0"


def canonical_bytes(value) -> bytes:
    """The documented Python JSON encoding, not RFC 8785/JCS."""
    try:
        return json.dumps(
            value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise BenchmarkError("invalid_integrity") from exc


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_of_hash(digest: str) -> str:
    # Hash the 64 lowercase hexadecimal ASCII characters, not the 32 raw bytes.
    return sha256(digest.encode("ascii"))


def check_digest(value) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise BenchmarkError("invalid_integrity")


def verify_pair(data: bytes, digest, second_digest) -> None:
    check_digest(digest)
    check_digest(second_digest)
    if not hmac.compare_digest(hash_of_hash(digest), second_digest):
        raise BenchmarkError("hash_hash_mismatch")
    if not hmac.compare_digest(sha256(data), digest):
        raise BenchmarkError("payload_hash_mismatch")


def verify_document(document: object) -> dict:
    if not isinstance(document, dict) or not isinstance(document.get("integrity"), dict):
        raise BenchmarkError("invalid_integrity")
    integrity = document["integrity"]
    required = {"version", "algorithm", "canonicalization", "payload_sha256", "hash_sha256"}
    if (
        set(integrity) != required
        or integrity["version"] != INTEGRITY_VERSION
        or integrity["algorithm"] != "sha256"
        or integrity["canonicalization"] != CANONICALIZATION
    ):
        raise BenchmarkError("invalid_integrity")
    payload = {key: value for key, value in document.items() if key != "integrity"}
    verify_pair(canonical_bytes(payload), integrity["payload_sha256"], integrity["hash_sha256"])
    return integrity


def seal_document(payload: dict) -> dict:
    if "integrity" in payload:
        raise BenchmarkError("invalid_integrity")
    digest = sha256(canonical_bytes(payload))
    document = {
        **payload,
        "integrity": {
            "version": INTEGRITY_VERSION,
            "algorithm": "sha256",
            "canonicalization": CANONICALIZATION,
            "payload_sha256": digest,
            "hash_sha256": hash_of_hash(digest),
        },
    }
    verify_document(document)
    return document


VERIFICATION_ERRORS = {
    "invalid_integrity", "hash_hash_mismatch", "payload_hash_mismatch",
    "verification_benchmark", "invalid_report", "invalid_log_bundle", "log_chain_mismatch", "log_audit_mismatch",
}
