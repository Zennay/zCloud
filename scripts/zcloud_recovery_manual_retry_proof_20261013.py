#!/usr/bin/env python3
"""Offline HMAC-bound manual recovery rearm reference; NEVER dispatches.

Even a valid signature permits only *consideration* of an atomic nonce
reservation, not production authority. A trusted owner must authenticate
the caller, protect the signing key, enforce exact-main GitHub evidence,
and reserve the nonce in durable shared state before a one-shot action.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

SCHEMA = "zcloud-recovery-manual-retry-proof-v1"
REPOSITORY = "Zennay/zCloud"
ACTION = "rearm_regression_once"
MAX_LIFETIME_SECONDS = 600
CLOCK_SKEW_SECONDS = 60
SHA = re.compile(r"^[0-9a-f]{40}$")
NONCE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SIGNATURE = re.compile(r"^[0-9a-f]{64}$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
FIELDS = frozenset({
    "schema", "repository", "action", "sha", "actor",
    "reason", "nonce", "issued_at", "expires_at",
})


def _canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def _deny(reason: str) -> dict:
    return {
        "schema": SCHEMA,
        "decision": "DENY",
        "reason": reason,
        "manual_reservation_candidate": False,
        "authorized": False,
        "dispatch_allowed": False,
        "mutations_performed": False,
    }


def _valid_body(body: Any, *, expected_sha: str, authenticated_actor: str, now: int) -> str | None:
    if not isinstance(body, dict) or frozenset(body) != FIELDS:
        return "missing_or_extra_fields"
    if (body.get("schema") != SCHEMA or body.get("repository") != REPOSITORY
            or body.get("action") != ACTION):
        return "wrong_audience_or_action"
    if (type(expected_sha) is not str or not SHA.fullmatch(expected_sha)
            or type(body.get("sha")) is not str or body["sha"] != expected_sha):
        return "sha_mismatch"
    actor = body.get("actor")
    if (type(actor) is not str or NAME.fullmatch(actor) is None
            or type(authenticated_actor) is not str
            or actor != authenticated_actor):
        return "actor_not_authenticated"
    reason = body.get("reason")
    if (type(reason) is not str or not (10 <= len(reason) <= 240)
            or "\n" in reason or "\r" in reason or "\x00" in reason):
        return "invalid_reason"
    nonce = body.get("nonce")
    if type(nonce) is not str or NONCE.fullmatch(nonce) is None:
        return "invalid_nonce"
    issued, expires = body.get("issued_at"), body.get("expires_at")
    if (type(issued) is not int or type(expires) is not int
            or type(now) is not int or issued < 1 or expires <= issued
            or expires - issued > MAX_LIFETIME_SECONDS):
        return "invalid_lifetime"
    if issued > now + CLOCK_SKEW_SECONDS or now > expires:
        return "expired_or_future_proof"
    return None


def check_manual_retry_proof(
    body: Any, signature: Any, *, secret: Any, expected_sha: str,
    authenticated_actor: str, now: int,
) -> dict:
    """Trusted-context-only HMAC check, never an authorization.

    The caller MUST source authenticated_actor from an independent verified
    identity. Caller-supplied body actor is not identity evidence. The secret
    MUST be owner-managed and MUST NOT be placed into workflow arguments.
    """
    error = _valid_body(body, expected_sha=expected_sha,
                        authenticated_actor=authenticated_actor, now=now)
    if error is not None:
        return _deny(error)
    if type(secret) is not bytes or len(secret) < 32:
        return _deny("untrusted_signing_key")
    if type(signature) is not str or SIGNATURE.fullmatch(signature) is None:
        return _deny("invalid_signature_format")
    expected = hmac.new(secret, _canonical(body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return _deny("signature_mismatch")
    # No nonce is reserved by this function. A signed token is replayable
    # until independently claimed via globally-shared compare-and-reserve.
    return {
        "schema": SCHEMA,
        "decision": "READY_FOR_MANUAL_NONCE_RESERVATION",
        "reason": "valid_offline_hmac_candidate_only",
        "sha": expected_sha,
        "actor": authenticated_actor,
        "nonce": body["nonce"],
        "reservation_key": "manual/" + expected_sha + "/" + body["nonce"],
        "manual_reservation_candidate": True,
        "requires_atomic_shared_claim": True,
        "requires_exact_main_recheck": True,
        "requires_serialized_owner_approval": True,
        "authorized": False,
        "dispatch_allowed": False,
        "mutations_performed": False,
    }
