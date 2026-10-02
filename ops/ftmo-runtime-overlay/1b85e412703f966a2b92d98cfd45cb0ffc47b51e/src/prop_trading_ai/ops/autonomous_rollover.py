from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from prop_trading_ai.research.immutable import write_immutable_text
from prop_trading_ai.research.provider_validation import compare_canonical_feeds
from prop_trading_ai.research.provider_validation_writer import write_atomic_provider_validation
from prop_trading_ai.research.readiness import freeze_verified_split
from prop_trading_ai.research.splits import ResearchSplit
from prop_trading_ai.research.window_reserve import (
    DEVELOPMENT_DURATION,
    HOLDOUT_DURATION,
    WALK_FORWARD_DURATION,
    ReservedWindow,
    load_window_reserve,
    validate_successor_reserve,
)


class AutonomousRolloverError(RuntimeError):
    pass


class RetryableProviderFoundationUnavailable(AutonomousRolloverError):
    """Known public-provider outage before any strategy outcome execution."""

    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(f"public provider foundation temporarily unavailable: {provider}")


_RESERVE_VERSION = re.compile(r"research_window_reserve_v([0-9]+)\.json$")
_HORIZON = 4
_RETRYABLE_PUBLIC_PROVIDER_ERRORS = {
    "prop_trading_ai.cli.v1_acquire_fxcm_public": (
        "fxcm_public",
        "FxcmPublicUnavailable",
    ),
    "prop_trading_ai.cli.v1_acquire_dukascopy_public": (
        "dukascopy_public",
        "DukascopyPublicUnavailable",
    ),
}


def _retryable_provider_foundation_unavailability(module: str, stderr: str) -> str | None:
    identity = _RETRYABLE_PUBLIC_PROVIDER_ERRORS.get(module)
    if identity is None:
        return None
    provider, marker = identity
    if module == "prop_trading_ai.cli.v1_acquire_fxcm_public" and "FxcmPublicNotFound" in stderr:
        # A canonical 404 is source evidence handled by the FXCM ingestion path,
        # never a generic transient-outage exemption.
        return None
    return provider if marker in stderr else None


def _strict(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise AutonomousRolloverError(f"{label} must be a real JSON file: {path}")

    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise AutonomousRolloverError(f"duplicate JSON key in {label}: {key}")
            out[key] = value
        return out

    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except json.JSONDecodeError as exc:
        raise AutonomousRolloverError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise AutonomousRolloverError(f"{label} must be a JSON object: {path}")
    return raw


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise AutonomousRolloverError(f"path escaped autonomous runtime root: {path}") from exc


def _payload(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reserve_version(path: Path) -> int:
    match = _RESERVE_VERSION.fullmatch(path.name)
    if not match:
        raise AutonomousRolloverError(f"unexpected reserve name: {path.name}")
    return int(match.group(1))


def _window_payload(number: int, previous: ReservedWindow) -> dict[str, Any]:
    start = previous.holdout_end + timedelta(hours=51)
    if start.weekday() != 0 or start.hour != 0:
        raise AutonomousRolloverError("reserve cadence does not produce Monday 00:00 UTC")
    development_end = start + DEVELOPMENT_DURATION
    walk_forward_end = development_end + WALK_FORWARD_DURATION
    holdout_end = walk_forward_end + HOLDOUT_DURATION
    quarter = ((start.month - 1) // 3) + 1
    stamp = lambda value: value.astimezone(timezone.utc).isoformat()
    return {
        "generation_number": number,
        "label": f"g{number}-chronological-{start.year}-q{quarter}",
        "status": "reserved",
        "development_start": stamp(start),
        "development_end": stamp(development_end),
        "walk_forward_start": stamp(development_end),
        "walk_forward_end": stamp(walk_forward_end),
        "holdout_start": stamp(walk_forward_end),
        "holdout_end": stamp(holdout_end),
    }


def latest_reserve(root: Path, base_reserve: Path) -> Path:
    """Return the fully validated append-only reserve head."""
    base_reserve = base_reserve.resolve()
    load_window_reserve(base_reserve)
    chain = root / "artifacts" / "autonomous_runtime" / "reserve_chain"
    if chain.exists() and chain.is_symlink():
        raise AutonomousRolloverError("reserve chain directory must not be a symlink")
    head = base_reserve
    version = _reserve_version(base_reserve)
    while True:
        candidate = chain / f"research_window_reserve_v{version + 1}.json"
        if not candidate.exists():
            return head
        try:
            validate_successor_reserve(head, candidate)
        except ValueError as exc:
            raise AutonomousRolloverError(f"invalid append-only reserve successor: {candidate}") from exc
        head = candidate
        version += 1


def ensure_reserve_horizon(root: Path, base_reserve: Path, *, through_generation: int) -> Path:
    """Append fixed chronological windows before their strategy outcomes exist."""
    head = latest_reserve(root, base_reserve)
    reserve = load_window_reserve(head)
    if reserve.windows[-1].generation_number >= through_generation:
        return head

    raw = _strict(head, "reserve head")
    successor = dict(raw)
    successor["windows"] = list(raw["windows"])
    successor["predecessor_reserve_sha256"] = reserve.sha256
    previous = reserve.windows[-1]
    for number in range(previous.generation_number + 1, through_generation + 1):
        row = _window_payload(number, previous)
        successor["windows"].append(row)
        previous = ReservedWindow(
            generation_number=number,
            label=row["label"],
            status=row["status"],
            development_start=datetime.fromisoformat(row["development_start"]),
            development_end=datetime.fromisoformat(row["development_end"]),
            walk_forward_start=datetime.fromisoformat(row["walk_forward_start"]),
            walk_forward_end=datetime.fromisoformat(row["walk_forward_end"]),
            holdout_start=datetime.fromisoformat(row["holdout_start"]),
            holdout_end=datetime.fromisoformat(row["holdout_end"]),
        )

    target = root / "artifacts" / "autonomous_runtime" / "reserve_chain" / f"research_window_reserve_v{_reserve_version(head) + 1}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    write_immutable_text(target, _payload(successor), artifact_name="append-only chronological reserve")
    try:
        validate_successor_reserve(head, target)
    except ValueError as exc:
        raise AutonomousRolloverError("generated reserve successor did not validate") from exc
    return target


def _window(reserve_path: Path, number: int) -> ReservedWindow:
    reserve = load_window_reserve(reserve_path)
    for window in reserve.windows:
        if window.generation_number == number:
            return window
    raise AutonomousRolloverError(f"generation {number} is absent from reserve {reserve_path}")


def generation_paths(number: int) -> dict[str, str]:
    """Stable, local-only artifact paths for a dynamically activated generation."""
    base = f"artifacts/autonomous_generations/g{number}"
    primary_id = f"eurusd-fxcm-autonomous-g{number}"
    secondary_id = f"eurusd-dukascopy-autonomous-g{number}"
    version = f"g{number}"
    primary = f"{base}/foundation/fxcm/{primary_id}/{version}"
    secondary = f"{base}/foundation/dukascopy/{secondary_id}/{version}"
    return {
        "base": base,
        "primary_dataset": primary,
        "secondary_dataset": secondary,
        "primary_manifest": f"{primary}/manifest.json",
        "primary_quality": f"{primary}/quality.json",
        "primary_bars": f"{primary}/bars.json",
        "primary_inspection": f"{base}/foundation/fxcm-inspection.json",
        "secondary_manifest": f"{secondary}/manifest.json",
        "secondary_quality": f"{secondary}/quality.json",
        "secondary_bars": f"{secondary}/bars.json",
        "secondary_inspection": f"{base}/foundation/dukascopy-inspection.json",
        "provider_validation": f"{base}/foundation/fxcm-vs-dukascopy-validation.json",
        "split": f"{base}/research-split.json",
        "preregistration": f"{base}/preregistration/pack_manifest.json",
        "development_summary": f"{base}/outcomes/development-pack-summary.json",
        "development_review": f"{base}/outcomes/development-review-summary.json",
        "walk_forward_summary": f"{base}/outcomes/walk-forward-summary.json",
        "final_holdout_summary": f"{base}/outcomes/final-holdout-summary.json",
        "closure": f"{base}/outcomes/generation-closure.json",
    }


def evidence_for_generation(number: int) -> dict[str, str]:
    paths = generation_paths(number)
    return {
        "primary_manifest": paths["primary_manifest"],
        "primary_inspection": paths["primary_inspection"],
        "secondary_manifest": paths["secondary_manifest"],
        "secondary_inspection": paths["secondary_inspection"],
        "provider_validation": paths["provider_validation"],
    }


def _atomic_activate(config_path: Path, expected_generation_id: str, active: dict[str, Any]) -> None:
    raw = _strict(config_path, "active research-machine config")
    current = raw.get("active_generation")
    if not isinstance(current, dict) or current.get("generation_id") != expected_generation_id:
        raise AutonomousRolloverError("active research-machine changed during rollover")
    payload = _payload({"schema_version": 1, "active_generation": active}).encode("utf-8")
    temporary = config_path.with_name(config_path.name + ".rollover.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise AutonomousRolloverError("stale rollover temporary file exists; refusing overwrite") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if _strict(config_path, "active research-machine config").get("active_generation", {}).get("generation_id") != expected_generation_id:
            raise AutonomousRolloverError("active research-machine changed before activation")
        os.replace(temporary, config_path)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise


def activate_next_generation(
    root: Path,
    *,
    layout,
    research_config: Path,
    reserve_path: Path,
) -> dict[str, Any]:
    next_number = layout.generation_number + 1
    reserve = load_window_reserve(reserve_path)
    window = _window(reserve_path, next_number)
    paths = generation_paths(next_number)
    next_id = f"eurusd-fxcm-{window.label}-v1-alpha-{next_number}"
    closure = root / layout.closure
    if closure.is_symlink() or not closure.is_file():
        raise AutonomousRolloverError("predecessor closure must be a real immutable file")
    record = {
        "schema_version": 1,
        "scope": "ftmo-autonomous-generation-rollover",
        "predecessor_generation_id": layout.generation_id,
        "predecessor_generation_number": layout.generation_number,
        "predecessor_closure": layout.closure,
        "predecessor_closure_sha256": _sha256(closure),
        "next_generation_id": next_id,
        "next_generation_number": next_number,
        "reserve_config": _relative(root, reserve_path),
        "reserve_sha256": reserve.sha256,
        "reserved_window": {
            "development_start": window.development_start.isoformat(),
            "development_end": window.development_end.isoformat(),
            "walk_forward_start": window.walk_forward_start.isoformat(),
            "walk_forward_end": window.walk_forward_end.isoformat(),
            "holdout_start": window.holdout_start.isoformat(),
            "holdout_end": window.holdout_end.isoformat(),
        },
        "strategy_outcomes_used_for_design": False,
        "final_holdout_feedback_used": False,
        "requires_new_provider_evidence_and_new_split": True,
        "live_trading_allowed": False,
        "broker_order_submission_allowed": False,
    }
    record_path = root / paths["base"] / "rollover.json"
    write_immutable_text(record_path, _payload(record), artifact_name="generation rollover record")
    active = {
        "generation_number": next_number,
        "generation_id": next_id,
        "predecessor_generation_id": layout.generation_id,
        "predecessor_closure": layout.closure,
        "provider_evidence": [
            paths["primary_manifest"], paths["primary_quality"], paths["primary_bars"], paths["primary_inspection"],
            paths["secondary_manifest"], paths["secondary_quality"], paths["secondary_bars"], paths["secondary_inspection"],
            paths["provider_validation"],
        ],
        "split": paths["split"],
        "preregistration": paths["preregistration"],
        "development_summary": paths["development_summary"],
        "development_review": paths["development_review"],
        "walk_forward_summary": paths["walk_forward_summary"],
        "walk_forward_pass_field": "pass_count",
        "final_holdout_summary": paths["final_holdout_summary"],
        "closure": paths["closure"],
    }
    _atomic_activate(research_config, layout.generation_id, active)
    return {
        "action": "next_generation_activated",
        "next_generation_number": next_number,
        "next_generation_id": next_id,
        "reserve_config": _relative(root, reserve_path),
        "reserve_horizon_last_generation": reserve.windows[-1].generation_number,
        "outcomes_executed": False,
    }


def run_provider_foundation(root: Path, *, number: int, reserve_path: Path) -> dict[str, Any]:
    paths = generation_paths(number)
    window = _window(reserve_path, number)
    primary_files = [paths[key] for key in ("primary_manifest", "primary_quality", "primary_bars", "primary_inspection")]
    secondary_files = [paths[key] for key in ("secondary_manifest", "secondary_quality", "secondary_bars", "secondary_inspection")]
    real = lambda value: (root / value).is_file() and not (root / value).is_symlink()
    primary_ready, secondary_ready = all(map(real, primary_files)), all(map(real, secondary_files))
    if any(map(real, primary_files)) and not primary_ready:
        raise AutonomousRolloverError("partial immutable FXCM foundation exists; refusing overwrite")
    if any(map(real, secondary_files)) and not secondary_ready:
        raise AutonomousRolloverError("partial immutable Dukascopy foundation exists; refusing overwrite")

    start = window.development_start.date().isoformat()
    end = window.holdout_end.date().isoformat()
    commands = (
        (not primary_ready, "prop_trading_ai.cli.v1_acquire_fxcm_public", paths["base"] + "/foundation/fxcm", f"eurusd-fxcm-autonomous-g{number}", paths["primary_inspection"]),
        (not secondary_ready, "prop_trading_ai.cli.v1_acquire_dukascopy_public", paths["base"] + "/foundation/dukascopy", f"eurusd-dukascopy-autonomous-g{number}", paths["secondary_inspection"]),
    )
    for needed, module, output_base, dataset_id, inspection in commands:
        if not needed:
            continue
        command = [
            sys.executable, "-m", module, "--start", start, "--end", end,
            "--output-base", output_base, "--dataset-id", dataset_id, "--version", f"g{number}",
            "--instrument", "EURUSD", "--inspection-output", inspection,
        ]
        result = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=3600)
        if result.returncode:
            retryable_provider = _retryable_provider_foundation_unavailability(
                module,
                result.stderr,
            )
            if retryable_provider is not None:
                raise RetryableProviderFoundationUnavailable(retryable_provider)
            raise AutonomousRolloverError(
                f"provider foundation failed closed for generation {number}: {result.stderr[-3000:]}"
            )

    primary = root / paths["primary_dataset"]
    secondary = root / paths["secondary_dataset"]
    validation_path = root / paths["provider_validation"]
    try:
        validation = compare_canonical_feeds(primary, secondary)
        write_atomic_provider_validation(validation_path, validation)
    except Exception as exc:
        raise AutonomousRolloverError(f"provider validation failed closed for generation {number}") from exc
    return {
        "action": "provider_foundation_verified" if validation.accepted else "provider_foundation_rejected",
        "outcomes_executed": False,
        "provider_validation_accepted": bool(validation.accepted),
    }


def freeze_generation_split(root: Path, *, number: int, reserve_path: Path) -> dict[str, Any]:
    paths = generation_paths(number)
    window = _window(reserve_path, number)
    primary = root / paths["primary_dataset"]
    inspection = root / paths["primary_inspection"]
    split_path = root / paths["split"]
    split = ResearchSplit(
        dataset_hash=json.loads((primary / "manifest.json").read_text(encoding="utf-8"))["manifest_hash"],
        development_start=window.development_start,
        development_end=window.development_end,
        walk_forward_start=window.walk_forward_start,
        walk_forward_end=window.walk_forward_end,
        holdout_start=window.holdout_start,
        holdout_end=window.holdout_end,
        inspection_sha256=_sha256(inspection),
    )
    try:
        freeze_verified_split(primary, inspection, split_path, split)
    except Exception as exc:
        raise AutonomousRolloverError(f"split freeze failed closed for generation {number}") from exc
    return {"action": "research_split_frozen", "outcomes_executed": False, "split_hash": split.split_hash}


def reserve_through_next_horizon(root: Path, base_reserve: Path, generation_number: int) -> Path:
    return ensure_reserve_horizon(root, base_reserve, through_generation=generation_number + _HORIZON)
