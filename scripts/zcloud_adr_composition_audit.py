#!/usr/bin/env python3
"""Audit the partial-overlap composition contract for zCloud ADR guards."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

POLICY = "adr-guard-composition-v1"
CAPABILITY = "high-blast-radius-adr-composition"
EXPECTED_SHARED = {"deletion_inclusive_diff", "immutable_read_only_ci"}
EXPECTED_OWNER_CONTROLS = {
    "#589": {
        "bootstrap_adr_seed",
        "numbered_adr_lifecycle",
        "status_enum",
        "transactional_blast_classifier_parity",
    },
    "#709": {
        "bounded_output",
        "exact_risk_path_coverage",
        "permanent_vps_proof",
        "placeholder_rejection",
        "symlink_rejection",
    },
}
EXPECTED_BRANCHES = {
    "#589": "worker/deploy-ops-high-blast-adr-gate-20261006",
    "#709": "feature/control-plane-blast-radius-adr-guard-20261006",
}
EXPECTED_CONTROLS = EXPECTED_SHARED | set().union(*EXPECTED_OWNER_CONTROLS.values())
EXPECTED_RESOLUTION = {
    "mode": "compose-before-retire",
    "replacement_owner_required": True,
    "retire_only_after_composite_green": True,
    "serialized_live_mutation": False,
}
TOP_KEYS = {
    "schema_version",
    "capability",
    "owners",
    "shared_controls",
    "required_controls",
    "resolution",
}
OWNER_KEYS = {"ref", "branch", "role", "unique_controls"}
REF_RE = re.compile(r"^#[1-9][0-9]{0,6}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,180}$")

SOURCE_MARKERS = {
    "#589": {
        "scripts/zcloud_adr_guard.py": (
            "promotion_blast_radius",
            "--diff-filter=ACMRD",
            "ALLOWED_STATUSES",
        ),
        "docs/adr/0001-high-blast-architecture-decisions.md": ("# ADR-0001:",),
        ".github/workflows/zcloud-high-blast-adr.yml": (
            "permissions:\n  contents: read",
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            "persist-credentials: false",
        ),
    },
    "#709": {
        "scripts/zcloud_high_blast_radius_adr_guard.py": (
            "adr_missing_risk_coverage",
            "adr_symlink_rejected",
            "_PLACEHOLDER_RE",
            "--diff-filter=ACMRD",
        ),
        ".github/workflows/zcloud-high-blast-radius-adr.yml": (
            "permissions:\n  contents: read",
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
            "persist-credentials: false",
            "runs-on: [self-hosted, zcloud, vps]",
            "scripts/zcloud_vps_runner_guard.py --json",
        ),
    },
}


class CompositionAuditError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _safe_path(raw: str) -> str:
    value = str(raw or "").replace("\\", "/").strip()
    parts = value.split("/")
    if (
        not value
        or value.startswith("/")
        or ".." in parts
        or any(not part for part in parts)
        or len(value) > 320
    ):
        raise CompositionAuditError("unsafe_path")
    return value


def _read_bounded(path: Path, *, max_bytes: int, missing_code: str) -> str:
    if path.is_symlink():
        raise CompositionAuditError("symlink_rejected")
    if not path.is_file():
        raise CompositionAuditError(missing_code)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CompositionAuditError("source_stat_failed") from exc
    if size > max_bytes:
        raise CompositionAuditError("source_too_large")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CompositionAuditError("source_unreadable") from exc


def load_contract(path: Path) -> dict[str, Any]:
    text = _read_bounded(path, max_bytes=32_000, missing_code="contract_missing")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CompositionAuditError("contract_invalid_json") from exc
    if not isinstance(payload, dict):
        raise CompositionAuditError("contract_not_object")
    if set(payload) != TOP_KEYS:
        raise CompositionAuditError("contract_schema_keys")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise CompositionAuditError("contract_schema_version")
    if payload["capability"] != CAPABILITY:
        raise CompositionAuditError("contract_capability")

    owners = payload["owners"]
    if not isinstance(owners, list) or len(owners) != 2:
        raise CompositionAuditError("contract_owner_count")
    seen: set[str] = set()
    for owner in owners:
        if not isinstance(owner, dict) or set(owner) != OWNER_KEYS:
            raise CompositionAuditError("contract_owner_schema")
        ref = owner["ref"]
        branch = owner["branch"]
        role = owner["role"]
        controls = owner["unique_controls"]
        if not isinstance(ref, str) or not REF_RE.fullmatch(ref):
            raise CompositionAuditError("contract_owner_ref")
        if ref in seen:
            raise CompositionAuditError("contract_duplicate_owner")
        seen.add(ref)
        if ref not in EXPECTED_OWNER_CONTROLS:
            raise CompositionAuditError("contract_unknown_owner")
        if (
            not isinstance(branch, str)
            or not BRANCH_RE.fullmatch(branch)
            or branch != EXPECTED_BRANCHES[ref]
        ):
            raise CompositionAuditError("contract_owner_branch")
        if not isinstance(role, str) or not role or len(role) > 64:
            raise CompositionAuditError("contract_owner_role")
        if not isinstance(controls, list) or any(not isinstance(v, str) for v in controls):
            raise CompositionAuditError("contract_owner_controls")
        if len(controls) != len(set(controls)):
            raise CompositionAuditError("contract_duplicate_control")
        if set(controls) != EXPECTED_OWNER_CONTROLS[ref]:
            raise CompositionAuditError("contract_owner_control_drift")

    if seen != set(EXPECTED_OWNER_CONTROLS):
        raise CompositionAuditError("contract_owner_set")

    shared = payload["shared_controls"]
    required = payload["required_controls"]
    if not isinstance(shared, list) or any(not isinstance(v, str) for v in shared):
        raise CompositionAuditError("contract_shared_controls")
    if not isinstance(required, list) or any(not isinstance(v, str) for v in required):
        raise CompositionAuditError("contract_required_controls")
    if len(shared) != len(set(shared)) or len(required) != len(set(required)):
        raise CompositionAuditError("contract_duplicate_control")
    if set(shared) != EXPECTED_SHARED:
        raise CompositionAuditError("contract_shared_control_drift")
    if set(required) != EXPECTED_CONTROLS:
        raise CompositionAuditError("contract_required_control_drift")
    if payload["resolution"] != EXPECTED_RESOLUTION:
        raise CompositionAuditError("contract_resolution_drift")
    return payload


def verify_owner_source(ref: str, root: Path) -> int:
    if ref not in SOURCE_MARKERS:
        raise CompositionAuditError("unknown_source_owner")
    if root.is_symlink() or not root.is_dir():
        raise CompositionAuditError("source_root_invalid")

    marker_count = 0
    for relative, markers in SOURCE_MARKERS[ref].items():
        safe = _safe_path(relative)
        text = _read_bounded(
            root / safe,
            max_bytes=160_000,
            missing_code=f"source_file_missing_{ref[1:]}",
        )
        for marker in markers:
            if marker not in text:
                raise CompositionAuditError(f"source_marker_missing_{ref[1:]}")
            marker_count += 1
    return marker_count


def audit(
    contract_path: Path,
    *,
    source_root_589: Path | None = None,
    source_root_709: Path | None = None,
) -> dict[str, Any]:
    contract = load_contract(contract_path)
    roots = {"#589": source_root_589, "#709": source_root_709}
    marker_counts: dict[str, int] = {}
    sources_verified = True

    for ref in ("#589", "#709"):
        root = roots[ref]
        if root is None:
            sources_verified = False
            marker_counts[ref] = 0
            continue
        marker_counts[ref] = verify_owner_source(ref, root.resolve())

    return {
        "schema_version": 1,
        "policy": POLICY,
        "capability": contract["capability"],
        "decision": "COMPOSITION_REQUIRED",
        "owner_refs": ["#589", "#709"],
        "required_control_count": len(EXPECTED_CONTROLS),
        "shared_control_count": len(EXPECTED_SHARED),
        "source_marker_count": sum(marker_counts.values()),
        "sources_verified": sources_verified,
        "retire_only_after_composite_green": True,
        "mutation_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path("ops/adr_guard_composition_requirements.json"),
    )
    parser.add_argument("--source-root-589", type=Path)
    parser.add_argument("--source-root-709", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = audit(
            args.contract.resolve(),
            source_root_589=args.source_root_589,
            source_root_709=args.source_root_709,
        )
    except CompositionAuditError as exc:
        print(json.dumps({"ok": False, "code": exc.code}, sort_keys=True, separators=(",", ":")))
        return 2

    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(
            "ZCLOUD_ADR_COMPOSITION "
            f"decision={result['decision']} "
            f"owners={len(result['owner_refs'])} "
            f"controls={result['required_control_count']} "
            f"sources_verified={str(result['sources_verified']).lower()}"
        )

    if args.require_complete and not result["sources_verified"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
