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


def runner_route(job):
    """Return bounded, non-authorizing route category and display labels."""
    if not isinstance(job, dict):
        raise ValueError("GitHub job must be an object")
    labels = job.get("labels")
    if labels is None:
        return {"runner_route": "unknown", "route_labels": [],
                "source_complete": False, "may_restart_runner": False}
    if (not isinstance(labels, list) or len(labels) > MAX_LABELS
            or any(not isinstance(x, str) or not SAFE_LABEL.fullmatch(x) for x in labels)
            or len(set(labels)) != len(labels)):
        raise ValueError("invalid GitHub Actions job labels")
    if not labels:
        return {"runner_route": "unknown", "route_labels": [],
                "source_complete": False, "may_restart_runner": False}
    category = "self_hosted" if "self-hosted" in labels else "hosted_or_custom"
    return {"runner_route": category, "route_labels": sorted(labels),
            "source_complete": True, "may_restart_runner": False}
