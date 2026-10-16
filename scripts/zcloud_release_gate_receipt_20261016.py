#!/usr/bin/env python3
"""Offline deny-first release evidence receipt for zCloud deploy-ops.

This is a source-only diagnostic, NOT release authority. Never dispatches a
workflow, contacts GitHub, modifies a queue, or writes to the VPS.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import unicodedata
from typing import Any

MAX_BYTES = 65536
MAX_JSON_DEPTH = 64
MAX_INTEGER_DIGITS = 128
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


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite_json")
    return result


def _bounded_integer(value: str) -> int:
    # Deterministic regardless of Python's int string conversion limit.
    if len(value.lstrip("-")) > MAX_INTEGER_DIGITS:
        raise ValueError("invalid_json")
    return int(value)


def _check_json_nesting(raw: bytes) -> None:
    """Bound JSON container depth before Python allocates a nested tree.

    Count brackets only outside strings (including escaped quotes) to avoid
    interpreting arbitrary user data as syntax. JSON grammar is checked by
    json.loads afterwards. This depth cap is independent of sys recursionlimit.
    """
    depth = 0
    quoted = False
    escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:  # backslash
                escaped = True
            elif byte == 34:  # quotation mark
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):  # '[' or '{'
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("invalid_json")
        elif byte in (93, 125):
            depth -= 1
            if depth < 0:
                raise ValueError("invalid_json")


def _reject_unsafe_unicode(value: Any) -> None:
    """Reject control, format and surrogate code points in *all* JSON text.

    Python accepts escaped isolated surrogates such as \\ud800 even though
    they cannot be encoded as valid UTF-8. Format characters can also hide
    or reorder values in downstream audit displays. Scan parsed keys and
    values iteratively, without echoing or recursing through untrusted data.
    """
    stack = [value]
    while stack:
        item = stack.pop()
        if type(item) is str:
            if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"}
                   for char in item):
                raise ValueError("invalid_json")
        elif type(item) is list:
            stack.extend(item)
        elif type(item) is dict:
            for key, child in item.items():
                stack.append(key)
                stack.append(child)


def decode_receipt(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_BYTES:
        raise ValueError("receipt_too_large")
    _check_json_nesting(raw)
    try:
        result = json.loads(raw.decode("utf-8", errors="strict"),
                            object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant,
                            parse_float=_finite_float,
                            parse_int=_bounded_integer)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("invalid_json") from error
    except ValueError as error:
        if str(error) in {"duplicate_json_key", "nonfinite_json"}:
            raise
        raise ValueError("invalid_json") from error
    if type(result) is not dict:
        raise ValueError("invalid_schema")
    _reject_unsafe_unicode(result)
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


def evaluate(receipt: Any, *, now: datetime | None = None,
             expected_main_sha: str | None = None) -> dict[str, Any]:
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
    if (not _valid_sha(candidate) or not _valid_sha(main) or
            candidate != main or
            (expected_main_sha is not None and
             (not _valid_sha(expected_main_sha) or main != expected_main_sha))):
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



# Fixed and deliberately value-free: malformed receipts must never reflect a
# path, log excerpt, token, control character, or untrusted source string.
INPUT_PROBLEMS = {
    "invalid_schema": (
        "Bewijsstructuur ongeldig of auditresultaat tegenstrijdig.",
        "Lever een bestand met uitsluitend het afgesproken schema aan.",
    ),
    "invalid_json": (
        "JSON bevat ongeldige of onveilige tekens of structuur.",
        "Maak een nieuw geldig UTF-8 JSON-bestand zonder stuurtekens.",
    ),
    "nonfinite_json": (
        "JSON bevat een ongeldig getal, bijvoorbeeld oneindig.",
        "Gebruik uitsluitend eindige JSON-getallen.",
    ),
    "duplicate_json_key": (
        "Eenzelfde JSON-sleutel komt meer dan één keer voor.",
        "Schrijf elke sleutel eenmaal en controleer de bron.",
    ),
    "receipt_too_large": (
        "Bewijsbestand overschrijdt de grens van 64 KiB.",
        "Lever een beknopt bestand met alleen de vereiste velden aan.",
    ),
    "unsafe_or_missing_receipt": (
        "Bewijsbestand ontbreekt of gebruikt een onveilig bestandspad.",
        "Kies een bestaand gewoon bestand zonder symlinks of onveilige padverwijzingen.",
    ),
    "unreadable_receipt": (
        "Bewijsbestand kon niet veilig worden beoordeeld.",
        "Controleer toegang en bestandstype; verstuur geen geheimen.",
    ),
}


def render_markdown(result: dict[str, Any]) -> str:
    """Produce bounded Dutch audit prose from known reason codes only.

    This is not a verification step: a perfectly filled-in but forged receipt
    remains an untrusted assertion and can never grant production authority.
    """
    expected_keys = {
        "contract", "evidence_source", "offline_claims_complete",
        "live_sources_verified", "production_mutation_authorized",
        "reason_codes",
    }
    codes = result.get("reason_codes") if type(result) is dict else None
    known = {code for code, _, _ in REPORT_GATES}
    allowed = known | {
        "invalid_schema", "receipt_too_large", "duplicate_json_key",
        "nonfinite_json", "invalid_json", "unsafe_or_missing_receipt",
        "unreadable_receipt",
    }
    valid_codes = (
        type(codes) is list
        and all(type(code) is str and code in allowed for code in codes)
        and len(set(codes)) == len(codes)
    )
    bad_shape = (
        type(result) is not dict
        or set(result) != expected_keys
        or result.get("contract") != "zcloud-release-evidence-v1"
        or result.get("evidence_source") != "untrusted_offline_receipt"
        or result.get("production_mutation_authorized") is not False
        or result.get("live_sources_verified") is not False
        or not valid_codes
        or result.get("offline_claims_complete") is not (not codes)
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
    if invalid:
        for code in codes:
            title, action = INPUT_PROBLEMS.get(code, INPUT_PROBLEMS["invalid_schema"])
            lines.extend([
                "",
                f"**Invoerprobleem:** {title}",
                f"**Op te lossen zonder live toegang:** {action}",
            ])
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


def read_receipt(path: Path) -> bytes:
    """Read one regular file through no-follow directory FDs, without TOCTOU.

    Every parent component is opened O_DIRECTORY|O_NOFOLLOW and kept by fd
    until its child is opened. A renamed or replaced symlink cannot redirect
    the final read, including in the short interval between path inspection
    and open. No writes, network calls or unbounded reads are performed.
    """
    parts = path.parts[1:] if path.is_absolute() else path.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError("unsafe_or_missing_receipt")
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        parent_fd = os.open("/" if path.is_absolute() else ".", dir_flags)
        try:
            for name in parts[:-1]:
                next_fd = os.open(name, dir_flags, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = next_fd
            file_fd = os.open(parts[-1], file_flags, dir_fd=parent_fd)
            try:
                if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                    raise ValueError("unsafe_or_missing_receipt")
                with os.fdopen(file_fd, "rb", closefd=True) as reader:
                    file_fd = -1  # fdopen now owns this descriptor
                    return reader.read(MAX_BYTES + 1)
            finally:
                if file_fd != -1:
                    os.close(file_fd)
        finally:
            os.close(parent_fd)
    except OSError as error:
        # Do not include paths, filesystem names or OS diagnostics in output.
        raise ValueError("unsafe_or_missing_receipt") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Local bounded JSON receipt path")
    parser.add_argument("--format", choices=("json", "markdown"), default="json",
                        help="Machine-readable JSON (default), or Dutch human-readable audit")
    parser.add_argument("--expected-main-sha",
                        help="Operator-supplied exact lowercase 40-char main SHA (NOT verified live)")
    parser.add_argument("--exit-zero-on-offline-claims", action="store_true",
                        help="Explicit offline-only opt-in: exit 0 if all UNVERIFIED claims are well formed; never release authority")
    args = parser.parse_args(argv)
    try:
        result = evaluate(
            decode_receipt(read_receipt(Path(args.input))),
            expected_main_sha=args.expected_main_sha
        )
        # An explicit diagnostic success must be bound to a separately
        # supplied exact main SHA. Missing context can never yield exit 0.
        # This does NOT fetch GitHub or turn the supplied SHA into live proof.
        if (args.exit_zero_on_offline_claims and
                args.expected_main_sha is None):
            if "candidate_not_exact_current_main" not in result["reason_codes"]:
                result["reason_codes"].append("candidate_not_exact_current_main")
            result["offline_claims_complete"] = False
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
    # Deny by default: a forged but schema-complete offline claim must never
    # look like a production admission success to a shell `set -e` gate.
    # Opt-in 0 is strictly a diagnostic claim-completeness signal, not a
    # verified GitHub / VPS / independent-review release decision.
    return 0 if (args.exit_zero_on_offline_claims and
                 result["offline_claims_complete"]) else 2


if __name__ == "__main__":
    sys.exit(main())
