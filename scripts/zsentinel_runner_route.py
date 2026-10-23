#!/usr/bin/env python3
"""Canonical read-only GitHub Actions job-route evidence for zSentinel.

A queued job without an assigned runner is not by itself an offline
self-hosted runner. Only exact job labels can distinguish self-hosted
dispatch from hosted queues. No endpoint is called and no work is scheduled.
"""
from __future__ import annotations

import re

SAFE_LABEL = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$")
MAX_LABELS = 16


def assignment_observation(job):
    """Classify one GitHub job receipt, not a live runner or service.

    GitHub can finish a job before it is assigned. An assigned job is not
    proof of listener health, and a terminal unassigned job is not, by
    itself, proof of billing failure or a missing runner.
    """
    if type(job) is not dict:
        raise ValueError("GitHub job must be an object")
    status = job.get("status")
    if status not in ("queued", "in_progress", "completed", "waiting", "pending"):
        return {"job_assignment_stage": "unknown_job_status",
                "assignment_source_complete": False,
                "runner_healthy_verified": False,
                "worker_resumed_verified": False}
    marker = object()
    runner = job.get("runner_id", marker)
    if type(runner) is bool:
        raise ValueError("boolean is not a GitHub runner ID")
    if runner is marker:
        stage = "runner_assignment_missing_from_source"
        complete = False
    elif runner is None or runner == 0:
        stage = ("terminal_without_runner_assignment" if status == "completed"
                 else "queued_without_runner_assignment" if status == "queued"
                 else "no_runner_assignment_observed")
        complete = True
    elif type(runner) is int and 0 < runner <= 2**53 - 1:
        stage = ("terminal_with_runner_assignment" if status == "completed"
                 else "job_with_runner_assignment")
        complete = True
    else:
        raise ValueError("untrusted GitHub runner assignment")
    return {"job_assignment_stage": stage,
            "assignment_source_complete": complete,
            "runner_healthy_verified": False,
            "worker_resumed_verified": False}


def runner_route(job):
    """Return bounded, non-authorizing route category and display labels."""
    if not isinstance(job, dict):
        raise ValueError("GitHub job must be an object")
    assignment = assignment_observation(job)
    labels = job.get("labels")
    if labels is None:
        return {"runner_route": "unknown", "route_labels": [],
                "source_complete": False, "may_restart_runner": False,
                **assignment}
    if (not isinstance(labels, list) or len(labels) > MAX_LABELS
            or any(not isinstance(x, str) or not SAFE_LABEL.fullmatch(x) for x in labels)
            or len(set(labels)) != len(labels)):
        raise ValueError("invalid GitHub Actions job labels")
    if not labels:
        return {"runner_route": "unknown", "route_labels": [],
                "source_complete": False, "may_restart_runner": False,
                **assignment}
    category = "self_hosted" if "self-hosted" in labels else "hosted_or_custom"
    return {"runner_route": category, "route_labels": sorted(labels),
            "source_complete": True, "may_restart_runner": False,
            **assignment}
