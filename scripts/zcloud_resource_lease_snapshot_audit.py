"""Offline, deny-first zCloud resource-lease snapshot consistency audit.

Inputs are exported JSON fixtures; no SQLite, network, runner, or service access.
An internally consistent snapshot is observation evidence, NEVER a mutation permit.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path


MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_SNAPSHOT_AGE_SECONDS = 300
_OWNER_STORAGE_LIMIT = 200


def _strict_json(text):
    def reject_duplicate_keys(pairs):
        output = {}
        for key, value in pairs:
            if key in output:
                raise ValueError("duplicate_json_key")
            output[key] = value
        return output

    def reject_nonfinite(token):
        raise ValueError("nonfinite_json_number")
    return json.loads(text, object_pairs_hook=reject_duplicate_keys, parse_constant=reject_nonfinite)


def _instant(value):
    if not isinstance(value, str) or not value:
        raise ValueError("invalid_timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone_required")
    return parsed.astimezone(timezone.utc)


def _nonnegative_integer(value):
    return type(value) is int and value >= 0


def _identity(item):
    if not isinstance(item, dict):
        raise ValueError("invalid_lease")
    fields = ("project_id", "owner_id", "pool")
    if any(not isinstance(item.get(k), str) or not item[k] or item[k] != item[k].strip() for k in fields):
        raise ValueError("invalid_lease_identity")
    if any(ord(ch) < 32 or ord(ch) == 127 for k in fields for ch in item[k]):
        raise ValueError("unsafe_control_character_in_identity")
    return tuple(item[k] for k in fields)


def audit_snapshot(snapshot, contracts, *, now=None, max_age_seconds=MAX_SNAPSHOT_AGE_SECONDS):
    """Return only codes and counts, never owner identifiers or lease metadata."""
    errors = set()
    # Callers must not weaken the five-minute review boundary with floats,
    # booleans, extreme integers, or a disabled age threshold.
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= MAX_SNAPSHOT_AGE_SECONDS:
        errors.add("invalid_audit_age_limit")
        max_age_seconds = MAX_SNAPSHOT_AGE_SECONDS
    if not isinstance(snapshot, dict) or not isinstance(contracts, dict):
        return {"ready_for_review": False, "safe_to_act": False,
                "mutation_performed": False, "errors": ["invalid_root"], "lease_count": 0}
    try:
        now_dt = _instant(now) if now is not None else datetime.now(timezone.utc)
        observed = _instant(snapshot.get("time"))
        if observed > now_dt:
            errors.add("future_snapshot")
        if (now_dt - observed).total_seconds() > max_age_seconds:
            errors.add("stale_snapshot")
    except (ValueError, TypeError, OverflowError):
        errors.add("invalid_snapshot_timestamp")
        observed = None

    pools = snapshot.get("pools")
    leases = snapshot.get("leases")
    pool_contracts = contracts.get("resource_pools")
    if type(contracts.get("schema_version")) is not int or contracts["schema_version"] != 1 or not isinstance(pool_contracts, dict) or not pool_contracts:
        errors.add("invalid_contracts")
        pool_contracts = {}
    if not isinstance(pools, dict) or not isinstance(leases, list):
        errors.add("invalid_snapshot_shape")
        pools, leases = {}, []
    if set(pools) != set(pool_contracts):
        errors.add("pool_registry_mismatch")

    project_contracts = contracts.get("projects")
    if not isinstance(project_contracts, dict) or not project_contracts:
        errors.add("invalid_project_registry")
        project_contracts = {}

    # A snapshot with no active leases must not hide a malformed contract for
    # an idle project: validate every registered project's compute binding.
    for project, contract in project_contracts.items():
        if not isinstance(project, str) or not project or project != project.strip():
            errors.add("invalid_project_contract_identity")
            continue
        compute = contract.get("compute") if isinstance(contract, dict) else None
        if not isinstance(compute, dict) or not isinstance(compute.get("pool"), str) or compute["pool"] not in pool_contracts:
            errors.add("invalid_project_compute_pool")

    contract_caps = {}
    for pool, cfg in pool_contracts.items():
        slots = cfg.get("slots") if isinstance(cfg, dict) else None
        if not _nonnegative_integer(slots):
            errors.add("invalid_contract_capacity")
        else:
            contract_caps[pool] = slots

    indexed = {}
    canonical_owners = set()
    for lease in leases:
        try:
            project, owner, pool = _identity(lease)
            identity = (project, owner, pool)
            if identity in indexed:
                errors.add("duplicate_lease")
            # DB primary key is (project_id, owner_id), irrespective of pool.
            if (project, owner) in canonical_owners:
                errors.add("owner_assigned_multiple_pools")
            canonical_owners.add((project, owner))
            indexed[identity] = lease
            if len(owner) >= _OWNER_STORAGE_LIMIT:
                # Runtime currently truncates newly inserted owner IDs at 200.
                # At the storage limit, the original caller identity is unknowable.
                errors.add("ambiguous_owner_storage_boundary")
            if pool not in pool_contracts:
                errors.add("unknown_lease_pool")
            contract = project_contracts.get(project)
            if not isinstance(contract, dict):
                errors.add("unknown_lease_project")
            else:
                compute = contract.get("compute")
                if not isinstance(compute, dict) or compute.get("pool") != pool:
                    errors.add("lease_pool_contract_mismatch")
            if observed is not None:
                acquired = _instant(lease.get("acquired_at"))
                expiry = _instant(lease.get("lease_until"))
                if acquired > observed or expiry <= observed or expiry <= acquired:
                    errors.add("invalid_lease_interval")
            cpu = lease.get("cpu_soft_cores")
            if type(cpu) not in (int, float) or cpu < 0 or (type(cpu) is float and not isfinite(cpu)):
                errors.add("invalid_lease_cpu")
            if not _nonnegative_integer(lease.get("memory_soft_mb")):
                errors.add("invalid_lease_memory")
            workload = lease.get("workload_class")
            if not isinstance(workload, str) or not workload or workload != workload.strip():
                errors.add("invalid_workload_class")
            if not isinstance(lease.get("metadata"), dict):
                errors.add("invalid_lease_metadata")
        except (ValueError, TypeError, KeyError, OverflowError):
            errors.add("invalid_lease")
    if len(indexed) != len(leases):
        errors.add("invalid_lease_identity_uniqueness")

    for pool, details in pools.items():
        if not isinstance(details, dict):
            errors.add("invalid_pool_record")
            continue
        capacity, used, available = (details.get(k) for k in ("capacity", "used", "available"))
        if not all(_nonnegative_integer(x) for x in (capacity, used, available)):
            errors.add("invalid_pool_counters")
            continue
        if pool not in contract_caps or capacity != contract_caps[pool]:
            errors.add("capacity_contract_mismatch")
        if used > capacity or available != capacity - used:
            errors.add("invalid_pool_capacity_arithmetic")
        holders = details.get("holders")
        if not isinstance(holders, list):
            errors.add("invalid_holders")
            continue
        holder_ids = []
        for holder in holders:
            try:
                identity = _identity(holder)
                holder_ids.append(identity)
                if identity[2] != pool:
                    errors.add("holder_pool_mismatch")
                if not isinstance(holder.get("metadata"), dict):
                    errors.add("invalid_holder_metadata")
                if identity not in indexed:
                    errors.add("holder_missing_from_leases")
                elif any(
                    holder.get(field) != indexed[identity].get(field)
                    for field in ("acquired_at", "lease_until", "cpu_soft_cores",
                                  "memory_soft_mb", "workload_class", "metadata")
                ):
                    errors.add("holder_lease_data_mismatch")
            except ValueError:
                errors.add("invalid_holder_identity")
        if len(set(holder_ids)) != len(holder_ids):
            errors.add("duplicate_holder")
        expected = {key for key in indexed if key[2] == pool}
        if set(holder_ids) != expected or len(holders) != used:
            errors.add("holder_count_or_set_mismatch")

    return {
        "ready_for_review": not errors,
        "safe_to_act": False,
        "mutation_performed": False,
        "errors": sorted(errors),
        "lease_count": len(leases),
    }


def _load(path):
    # Enforce the bound *during* input, not after an unbounded read into RAM.
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("oversized_input")
    return _strict_json(raw.decode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, help="Existing, exported resource_status JSON")
    parser.add_argument("--contracts", required=True, help="Read-only project-contracts JSON")
    parser.add_argument("--now", help="Explicit timezone-aware audit timestamp for offline replay")
    args = parser.parse_args(argv)
    try:
        result = audit_snapshot(_load(args.snapshot), _load(args.contracts), now=args.now)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
        result = {"ready_for_review": False, "safe_to_act": False,
                  "mutation_performed": False, "errors": ["input_unreadable_or_ambiguous"],
                  "lease_count": 0}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["ready_for_review"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
