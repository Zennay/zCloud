"""Deterministic execution-lane derivation for the zCloud portfolio queue.

The generator is intentionally pure: it receives project metadata, queue backlog and
active task claims and returns at most one owner per lane. SQLite remains the source
of truth; this module only decides which non-overlapping work scopes are eligible.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

ACTIVE_STATUSES = frozenset({"claimed", "running", "verifying"})
PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

PROFILE_LANES = {
    "portfolio-recovery": (
        {
            "id": "release-integration",
            "keywords": (
                "merge", "pull request", "pr", "main", "branch",
                "review", "approval", "integrate", "integration",
            ),
        },
        {
            "id": "product-remediation",
            "keywords": (
                "issue", "issues", "bug", "fix", "repair", "defect",
                "product", "feature", "ux", "ui", "customer",
            ),
        },
        {
            "id": "worker-recovery",
            "keywords": (
                "worker", "scheduler", "queue", "claim", "browser",
                "runtime", "memory", "ram", "swap", "crash", "stalled",
            ),
        },
        {
            "id": "ci-deploy-recovery",
            "keywords": (
                "deploy", "workflow", "runner", "vps", "service",
                "ci", "build", "release", "github actions", "pipeline",
            ),
        },
    ),
    "research-validation": (
        {
            "id": "critical-path",
            "keywords": (
                "critical", "generation", "strategy", "research", "candidate",
                "experiment", "backtest", "implementation", "recovery",
            ),
        },
        {
            "id": "strategy-implementation",
            "keywords": (
                "walk-forward", "walk forward", "holdout", "risk", "execution",
                "preregistration", "stress", "portfolio", "position",
                "orders", "risk-model", "risk model", "reliability",
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
            "id": "model-improvement",
            "keywords": (
                "arena", "evaluation", "benchmark", "champion", "inference",
                "latency", "gameplay", "performance", "improvement",
                "tuning", "accuracy", "physics", "reliability",
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
            "id": "bugfix-delivery",
            "keywords": (
                "bug", "fix", "repair", "regression", "broken", "crash",
                "error", "recovery", "reliability", "build", "accessibility",
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
            "id": "bugfix-delivery",
            "keywords": (
                "bug", "fix", "repair", "regression", "broken", "crash",
                "error", "recovery", "reliability", "build", "battery",
                "false-positive",
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
    ("ftmo", "strategy-implementation"): ("ftmo-validation",),
    ("ftmo", "data-provenance"): ("ftmo-data-provenance",),
}


def _scope_values(scope, key):
    """Accept a single scope name as one value, never as individual chars.

    Persisted task metadata can contain either a string or an array. Splitting
    "server.py" into characters silently loses real file-scope collisions.
    """
    values = scope.get(key)
    if isinstance(values, str):
        return (values,)
    return values if isinstance(values, (list, tuple, set, frozenset)) else ()


def _normal_scope(scope):
    scope = scope if isinstance(scope, dict) else {}
    capabilities = []
    for value in _scope_values(scope, "capabilities"):
        item = re.sub(r"[^a-z0-9._:/-]+", "-", str(value or "").strip().lower()).strip("-")
        if item and item not in capabilities:
            capabilities.append(item)
    files = []
    for value in _scope_values(scope, "files"):
        item = str(value or "").strip().replace("\\", "/")
        while item.startswith("./"):
            item = item[2:]
        item = item.strip("/")
        if not item:
            continue
        parts = [part for part in item.split("/") if part not in ("", ".")]
        if not parts or any(part == ".." for part in parts):
            continue
        item = "/".join(parts)
        if item not in files:
            files.append(item)
    return {"capabilities": capabilities, "files": files}


def _recovery_declared_scope(metadata):
    """Fail closed on ambiguous zSentinel WRITE scopes, never synthesized ones.

    A generated `execution_lane.scope` is NOT an owner declaration.
    Reject the *whole* declaration if a value is malformed: accepting one
    valid capability alongside an invalid file would falsely prove safety.
    Other project profiles keep their existing scope representation.
    """
    raw = metadata.get("conflict_scope") if isinstance(metadata, dict) else None
    if not isinstance(raw, dict):
        return {"capabilities": [], "files": []}
    parsed = {"capabilities": [], "files": []}
    for key in ("capabilities", "files"):
        value = raw.get(key, [])
        values = [value] if isinstance(value, str) else value
        if not isinstance(values, (list, tuple)) or len(values) > 64:
            return {"capabilities": [], "files": []}
        for original in values:
            if not isinstance(original, str) or not 1 <= len(original) <= 256:
                return {"capabilities": [], "files": []}
            name = original.strip()
            if not name or name != original or any(c in name for c in ("*", "?", "[", "]", "\\", "\x00")):
                return {"capabilities": [], "files": []}
            if key == "files":
                if name.startswith("/") or name.endswith("/") or "//" in name:
                    return {"capabilities": [], "files": []}
                if any(segment in ("", ".", "..") for segment in name.split("/")):
                    return {"capabilities": [], "files": []}
                if not re.fullmatch(r"[A-Za-z0-9_./@+-]+", name):
                    return {"capabilities": [], "files": []}
            else:
                name = name.lower()
                if not re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*", name):
                    return {"capabilities": [], "files": []}
            if name not in parsed[key]:
                parsed[key].append(name)
    # A capability label alone names a lane, not the files it may edit.
    # Only explicit, finite real file paths can prove two code writers
    # are independent. Read-only tasks are never granted writer authority.
    if not parsed["files"]:
        return {"capabilities": [], "files": []}
    return parsed


def recovery_scope_diagnostic(item):
    """Human-safe metadata classification; never returns raw paths or owners."""
    metadata = _metadata(item)
    if not isinstance(metadata.get("conflict_scope"), dict):
        return "missing_declaration"
    return ("declared" if any(_recovery_declared_scope(metadata).values())
            else "invalid_or_empty_declaration")


def recovery_active_profile_mismatch(item):
    """Old active lane metadata is a reservation, not new four-lane authority."""
    record = _metadata(item).get("execution_lane")
    if not isinstance(record, dict):
        return True  # Missing provenance does not prove a safe migration.
    return str(record.get("profile") or "").strip().lower() != "portfolio-recovery"


def _recovery_scope_from_item(item):
    return _recovery_declared_scope(_metadata(item))


def _recovery_scope_from_claim(claim):
    metadata = claim.get("metadata")
    if not isinstance(metadata, dict):
        metadata = _metadata(claim)
    return _recovery_declared_scope(metadata)


def _metadata(item):
    value = item.get("metadata")
    if isinstance(value, dict):
        return value
    raw = item.get("metadata_json")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


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
    explicit = (_recovery_scope_from_item(item or {}) if project_id == "zsentinel"
                else _scope_from_item(item or {}))
    for capability in explicit["capabilities"]:
        if capability not in capabilities:
            capabilities.append(capability)
    return {
        "capabilities": capabilities,
        "files": list(explicit["files"]),
    }


def _lane_score(text, lane, profile=None):
    score = 0
    for keyword in lane["keywords"]:
        pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            weight = 3 if " " in keyword or "-" in keyword else 1
            # All issue tickets mention "issue" or "fix", regardless of the
            # worker best equipped to handle them. Keep those words as a
            # fallback for product remediation, but prefer an actual runner,
            # claim, deploy or merge signal when one is present.
            if profile == "portfolio-recovery" and keyword in {
                "issue", "issues", "bug", "fix", "repair",
                "product", "feature",
            }:
                weight = 0.25
            score += weight
    return score


def classify_backlog_item(project, item):
    profile = _profile(project)
    lanes = PROFILE_LANES[profile]
    text = (
        str(item.get("title") or "") + "\n" +
        str(item.get("completion_criteria") or "")
    ).lower()
    scored = [(_lane_score(text, lane, profile), index, lane) for index, lane in enumerate(lanes)]
    score, _, lane = max(scored, key=lambda value: (value[0], -value[1]))
    # Unknown product work belongs to product remediation, not the PR
    # release lane. A title without "PR"/"merge" evidence must never
    # masquerade as an unfinished product PR.
    if score <= 0:
        lane = (next(l for l in lanes if l["id"] == "product-remediation")
                if profile == "portfolio-recovery" else lanes[0])
    if (profile == "portfolio-recovery"
            and str(item.get("status") or "").strip().lower() in ACTIVE_STATUSES):
        # An existing active worker owns the *persisted* canonical lane,
        # even when title/criteria wording changes during execution.
        # Queued work is still classified normally; a missing/legacy lane
        # remains subject to the global active-profile HOLD downstream.
        persisted = _metadata(item).get("execution_lane")
        if isinstance(persisted, dict) and persisted.get("profile") == profile:
            lane_id = persisted.get("lane_id")
            if isinstance(lane_id, str):
                pinned = next((l for l in lanes if l["id"] == lane_id), None)
                if pinned is not None:
                    lane = pinned
    project_id = str(project.get("id") or item.get("project_id") or "").strip().lower()
    return {
        "lane_id": lane["id"],
        "profile": profile,
        "scope": _lane_scope(project_id, lane["id"], item),
    }


def work_package_rank(item):
    """Rank delivery-oriented work ahead of isolated QA within equal priority.

    Higher-priority P0/P1 work, already active claims, manual-start intent and
    conflict scopes always retain their separate authority.
    """
    title = str(item.get("title") or "").strip().lower()
    criteria = str(item.get("completion_criteria") or "").lower()
    if title.startswith((
        "test ", "qa ", "audit ", "inspect ", "review ", "verify ",
        "check ", "monitor ", "report ", "document ", "validate ",
    )):
        return 2
    words = (
        "implement", "build", "ship", "deliver", "complete", "finish",
        "integrate", "migrate", "fix", "repair", "recover", "restore",
        "launch", "enable", "harden", "connect", "refactor",
    )
    if any(re.search(r'\b' + word + r'\b', title) for word in words):
        return 0
    if any(value in criteria for value in (
        "end-to-end implementation", "end-to-end feature", "complete user flow",
        "multi-step work package", "substantial roadmap work package",
    )):
        return 0
    return 1


def _prohibited_recovery_qa(item):
    """Do not admit a NEW QA/validation package to zSentinel recovery.

    Existing active work remains reserved; mandatory GitHub CI still runs
    under its independent release gates. An incidental mention of tests in a
    genuine bug-fix ticket does not turn that product fix into a QA package.
    """
    title = str(item.get("title") or "").strip().lower()
    if re.match(
        r"^(?:qa(?:[ :(-]|$)|tests?(?:[ :(-]|$)|regression[ -]tests?(?:[ :(-]|$)|"
        r"quality[ -]validation(?:[ :(-]|$)|validation[ -]suite(?:[ :(-]|$)|"
        r"regression[ -]suite(?:[ :(-]|$)|run[ -](?:qa|regression)(?:[ :(-]|$))",
        title,
    ):
        return True
    metadata = _metadata(item)
    lanes = [
        metadata.get("lane_id"),
        metadata.get("work_type"),
        metadata.get("work_lane"),
    ]
    execution_lane = metadata.get("execution_lane")
    if isinstance(execution_lane, dict):
        lanes.append(execution_lane.get("lane_id"))
    return any(
        str(value or "").strip().lower().replace("_", "-") in {
            "qa", "qa-validation", "quality-validation", "regression-testing",
            "test-suite", "testing-only",
        }
        for value in lanes
    )


def _queue_order(item):
    status = str(item.get("status") or "queued").lower()
    return (
        0 if status in ACTIVE_STATUSES else 1,
        PRIORITY_RANK.get(str(item.get("priority") or "P3").upper(), 3),
        work_package_rank(item),
        str(item.get("created_at") or ""),
        str(item.get("queue_id") or ""),
    )


def _trust_active_lease(item, now_utc):
    """Never infer a writer has a sound lease from an end time alone."""
    def parsed(value):
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            date = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return date.astimezone(timezone.utc) if date.tzinfo else None
        except (ValueError, OverflowError):
            return None

    start, expiry = parsed(item.get("claimed_at")), parsed(item.get("claim_expires"))
    return start is not None and expiry is not None and start <= now_utc < expiry and start < expiry


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
    deferred_qa_by_lane = {lane["id"]: 0 for lane in lane_defs}
    for item in classified:
        if (profile == "portfolio-recovery"
                and str(item.get("status") or "queued").lower() == "queued"
                and _prohibited_recovery_qa(item)):
            deferred_qa_by_lane[item["lane_id"]] += 1


    # The cross-project zSentinel profile must fail closed if already active
    # work does not declare its actual file/capability scope. A synthetic
    # lane-only capability does not prove that another lane can edit safely.
    # Existing owners remain active; this only suppresses NEW queued admission.
    unknown_active_scopes = [
        item for item in classified
        if profile == "portfolio-recovery"
        and str(item.get("status") or "").lower() in ACTIVE_STATUSES
        and not any(_recovery_scope_from_item(item).values())
    ]
    # Active leases from an older profile must not be reinterpreted into
    # independent four-lane writes, even if someone supplies file scopes.
    # Existing owners are preserved; the runtime owner must reconcile the
    # currently deployed profile and the original worker's claim first.
    mismatched_active_profiles = [
        item for item in classified
        if profile == "portfolio-recovery"
        and str(item.get("status") or "").lower() in ACTIVE_STATUSES
        and recovery_active_profile_mismatch(item)
    ]
    # An active row with an impossible, expired or absent queue lease stays
    # owned by its current worker, but cannot authorize another parallel
    # writer. Only the portfolio-recovery profile has this extra hold.
    observed_now = datetime.now(timezone.utc) if profile == "portfolio-recovery" else None
    untrusted_active_leases = [
        item for item in classified
        if observed_now is not None
        and str(item.get("status") or "").lower() in ACTIVE_STATUSES
        and not _trust_active_lease(item, observed_now)
    ]

    active_claims = []
    unknown_claim_scopes = 0
    for claim in claims or ():
        if str(claim.get("project_id") or project_id).strip().lower() != project_id:
            continue
        scope = (_recovery_scope_from_claim(claim) if profile == "portfolio-recovery"
                 else _scope_from_claim(claim))
        if scope["capabilities"] or scope["files"]:
            active_claims.append({
                "claim_key": str(claim.get("claim_key") or ""),
                "owner_id": str(claim.get("owner_id") or ""),
                "worker_id": str(claim.get("worker_id") or ""),
                "scope": scope,
            })
        elif profile == "portfolio-recovery":
            # An externally supplied claim is still an owner reservation even
            # when its scope is absent/invalid. Never silently discard it and
            # admit another writer to the same unknown file scope.
            unknown_claim_scopes += 1

    lane_items = {}
    for lane_def in lane_defs:
        lane_id = lane_def["id"]
        lane_items[lane_id] = sorted(
            [item for item in classified if item["lane_id"] == lane_id],
            key=_queue_order,
        )

    chosen_by_lane = {}
    blocked_by_lane = {lane_def["id"]: [] for lane_def in lane_defs}
    active_backlog_counts = {lane_def["id"]: 0 for lane_def in lane_defs}
    occupied_scopes = []

    # Existing active work is authoritative: preserve it and reserve its write
    # scope before considering any new queued work.
    for lane_def in lane_defs:
        lane_id = lane_def["id"]
        active = [
            item for item in lane_items[lane_id]
            if str(item.get("status") or "").lower() in ACTIVE_STATUSES
        ]
        active_backlog_counts[lane_id] = len(active)
        if active:
            # The first active task remains the displayed lane owner, but ALL
            # active tasks reserve their write scopes. A second in-flight task
            # must not disappear from cross-lane collision admission just
            # because the UI can display only one task per lane.
            chosen_by_lane[lane_id] = active[0]
            for in_flight in active:
                occupied_scopes.append({
                    "queue_id": str(in_flight.get("queue_id") or ""),
                    "lane_id": lane_id,
                    "scope": in_flight["scope"],
                })

    unscoped_queued_writer_reserved = False
    queued = sorted(
        [
            item for item in classified
            if str(item.get("status") or "queued").lower() == "queued"
            and item["lane_id"] not in chosen_by_lane
            and not (profile == "portfolio-recovery" and _prohibited_recovery_qa(item))
        ],
        key=_queue_order,
    )
    for candidate in queued:
        lane_id = candidate["lane_id"]
        if lane_id in chosen_by_lane:
            continue
        if (unknown_active_scopes or unknown_claim_scopes
                or untrusted_active_leases or mismatched_active_profiles):
            # Existing queue writers and externally supplied claims retain
            # their ownership; neither an empty scope nor a malformed scope
            # proves that a new queued writer is independent.
            if mismatched_active_profiles:
                blocked_by_lane[lane_id].append({
                    "kind": "queue",
                    "reason": "active_legacy_profile_unreconciled",
                    "ambiguous_active_count": len(mismatched_active_profiles),
                    "overlap": {"capabilities": [], "files": []},
                })
            if unknown_active_scopes:
                blocked_by_lane[lane_id].append({
                    "kind": "queue",
                    "reason": "active_write_scope_not_declared",
                    "ambiguous_active_count": len(unknown_active_scopes),
                    "overlap": {"capabilities": [], "files": []},
                })
            if unknown_claim_scopes:
                blocked_by_lane[lane_id].append({
                    "kind": "claim",
                    "reason": "active_claim_write_scope_not_declared",
                    "ambiguous_claim_count": unknown_claim_scopes,
                    "overlap": {"capabilities": [], "files": []},
                })
            if untrusted_active_leases:
                blocked_by_lane[lane_id].append({
                    "kind": "queue",
                    "reason": "active_queue_lease_untrusted",
                    "ambiguous_active_count": len(untrusted_active_leases),
                    "overlap": {"capabilities": [], "files": []},
                })
            continue

        candidate_without_scope = (
            profile == "portfolio-recovery"
            and not any(_recovery_scope_from_item(candidate).values())
        )
        if profile == "portfolio-recovery" and (
            unscoped_queued_writer_reserved
            or (candidate_without_scope and (occupied_scopes or active_claims))
        ):
            # With no declared write scope, cross-lane file independence
            # cannot be proven. Admit at most one such worker, serialized,
            # and never alongside known active/scoped work.
            blocked_by_lane[lane_id].append({
                "kind": "queue",
                "reason": "unscoped_queued_writer_must_be_serial",
                "overlap": {"capabilities": [], "files": []},
            })
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

        chosen_by_lane[lane_id] = candidate
        occupied_scopes.append({
            "queue_id": str(candidate.get("queue_id") or ""),
            "lane_id": lane_id,
            "scope": candidate["scope"],
        })
        if candidate_without_scope:
            unscoped_queued_writer_reserved = True

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
            "status": str(chosen.get("status") or "") if chosen else "blocked" if (blocked_by or deferred_qa_by_lane[lane_id]) else "idle",
            "deferred_qa_packages": deferred_qa_by_lane[lane_id],
            "scope": scope,
            # Advisory reconciliation only: never take, release, or supersede
            # an existing worker claim on the strength of this count.
            "active_backlog_count": active_backlog_counts[lane_id],
            "parallel_active_warning": active_backlog_counts[lane_id] > 1,
            "unknown_active_scope_count": len(unknown_active_scopes),
            "active_profile_mismatch_count": len(mismatched_active_profiles),
            "unknown_claim_scope_count": unknown_claim_scopes,
            "untrusted_active_lease_count": len(untrusted_active_leases),
            "serialized_unscoped_queue_admitted": (
                bool(unscoped_queued_writer_reserved)
                and chosen is not None and chosen.get("status") == "queued"
                and not any(_recovery_scope_from_item(chosen).values())
            ),
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
