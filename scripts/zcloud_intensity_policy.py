#!/usr/bin/env python3
"""Deterministic, side-effect-free mapping from UI intensity to scheduler intent."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACTS = ROOT / "project-contracts.json"
POLICY_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class IntensityTier:
    name: str
    minimum: int
    maximum: int
    queue_weight: int
    dispatch_interval_multiplier: float
    cpu_target_fraction: float
    idle_borrow: bool
    burst_eligible: bool
    backpressure_bias: str


TIERS = (
    IntensityTier("minimum", 0, 20, 25, 2.0, 0.35, False, False, "early"),
    IntensityTier("conservative", 21, 40, 50, 1.5, 0.50, True, False, "early"),
    IntensityTier("balanced", 41, 60, 100, 1.0, 0.70, True, False, "standard"),
    IntensityTier("accelerated", 61, 80, 150, 0.75, 0.85, True, True, "standard"),
    IntensityTier("maximum", 81, 100, 200, 0.5, 1.00, True, True, "late"),
)


class PolicyError(ValueError):
    """Raised when an intensity request cannot be mapped safely."""


def _tier_for(intensity: int) -> IntensityTier:
    if isinstance(intensity, bool) or not isinstance(intensity, int):
        raise PolicyError("intensity_not_integer")
    if not 0 <= intensity <= 100:
        raise PolicyError("intensity_out_of_range")
    for tier in TIERS:
        if tier.minimum <= intensity <= tier.maximum:
            return tier
    raise PolicyError("intensity_unmapped")


def _load_contracts(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("projects"), dict):
        raise PolicyError("contracts_projects_missing")
    return payload


def _effective_worker_cap(project: dict[str, Any], contracts: dict[str, Any]) -> int:
    defaults = contracts.get("defaults")
    default_cap = defaults.get("ai_worker_cap") if isinstance(defaults, dict) else None
    cap = project.get("ai_worker_cap", default_cap)
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
        raise PolicyError("ai_worker_cap_invalid")
    return cap


def policy_for(
    project_id: str,
    intensity: int,
    *,
    contracts: dict[str, Any],
) -> dict[str, Any]:
    project_id = str(project_id or "").strip()
    if not project_id:
        raise PolicyError("project_missing")

    projects = contracts.get("projects")
    if not isinstance(projects, dict) or project_id not in projects:
        raise PolicyError("project_unknown")

    project = projects[project_id]
    if not isinstance(project, dict):
        raise PolicyError("project_contract_invalid")

    compute = project.get("compute")
    if not isinstance(compute, dict):
        raise PolicyError("compute_contract_missing")

    pool = str(compute.get("pool") or "").strip()
    if not pool:
        raise PolicyError("compute_pool_missing")
    resource_pools = contracts.get("resource_pools")
    if not isinstance(resource_pools, dict) or pool not in resource_pools:
        raise PolicyError("compute_pool_unknown")

    cpu_raw = compute.get("cpu_soft_cores")
    if isinstance(cpu_raw, bool) or not isinstance(cpu_raw, (int, float)):
        raise PolicyError("cpu_soft_cores_invalid")
    cpu_soft_cores = float(cpu_raw)
    if not math.isfinite(cpu_soft_cores) or cpu_soft_cores < 0:
        raise PolicyError("cpu_soft_cores_invalid")

    protected_raw = compute.get("protected", False)
    if not isinstance(protected_raw, bool):
        raise PolicyError("protected_invalid")
    protected = protected_raw

    ai_worker_cap = _effective_worker_cap(project, contracts)
    disabled = pool == "disabled" or cpu_soft_cores == 0
    tier = _tier_for(intensity)

    if disabled:
        cpu_target = 0.0
    else:
        cpu_target = min(
            cpu_soft_cores,
            max(0.25, round(cpu_soft_cores * tier.cpu_target_fraction, 2)),
        )

    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "project_id": project_id,
        "intensity": intensity,
        "tier": tier.name,
        "scheduler": {
            "queue_weight": tier.queue_weight,
            "dispatch_interval_multiplier": tier.dispatch_interval_multiplier,
            "cpu_target_cores": cpu_target,
            "allow_idle_capacity_borrow": bool(tier.idle_borrow and not disabled),
            "burst_eligible": bool(
                tier.burst_eligible and not disabled and not protected
            ),
            "backpressure_bias": tier.backpressure_bias,
        },
        "guardrails": {
            "pool": pool,
            "cpu_soft_cores_max": cpu_soft_cores,
            "protected": protected,
            "ai_worker_cap": ai_worker_cap,
            "slider_may_override_worker_cap": False,
            "slider_may_override_pool_admission": False,
            "slider_may_override_memory_guard": False,
            "slider_may_preempt_protected_capacity": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Map a zCloud per-project intensity value to bounded scheduler intent"
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--intensity", required=True, type=int)
    parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = policy_for(
            args.project,
            args.intensity,
            contracts=_load_contracts(args.contracts),
        )
    except (OSError, json.JSONDecodeError, PolicyError) as exc:
        error = str(exc) if isinstance(exc, PolicyError) else "contracts_unreadable"
        if args.json:
            print(json.dumps({"ok": False, "error": error}, sort_keys=True))
        else:
            print(f"ZCLOUD_INTENSITY_POLICY_BLOCKED error={error}")
        return 2

    if args.json:
        print(json.dumps({"ok": True, "policy": result}, sort_keys=True))
    else:
        scheduler = result["scheduler"]
        print(
            "ZCLOUD_INTENSITY_POLICY_GREEN",
            f"project={result['project_id']}",
            f"intensity={result['intensity']}",
            f"tier={result['tier']}",
            f"queue_weight={scheduler['queue_weight']}",
            f"cpu_target_cores={scheduler['cpu_target_cores']}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
