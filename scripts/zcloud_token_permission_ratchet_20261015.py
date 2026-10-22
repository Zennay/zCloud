#!/usr/bin/env python3
"""Reject newly introduced elevated GitHub Actions token-permission declarations.

Read-only Git object comparison. This is a bounded lexical ratchet, not a YAML
interpreter, protection-rule replacement, or authorization to deploy.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys

SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
MAX_FILES = 512
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 24 * MAX_FILE_BYTES
MAX_FINDINGS = 4096


class IncompleteSource(ValueError):
    """Cannot prove the two immutable Git trees are complete and comparable."""


def git(root: Path, *args: str, timeout: int = 15) -> bytes:
    return subprocess.check_output(
        ["git", *args], cwd=root, stderr=subprocess.DEVNULL, timeout=timeout
    )


def tree(root: Path, sha: str) -> dict[str, str]:
    if not SHA.fullmatch(sha):
        raise IncompleteSource("invalid_sha")
    if git(root, "cat-file", "-t", sha).strip() != b"commit":
        raise IncompleteSource("missing_commit")
    if git(root, "cat-file", "-t", f"{sha}:.github/workflows").strip() != b"tree":
        raise IncompleteSource("missing_workflow_tree")
    raw = git(root, "ls-tree", "-r", "-z", sha, "--", ".github/workflows")
    result: dict[str, str] = {}
    total = 0
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            meta, filename = record.split(b"\t", 1)
            mode, kind, blob = meta.decode("ascii").split(" ")
            path = filename.decode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise IncompleteSource("bad_tree") from exc
        if (mode != "100644" and mode != "100755") or kind != "blob":
            raise IncompleteSource("symlink_or_gitlink")
        if not WORKFLOW.fullmatch(path):
            raise IncompleteSource("unexpected_workflow_path")
        if path in result or len(result) >= MAX_FILES:
            raise IncompleteSource("duplicate_or_oversized_tree")
        size_raw = git(root, "cat-file", "-s", blob).strip()
        if not size_raw.isdigit():
            raise IncompleteSource("invalid_blob_size")
        size = int(size_raw)
        total += size
        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise IncompleteSource("oversized_source")
        source = git(root, "cat-file", "blob", blob)
        if len(source) != size or b"\0" in source:
            raise IncompleteSource("invalid_blob_contents")
        try:
            result[path] = source.decode("utf-8", "strict")
        except UnicodeError as exc:
            raise IncompleteSource("non_utf8_workflow") from exc
    if not result:
        raise IncompleteSource("empty_workflows")
    return result


def _uncomment(line: str) -> str:
    # Remove ordinary comments, but retain # characters inside quoted scalars.
    quote = None
    escaped = False
    for n, char in enumerate(line):
        if char == "\\" and quote == '"' and not escaped:
            escaped = True
            continue
        if char in ("'", '"') and not escaped:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        if char == "#" and quote is None and (n == 0 or line[n - 1].isspace()):
            return line[:n]
        escaped = False
    return line


def _fingerprint(value: str) -> str:
    # Internal scope-sensitive fingerprint, never log original YAML or secrets.
    return sha256(value.encode("utf-8", "strict")).hexdigest()


def _scalar(value: str) -> str:
    raw = value.strip().lower()
    if not raw:
        return "__opaque__"
    if raw.startswith(("'", '"')) or raw.endswith(("'", '"')):
        if len(raw) < 2 or raw[0] != raw[-1]:
            return "__opaque__"
        return raw[1:-1]
    return raw


def _mapping_risk(body: list[str], indent: int) -> str | None:
    if not body:
        return "opaque_permissions"
    seen: set[str] = set()
    risky = []
    for line in body:
        stripped = _uncomment(line).strip()
        if not stripped:
            continue
        width = len(line) - len(line.lstrip(" "))
        if width != indent + 2 or "\t" in line[:width]:
            return "opaque_permissions"
        pair = re.fullmatch(r"""([A-Za-z][A-Za-z-]*|['"][A-Za-z][A-Za-z-]*['"]):\s*([^{}\[\],&*]+?)\s*""", stripped)
        if not pair:
            return "opaque_permissions"
        key = _scalar(pair.group(1))
        value = _scalar(pair.group(2))
        if key in seen or value not in ("read", "write", "none"):
            return "opaque_permissions"
        seen.add(key)
        if value == "write":
            risky.append(key)
    if not seen:
        return "opaque_permissions"
    return "write:" + ",".join(sorted(risky)) if risky else None


def permission_debt(
    source: str, path: str, *, protected: Counter[str] | None = None
) -> Counter[str]:
    """Capture source-scoped risky declarations; unchanged legacy risk stays grandfathered."""
    lines = source.splitlines()
    findings: Counter[str] = Counter()
    in_jobs = False
    job = None
    jobs_seen: set[str] = set()
    jobs_header_count = 0
    saw_root_key = False
    jobs_with_explicit_scopes: set[str] = set()
    safe_root_scope = False
    run_scalar_indent: int | None = None
    for i, original in enumerate(lines):
        line = _uncomment(original)
        if not line.strip():
            continue
        if "\t" in original[:len(original) - len(original.lstrip())]:
            raise IncompleteSource("tab_indentation")
        width = len(original) - len(original.lstrip(" "))
        if not saw_root_key and not re.fullmatch(r"(?:---|\.\.\.|%YAML\s+.+)", line.strip()):
            saw_root_key = True
            if width != 0:
                # A root mapping indented as a whole may be valid YAML. Our
                # zero-indent top-level job parser would miss its new jobs
                # inheriting repository write-token defaults. Bind every
                # legacy noncanonical root to the full immutable YAML blob.
                findings[f"{path}|opaque_indented_root|{_fingerprint(line)}"] += 1
        # Text beneath a literal/folded 'run:' script is inert YAML scalar
        # content, not workflow mapping keys. Do not mistake snippets like
        # "permissions: write-all" within examples or shell heredocs for a
        # real GitHub token grant. The first de-dented mapping resumes checks.
        if run_scalar_indent is not None:
            if width > run_scalar_indent:
                continue
            run_scalar_indent = None
        if re.fullmatch(r"\s*(?:-\s*)?run\s*:\s*[|>][-+1-9]*\s*", line):
            run_scalar_indent = width
        if width == 0:
            # GitHub Actions permits YAML variants of the 'jobs' mapping
            # (quoted keys, aliases and nonstandard whitespace). We only
            # model the conventional literal jobs key and two-space job keys.
            # The rest may hide new jobs inheriting repository token defaults;
            # freeze the entire source blob instead of reusing the old
            # top-level implicit-default finding for every new job.
            jobs_header = re.match(r"""^(?:jobs|['"]jobs['"])\s*:\s*(.*)$""", line)
            if jobs_header:
                jobs_header_count += 1
                if jobs_header_count > 1:
                    # Duplicate top-level jobs mappings can shadow a previous
                    # job and revoke its explicit read-only token boundary.
                    findings[f"{path}|opaque_duplicate_jobs_mapping|{_fingerprint(line)}"] += 1
            in_jobs = bool(re.fullmatch(r"jobs:\s*", line))
            if (jobs_header and not in_jobs) or re.fullmatch(
                r"""\?\s*(?:jobs|['"]jobs['"])\s*""", line
            ) or re.match(
                r"""^(?:\?\s*)?(?:(?:!![\w:-]+|!\S+|&[\w-]+)\s+)+(?:jobs|['"]jobs['"])\s*(?::|$)""",
                line,
            ):
                # Explicit YAML complex mapping keys can hide the 'jobs'
                # mapping, even when its child lines are ordinary job ids.
                findings[f"{path}|opaque_job_structure|{_fingerprint(line)}"] += 1
            job = None
        elif in_jobs and width == 2:
            head = re.fullmatch(r"  ([A-Za-z_][A-Za-z0-9_-]*):\s*", line)
            job = head.group(1) if head else None
            if job is not None:
                if job in jobs_seen:
                    # Duplicate job ids are parser-dependent; do not assume
                    # that the first job's safe permissions still apply.
                    findings[f"{path}|opaque_duplicate_job|{_fingerprint(line)}"] += 1
                jobs_seen.add(job)
            else:
                # Inline flow jobs, aliases and nonliteral job keys can hide
                # effective "permissions" that override a safe root policy.
                findings[f"{path}|opaque_job_declaration|{_fingerprint(line)}"] += 1
        elif in_jobs and width > 2 and job is None:
            # Four-space initial job keys (or an exotic structure) are not
            # safely identifiable by the two-space lexical job inventory.
            findings[f"{path}|opaque_job_indentation|{_fingerprint(line)}"] += 1
        # Explicit YAML mapping keys, merge aliases and decoded mapping
        # keys are not proven safe by lexical checks. Conservatively deny new
        # instances, while preserving the exact source baseline as old debt.
        ambiguous = (
            # Any explicit YAML complex mapping key can resolve to a
            # permissions/job policy via quotes, escapes, tags or multiline
            # scalar spelling. No lexical substring check can prove safety.
            re.match(r"^\s*\?(?:\s|$)", line)
            or re.match(r"^\s*\?\s*(?:permissions|['\"]permissions['\"])\b", line)
            or re.match(r"^\s*<<\s*:", line)
            or re.match(r"""^\s*['"].*\\(?:u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|U[0-9a-fA-F]{8}).*['"]\s*:""", line)
            or re.match(
                r"""^\s*(?:\?\s*)?(?:(?:!![\w:-]+|!\S+|&[\w-]+)\s+)+(?:permissions|['"]permissions['"])\s*(?::|$)""",
                line,
            )
            or re.match(
                # YAML tags and key anchors can make even an encoded name
                # like !!str "permiss\\u0069ons" resolve to permissions.
                # We cannot safely decode every variant lexically, so a
                # tagged/anchored mapping key is document-bound opaque debt.
                r"""^\s*(?:\?\s*)?(?:(?:!!\S+|!\S+|&[\w-]+)\s+)+(?:["'][^"']+["']|[A-Za-z_][\w-]*)\s*(?::|$)""",
                line,
            )
        )
        if ambiguous:
            findings[f"{path}|opaque_yaml_key|{_fingerprint(line)}"] += 1
        match = re.match(r"""^\s*(?:permissions|['"]permissions['"])\s*:\s*(.*)$""", line)
        if not match:
            continue
        scope = "top" if width == 0 else f"job:{job}" if width == 4 and in_jobs and job else "unsupported"
        value = match.group(1).strip()
        if not value:
            body = []
            for child in lines[i + 1:]:
                clean = _uncomment(child)
                if not clean.strip():
                    continue
                child_width = len(child) - len(child.lstrip(" "))
                if child_width <= width:
                    break
                body.append(child)
            risk = _mapping_risk(body, width)
        elif _scalar(value) in ("read-all", "{}"):
            risk = None
        elif _scalar(value) == "write-all":
            risk = "write-all"
        elif value.startswith("{") and value.endswith("}"):
            # Flow permissions are intentionally not parsed: unsafe to assume
            # aliases, Unicode escapes, or mixed mappings are read-only.
            risk = "opaque_flow_permissions"
        else:
            risk = "opaque_permissions"
        if scope == "top" and risk is None:
            safe_root_scope = True
        if scope.startswith("job:"):
            jobs_with_explicit_scopes.add(scope[4:])
        if risk is None and protected is not None:
            protected[f"{path}|{scope}"] += 1
        if risk is not None:
            if risk.startswith("opaque"):
                sample = value if value else "\n".join(body)
                risk += ":" + _fingerprint(sample)
            findings[f"{path}|{scope}|{risk}"] += 1
            if sum(findings.values()) > MAX_FINDINGS:
                raise IncompleteSource("too_many_findings")
    # A new workflow/job without an explicit scope may inherit a broader
    # repository or root workflow default. Track this per job and path so
    # merely appending a new implicit job cannot reuse old debt.
    if not safe_root_scope:
        for existing_job in sorted(jobs_seen - jobs_with_explicit_scopes):
            findings[f"{path}|job:{existing_job}|implicit_default"] += 1
        if not jobs_seen:
            findings[f"{path}|top|implicit_default"] += 1
    # An alias/flow/escaped YAML key can resolve through an anchor defined
    # elsewhere in this workflow. Binding just the opaque scalar would allow
    # unchanged "*trust" to inherit a newly elevated &trust target.
    # Reuse historic opaque debt only if the *entire* committed workflow blob
    # is unchanged (intentionally conservative for YAML we cannot parse).
    if any("|opaque" in key for key in findings):
        findings[f"{path}|opaque_document_sha|{_fingerprint(source)}"] += 1
    if sum(findings.values()) > MAX_FINDINGS:
        raise IncompleteSource("too_many_findings")
    return findings


def check(root: Path, base: str, head: str) -> dict[str, object]:
    if not SHA.fullmatch(base) or not SHA.fullmatch(head) or base == head:
        raise IncompleteSource("invalid_commit_pair")
    try:
        git(root, "merge-base", "--is-ancestor", base, head)
        old = tree(root, base)
        new = tree(root, head)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise IncompleteSource("git_source_unverifiable") from exc
    previous_safe: Counter[str] = Counter()
    current_safe: Counter[str] = Counter()
    before = sum(
        (permission_debt(text, name, protected=previous_safe) for name, text in old.items()),
        Counter(),
    )
    after = sum(
        (permission_debt(text, name, protected=current_safe) for name, text in new.items()),
        Counter(),
    )
    introduced = after - before
    removed_safe = previous_safe - current_safe
    # Do not print arbitrary workflow source, paths or permission values in CI.
    return {
        "policy": "zcloud-token-permission-no-new-debt-v1",
        "baseline_risks": sum(before.values()),
        "candidate_risks": sum(after.values()),
        "new_risks": sum(introduced.values()) + sum(removed_safe.values()),
        "removed_safe_scopes": sum(removed_safe.values()),
        "ok": not introduced and not removed_safe,
        "release_authorized": False,
        "deployment_authorized": False,
        "mutation_performed": False,
    }



def format_ci_summary(payload: dict[str, object]) -> str:
    """Dutch, bounded, fixed-wording CI summary: no source/secret interpolation."""
    if (type(payload) is not dict
            or payload.get("policy") != "zcloud-token-permission-no-new-debt-v1"
            or type(payload.get("ok")) is not bool
            or any(payload.get(flag) is not False for flag in
                   ("release_authorized", "deployment_authorized", "mutation_performed"))):
        raise IncompleteSource("contradictory_report_authority")
    lines = ["### zCloud GitHub-tokenrechten (alleen broncontrole)"]
    if payload["ok"] is False:
        lines.append("**Afgewezen:** onvolledig bewijs of nieuwe bronrisico's.")
    else:
        keys = ("baseline_risks", "candidate_risks",
                "new_risks", "removed_safe_scopes")
        if any(type(payload.get(key)) is not int or
               not 0 <= payload[key] <= MAX_FILES * MAX_FINDINGS for key in keys):
            raise IncompleteSource("invalid_report_counts")
        if payload["new_risks"] or payload["removed_safe_scopes"]:
            raise IncompleteSource("contradictory_report_success")
        lines.extend((
            "**Broncontrole geslaagd.** Geen nieuwe tokenrechten in de gecontroleerde bron.",
            f"- Eerdere risicodeclaraties: {payload['baseline_risks']}.",
            f"- Kandidaat-risicodeclaraties: {payload['candidate_risks']}.",
            f"- Nieuwe risico's: {payload['new_risks']}.",
            f"- Verwijderde expliciete veilige scopes: {payload['removed_safe_scopes']}.",
        ))
    lines.append("**Geen productievrijgave:** release, deployment en mutatie blijven verboden.")
    lines.append("Dit is een syntactische Git-broncontrole, geen runner- of VPS-acceptatie.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--summary-to", type=Path)
    args = parser.parse_args(argv)
    try:
        result = check(args.root.resolve(), args.base, args.head)
    except (IncompleteSource, OSError, subprocess.SubprocessError, UnicodeError):
        result = {
            "policy": "zcloud-token-permission-no-new-debt-v1",
            "ok": False, "reason": "incomplete_git_source",
            "release_authorized": False, "deployment_authorized": False,
            "mutation_performed": False,
        }
    if args.summary_to is not None:
        try:
            summary = format_ci_summary(result)
            with args.summary_to.open("a", encoding="utf-8") as handle:
                handle.write(summary)
        except (OSError, IncompleteSource):
            result = {
                "policy": "zcloud-token-permission-no-new-debt-v1",
                "ok": False, "reason": "summary_evidence_failed",
                "release_authorized": False, "deployment_authorized": False,
                "mutation_performed": False,
            }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
