"""Pure admission policy for the zSSH main-protection attestation lane.

No GitHub API, filesystem, runner, or environment mutation occurs here. The
policy separates hosted PR validation from future live attestation admission.
"""

from __future__ import annotations

from typing import Final


CANONICAL_REPOSITORY: Final = "Zennay/zCloud"
CANONICAL_MAIN_REF: Final = "refs/heads/main"
CANONICAL_ACTOR: Final = "Zennay"
SCHEMA_VERSION: Final = 1
MAX_INPUT_LENGTH: Final = 256


def _bounded_text(name: str, value: object) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or len(value) > MAX_INPUT_LENGTH:
        raise ValueError(f"{name} must be non-empty and bounded")
    return value


def evaluate_attestation_admission(
    *,
    event_name: str,
    repository: str,
    ref: str,
    actor: str,
    pull_request_head_repository: str | None = None,
) -> dict[str, object]:
    """Return a bounded decision without echoing caller-controlled values."""
    event_name = _bounded_text("event_name", event_name)
    repository = _bounded_text("repository", repository)
    ref = _bounded_text("ref", ref)
    actor = _bounded_text("actor", actor)

    if event_name == "pull_request":
        head_repo = _bounded_text("pull_request_head_repository", pull_request_head_repository)
        trusted = (
            repository == CANONICAL_REPOSITORY
            and actor == CANONICAL_ACTOR
            and head_repo == CANONICAL_REPOSITORY
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "VALIDATE_ONLY" if trusted else "REJECTED",
            "live_mutation_allowed": False,
            "reason": "trusted_same_repo_pr" if trusted else "untrusted_pr_context",
        }

    live_allowed = (
        event_name in {"push", "workflow_dispatch"}
        and repository == CANONICAL_REPOSITORY
        and ref == CANONICAL_MAIN_REF
        and actor == CANONICAL_ACTOR
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "LIVE_ALLOWED" if live_allowed else "REJECTED",
        "live_mutation_allowed": live_allowed,
        "reason": "trusted_main_event" if live_allowed else "live_context_not_trusted",
    }
