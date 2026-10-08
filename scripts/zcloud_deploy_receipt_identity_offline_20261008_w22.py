"""Offline, fail-closed deployment receipt identity proof.

This deliberately does NOT authorize deployment, recovery or environment approval.
It validates an immutable receipt envelope before an operator may *consider* it as
evidence. No network, subprocess, filesystem or GitHub API access.
"""
import re

_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ALLOWED_KEYS = frozenset(("repository", "workflow_run_id", "run_attempt",
                           "head_sha", "environment", "artifact_digest",
                           "deployment_id"))


def receipt_matches(receipt, *, repository, workflow_run_id, run_attempt,
                    head_sha, environment, artifact_digest, deployment_id):
    """Require exact typed identity, and fail closed on untrusted ambiguity."""
    if not isinstance(receipt, dict) or set(receipt) != _ALLOWED_KEYS:
        return False
    if not isinstance(repository, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        return False
    if not isinstance(environment, str) or not environment or len(environment) > 100:
        return False
    if not isinstance(head_sha, str) or not _SHA.fullmatch(head_sha):
        return False
    if not isinstance(artifact_digest, str) or not _DIGEST.fullmatch(artifact_digest):
        return False
    for value in (workflow_run_id, run_attempt, deployment_id):
        if type(value) is not int or value <= 0:
            return False
    expected = dict(repository=repository, workflow_run_id=workflow_run_id,
                    run_attempt=run_attempt, head_sha=head_sha,
                    environment=environment, artifact_digest=artifact_digest,
                    deployment_id=deployment_id)
    return all(type(receipt[key]) is type(value) and receipt[key] == value
               for key, value in expected.items())


def live_deployment_authorized(*_args, **_kwargs):
    """Evidence-only module; live authorization is intentionally impossible."""
    return False
