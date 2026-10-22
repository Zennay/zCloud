"""Offline-only, non-authorizing classification of deploy-ops workflow_run provenance.

A clean origin is *evidence*, not permission to cancel Actions runs, restart the
VPS listener, push workers or touch the production database. The serialised
release owner may adopt this policy, but this helper never executes operations.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

REPOSITORY = "Zennay/zCloud"
# Immutable numeric repo identity verified through GitHub REST /repos/Zennay/zCloud.
REPOSITORY_ID = 1389227570
UPSTREAM_WORKFLOW = "zCloud regression smoke"
UPSTREAM_WORKFLOW_ID = 367943538
UPSTREAM_PATH = ".github/workflows/zcloud-regression-smoke.yml"
SHA = re.compile(r"[0-9a-f]{40}\Z")


@dataclass(frozen=True)
class Decision:
    trusted_origin_observed: bool
    reason: str
    actions_cancel_authorized: bool = False
    runner_restart_authorized: bool = False
    worker_push_authorized: bool = False
    deploy_authorized: bool = False


def classify_github_payload(
    payload: Any, *, event_name: Any, canonical_main_sha: Any
) -> Decision:
    """Evaluate an actual GitHub workflow_run webhook payload, offline.

    GitHub supplies the event name out-of-band (for example
    GITHUB_EVENT_NAME); the JSON payload has a repository *object* and
    action=completed. This adapter retains that distinction rather than
    accepting an untrusted nested string pretending to be the event name.
    Its output is strictly observational, never an execution grant.
    """
    if type(payload) is not dict:
        return Decision(False, "event_not_object")
    if event_name != "workflow_run":
        return Decision(False, "event_not_workflow_run")
    repository = payload.get("repository")
    if type(repository) is not dict or repository.get("full_name") != REPOSITORY:
        return Decision(False, "trigger_repository_mismatch")
    if payload.get("action") != "completed":
        return Decision(False, "source_action_not_completed")
    # Real GitHub workflow_run payloads contain three separately identifiable
    # repository objects. Names alone can be internally contradictory; check
    # their exact positive integer IDs before trusting even observational
    # provenance. An absent ID is not a substitute for an identity match.
    repo_id = repository.get("id")
    if type(repo_id) is not int or repo_id <= 0:
        return Decision(False, "invalid_event_repository_id")
    if repo_id != REPOSITORY_ID:
        return Decision(False, "event_repository_id_mismatch")
    source = payload.get("workflow_run")
    if type(source) is not dict:
        return Decision(False, "source_not_object")
    run_repo = source.get("repository")
    if type(run_repo) is not dict or run_repo.get("full_name") != REPOSITORY:
        return Decision(False, "untrusted_run_repository")
    if type(run_repo.get("id")) is not int or run_repo["id"] != repo_id:
        return Decision(False, "run_repository_id_mismatch")
    head_repo = source.get("head_repository")
    if type(head_repo) is not dict or head_repo.get("full_name") != REPOSITORY:
        return Decision(False, "untrusted_source_repository")
    if type(head_repo.get("id")) is not int or head_repo["id"] != repo_id:
        return Decision(False, "head_repository_id_mismatch")
    return classify(
        {
            "repository": repository["full_name"],
            "event_name": event_name,
            "workflow_run": payload.get("workflow_run"),
        },
        canonical_main_sha=canonical_main_sha,
    )


def classify(event: Any, *, canonical_main_sha: Any) -> Decision:
    def deny(reason: str) -> Decision:
        return Decision(False, reason)

    if type(canonical_main_sha) is not str or SHA.fullmatch(canonical_main_sha) is None:
        return deny("canonical_main_unknown")
    if type(event) is not dict:
        return deny("event_not_object")
    if event.get("repository") != REPOSITORY:
        return deny("trigger_repository_mismatch")
    if event.get("event_name") != "workflow_run":
        return deny("event_not_workflow_run")

    source = event.get("workflow_run")
    if type(source) is not dict:
        return deny("source_not_object")
    if source.get("name") != UPSTREAM_WORKFLOW:
        return deny("wrong_workflow")
    if type(source.get("workflow_id")) is not int or source["workflow_id"] != UPSTREAM_WORKFLOW_ID:
        return deny("wrong_workflow_id")
    if source.get("path") != UPSTREAM_PATH:
        return deny("wrong_workflow_path")
    if source.get("event") != "push":
        return deny("untrusted_source_event")
    if source.get("head_branch") != "main":
        return deny("untrusted_source_branch")
    head_repo = source.get("head_repository")
    if type(head_repo) is not dict or head_repo.get("full_name") != REPOSITORY:
        return deny("untrusted_source_repository")
    if source.get("status") != "completed" or source.get("conclusion") != "success":
        return deny("source_not_successful_terminal")
    if type(source.get("id")) is not int or source["id"] <= 0:
        return deny("invalid_source_run_id")
    if type(source.get("run_attempt")) is not int or not 1 <= source["run_attempt"] <= 100000:
        return deny("invalid_source_attempt")

    sha = source.get("head_sha")
    if type(sha) is not str or SHA.fullmatch(sha) is None or sha != canonical_main_sha:
        return deny("stale_or_invalid_source_sha")

    # Classification of evidence is deliberately not a mutation grant:
    # another independent, live, serialized owner/queue admission is required.
    return Decision(True, "exact_main_origin_observed")
