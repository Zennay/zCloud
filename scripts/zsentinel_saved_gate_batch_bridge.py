#!/usr/bin/env python3
"""Convert complete saved zSentinel PR-gate batches to one offline workbench import.

Never connects to GitHub, modifies a worker/claim, verifies merge eligibility,
or turns a saved observation into current-head approval.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

SCHEMA = "zsentinel-offline-merge-first-review-v1"
GATE_SCHEMA = "zsentinel-current-pr-gate-observation-v1"
OUTPUT_SCHEMA = "zsentinel-integrated-issue-pr-scope-v1"
PR = re.compile(r"https://github\.com/(Zennay/[A-Za-z0-9_.-]{1,100})/pull/([1-9][0-9]{0,8})\Z")
ISSUE = re.compile(r"(Zennay/[A-Za-z0-9_.-]{1,100})#([1-9][0-9]{0,8})\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")
HEAD = re.compile(r"[a-f0-9]{40}\Z")
MAX_BYTES = 1024 * 1024
MAX_TOTAL = 8 * MAX_BYTES
ALLOWED_STATUS = {"github_source_unavailable_or_head_changed",
                  "observed_current_head_partial_gate_evidence"}
# Only these exact source fields may reach the browser; unknown metadata
# and any accidental credentials are never copied through.
GATE_FIELDS = (
    "url", "status", "head_sha_observed", "exact_head_stable_on_second_read",
    "observed_checks_pending", "observed_checks_not_successful",
    "observed_independent_exact_head_approvals",
    "observed_exact_head_changes_requested", "legacy_status_pending_observed",
    "legacy_status_failure_or_error_observed", "observed_checks_completed",
    "legacy_status_contexts_observed", "legacy_status_source_may_be_truncated",
    "check_runs_page_complete", "review_page_may_be_truncated",
    "human_next_gate_step", "required_branch_checks_and_rules_verified",
    "required_review_policy_verified", "mergable_by_automation",
    "merge_authorized", "deployment_authorized", "read_only",
)


def unique_object(pairs):
    """Prevent a late duplicate flag from hiding earlier contradictory evidence."""
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("Duplicate JSON evidence field")
        obj[key] = value
    return obj


def invalid_constant(_value):
    raise ValueError("Nonfinite number in source JSON")


def bounded_json(path: Path) -> tuple[dict, bytes]:
    """One identity-bound, nofollow, nonblocking open for offline evidence."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise ValueError("Required safe file opening is unavailable")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 2 <= before.st_size <= MAX_BYTES:
        raise ValueError("Source is not a bounded regular file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino) != (before.st_dev, before.st_ino)
                or metadata.st_size != before.st_size):
            raise ValueError("Source replaced between metadata and read")
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            content = stream.read(MAX_BYTES + 1)
        after = os.fstat(descriptor)
        if (len(content) != metadata.st_size or len(content) > MAX_BYTES
                or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                != (metadata.st_dev, metadata.st_ino, metadata.st_size,
                    metadata.st_mtime_ns)):
            raise ValueError("Source content or metadata changed during read")
    finally:
        os.close(descriptor)
    value = json.loads(content.decode("utf-8", errors="strict"),
                       object_pairs_hook=unique_object,
                       parse_constant=invalid_constant)
    if type(value) is not dict:
        raise ValueError("Source must be a JSON object")
    return value, content


def canonical_manifest(source: dict, raw: bytes):
    if (source.get("schema") != SCHEMA
            or source.get("evidence_scope") != "bounded_saved_snapshot_not_live"
            or any(source.get(key) is not False for key in
                   ("claim_authorized", "merge_authorized", "deploy_authorized",
                    "dispatch_authorized"))
            or type(source.get("source_board_sha256")) is not str
            or not SHA.fullmatch(source["source_board_sha256"])):
        raise ValueError("Not a canonical, non-authorizing review manifest")
    refs = source.get("pr_references")
    if type(refs) is not list or not 1 <= len(refs) <= 300:
        raise ValueError("Review candidate count is invalid")
    priorities = {"P0": 0, "P1": 1, "P2": 2, "Unspecified": 3}
    selected = []
    seen = set()
    for ref in refs:
        if (type(ref) is not dict or type(ref.get("url")) is not str
                or PR.fullmatch(ref["url"]) is None
                or ref.get("merge_authorized") is not False
                or ref.get("highest_linked_priority") not in priorities
                or type(ref.get("observed_issue_refs")) is not list
                or not ref["observed_issue_refs"]):
            raise ValueError("Malformed saved PR reference")
        url = ref["url"]
        if url in seen:
            raise ValueError("Duplicate PR reference")
        seen.add(url)
        match = PR.fullmatch(url)
        repository = match.group(1)
        linked_issues = set()
        if len(ref["observed_issue_refs"]) > 60:
            raise ValueError("Too many observed issues for one saved PR")
        for item in ref["observed_issue_refs"]:
            if (type(item) is not dict or type(item.get("issue")) is not str
                    or ISSUE.fullmatch(item["issue"]) is None
                    or item.get("url") != "https://github.com/" +
                    item["issue"].replace("#", "/issues/")):
                raise ValueError("Invalid canonical issue-to-PR relationship")
            # A saved manifest is untrusted input. Never display unrelated
            # repository issues as the repair target of this PR: the real
            # board admits only in-repository PR links, so preserve it here.
            issue_repo = item["issue"].rsplit("#", 1)[0]
            if issue_repo != repository or item["issue"] in linked_issues:
                raise ValueError("Foreign or duplicate issue-to-PR association")
            linked_issues.add(item["issue"])
        selected.append((priorities[ref["highest_linked_priority"]],
                         repository, int(match.group(2)), ref))
    selected.sort(key=lambda row: (row[0], row[1], row[2]))
    return selected, hashlib.sha256(raw).hexdigest()


def observed_time(value: str):
    if type(value) is not str or len(value) > 40:
        raise ValueError("Invalid UTC time")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError("Not an explicit UTC timestamp")
    moment = stamp.timestamp()
    if moment > datetime.now(timezone.utc).timestamp() + 300:
        raise ValueError("Future observation")
    return moment


def combined_manifest(manifest: dict, raw: bytes, paths: list[Path], *, allow_partial=False):
    selected, digest = canonical_manifest(manifest, raw)
    total = (len(selected) + 3) // 4
    if not 1 <= len(paths) <= 75:
        raise ValueError("Expected one to 75 saved batch receipts")
    indexed = {}
    bytes_total = len(raw)
    earliest, latest = float("inf"), float("-inf")
    for path in paths:
        receipt, payload = bounded_json(path)
        bytes_total += len(payload)
        if bytes_total > MAX_TOTAL:
            raise ValueError("Combined source exceeds eight MiB")
        count = receipt.get("source_review_pr_count")
        idx = receipt.get("source_review_batch_index")
        first = (idx - 1) * 4 if type(idx) is int else -1
        expected_rows = selected[first:first + 4] if 0 <= first < len(selected) else []
        rows = receipt.get("items")
        if (receipt.get("schema") != GATE_SCHEMA
                or receipt.get("merge_authorized") is not False
                or receipt.get("deployment_authorized") is not False
                or receipt.get("writes_performed") is not False
                or receipt.get("required_branch_protection_verified") is not False
                or receipt.get("current_review_and_check_samples_only") is not True
                or receipt.get("source_review_manifest_sha256") != digest
                or receipt.get("source_review_board_sha256") != manifest["source_board_sha256"]
                or count != len(selected) or type(count) is not int
                or type(idx) is not int or not 1 <= idx <= total
                or idx in indexed or receipt.get("source_review_batches_total") != total
                or receipt.get("source_review_batch_first_pr") != first + 1
                or receipt.get("source_review_batch_count") != len(expected_rows)
                or receipt.get("review_only_top_four_observed") is not (len(selected) > 4)
                or type(rows) is not list or len(rows) != len(expected_rows)):
            raise ValueError("Gate batch provenance, order or non-authorizing flags invalid")
        timestamp = observed_time(receipt.get("observed_at_utc"))
        earliest = min(earliest, timestamp)
        latest = max(latest, timestamp)
        if latest - earliest > 600:
            raise ValueError("Gate observations exceed ten-minute common window")
        sanitized = {}
        for row, expected in zip(rows, expected_rows):
            url = expected[3]["url"]
            if (type(row) is not dict or row.get("url") != url
                    or row.get("status") not in ALLOWED_STATUS
                    or row.get("read_only") is not True
                    or row.get("merge_authorized") is not False
                    or row.get("deployment_authorized") is not False
                    or row.get("required_branch_checks_and_rules_verified") is not False):
                raise ValueError("PR receipt is foreign or authorization is unsafe")
            if row["status"] == "observed_current_head_partial_gate_evidence":
                counters = ("observed_checks_pending", "observed_checks_not_successful",
                            "observed_independent_exact_head_approvals",
                            "observed_exact_head_changes_requested",
                            "legacy_status_pending_observed",
                            "legacy_status_failure_or_error_observed",
                            "observed_checks_completed", "legacy_status_contexts_observed")
                booleans = ("exact_head_stable_on_second_read",
                            "legacy_status_source_may_be_truncated",
                            "check_runs_page_complete", "review_page_may_be_truncated",
                            "required_review_policy_verified", "mergable_by_automation")
                if (type(row.get("head_sha_observed")) is not str
                        or HEAD.fullmatch(row["head_sha_observed"]) is None
                        or row.get("exact_head_stable_on_second_read") is not True
                        or any(type(row.get(k)) is not int or not 0 <= row[k] <= 100
                               for k in counters)
                        or any(type(row.get(k)) is not bool for k in booleans)
                        or row.get("required_review_policy_verified") is not False
                        or row.get("mergable_by_automation") is not False
                        or type(row.get("human_next_gate_step")) is not str
                        or re.fullmatch("[a-z_]{5,80}", row["human_next_gate_step"]) is None):
                    raise ValueError("Malformed exact-head gate observation")
            # Failed/unavailable observations carry no trustworthy gate details.
            # Never pass accidental receipt metadata through to browser/exports.
            fields = (GATE_FIELDS if row["status"] ==
                      "observed_current_head_partial_gate_evidence" else
                      ("url", "status", "read_only", "merge_authorized",
                       "deployment_authorized",
                       "required_branch_checks_and_rules_verified"))
            sanitized[url] = {k: row[k] for k in fields if k in row}
        indexed[idx] = sanitized
    missing = sorted(set(range(1, total + 1)) - set(indexed))
    if missing and not allow_partial:
        raise ValueError("Saved review batches missing; explicitly allow partial observations")
    # Show exact *existing* unobserved PR references for human follow-up.
    # These are source-manifest references, not evidence that the PR is open,
    # unclaimed, green or authorized for merge/release.
    missing_batch_prs = [
        {"batch_index": index,
         "unobserved_pr_urls": [
             row[3]["url"] for row in selected[(index - 1) * 4:index * 4]]}
        for index in missing
    ]
    issue_map = {}
    for position, (_, _, _, ref) in enumerate(selected):
        url = ref["url"]
        idx = position // 4 + 1
        if idx not in indexed:
            # A missing batch is UNKNOWN, never represented as a passed PR.
            continue
        gate = indexed[idx][url]
        for item in ref["observed_issue_refs"]:
            identity = item["issue"]
            if identity not in issue_map and len(issue_map) >= 60:
                raise ValueError("More than sixty issue cards; browser importer bound exceeded")
            issue = issue_map.setdefault(identity, {
                "issue": identity, "url": item["url"], "existing_prs": []})
            if issue["url"] != item["url"]:
                raise ValueError("Contradictory issue URL")
            issue["existing_prs"].append({
                "url": url, "live_exact_head_review_checks_observation": gate})
            if len(issue["existing_prs"]) > 5:
                raise ValueError("One issue links more than five PRs; cannot import safely")
    return {
        "schema": OUTPUT_SCHEMA,
        # Browser imports use generated_at_utc for stale warnings: use the
        # OLDEST observed batch, never newest, to avoid disguising aged rows.
        "generated_at_utc": datetime.fromtimestamp(earliest, timezone.utc).isoformat(),
        "earliest_observed_at_utc": datetime.fromtimestamp(earliest, timezone.utc).isoformat(),
        "latest_observed_at_utc": datetime.fromtimestamp(latest, timezone.utc).isoformat(),
        "issues": list(issue_map.values()),
        "original_review_manifest_sha256": digest,
        "original_board_sha256": manifest["source_board_sha256"],
        "observed_batch_count": len(indexed),
        "expected_batch_count": total,
        "review_candidate_pr_count": len(selected),
        "missing_batch_count": len(missing),
        "missing_batch_indexes": missing,
        "missing_batch_prs": missing_batch_prs,
        "all_batches_observed_not_all_checks_required_verified": not missing,
        "saved_receipts_not_required_checks_verified": True,
        "claim_authorized": False,
        "merge_authorized": False,
        "deploy_authorized": False,
        "may_take_ownership": False,
        "writes_performed": False,
    }



def markdown_brief(manifest: dict, raw: bytes, integrated: dict) -> str:
    """Human-readable, priority-ordered handoff from saved read-only gate receipts.

    Never upgrades observed checks/reviews to required approval or infers merge.
    This is the same snapshot as the JSON browser bridge, with no extra GETs.
    """
    selected, digest = canonical_manifest(manifest, raw)
    if (integrated.get("original_review_manifest_sha256") != digest
            or integrated.get("merge_authorized") is not False
            or integrated.get("deploy_authorized") is not False):
        raise ValueError("Review handoff provenance has changed")
    observed = {}
    for issue in integrated["issues"]:
        for item in issue["existing_prs"]:
            url, gate = item["url"], item["live_exact_head_review_checks_observation"]
            if url in observed and observed[url] != gate:
                raise ValueError("Conflicting PR observations in saved review")
            observed[url] = gate
    missing = set(integrated["missing_batch_indexes"])
    priority_name = ("P0", "P1", "P2", "Unspecified")
    lines = [
        "# zSentinel — private merge-first PR-overdracht",
        "",
        "> Alleen een begrensde opgeslagen momentopname, geen actuele merge-",
        "> review-, eigenaars-, CI- of deployautorisatie. Controleer alles live.",
        "",
        "- Manifest SHA256: `" + digest + "`",
        "- Oorspronkelijk board SHA256: `" + integrated["original_board_sha256"] + "`",
        "- Oudste gate-observatie UTC: " + integrated["earliest_observed_at_utc"],
        "- Nieuwste gate-observatie UTC: " + integrated["latest_observed_at_utc"],
        "- Batches waargenomen: " + str(integrated["observed_batch_count"]) +
          "/" + str(integrated["expected_batch_count"]),
        "- PR-kandidaten uit bron: " + str(len(selected)) +
          " (geen bewijs dat ze nu open of vrij zijn)",
        "- Niet bekeken batches: " + (", ".join(map(str, sorted(missing))) or "geen"),
        "- Claims, verplichte branchregels, CI, review en release: **NIET VRIJGEGEVEN**",
        "",
        "## Prioritaire bestaande PR-kandidaten",
        "",
    ]
    for position, (priority, repo, number, ref) in enumerate(selected):
        url = ref["url"]
        batch = position // 4 + 1
        lines.extend([
            "### " + priority_name[priority] + " · [" + repo + "#" +
            str(number) + "](" + url + ")",
            "",
            "Bronissues: " + ", ".join(
                "[" + issue["issue"] + "](" + issue["url"] + ")"
                for issue in ref["observed_issue_refs"]),
        ])
        gate = observed.get(url)
        if batch in missing or gate is None:
            lines.append("**NIET BEOORDEELD:** gate-batch " + str(batch) +
                         " ontbreekt; actuele HEAD, CI en reviews onbekend.")
        elif gate["status"] != "observed_current_head_partial_gate_evidence":
            lines.append("**ONBEKEND:** GitHub onbereikbaar of PR-HEAD gewijzigd; "
                         "haal nieuwe exacte bron op vóór opvolging.")
        else:
            lines.extend([
                "Waargenomen HEAD: `" + gate["head_sha_observed"] + "`",
                "Check-run steekproef: " +
                str(gate["observed_checks_completed"]) + " afgerond, " +
                str(gate["observed_checks_pending"]) + " lopend, " +
                str(gate["observed_checks_not_successful"]) + " niet geslaagd.",
                "Legacy status: " +
                str(gate["legacy_status_pending_observed"]) + " lopend, " +
                str(gate["legacy_status_failure_or_error_observed"]) + " fout.",
                "Onafhankelijke reviews waargenomen: " +
                str(gate["observed_independent_exact_head_approvals"]) +
                " approvals, " + str(gate["observed_exact_head_changes_requested"]) +
                " wijzigingen gevraagd (vereiste reviewpolicy onbekend).",
                "Volgende menselijke stap: `" + gate["human_next_gate_step"] + "`.",
            ])
            if (not gate["check_runs_page_complete"] or
                    gate["review_page_may_be_truncated"] or
                    gate["legacy_status_source_may_be_truncated"]):
                lines.append("**GEDEELTELIJKE BRON:** checks, reviews of status "
                             "kunnen buiten de waargenomen pagina vallen.")
        lines.extend([
            "Merge, actieve eigenaar en productielive-status: **ONBEKEND / "
            "NIET GEAUTORISEERD**.",
            "",
        ])
    lines.extend([
        "## Handmatige vrijgavegrenzen",
        "",
        "Ververs exact-head verplichte GitHub CI en branches, vraag onafhankelijke "
        "review en controleer huidige Notion/workerclaims. Alleen geautoriseerde "
        "GitHub merge/release met echte main-SHA geldt als oplevering. "
        "Niets in dit rapport voert een actie uit.",
        "",
    ])
    return "\n".join(lines)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-review", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, action="append", required=True)
    parser.add_argument("--allow-partial", action="store_true",
                        help="Import only supplied, source-bound batches; label missing batches UNKNOWN")
    parser.add_argument("--format", choices=("json", "markdown"), default="json",\n                        help="Private JSON browser import or readable merge-first Markdown")\n    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        review, raw = bounded_json(args.from_review)
        result = combined_manifest(review, raw, args.receipt,
                                   allow_partial=args.allow_partial)
        if args.format == "markdown":
            rendered = markdown_brief(review, raw, result).encode("utf-8")
        else:
            rendered = (json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2) +
                        "\n").encode("utf-8")
        if len(rendered) > MAX_BYTES:
            raise ValueError("Private handoff output exceeds one MiB")
        fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(rendered)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            args.output.unlink(missing_ok=True)
            raise
        if result["missing_batch_count"]:
            print("Private PARTIAL saved-gate " + args.format + " handoff written: missing batch data UNKNOWN; no action authorized.")
        else:
            print("Private COMPLETE saved-gate " + args.format + " handoff written: required CI/review still UNKNOWN.")
        return 0
    except (OSError, ValueError, UnicodeError, OverflowError, TypeError):
        print("Refused: invalid bounded source, incomplete/mismatched batches or output.",
              file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
