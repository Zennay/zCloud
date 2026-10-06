#!/usr/bin/env python3
"""Gate the next autonomous zCloud improvement on measured prior effect."""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path


ITERATION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
METRIC_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
EFFECT_OUTCOMES = {"improved", "neutral", "regressed"}
VALIDATION_OUTCOMES = {"passed", "failed"}
MAX_HISTORY = 20


class EffectGateError(ValueError):
    pass


def finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EffectGateError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise EffectGateError(f"{field} must be finite")
    return number


@dataclass(frozen=True)
class EffectMeasurement:
    metric_id: str
    before: float
    after: float
    effect_outcome: str
    validation_outcome: str

    @classmethod
    def from_mapping(cls, value: object) -> "EffectMeasurement":
        if not isinstance(value, dict):
            raise EffectGateError("measurement must be an object")
        allowed = {
            "metric_id",
            "before",
            "after",
            "effect_outcome",
            "validation_outcome",
        }
        if set(value) - allowed:
            raise EffectGateError("measurement contains unsupported fields")
        if set(value) != allowed:
            raise EffectGateError("measurement is incomplete")
        metric_id = str(value.get("metric_id") or "").strip().lower()
        if not METRIC_ID_RE.fullmatch(metric_id):
            raise EffectGateError("metric_id must be a bounded machine identifier")
        effect_outcome = str(value.get("effect_outcome") or "").strip().lower()
        validation_outcome = str(value.get("validation_outcome") or "").strip().lower()
        if effect_outcome not in EFFECT_OUTCOMES:
            raise EffectGateError("unsupported effect_outcome")
        if validation_outcome not in VALIDATION_OUTCOMES:
            raise EffectGateError("unsupported validation_outcome")
        return cls(
            metric_id=metric_id,
            before=finite_number(value.get("before"), "before"),
            after=finite_number(value.get("after"), "after"),
            effect_outcome=effect_outcome,
            validation_outcome=validation_outcome,
        )


@dataclass(frozen=True)
class Iteration:
    iteration_id: str
    revision: str
    state: str
    measurement: EffectMeasurement | None

    @classmethod
    def from_mapping(cls, value: object) -> "Iteration":
        if not isinstance(value, dict):
            raise EffectGateError("iteration must be an object")
        allowed = {"iteration_id", "revision", "state", "measurement"}
        if set(value) - allowed:
            raise EffectGateError("iteration contains unsupported fields")
        required = {"iteration_id", "revision", "state"}
        if not required.issubset(value):
            raise EffectGateError("iteration is incomplete")
        iteration_id = str(value.get("iteration_id") or "").strip().lower()
        revision = str(value.get("revision") or "").strip().lower()
        state = str(value.get("state") or "").strip().lower()
        if not ITERATION_ID_RE.fullmatch(iteration_id):
            raise EffectGateError("invalid iteration_id")
        if not REVISION_RE.fullmatch(revision):
            raise EffectGateError("revision must be a 40-char git SHA")
        if state not in {"completed", "rolled_back"}:
            raise EffectGateError("unsupported iteration state")
        raw_measurement = value.get("measurement")
        measurement = (
            None
            if raw_measurement is None
            else EffectMeasurement.from_mapping(raw_measurement)
        )
        return cls(
            iteration_id=iteration_id,
            revision=revision,
            state=state,
            measurement=measurement,
        )


def parse_history(payload: object) -> list[Iteration]:
    if not isinstance(payload, dict) or set(payload) != {"iterations"}:
        raise EffectGateError("payload must contain only iterations")
    raw = payload["iterations"]
    if not isinstance(raw, list):
        raise EffectGateError("iterations must be a list")
    if len(raw) > MAX_HISTORY:
        raise EffectGateError(f"iteration history exceeds {MAX_HISTORY}")
    iterations = [Iteration.from_mapping(item) for item in raw]
    ids = [item.iteration_id for item in iterations]
    if len(ids) != len(set(ids)):
        raise EffectGateError("iteration ids must be unique")
    return iterations


def gate(iterations: list[Iteration]) -> dict:
    if not iterations:
        return {
            "decision": "NEXT_ITERATION_ALLOWED",
            "reason": "no_prior_iteration",
            "effect_outcome": None,
        }

    latest = iterations[-1]
    if latest.state == "rolled_back":
        return {
            "decision": "NEXT_ITERATION_ALLOWED",
            "reason": "prior_iteration_rolled_back",
            "effect_outcome": latest.measurement.effect_outcome if latest.measurement else None,
        }

    measurement = latest.measurement
    if measurement is None:
        return {
            "decision": "MEASURE_EFFECT_REQUIRED",
            "reason": "completed_iteration_missing_measurement",
            "effect_outcome": None,
        }
    if measurement.validation_outcome != "passed":
        return {
            "decision": "REMEDIATE_OR_ROLLBACK_REQUIRED",
            "reason": "validation_failed",
            "effect_outcome": measurement.effect_outcome,
        }
    if measurement.effect_outcome == "regressed":
        return {
            "decision": "REMEDIATE_OR_ROLLBACK_REQUIRED",
            "reason": "measured_regression",
            "effect_outcome": measurement.effect_outcome,
        }
    return {
        "decision": "NEXT_ITERATION_ALLOWED",
        "reason": "effect_measured",
        "effect_outcome": measurement.effect_outcome,
    }


def evaluate_payload(payload: object) -> dict:
    iterations = parse_history(payload)
    decision = gate(iterations)
    return {
        "schema_version": 1,
        "policy": "iteration-effect-gate-v1",
        "iterations_observed": len(iterations),
        **decision,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-next-allowed", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.input:
            if args.input.is_symlink() or not args.input.is_file():
                raise EffectGateError("input must be a regular non-symlink file")
            payload = json.loads(args.input.read_text(encoding="utf-8"))
        else:
            payload = json.load(__import__("sys").stdin)
        result = evaluate_payload(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, EffectGateError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True))
        return 2

    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(
            "ZCLOUD_ITERATION_EFFECT_GATE "
            f"decision={result['decision']} "
            f"reason={result['reason']} "
            f"iterations={result['iterations_observed']}"
        )
    if args.require_next_allowed and result["decision"] != "NEXT_ITERATION_ALLOWED":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
