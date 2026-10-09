"""Deterministic execution-lane derivation for the zCloud portfolio queue.

The generator is intentionally pure: it receives project metadata, queue backlog and
active task claims and returns at most one owner per lane. SQLite remains the source
of truth; this module only decides which non-overlapping work scopes are eligible.
"""

from __future__ import annotations

import json
import re

ACTIVE_STATUSES = frozenset({"claimed", "running", "verifying"})
PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

PROFILE_LANES = {
    "research-validation": (
        {
            "id": "critical-path",
            "keywords": (
                "critical", "generation", "strategy", "research", "candidate",
                "experiment", "backtest", "implementation", "recovery",
            ),
        },
        {
            "id": "qa-validation",
            "keywords": (
                "qa", "validation", "validate", "test", "gate", "walk-forward",
                "walk forward", "holdout", "stress", "preregistration", "verify",
            ),
        },
        {
            "id": "data-provenance",
            "keywords": (
                "data", "dataset", "provenance", "provider", "split", "feed",
                "history", "telemetry", "source",
            ),
        },
    ),
    "ml-training": (
        {
            "id": "model-training",
            "keywords": (
                "model", "train", "training", "rollout", "policy", "self-play",
                "behavior", "agent",
            ),
        },
        {
            "id": "evaluation-validation",
            "keywords": (
                "arena", "evaluation", "validate", "validation", "benchmark",
                "quality", "gate", "test", "champion",
            ),
        },
        {
            "id": "data-pipeline",
            "keywords": (
                "data", "dataset", "replay", "pipeline", "ingest", "sample",
                "feature", "provenance",
            ),
        },
    ),
    "platform": (
        {
            "id": "control-plane",
            "keywords": (
                "queue", "scheduler", "lane", "claim", "worker", "orchestration",
                "control", "api", "server", "allocation", "state",
            ),
        },
        {
            "id": "runtime-automation",
            "keywords": (
                "firefox", "browser", "userscript", "extension", "runtime",
                "conversation", "tab", "automation", "reconnect",
            ),
        },
        {
            "id": "deploy-ops",
            "keywords": (
                "deploy", "workflow", "runner", "vps", "service", "systemd",
                "release", "health", "rollback", "promotion", "ci",
            ),
        },
    ),
    "product": (
        {
            "id": "product-core",
            "keywords": (
                "ui", "ux", "screen", "planner", "feature", "flow", "state",
                "component", "product", "mobile",
            ),
        },
        {
            "id": "integration-runtime",
            "keywords": (
                "api", "integration", "provider", "sync", "runtime", "backend",
                "gateway", "connector", "auth",
            ),
        },
        {
            "id": "quality-validation",
            "keywords": (
                "test", "validation", "validate", "qa", "regression", "build",
                "smoke", "accessibility",
            ),
        },
    ),
    "device-product": (
        {
            "id": "app-core",
            "keywords": (
                "ui", "flow", "state", "gesture", "dictation", "compose",
                "screen", "app",
            ),
        },
        {
            "id": "device-integration",
            "keywords": (
                "watch", "device", "sensor", "microphone", "gateway", "provider",
                "pairing", "geckoview", "integration",
            ),
        },
        {
            "id": "quality-validation",
            "keywords": (
                "test", "validation", "validate", "qa", "regression", "build",
                "battery", "false-positive", "smoke",
            ),
        },
    ),

    "security-lab": (
        {
            "id": "scope-authorization",
            "keywords": (
                "authorization", "authorisation", "scope", "approval", "consent",
                "target", "tenant", "request", "permission", "guardrail",
            ),
        },
        {
            "id": "assessment-runtime",
            "keywords": (
                "assessment", "orchestration", "orchestrator", "discovery", "passive",
                "scan", "probe", "lab", "runtime", "plan", "workflow",
            ),
        },
        {
            "id": "evidence-remediation",
            "keywords": (
                "evidence", "finding", "findings", "remediation", "retest", "report",
                "audit", "triage", "severity", "fix", "verification",
            ),
        },
    ),
    "human-gated": (
        {
            "id": "external-gate",
            "keywords": (
                "human", "participant", "approval", "physical", "external", "gate",
            ),
        },
    ),
}

SUPPORTED_PROFILES = frozenset(PROFILE_LANES)

PROJECT_SCOPE_ALIASES = {
    ("cloud", "control-plane"): (
        "zcloud-control-plane", "zcloud-queue", "zcloud-scheduler",
    ),
    ("cloud", "runtime-automation"): (
        "zcloud-worker-runtime", "firefox-automation",
    ),
    ("cloud", "deploy-ops"): (
        "zcloud-production-deploy", "zcloud-vps-execution",
    ),
    ("zssh", "deploy-ops"): ("zssh-runtime-deploy",),
    ("ftmo", "critical-path"): ("ftmo-research-critical-path",),
    ("ftmo", "qa-validation"): ("ftmo-validation",),
    ("ftmo", "data-provenance"): ("ftmo-data-provenance",),
}


def _scope_entries(value, field):
    """Preserve scalar strings, but never reinterpret malformed write scopes.

    A mapping silently iterates its keys, while a number may crash during
    iteration. Neither is an authoritative file/capability reservation.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"invalid conflict_scope.{field}: expected strings")
    if any(not isinstance(entry, str) for entry in value):
        raise ValueError(f"invalid conflict_scope.{field}: expected strings")
    return value


def _normal_scope(scope):
    if scope is None:
        scope = {}
    if not isinstance(scope, dict):
        raise ValueError("invalid conflict_scope: expected object")
    capabilities = []
    # A single path/capability is one scope entry, not an iterable of chars.
    # List-shaped queue/claim metadata remains fully backwards compatible.
    for value in _scope_entries(scope.get("capabilities"), "capabilities"):
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("invalid conflict_scope.capabilities: control character")
        item = re.sub(r"[^a-z0-9._:/-]+", "-", str(value or "").strip().lower()).strip("-")
        if not item:
            raise ValueError("invalid conflict_scope.capabilities: empty entry")
        if item not in capabilities:
            capabilities.append(item)
    files = []
    for value in _scope_entries(scope.get("files"), "files"):
        # Validate *before* whitespace stripping, otherwise a trailing newline
        # or tab turns one unsafe scope declaration into a different path.
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("invalid conflict_scope.files: control-character path")
        item = str(value or "").strip().replace("\\", "/")
        while item.startswith("./"):
            item = item[2:]
        item = item.strip("/")
        if not item or any(ord(char) < 32 or ord(char) == 127 for char in item):
            raise ValueError("invalid conflict_scope.files: empty or control-character path")
        parts = [part for part in item.split("/") if part not in ("", ".")]
        if any(part == ".." for part in parts):
            raise ValueError("invalid conflict_scope.files: parent traversal")
        if not parts:
            raise ValueError("invalid conflict_scope.files: empty path")
        item = "/".join(parts)
        if item not in files:
            files.append(item)
    return {"capabilities": capabilities, "files": files}


def _metadata(item):
    value = item.get("metadata")
    if isinstance(value, dict):
        return value
    # NULL/missing metadata is the documented "no metadata" state.
    # A present but blank/non-JSON value is corrupted evidence, not an
    # authoritative declaration that no files are owned.
    raw = item.get("metadata_json")
    if raw is None:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        # A corrupted queue row or task-claim receipt cannot safely be
        # interpreted as "owns no files/capabilities".
        raise ValueError("invalid metadata_json: unreadable object") from exc
    if not isinstance(value, dict):
        raise ValueError("invalid metadata_json: expected object")
    return value


def _scope_from_item(item):
    return _normal_scope(_metadata(item).get("conflict_scope"))


def _scope_from_claim(claim):
    metadata = claim.get("metadata")
    if not isinstance(metadata, dict):
        metadata = _metadata(claim)
    return _normal_scope(metadata.get("conflict_scope") if isinstance(metadata, dict) else {})


def scopes_overlap(left, right):
    left = _normal_scope(left)
    right = _normal_scope(right)
    shared_caps = sorted(set(left["capabilities"]) & set(right["capabilities"]))
    shared_files = []
    for a in left["files"]:
        for b in right["files"]:
            if a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/"):
                shared_files.append(a if len(a) <= len(b) else b)
    return {
        "capabilities": shared_caps,
        "files": sorted(set(shared_files)),
    }


def _profile(project):
    explicit = str(project.get("lane_profile") or "").strip().lower()
    if explicit:
        if explicit in PROFILE_LANES:
            return explicit
        raise ValueError(f"unsupported lane_profile {explicit!r}")
    project_id = str(project.get("id") or "").strip().lower()
    return {
        "ftmo": "research-validation",
        "haxlab": "ml-training",
        "cloud": "platform",
        "zssh": "platform",
        "raiseai": "device-product",
    }.get(project_id, "product")


def _lane_scope(project_id, lane_id, item=None):
    capabilities = [f"{project_id}:{lane_id}"]
    capabilities.extend(PROJECT_SCOPE_ALIASES.get((project_id, lane_id), ()))
    explicit = _scope_from_item(item or {})
    for capability in explicit["capabilities"]:
        if capability not in capabilities:
            capabilities.append(capability)
    return {
        "capabilities": capabilities,
        "files": list(explicit["files"]),
    }


def _lane_score(text, lane):
    score = 0
    for keyword in lane["keywords"]:
        pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            score += 3 if " " in keyword or "-" in keyword else 1
    return score


def classify_backlog_item(project, item):
    profile = _profile(project)
    lanes = PROFILE_LANES[profile]
    text = (
        str(item.get("title") or "") + "\n" +
        str(item.get("completion_criteria") or "")
    ).lower()
    scored = [(_lane_score(text, lane), index, lane) for index, lane in enumerate(lanes)]
    score, _, lane = max(scored, key=lambda value: (value[0], -value[1]))
    # No keyword match: keep the first lane as the deterministic primary work lane.
    if score <= 0:
        lane = lanes[0]
    project_id = str(project.get("id") or item.get("project_id") or "").strip().lower()
    return {
        "lane_id": lane["id"],
        "profile": profile,
        "scope": _lane_scope(project_id, lane["id"], item),
    }


def _queue_order(item):
    status = str(item.get("status") or "queued").lower()
    return (
        0 if status in ACTIVE_STATUSES else 1,
        PRIORITY_RANK.get(str(item.get("priority") or "P3").upper(), 3),
        str(item.get("created_at") or ""),
        str(item.get("queue_id") or ""),
    )


def generate_execution_lanes(project, backlog, claims=()):
    """Return deterministic, non-overlapping lanes for one project's live backlog.

    Active queue work occupies its lane first. Queued candidates are then admitted
    by queue priority only when both their task-claim scope and every already
    occupied execution-lane scope are non-overlapping. This prevents two different
    derived lanes from writing the same explicit file/capability concurrently.
    """

    project_id = str(project.get("id") or "").strip().lower()
    profile = _profile(project)
    lane_defs = PROFILE_LANES[profile]
    items = [
        dict(item) for item in backlog
        if str(item.get("project_id") or project_id).strip().lower() == project_id
        and str(item.get("status") or "queued").lower() in (ACTIVE_STATUSES | {"queued"})
    ]
    classified = []
    for item in items:
        lane = classify_backlog_item(project, item)
        classified.append({**item, **lane})

    active_claims = []
    for claim in claims or ():
        if str(claim.get("project_id") or project_id).strip().lower() != project_id:
            continue
        scope = _scope_from_claim(claim)
        if scope["capabilities"] or scope["files"]:
            active_claims.append({
                "claim_key": str(claim.get("claim_key") or ""),
                "owner_id": str(claim.get("owner_id") or ""),
                "worker_id": str(claim.get("worker_id") or ""),
                "scope": scope,
            })

    lane_items = {}
    for lane_def in lane_defs:
        lane_id = lane_def["id"]
        lane_items[lane_id] = sorted(
            [item for item in classified if item["lane_id"] == lane_id],
            key=_queue_order,
        )

    chosen_by_lane = {}
    blocked_by_lane = {lane_def["id"]: [] for lane_def in lane_defs}
    occupied_scopes = []

    # Existing active work is authoritative: preserve it and reserve its write
    # scope before considering any new queued work.
    for lane_def in lane_defs:
        lane_id = lane_def["id"]
        active = [
            item for item in lane_items[lane_id]
            if str(item.get("status") or "").lower() in ACTIVE_STATUSES
        ]
        if active:
            # One lane has one displayed representative, but *every* active
            # task owns its declared write scope. Never hide a second active
            # writer from cross-lane conflict detection.
            chosen_by_lane[lane_id] = active[0]
            for existing in active:
                occupied_scopes.append({
                    "queue_id": str(existing.get("queue_id") or ""),
                    "lane_id": lane_id,
                    "scope": existing["scope"],
                })

    queued = sorted(
        [
            item for item in classified
            if str(item.get("status") or "queued").lower() == "queued"
            and item["lane_id"] not in chosen_by_lane
        ],
        key=_queue_order,
    )
    for candidate in queued:
        lane_id = candidate["lane_id"]
        if lane_id in chosen_by_lane:
            continue

        conflicts = []
        for claim in active_claims:
            overlap = scopes_overlap(candidate["scope"], claim["scope"])
            if overlap["capabilities"] or overlap["files"]:
                conflicts.append({
                    "kind": "claim",
                    "reason": "task_claim_scope_conflict",
                    "claim_key": claim["claim_key"],
                    "owner_id": claim["owner_id"],
                    "worker_id": claim["worker_id"],
                    "overlap": overlap,
                })
        for occupied in occupied_scopes:
            overlap = scopes_overlap(candidate["scope"], occupied["scope"])
            if overlap["capabilities"] or overlap["files"]:
                conflicts.append({
                    "kind": "queue",
                    "reason": "queue_scope_conflict",
                    "queue_id": occupied["queue_id"],
                    "lane_id": occupied["lane_id"],
                    "overlap": overlap,
                })
        if conflicts:
            blocked_by_lane[lane_id].extend(conflicts)
            continue

        # An earlier, higher-priority candidate may have been rejected by a
        # foreign claim or an occupied lane. Once a safe fallback is chosen,
        # its own admission is conflict-free: never expose the rejected
        # candidate's blockers as if they belonged to the selected task.
        blocked_by_lane[lane_id] = []
        chosen_by_lane[lane_id] = candidate
        occupied_scopes.append({
            "queue_id": str(candidate.get("queue_id") or ""),
            "lane_id": lane_id,
            "scope": candidate["scope"],
        })

    results = []
    for lane_def in lane_defs:
        lane_id = lane_def["id"]
        chosen = chosen_by_lane.get(lane_id)
        blocked_by = blocked_by_lane[lane_id]
        scope = _lane_scope(project_id, lane_id, chosen or {})
        results.append({
            "project_id": project_id,
            "profile": profile,
            "lane_id": lane_id,
            "queue_id": str(chosen.get("queue_id") or "") if chosen else None,
            "status": str(chosen.get("status") or "") if chosen else "blocked" if blocked_by else "idle",
            "scope": scope,
            "blocked_by": blocked_by,
        })
    return results

def eligible_queue_lane_map(project, backlog, claims=()):
    """Map currently eligible queued IDs to the lane record that admitted them."""

    lanes = generate_execution_lanes(project, backlog, claims)
    return {
        lane["queue_id"]: lane
        for lane in lanes
        if lane.get("queue_id") and lane.get("status") == "queued"
    }
