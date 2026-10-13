#!/usr/bin/env python3
"""GitHub-backed durable recovery idempotency reference — OFFLINE plan only.

A deterministic lightweight Git tag ref can serve as a persistent, shared
create-if-absent reservation. GitHub's POST /git/refs creates a ref (201) or
reports a conflict/validation error (409/422). This module deliberately NEVER
issues HTTP or mutates Git: it only builds a fixed request and classifies a
supplied response. A serialized, authorized workflow owner must implement
authenticated transport and policy gates separately.

A reservation is not deployment authorization. Never treat 409/422, a timeout,
an existing ref, or a successful POST as permission to redispatch/deploy.
Do not delete or rewrite a reservation to retry a failed/ambiguous dispatch.
"""

from __future__ import annotations

import re
from typing import Any

SCHEMA = "zcloud-recovery-git-ref-reservation-v1"
REPOSITORY = "Zennay/zCloud"
REPOSITORY_ID = 1389227570
SHA = re.compile(r"^[0-9a-f]{40}$")
PREFIX = "refs/tags/zcloud-recovery-reserved-v1/regression"
API_PATH = "/repos/Zennay/zCloud/git/refs"
CREATE_ONLY = "POST"


class ContractViolation(ValueError):
    pass


def _valid_sha(value: Any) -> bool:
    return type(value) is str and bool(SHA.fullmatch(value))


def reservation_plan(evidence: dict[str, Any]) -> dict[str, Any]:
    """Plan deterministic persisted one-shot claim; never performs a write.

    Only a reviewed snapshot with explicit candidate decision is eligible to
    be described as a potential reservation. The candidate never inherits
    authority to create the ref; authorized stays false.
    """
    if not isinstance(evidence, dict):
        raise ContractViolation("invalid_evidence")
    if evidence.get("decision") != "READY_FOR_EXCLUSIVE_RESERVATION":
        raise ContractViolation("no_candidate")
    if evidence.get("authorized") is not False:
        raise ContractViolation("unsafe_authorization_flag")
    sha = evidence.get("sha")
    if not _valid_sha(sha):
        raise ContractViolation("invalid_sha")
    name = PREFIX + "/" + sha
    return {
        "schema": SCHEMA,
        "repository": REPOSITORY,
        "repository_id": REPOSITORY_ID,
        "sha": sha,
        "ref": name,
        "method": CREATE_ONLY,
        "path": API_PATH,
        "payload": {"ref": name, "sha": sha},
        "authorized": False,
        "dispatch_allowed": False,
        "mutations_performed": False,
        "requires_owner_provenance": True,
        "requires_exact_main_recheck_before_claim": True,
        "requires_exact_main_recheck_after_claim": True,
        "requires_protected_tag_namespace": True,
        "requires_durable_owner_review": True,
        "requires_no_tag_deletion_or_rewrite": True,
    }


def classify_create_result(
    plan: dict[str, Any], *, status: int | None, response: Any = None,
) -> dict[str, Any]:
    """Classify supplied POST response. Never grant dispatch permission.

    A 201 with exact matching lightweight commit ref proves this particular
    reservation request was created, not that anything can be dispatched.
    409 and 422 MUST NOT be interpreted as a successful duplicate: GitHub 422
    can be validation failure, spam mitigation or ref uniqueness conflict.
    HTTP timeouts/5xx may have created the ref; they consume the automatic
    attempt until independent manual reconciliation.
    """
    if not isinstance(plan, dict) or plan.get("schema") != SCHEMA:
        raise ContractViolation("invalid_plan")
    sha, ref = plan.get("sha"), plan.get("ref")
    if (not _valid_sha(sha) or ref != PREFIX + "/" + sha or
            plan.get("repository") != REPOSITORY or
            type(plan.get("repository_id")) is not int or
            plan["repository_id"] != REPOSITORY_ID or
            plan.get("method") != CREATE_ONLY or
            plan.get("path") != API_PATH or
            plan.get("payload") != {"ref": ref, "sha": sha} or
            plan.get("authorized") is not False or
            plan.get("dispatch_allowed") is not False):
        raise ContractViolation("unsafe_or_inconsistent_plan")
    state, reason = "DENY", "indeterminate_or_failed_create"
    if status == 201:
        if (isinstance(response, dict)
                and response.get("ref") == ref
                and isinstance(response.get("object"), dict)
                and response["object"].get("sha") == sha
                and response["object"].get("type") == "commit"):
            state, reason = "RESERVATION_RECORDED", "exact_ref_created"
        else:
            reason = "created_ref_response_mismatch"
    elif status in {409, 422}:
        reason = "ref_conflict_or_validation_denied"
    elif status in {401, 403}:
        reason = "missing_or_denied_write_authority"
    elif status is None:
        reason = "unknown_transport_outcome"
    return {
        "schema": SCHEMA,
        "reservation_state": state,
        "reason": reason,
        "sha": sha,
        "ref": ref,
        "authorized": False,
        "dispatch_allowed": False,
        "mutations_performed": False,
        "do_not_retry_automatically": True,
    }


def classify_existing_ref(plan: dict[str, Any], response: Any) -> dict[str, Any]:
    """Read-only reconciliation after ambiguous POST; never grants ownership.

    GET 404 after timeout does NOT prove that another automatic retry is safe.
    """
    verified = classify_create_result(plan, status=None)
    match = (
        isinstance(response, dict)
        and response.get("ref") == plan["ref"]
        and isinstance(response.get("object"), dict)
        and response["object"].get("type") == "commit"
        and response["object"].get("sha") == plan["sha"]
    )
    return {
        **verified,
        "reservation_state": "EXISTING_RESERVATION" if match else "DENY",
        "reason": "matching_ref_already_present" if match
                  else "missing_or_ambiguous_ref",
        "do_not_retry_automatically": True,
    }
