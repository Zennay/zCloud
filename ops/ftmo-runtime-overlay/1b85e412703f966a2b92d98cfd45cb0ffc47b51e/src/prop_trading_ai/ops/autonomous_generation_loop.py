from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

from prop_trading_ai.data.market.io import load_dataset
from prop_trading_ai.research.development_runner import execute_verified_development_variant
from prop_trading_ai.research.generation_closure import (
    CLOSED_DEVELOPMENT_REJECT,
    CLOSED_FINAL_HOLDOUT,
    CLOSED_WALK_FORWARD_REJECT,
    GenerationClosureSpec,
    freeze_generation_closure,
)
from prop_trading_ai.research.holdout import (
    HoldoutRelease,
    authorize_holdout,
    freeze_holdout_release,
    load_holdout_release,
)
from prop_trading_ai.research.hypothesis_runner import evaluate_hypothesis_variant
from prop_trading_ai.research.immutable import write_immutable_text
from prop_trading_ai.research.preregistration import (
    ExperimentSpec,
    freeze_verified_experiment,
    load_experiment,
)
from prop_trading_ai.research.promotion import (
    build_candidate_review,
    freeze_candidate_review,
    load_candidate_review,
)
from prop_trading_ai.research.provider_gate import require_provider_gate
from prop_trading_ai.research.autonomous_provider_skip import (
    build_autonomous_provider_window_skip,
    coverage_failure_reasons,
    freeze_autonomous_provider_window_skip,
)
from prop_trading_ai.research.provider_validation import load_provider_validation
from prop_trading_ai.research.readiness import verify_research_readiness
from prop_trading_ai.research.window_reserve import load_window_reserve
from prop_trading_ai.ops.autonomous_rollover import (
    RetryableProviderFoundationUnavailable,
    activate_next_generation,
    evidence_for_generation,
    freeze_generation_split,
    generation_paths,
    reserve_through_next_horizon,
    run_provider_foundation,
)
from prop_trading_ai.research.research_machine import load_generation_layout
from prop_trading_ai.research.splits import load_split
from prop_trading_ai.research.trials import trial_evidence_hashes
from prop_trading_ai.research.walk_forward_stage import (
    build_walk_forward_review,
    execute_verified_walk_forward_candidate,
    freeze_walk_forward_review,
    load_walk_forward_review,
    load_walk_forward_run,
)


class AutonomousGenerationLoopError(RuntimeError):
    pass


RECIPE_VERSION = "v1-diversity-first-4x1"
FAMILIES = (
    ("breakout", "lookback=30m|entry_buffer=0.5pip|max_hold=45m"),
    ("momentum", "lookback=60m|move_threshold=0.50atr|max_hold=45m"),
    ("pullback", "trend=ema20>ema80|pullback=0.25atr|max_hold=45m"),
    ("mean_reversion", "lookback=60m|entry_z=2.0|exit_z=0.5|max_hold=45m"),
)
MIN_DEVELOPMENT_TRADES = 15
MIN_WALK_FORWARD_TRADES = 5
COST_MULTIPLIERS = (1.0, 1.5, 2.0)


def _strict(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise AutonomousGenerationLoopError(f"{label} must be a real JSON file: {path}")

    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise AutonomousGenerationLoopError(f"duplicate JSON key in {label}: {key}")
            out[key] = value
        return out

    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except json.JSONDecodeError as exc:
        raise AutonomousGenerationLoopError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise AutonomousGenerationLoopError(f"{label} must be a JSON object: {path}")
    return raw


def _safe(root: Path, value: str, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise AutonomousGenerationLoopError(f"{field} must be a relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise AutonomousGenerationLoopError(f"{field} must stay inside runtime root")
    target = (root / path).resolve(strict=False)
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise AutonomousGenerationLoopError(f"{field} escaped runtime root") from exc
    return target


def _payload(path: Path, value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _zero_cost_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "FTMO_AUTONOMOUS_CLOUD_BUDGET_USD": "0.00",
            "FTMO_ALLOW_PAID_AWS": "",
            "PYTHONHASHSEED": "0",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
    )
    return env


def _evidence(config: dict[str, Any], key: str) -> str:
    evidence = config.get("evidence")
    if not isinstance(evidence, dict) or not isinstance(evidence.get(key), str):
        raise AutonomousGenerationLoopError(f"generation-loop evidence.{key} is required")
    return evidence[key]


def _find_materialized(base: Path, *, manifest_hash: str | None = None, exclude: set[Path] = set()) -> Path | None:
    if not base.exists():
        return None
    found = []
    for manifest in sorted(base.rglob("manifest.json")):
        if manifest.is_symlink() or not manifest.is_file():
            raise AutonomousGenerationLoopError(f"materialized manifest must be a real file: {manifest}")
        if manifest.parent in exclude:
            continue
        raw = _strict(manifest, "materialized dataset manifest")
        if manifest_hash is None or raw.get("manifest_hash") == manifest_hash:
            found.append(manifest.parent)
    if len(found) > 1:
        raise AutonomousGenerationLoopError(f"ambiguous materialized dataset evidence under {base}")
    return found[0] if found else None


def _materialize(root: Path, config: dict[str, Any], split_hash: str, dataset_hash: str) -> tuple[Path, Path]:
    if config.get("dynamic_generation") is True:
        primary = _safe(root, _evidence(config, "primary_dataset"), "dynamic primary dataset")
        secondary = _safe(root, _evidence(config, "secondary_dataset"), "dynamic secondary dataset")
        if primary.is_symlink() or secondary.is_symlink() or not (primary / "manifest.json").is_file() or not (secondary / "manifest.json").is_file():
            raise AutonomousGenerationLoopError("dynamic canonical provider datasets are incomplete")
        split = load_split(_safe(root, config["split"], "split"))
        require_provider_gate(
            primary / "manifest.json",
            expected_dataset_hash=split.dataset_hash,
            provider_validation_path=_safe(root, _evidence(config, "provider_validation"), "provider_validation"),
            primary_dataset_dir=primary,
            secondary_dataset_dir=secondary,
        )
        return primary, secondary
    base = _safe(root, config.get("materialized_data_root", ".scratch/autonomy/generations"), "materialized_data_root")
    generation_dir = base / str(config.get("generation_number", "active"))
    datasets = generation_dir / "datasets"
    datasets.mkdir(parents=True, exist_ok=True)

    primary = _find_materialized(datasets, manifest_hash=dataset_hash)
    if primary is None:
        command = [
            sys.executable,
            "-m",
            "prop_trading_ai.cli.v1_materialize_dataset_evidence",
            "--manifest",
            _safe(root, _evidence(config, "primary_manifest"), "primary_manifest").relative_to(root).as_posix(),
            "--bars-gzip",
            _safe(root, _evidence(config, "primary_bars_gzip"), "primary_bars_gzip").relative_to(root).as_posix(),
            "--quality",
            _safe(root, _evidence(config, "primary_quality"), "primary_quality").relative_to(root).as_posix(),
            "--inspection",
            _safe(root, _evidence(config, "primary_inspection"), "primary_inspection").relative_to(root).as_posix(),
            "--output-base",
            datasets.relative_to(root).as_posix(),
        ]
        result = subprocess.run(command, cwd=root, env=_zero_cost_env(), text=True, capture_output=True, timeout=900)
        if result.returncode:
            raise AutonomousGenerationLoopError(f"primary dataset materialization failed: {result.stderr[-3000:]}")
        primary = _find_materialized(datasets, manifest_hash=dataset_hash)
    if primary is None:
        raise AutonomousGenerationLoopError("primary canonical dataset was not materialized")

    secondary = _find_materialized(datasets, exclude={primary})
    if secondary is None:
        command = [
            sys.executable,
            "-m",
            "prop_trading_ai.cli.v1_materialize_dataset_evidence",
            "--manifest",
            _safe(root, _evidence(config, "secondary_manifest"), "secondary_manifest").relative_to(root).as_posix(),
            "--bars-gzip",
            _safe(root, _evidence(config, "secondary_bars_gzip"), "secondary_bars_gzip").relative_to(root).as_posix(),
            "--quality",
            _safe(root, _evidence(config, "secondary_quality"), "secondary_quality").relative_to(root).as_posix(),
            "--output-base",
            datasets.relative_to(root).as_posix(),
        ]
        result = subprocess.run(command, cwd=root, env=_zero_cost_env(), text=True, capture_output=True, timeout=900)
        if result.returncode:
            raise AutonomousGenerationLoopError(f"secondary dataset materialization failed: {result.stderr[-3000:]}")
        secondary = _find_materialized(datasets, exclude={primary})
    if secondary is None:
        raise AutonomousGenerationLoopError("secondary canonical dataset was not materialized")

    split = load_split(_safe(root, config["split"], "split"))
    require_provider_gate(
        primary / "manifest.json",
        expected_dataset_hash=split.dataset_hash,
        provider_validation_path=_safe(root, _evidence(config, "provider_validation"), "provider_validation"),
        primary_dataset_dir=primary,
        secondary_dataset_dir=secondary,
    )
    return primary, secondary


def _evaluate(family: str, variant: str, bars, minimum_trades: int) -> dict[str, Any]:
    result = dict(evaluate_hypothesis_variant(family, variant, bars))
    thresholds = dict(result["variant_thresholds"])
    thresholds.pop("at_least_30_closed_trades", None)
    thresholds[f"at_least_{minimum_trades}_closed_trades"] = (
        int(result["closed_trades"]) >= minimum_trades
    )
    result["variant_thresholds"] = thresholds
    result["passes_variant_thresholds"] = all(thresholds.values())
    result["selection_policy"] = "positive_total_1.5x_without_best_and_minimum_closed_trades"
    return result


def _result_score(result: dict[str, Any]) -> tuple[bool, Decimal, Decimal, Decimal, str]:
    return (
        bool(result.get("passes_variant_thresholds")),
        Decimal(str(result.get("cost_1_5x_pnl", "0"))),
        Decimal(str(result.get("without_best_trade_pnl", "0"))),
        Decimal(str(result.get("total_pnl", "0"))),
        str(result.get("variant", "")),
    )


def _experiment_paths(prereg: Path) -> list[Path]:
    directory = prereg.parent / "experiments"
    if not directory.is_dir() or directory.is_symlink():
        return []
    return sorted(path for path in directory.glob("*/experiment.json") if path.is_file() and not path.is_symlink())


def _make_spec(generation_id: str, generation_number: int, dataset_hash: str, split_hash: str, family: str, variant: str) -> ExperimentSpec:
    features = {
        "breakout": ("mid_close", "rolling_high_low", "spread"),
        "momentum": ("mid_close", "atr_14", "spread"),
        "pullback": ("mid_close", "ema20_ema80", "atr_14", "spread"),
        "mean_reversion": ("mid_close", "rolling_zscore", "spread"),
    }[family]
    return ExperimentSpec(
        experiment_id=f"{generation_id}-{family}-001",
        dataset_hash=dataset_hash,
        split_hash=split_hash,
        family=family,
        hypothesis=f"Frozen {family} mechanism with one ex-ante parameter variant.",
        rationale="Diversity-first registered family; no outcome feedback is used for recipe construction.",
        features=features,
        parameter_space=(variant,),
        cost_multipliers=COST_MULTIPLIERS,
        protocol="chronological development only; one-shot registered execution; fixed-trade spread repricing at 1.5x and 2.0x",
        success_criteria=f"positive total_pnl, positive cost_1_5x_pnl, positive without_best_trade_pnl, and at least {MIN_DEVELOPMENT_TRADES} closed trades",
        failure_criteria="any success criterion fails; no retries, retuning, or window changes",
        seed=generation_number * 100 + (FAMILIES.index((family, variant)) + 1),
    )


def _freeze_prereg(root: Path, layout, config: dict[str, Any]) -> dict[str, Any]:
    split_path = _safe(root, layout.split, "split")
    split = load_split(split_path)
    config["generation_number"] = layout.generation_number
    config["split"] = layout.split
    config["generation_id"] = layout.generation_id
    primary, secondary = _materialize(root, config, split.split_hash, split.dataset_hash)
    prereg = _safe(root, layout.preregistration, "preregistration")
    experiment_dir = prereg.parent / "experiments"
    experiment_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for family, variant in FAMILIES:
        spec = _make_spec(layout.generation_id, layout.generation_number, split.dataset_hash, split.split_hash, family, variant)
        path = experiment_dir / spec.experiment_id / "experiment.json"
        freeze_verified_experiment(
            path,
            spec,
            split_path,
            primary / "manifest.json",
            _safe(root, _evidence(config, "provider_validation"), "provider_validation"),
            primary,
            secondary,
        )
        entries.append(
            {
                "experiment_id": spec.experiment_id,
                "family": family,
                "variant": variant,
                "spec_path": path.relative_to(root).as_posix(),
                "spec_hash": spec.spec_hash,
            }
        )
    manifest = {
        "schema_version": 1,
        "scope": "ftmo-autonomous-development-preregistration",
        "generation_id": layout.generation_id,
        "generation_number": layout.generation_number,
        "recipe_version": RECIPE_VERSION,
        "dataset_hash": split.dataset_hash,
        "split_hash": split.split_hash,
        "declared_variant_count": len(entries),
        "declared_family_count": len(entries),
        "experiments": entries,
        "cost_multipliers": list(COST_MULTIPLIERS),
        "minimum_development_closed_trades": MIN_DEVELOPMENT_TRADES,
        "walk_forward_selection": "only development candidates; deterministic highest WF cost_1.5x, then without_best, then total, then experiment_id",
        "holdout_policy": "exactly one selected passing walk-forward candidate; one-time release and one-time execution",
        "walk_forward_accessed": False,
        "final_holdout_accessed": False,
        "retuning_allowed": False,
        "live_trading_allowed": False,
        "broker_order_submission_allowed": False,
    }
    write_immutable_text(prereg, _payload(prereg, manifest), artifact_name="autonomous preregistration pack")
    return {"action": "preregistered", "experiment_count": len(entries), "dataset_hash": split.dataset_hash, "split_hash": split.split_hash}


def _trial_results(spec: ExperimentSpec, trials_dir: Path) -> list[dict[str, Any]]:
    out = []
    for variant, _ in trial_evidence_hashes(spec, trials_dir):
        index = spec.parameter_space.index(variant) + 1
        digest = __import__("hashlib").sha256(variant.encode()).hexdigest()[:12]
        path = trials_dir / f"{index:03d}-{digest}.json"
        out.append(_strict(path, "trial evidence")["result"])
    return out


def _review_development(root: Path, layout, config: dict[str, Any]) -> dict[str, Any]:
    prereg = _safe(root, layout.preregistration, "preregistration")
    experiments = _experiment_paths(prereg)
    if len(experiments) != len(FAMILIES):
        raise AutonomousGenerationLoopError("development review requires exactly the frozen four-family recipe")
    rows = []
    candidates = 0
    rejects = 0
    for path in experiments:
        spec = load_experiment(path)
        trials_dir = path.parent / "trials"
        results = _trial_results(spec, trials_dir)
        if len(results) != spec.declared_variant_count:
            raise AutonomousGenerationLoopError(f"incomplete trial budget for {spec.experiment_id}")
        selected = max(results, key=_result_score)
        outcome = "candidate" if bool(selected.get("passes_variant_thresholds")) else "reject"
        review = build_candidate_review(
            spec,
            trials_dir,
            outcome=outcome,
            selected_variant=selected.get("variant") if outcome == "candidate" else None,
            rationale="Deterministic frozen selection policy; no OOS evidence was read.",
        )
        freeze_candidate_review(path.parent / "candidate-review.json", review, spec, trials_dir)
        if outcome == "candidate":
            candidates += 1
        else:
            rejects += 1
        rows.append({"experiment_id": spec.experiment_id, "family": spec.family, "outcome": outcome, "selected_variant": review.selected_variant, "review_hash": review.review_hash, "result": selected})
    split = load_split(_safe(root, layout.split, "split"))
    summary = {
        "schema_version": 1,
        "scope": "ftmo-autonomous-development-review",
        "generation_id": layout.generation_id,
        "generation_number": layout.generation_number,
        "dataset_hash": split.dataset_hash,
        "split_hash": split.split_hash,
        "candidate_count": candidates,
        "reject_count": rejects,
        "family_count": len(rows),
        "rows": rows,
        "walk_forward_accessed": False,
        "final_holdout_accessed": False,
        "retuning_allowed": False,
    }
    write_immutable_text(_safe(root, layout.development_summary, "development_summary"), _payload(_safe(root, layout.development_summary, "development_summary"), {**summary, "scope": "ftmo-autonomous-development-pack-summary"}), artifact_name="development summary")
    write_immutable_text(_safe(root, layout.development_review, "development_review"), _payload(_safe(root, layout.development_review, "development_review"), summary), artifact_name="development review")
    return {"action": "development_reviewed", "candidate_count": candidates, "reject_count": rejects}


def _walk_forward(root: Path, layout, config: dict[str, Any]) -> dict[str, Any]:
    prereg = _safe(root, layout.preregistration, "preregistration")
    split_path = _safe(root, layout.split, "split")
    split = load_split(split_path)
    primary, secondary = _materialize(root, config, split.split_hash, split.dataset_hash)
    passes = []
    total = 0
    for path in _experiment_paths(prereg):
        candidate_path = path.parent / "candidate-review.json"
        if not candidate_path.exists():
            continue
        candidate = load_candidate_review(candidate_path)
        spec = load_experiment(path)
        if candidate.outcome != "candidate":
            continue
        total += 1
        run_path = path.parent / "walk-forward-run.json"
        review_path = path.parent / "walk-forward-review.json"
        if not run_path.exists():
            execute_verified_walk_forward_candidate(
                output_path=run_path,
                split_path=split_path,
                primary_dataset_dir=primary,
                inspection_path=_safe(root, _evidence(config, "primary_inspection"), "primary_inspection"),
                experiment_path=path,
                candidate_review_path=candidate_path,
                trials_dir=path.parent / "trials",
                provider_validation_path=_safe(root, _evidence(config, "provider_validation"), "provider_validation"),
                secondary_dataset_dir=secondary,
                run_fn=lambda variant, bars, family=spec.family: _evaluate(family, variant, bars, MIN_WALK_FORWARD_TRADES),
            )
        run = load_walk_forward_run(run_path)
        if not review_path.exists():
            outcome = "pass" if bool(run.result.get("passes_variant_thresholds")) else "reject"
            review = build_walk_forward_review(run, spec, candidate, path.parent / "trials", outcome=outcome, rationale="Frozen walk-forward gate; holdout remains sealed.", reviewed_by="ftmo-autonomous")
            freeze_walk_forward_review(review_path, review, run, spec, candidate, path.parent / "trials")
        review = load_walk_forward_review(review_path)
        if review.outcome == "pass":
            passes.append({"experiment_id": spec.experiment_id, "review_hash": review.review_hash, "run_hash": run.run_hash, "result": run.result})
    summary = {
        "schema_version": 1,
        "scope": "ftmo-autonomous-walk-forward-summary",
        "generation_id": layout.generation_id,
        "generation_number": layout.generation_number,
        "pass_count": len(passes),
        "candidate_count": total,
        "passes": passes,
        "walk_forward_accessed": True,
        "final_holdout_accessed": False,
        "retuning_allowed": False,
    }
    write_immutable_text(_safe(root, layout.walk_forward_summary, "walk_forward_summary"), _payload(_safe(root, layout.walk_forward_summary, "walk_forward_summary"), summary), artifact_name="walk-forward summary")
    return {"action": "walk_forward_completed", "candidate_count": total, "pass_count": len(passes), "walk_forward_accessed": True}


def _holdout(root: Path, layout, config: dict[str, Any]) -> dict[str, Any]:
    split_path = _safe(root, layout.split, "split")
    split = load_split(split_path)
    prereg = _safe(root, layout.preregistration, "preregistration")
    walk = _strict(_safe(root, layout.walk_forward_summary, "walk_forward_summary"), "walk-forward summary")
    passed = sorted(walk.get("passes", []), key=lambda row: (
        -Decimal(str(row["result"].get("cost_1_5x_pnl", "0"))),
        -Decimal(str(row["result"].get("without_best_trade_pnl", "0"))),
        -Decimal(str(row["result"].get("total_pnl", "0"))),
        row["experiment_id"],
    ))
    if len(passed) != int(walk["pass_count"]) or not passed:
        raise AutonomousGenerationLoopError("holdout requires the complete non-empty WF pass set")
    selected_id = passed[0]["experiment_id"]
    path = next(p for p in _experiment_paths(prereg) if load_experiment(p).experiment_id == selected_id)
    spec = load_experiment(path)
    candidate = load_candidate_review(path.parent / "candidate-review.json")
    wf_run = load_walk_forward_run(path.parent / "walk-forward-run.json")
    wf_review = load_walk_forward_review(path.parent / "walk-forward-review.json")
    release_path = path.parent / "holdout-release.json"
    release = HoldoutRelease(
        dataset_hash=split.dataset_hash,
        split_hash=split.split_hash,
        experiment_spec_hash=spec.spec_hash,
        candidate_review_hash=candidate.review_hash,
        walk_forward_review_hash=wf_review.review_hash,
        decision_ref=f"{layout.generation_id}:walk-forward:{selected_id}",
        approved_by="ftmo-autonomous",
        rationale="Pre-registered one-candidate holdout release after deterministic WF ranking.",
    )
    freeze_holdout_release(release_path, release, split, candidate, spec, path.parent / "trials", path.parent / "walk-forward-run.json", path.parent / "walk-forward-review.json")
    authorize_holdout(split, load_holdout_release(release_path), spec.spec_hash, candidate, wf_review)
    primary, secondary = _materialize(root, config, split.split_hash, split.dataset_hash)
    _, bars = load_dataset(primary)
    holdout_bars = tuple(bar for bar in bars if split.holdout_start <= bar.event_time < split.holdout_end)
    if not holdout_bars:
        raise AutonomousGenerationLoopError("frozen holdout window contains no canonical bars")
    run_path = path.parent / "final-holdout-run.json"
    if run_path.exists():
        raw = _strict(run_path, "final holdout run")
        result = raw["result"]
    else:
        run_path.touch(exist_ok=False)
        result = _evaluate(spec.family, candidate.selected_variant, holdout_bars, MIN_WALK_FORWARD_TRADES)
        stable = {"schema_version": 1, "generation_id": layout.generation_id, "experiment_id": spec.experiment_id, "release_hash": release.release_hash, "result": result}
        run_path.write_text(_payload(run_path, stable), encoding="utf-8")
    summary = {
        "schema_version": 1,
        "scope": "ftmo-autonomous-final-holdout-summary",
        "generation_id": layout.generation_id,
        "generation_number": layout.generation_number,
        "selected_experiment_id": spec.experiment_id,
        "holdout_release_hash": release.release_hash,
        "result": result,
        "final_holdout_accessed": True,
        "walk_forward_accessed": True,
        "retuning_allowed": False,
        "live_trading_allowed": False,
    }
    write_immutable_text(_safe(root, layout.final_holdout_summary, "final_holdout_summary"), _payload(_safe(root, layout.final_holdout_summary, "final_holdout_summary"), summary), artifact_name="final holdout summary")
    return {"action": "final_holdout_completed", "final_holdout_accessed": True, "selected_experiment_id": spec.experiment_id}


def _close(root: Path, layout, stage: str) -> dict[str, Any]:
    split = load_split(_safe(root, layout.split, "split"))
    review = _strict(_safe(root, layout.development_review, "development_review"), "development review")
    candidate_count = int(review["candidate_count"])
    reject_count = int(review["reject_count"])
    attempted = int(review.get("declared_variant_count", len(FAMILIES)))
    if stage == "close_development_reject":
        status, wf_candidates, wf_accessed, holdout = CLOSED_DEVELOPMENT_REJECT, 0, False, False
        reason = "No development family passed the frozen preregistered gate."
    elif stage == "close_walk_forward_reject":
        status, wf_candidates, wf_accessed, holdout = CLOSED_WALK_FORWARD_REJECT, 0, True, False
        reason = "No development candidate passed the frozen walk-forward gate."
    elif stage == "close_validated":
        walk = _strict(_safe(root, layout.walk_forward_summary, "walk_forward_summary"), "walk-forward summary")
        status, wf_candidates, wf_accessed, holdout = CLOSED_FINAL_HOLDOUT, int(walk["pass_count"]), True, True
        reason = "Exactly one deterministic WF-selected candidate was released to final holdout."
    else:
        raise AutonomousGenerationLoopError(f"unsupported closure stage: {stage}")
    spec = GenerationClosureSpec(
        generation_id=layout.generation_id,
        status=status,
        dataset_hash=split.dataset_hash,
        split_hash=split.split_hash,
        declared_development_variants=len(FAMILIES),
        attempted_development_variants=attempted,
        development_family_count=len(FAMILIES),
        development_candidate_count=candidate_count,
        development_reject_count=reject_count,
        walk_forward_candidate_count=wf_candidates,
        walk_forward_accessed=wf_accessed,
        final_holdout_accessed=holdout,
        holdout_release_allowed=holdout,
        reason=reason,
        next_generation_rule="advance only to a fresh strictly chronological pre-registered reserve window; never use holdout outcomes for recipe or window selection",
        aws_used=False,
    )
    freeze_generation_closure(_safe(root, layout.closure, "closure"), spec)
    return {"action": "generation_closed", "status": status, "next_generation": layout.generation_number + 1}


def _next_generation_design(root: Path, layout, config: dict[str, Any]) -> dict[str, Any]:
    base_reserve = _safe(root, config.get("reserve_config", "configs/research_window_reserve_v6.json"), "reserve_config")
    reserve = reserve_through_next_horizon(root, base_reserve, layout.generation_number)
    research_config = _safe(root, config.get("research_config", "configs/research_machine.json"), "research_config")
    return activate_next_generation(
        root,
        layout=layout,
        research_config=research_config,
        reserve_path=reserve,
    )


def _dynamic_provider_validation(root: Path, layout, config: dict[str, Any]):
    if layout.generation_number < 19:
        return None, None
    path = _safe(root, _evidence(config, "provider_validation"), "provider_validation")
    if path.is_symlink() or not path.is_file():
        return path, None
    return path, load_provider_validation(path)


def _skip_rejected_dynamic_provider_window(root: Path, layout, config: dict[str, Any]) -> dict[str, Any]:
    validation_path, validation = _dynamic_provider_validation(root, layout, config)
    if validation_path is None or validation is None or validation.accepted:
        raise AutonomousGenerationLoopError("autonomous provider skip requires an existing rejected validation")
    _assert_pre_outcome_skip_boundary(root, layout)
    base_reserve = _safe(root, config.get("reserve_config", "configs/research_window_reserve_v6.json"), "reserve_config")
    reserve = reserve_through_next_horizon(root, base_reserve, layout.generation_number)
    payload = build_autonomous_provider_window_skip(
        validation_path=validation_path,
        reserve_path=reserve,
        generation_number=layout.generation_number,
        generation_id=layout.generation_id,
    )
    freeze_autonomous_provider_window_skip(_safe(root, layout.closure, "provider skip closure"), payload)
    rollover = activate_next_generation(
        root,
        layout=layout,
        research_config=_safe(root, config.get("research_config", "configs/research_machine.json"), "research_config"),
        reserve_path=reserve,
    )
    return {
        "action": "provider_window_skipped_and_next_generation_activated",
        "provider_validation_accepted": False,
        "provider_skip_hash": payload["skip_hash"],
        "next_generation_number": rollover["next_generation_number"],
        "next_generation_id": rollover["next_generation_id"],
        "reserve_config": rollover["reserve_config"],
        "outcomes_executed": False,
    }


def _foundation_coverage_failure(
    root: Path,
    layout,
    config: dict[str, Any],
    reserve_path: Path,
) -> dict[str, Any] | None:
    validation_path, validation = _dynamic_provider_validation(root, layout, config)
    if validation_path is None or validation is None or validation.accepted is not True:
        return None

    primary = _safe(root, _evidence(config, "primary_dataset"), "dynamic primary dataset")
    secondary = _safe(root, _evidence(config, "secondary_dataset"), "dynamic secondary dataset")
    primary_inspection = _safe(root, _evidence(config, "primary_inspection"), "primary inspection")
    secondary_inspection = _safe(root, _evidence(config, "secondary_inspection"), "secondary inspection")
    primary_ready = verify_research_readiness(primary, primary_inspection)
    secondary_ready = verify_research_readiness(secondary, secondary_inspection)
    window = load_window_reserve(reserve_path).window_for_generation(layout.generation_number)

    coverage = {
        "required_window": {
            "development_start": window.development_start.isoformat(),
            "holdout_end": window.holdout_end.isoformat(),
        },
        "primary": {
            "dataset_hash": primary_ready.dataset_hash,
            "inspection_sha256": primary_ready.inspection_sha256,
            "coverage_start": primary_ready.coverage_start,
            "coverage_end": primary_ready.coverage_end,
            "bar_count": primary_ready.bar_count,
        },
        "secondary": {
            "dataset_hash": secondary_ready.dataset_hash,
            "inspection_sha256": secondary_ready.inspection_sha256,
            "coverage_start": secondary_ready.coverage_start,
            "coverage_end": secondary_ready.coverage_end,
            "bar_count": secondary_ready.bar_count,
        },
    }
    return coverage if coverage_failure_reasons(coverage) else None


def _assert_pre_outcome_skip_boundary(root: Path, layout) -> None:
    for relative in (
        layout.split,
        layout.preregistration,
        layout.development_summary,
        layout.development_review,
        layout.walk_forward_summary,
        layout.final_holdout_summary,
    ):
        path = _safe(root, relative, "strategy outcome evidence")
        if path.exists():
            raise AutonomousGenerationLoopError(
                "provider-window skip is forbidden after split, preregistration, or strategy outcomes exist"
            )


def _skip_incomplete_dynamic_provider_window(
    root: Path,
    layout,
    config: dict[str, Any],
    *,
    reserve_path: Path,
    coverage_failure: dict[str, Any],
) -> dict[str, Any]:
    validation_path, validation = _dynamic_provider_validation(root, layout, config)
    if validation_path is None or validation is None or validation.accepted is not True:
        raise AutonomousGenerationLoopError(
            "autonomous coverage skip requires an existing accepted provider validation"
        )
    _assert_pre_outcome_skip_boundary(root, layout)
    payload = build_autonomous_provider_window_skip(
        validation_path=validation_path,
        reserve_path=reserve_path,
        generation_number=layout.generation_number,
        generation_id=layout.generation_id,
        coverage_failure=coverage_failure,
    )
    freeze_autonomous_provider_window_skip(
        _safe(root, layout.closure, "provider coverage skip closure"),
        payload,
    )
    rollover = activate_next_generation(
        root,
        layout=layout,
        research_config=_safe(
            root,
            config.get("research_config", "configs/research_machine.json"),
            "research_config",
        ),
        reserve_path=reserve_path,
    )
    return {
        "action": "provider_coverage_window_skipped_and_next_generation_activated",
        "provider_validation_accepted": True,
        "provider_skip_hash": payload["skip_hash"],
        "coverage_failure_reasons": payload["failure_reasons"],
        "next_generation_number": rollover["next_generation_number"],
        "next_generation_id": rollover["next_generation_id"],
        "reserve_config": rollover["reserve_config"],
        "outcomes_executed": False,
    }



def run_autonomous_generation_tick(*, root: Path, decision: dict[str, Any], controller_config: dict[str, Any], now_utc: datetime) -> dict[str, Any]:
    generation = controller_config.get("generation_loop")
    if not isinstance(generation, dict) or generation.get("enabled") is not True:
        return {"enabled": False, "action": "not_configured", "outcomes_executed": False}
    if generation.get("mode") != "frozen_recipe":
        raise AutonomousGenerationLoopError("generation loop mode must remain frozen_recipe")
    if controller_config.get("outcome_execution") != "registered_commands_only":
        raise AutonomousGenerationLoopError("generation loop requires registered_commands_only")
    root = root.resolve()
    layout = load_generation_layout(_safe(root, generation.get("research_config", "configs/research_machine.json"), "research_config"))
    if decision.get("generation_id") != layout.generation_id:
        raise AutonomousGenerationLoopError("controller decision and active generation differ")
    stage = decision.get("next_stage")
    if stage in {"development", "development_review"} and decision.get("development_access_allowed") is not True:
        raise AutonomousGenerationLoopError("development stage is not authorized by the research machine")
    if stage == "walk_forward" and decision.get("walk_forward_access_allowed") is not True:
        raise AutonomousGenerationLoopError("walk-forward stage is not authorized by the research machine")
    if stage == "final_holdout" and decision.get("final_holdout_access_allowed") is not True:
        raise AutonomousGenerationLoopError("final holdout stage is not authorized by the research machine")
    if stage == "blocked":
        return {"enabled": True, "action": "blocked_by_immutable_gate", "outcomes_executed": False, "stage": stage}
    config = dict(generation)
    config["split"] = layout.split
    config["generation_id"] = layout.generation_id
    config["generation_number"] = layout.generation_number
    if layout.generation_number >= 20:
        dynamic_paths = generation_paths(layout.generation_number)
        config["dynamic_generation"] = True
        config["evidence"] = {
            **evidence_for_generation(layout.generation_number),
            "primary_dataset": dynamic_paths["primary_dataset"],
            "secondary_dataset": dynamic_paths["secondary_dataset"],
        }
    if layout.generation_number >= 19 and stage in {"freeze_data_split", "await_preregistration"}:
        _, provider_validation = _dynamic_provider_validation(root, layout, config)
        if provider_validation is not None and provider_validation.accepted is False:
            result = _skip_rejected_dynamic_provider_window(root, layout, config)
            result.update({
                "enabled": True,
                "stage": stage,
                "generation_id": layout.generation_id,
                "outcomes_executed": False,
            })
            return result
    if stage == "provider_foundation":
        if layout.generation_number < 20:
            result = {"action": "waiting_for_pre_registered_foundation", "outcomes_executed": False}
        else:
            reserve = reserve_through_next_horizon(
                root,
                _safe(root, config.get("reserve_config", "configs/research_window_reserve_v6.json"), "reserve_config"),
                layout.generation_number,
            )
            try:
                result = run_provider_foundation(
                    root,
                    number=layout.generation_number,
                    reserve_path=reserve,
                )
            except RetryableProviderFoundationUnavailable as exc:
                # Public-provider availability is independent from the frozen
                # research design. Return an explicit outcome-free retry state;
                # never turn a transient outage into a provider rejection/skip.
                result = {
                    "action": "provider_foundation_provider_unavailable_retry_later",
                    "retryable_provider": exc.provider,
                    "provider_validation_accepted": None,
                    "outcomes_executed": False,
                }
            else:
                if result.get("provider_validation_accepted") is False:
                    result = _skip_rejected_dynamic_provider_window(root, layout, config)
                else:
                    coverage_failure = _foundation_coverage_failure(root, layout, config, reserve)
                    if coverage_failure is not None:
                        result = _skip_incomplete_dynamic_provider_window(
                            root,
                            layout,
                            config,
                            reserve_path=reserve,
                            coverage_failure=coverage_failure,
                        )
    elif stage == "freeze_data_split":
        if layout.generation_number < 20:
            result = {"action": "waiting_for_pre_registered_foundation", "outcomes_executed": False}
        else:
            reserve = reserve_through_next_horizon(
                root,
                _safe(root, config.get("reserve_config", "configs/research_window_reserve_v6.json"), "reserve_config"),
                layout.generation_number,
            )
            coverage_failure = _foundation_coverage_failure(root, layout, config, reserve)
            if coverage_failure is not None:
                result = _skip_incomplete_dynamic_provider_window(
                    root,
                    layout,
                    config,
                    reserve_path=reserve,
                    coverage_failure=coverage_failure,
                )
            else:
                result = freeze_generation_split(root, number=layout.generation_number, reserve_path=reserve)
    elif stage == "await_preregistration":
        result = _freeze_prereg(root, layout, config)
    elif stage == "development":
        prereg = _safe(root, layout.preregistration, "preregistration")
        primary, secondary = _materialize(root, config, load_split(_safe(root, layout.split, "split")).split_hash, load_split(_safe(root, layout.split, "split")).dataset_hash)
        for path in _experiment_paths(prereg):
            spec = load_experiment(path)
            attempted = {variant for variant, _ in trial_evidence_hashes(spec, path.parent / "trials")}
            for variant in spec.parameter_space:
                if variant in attempted:
                    continue
                execute_verified_development_variant(
                    experiment_path=path,
                    variant=variant,
                    output_dir=path.parent / "trials",
                    split_path=_safe(root, layout.split, "split"),
                    primary_dataset_dir=primary,
                    inspection_path=_safe(root, _evidence(config, "primary_inspection"), "primary_inspection"),
                    provider_validation_path=_safe(root, _evidence(config, "provider_validation"), "provider_validation"),
                    secondary_dataset_dir=secondary,
                    run_fn=lambda registered, bars, family=spec.family: _evaluate(family, registered, bars, MIN_DEVELOPMENT_TRADES),
                )
        result = _review_development(root, layout, config)
        result["outcomes_executed"] = True
        result["development_completed"] = True
    elif stage == "development_review":
        result = _review_development(root, layout, config)
    elif stage in {"walk_forward"}:
        result = _walk_forward(root, layout, config)
        result["outcomes_executed"] = True
    elif stage in {"close_development_reject", "close_walk_forward_reject", "close_validated"}:
        result = _close(root, layout, stage)
    elif stage == "next_generation_design":
        result = _next_generation_design(root, layout, config)
    elif stage == "final_holdout":
        result = _holdout(root, layout, config)
        result["outcomes_executed"] = True
    else:
        raise AutonomousGenerationLoopError(f"no audited generation-loop adapter for stage: {stage}")
    result.update({"enabled": True, "stage": stage, "generation_id": layout.generation_id, "outcomes_executed": bool(result.get("outcomes_executed", False))})
    return result
