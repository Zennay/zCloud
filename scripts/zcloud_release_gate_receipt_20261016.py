#!/usr/bin/env python3
"""Offline deny-first release evidence receipt for zCloud deploy-ops.

This is a source-only diagnostic, NOT release authority. Never dispatches a
workflow, contacts GitHub, modifies a queue, or writes to the VPS.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any

MAX_BYTES = 65536
MAX_AGE_SECONDS = 600
SHA = re.compile(r"[0-9a-f]{40}\Z")
STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\Z")
RUN_URL = re.compile(r"https://github[.]com/Zennay/zCloud/actions/runs/[1-9][0-9]*\Z")
JOB_URL = re.compile(r"https://github[.]com/Zennay/zCloud/actions/runs/[1-9][0-9]*/job/[1-9][0-9]*\Z")
ROOT_KEYS = {"schema", "candidate_sha", "canonical_main_sha", "observed_at",
             "ci", "review", "protection", "runner", "locks", "release"}
NESTED_KEYS = {
    "ci": {"head_sha", "conclusion", "run_url"},
    "review": {"head_sha", "approved", "independent"},
    "protection": {"main_sha", "enforced", "reviews_required"},
    "runner": {"main_sha", "runner_id", "labels", "job_url"},
    "locks": {"main_sha", "production_writer_jobs", "all_serialized"},
    "release": {"main_sha", "owner_approved", "exclusive_window"},
}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("nonfinite_json")


def decode_receipt(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_BYTES:
        raise ValueError("receipt_too_large")
    try:
        result = json.loads(raw.decode("utf-8", errors="strict"),
                            object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid_json") from error
    if type(result) is not dict:
        raise ValueError("invalid_schema")
    return result


def _valid_sha(value: Any) -> bool:
    return type(value) is str and SHA.fullmatch(value) is not None


def _exact_shape(value: Any, keys: set[str]) -> bool:
    return type(value) is dict and set(value) == keys


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _utc_stamp(value: Any) -> datetime | None:
    if type(value) is not str or STAMP.fullmatch(value) is None:
        return None
    try:
        stamp = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if stamp.utcoffset() != timezone.utc.utcoffset(stamp):
        return None
    return stamp


def evaluate(receipt: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Classify untrusted claimed evidence without ever authorizing a live write."""
    reason_codes: list[str] = []
    output: dict[str, Any] = {
        "contract": "zcloud-release-evidence-v1",
        "evidence_source": "untrusted_offline_receipt",
        "live_sources_verified": False,
        "offline_claims_complete": False,
        "production_mutation_authorized": False,
        "reason_codes": reason_codes,
    }
    if (not _exact_shape(receipt, ROOT_KEYS) or
            receipt.get("schema") != "zcloud-release-evidence-v1" or
            any(not _exact_shape(receipt.get(k), shape)
                for k, shape in NESTED_KEYS.items())):
        reason_codes.append("invalid_schema")
        return output

    candidate = receipt["candidate_sha"]
    main = receipt["canonical_main_sha"]
    if not _valid_sha(candidate) or not _valid_sha(main) or candidate != main:
        reason_codes.append("candidate_not_exact_current_main")

    clock = now if now is not None else datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("now_must_be_timezone_aware")
    stamp = _utc_stamp(receipt["observed_at"])
    if stamp is None or not (0 <= (clock - stamp).total_seconds() <= MAX_AGE_SECONDS):
        reason_codes.append("stale_or_invalid_observation")

    ci = receipt["ci"]
    if (ci["head_sha"] != candidate or not _valid_sha(ci["head_sha"]) or
            ci["conclusion"] != "success" or
            type(ci["run_url"]) is not str or not RUN_URL.fullmatch(ci["run_url"])):
        reason_codes.append("ci_not_exact_head_success")

    review = receipt["review"]
    if (review["head_sha"] != candidate or
            review["approved"] is not True or review["independent"] is not True):
        reason_codes.append("independent_review_missing")

    protection = receipt["protection"]
    if (protection["main_sha"] != main or protection["enforced"] is not True or
            protection["reviews_required"] is not True):
        reason_codes.append("main_protection_missing")

    runner = receipt["runner"]
    if (runner["main_sha"] != main or not _positive_int(runner["runner_id"]) or
            type(runner["labels"]) is not list or
            not all(type(label) is str for label in runner["labels"]) or
            not {"self-hosted", "zcloud", "vps"}.issubset(set(
                label for label in runner["labels"] if type(label) is str)) or
            type(runner["job_url"]) is not str or
            not JOB_URL.fullmatch(runner["job_url"])):
        reason_codes.append("vps_runner_assignment_unproven")

    locks = receipt["locks"]
    if (locks["main_sha"] != main or
            type(locks["production_writer_jobs"]) is not int or
            locks["production_writer_jobs"] != 7 or
            locks["all_serialized"] is not True):
        reason_codes.append("production_writer_lock_incomplete")

    release = receipt["release"]
    if (release["main_sha"] != main or release["owner_approved"] is not True or
            release["exclusive_window"] is not True):
        reason_codes.append("serialized_release_window_unapproved")

    output["offline_claims_complete"] = not reason_codes
    return output



# Each row describes the *status of a supplied claim*, never live verification.
# Labels, follow-ups and links are fixed source literals: no untrusted values
# from receipt JSON are interpolated into Markdown or terminal output.
REPORT_GATES = (
    ("candidate_not_exact_current_main", "Actuele main-commit",
     "Vergelijk kandidaat met de actuele 40-tekens commit op main."),
    ("stale_or_invalid_observation", "Vers bewijsmoment",
     "Maak een nieuwe UTC-observatie; gebruik geen toekomsttijd."),
    ("ci_not_exact_head_success", "Volledige regressie op dezelfde commit",
     "Controleer echte GitHub Actions-jobuitkomst en exacte SHA."),
    ("independent_review_missing", "Onafhankelijke review",
     "Laat een andere reviewer de exacte commit beoordelen."),
    ("main_protection_missing", "Bescherming hoofdbranch",
     "Los bestaande direct-main writers op vóór #922 governance."),
    ("vps_runner_assignment_unproven", "Werkende VPS-runner",
     "Verifieer echte zCloud runner-ID en jobtoewijzing via #1326."),
    ("production_writer_lock_incomplete", "Geserialiseerde deploy-writers",
     "Controleer alle zeven writerjobs en gedeelde lock onder #1447."),
    ("serialized_release_window_unapproved", "Expliciete productievrijgave",
     "Wacht op afzonderlijke eigenaarstoestemming en #580-releasevenster."),
)


def render_markdown(result: dict[str, Any]) -> str:
    """Produce bounded Dutch audit prose from known reason codes only.

    This is not a verification step: a perfectly filled-in but forged receipt
    remains an untrusted assertion and can never grant production authority.
    """
    codes = result.get("reason_codes")
    known = {code for code, _, _ in REPORT_GATES}
    bad_shape = (
        type(codes) is not list
        or any(type(code) is not str or code not in known | {
            "invalid_schema", "receipt_too_large", "duplicate_json_key",
            "nonfinite_json", "invalid_json", "unsafe_or_missing_receipt",
            "unreadable_receipt",
        } for code in codes)
        or result.get("production_mutation_authorized") is not False
        or result.get("live_sources_verified") is not False
    )
    if bad_shape:
        codes = ["invalid_schema"]
    invalid = any(code not in known for code in codes)
    lines = [
        "## zCloud deploy-ops — releasecontrole",
        "",
        "**Beslissing: GEEN PRODUCTIEVRIJGAVE.** Dit overzicht beoordeelt",
        "uitsluitend een aangeleverd offline bestand. Er is geen live",
        "GitHub-, runner-, review- of productiestatus geverifieerd.",
        "",
        "| Voorwaarde | Offline claim | Nog nodig |",
        "|---|---|---|",
    ]
    for reason, title, action in REPORT_GATES:
        status = ("Niet beoordeelbaar" if invalid else
                  "Claim ontbreekt of is ongeldig" if reason in codes else
                  "Aangeleverd als compleet, niet geverifieerd")
        lines.append(f"| {title} | {status} | {action} |")
    lines.extend([
        "",
        "**Waarom geen release?** De gegevens zijn niet onafhankelijk",
        "geverifieerd. Zelfs bij acht complete claims blijven",
        " `live_sources_verified=false` en",
        " `production_mutation_authorized=false` verplicht.",
        "",
        "**Kan dit nu worden opgelost?** Ontbrekend of ongeldig offline",
        "bewijs kan opnieuw worden aangeleverd. Branchbeveiliging,",
        "VPS-listener, writer-locks en productievrijgave moeten door",
        "de bestaande eigenaren met vers, onafhankelijk bewijs",
        "worden afgerond; dit rapport voert geen wijzigingen uit.",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Local bounded JSON receipt path")
    parser.add_argument("--format", choices=("json", "markdown"), default="json",
                        help="Machine-readable JSON (default), or Dutch human-readable audit")
    args = parser.parse_args(argv)
    try:
        path = Path(args.input)
        if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
            raise ValueError("unsafe_or_missing_receipt")
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        result = evaluate(decode_receipt(raw))
    except (OSError, ValueError, TypeError) as error:
        allowed = {"receipt_too_large", "duplicate_json_key", "nonfinite_json",
                   "invalid_json", "invalid_schema", "unsafe_or_missing_receipt"}
        code = str(error) if str(error) in allowed else "unreadable_receipt"
        result = {"contract": "zcloud-release-evidence-v1",
                  "evidence_source": "untrusted_offline_receipt",
                  "offline_claims_complete": False,
                  "live_sources_verified": False,
                  "production_mutation_authorized": False,
                  "reason_codes": [code]}
    if args.format == "markdown":
        print(render_markdown(result))
    else:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["offline_claims_complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
