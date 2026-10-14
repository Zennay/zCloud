#!/usr/bin/env python3
"""Read-only Git source ratchet: deny cancellation of possible VPS jobs.

This is not a deployment or release authorization; no HTTP/Actions/VPS/DB writes.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
HOSTED = re.compile(r"(?:ubuntu-(?:latest|22\.04|24\.04(?:-arm)?)|windows-(?:latest|2022|2025)|macos-(?:latest|13|14|15|26))\Z")
RUNNER = re.compile(r"(?m)^    runs-on:\s*([^\r\n#]*)")
JOB = re.compile(r"(?m)^  ([A-Za-z_][A-Za-z0-9_-]*):\s*$")


class Unverifiable(ValueError):
    pass


def git(root, *args):
    return subprocess.check_output(
        ["git", *args], cwd=root, timeout=15, stderr=subprocess.DEVNULL
    )


def changed(root, base, head):
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise Unverifiable("sha")
    if git(root, "merge-base", base, head).decode("ascii", "strict").strip() != base:
        raise Unverifiable("base_not_ancestor")
    raw = git(root, "diff", "--name-only", "-z", "--diff-filter=ACMRT",
              f"{base}...{head}", "--", ".github/workflows/")
    paths = [part.decode("utf-8", "strict") for part in raw.split(b"\0") if part]
    if len(paths) > 250 or len(paths) != len(set(paths)) or any(
        not WORKFLOW.fullmatch(path) for path in paths
    ):
        raise Unverifiable("paths")
    return paths


def source(root, sha, path, *, missing_ok=False):
    if not SHA.fullmatch(sha) or not WORKFLOW.fullmatch(path):
        raise Unverifiable("path")
    info = git(root, "ls-tree", "-z", sha, "--", path)
    if not info:
        if missing_ok:
            return None
        raise Unverifiable("missing")
    if info.count(b"\0") != 1 or not info.startswith(b"100644 blob "):
        raise Unverifiable("mode")
    raw = git(root, "show", f"{sha}:{path}")
    if len(raw) > 100000 or b"\0" in raw:
        raise Unverifiable("size")
    try:
        return raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise Unverifiable("encoding") from exc


def _cancel_values(area, *, indent):
    matches = re.findall(
        r"(?m)^" + re.escape(indent) + r"cancel-in-progress:\s*([^\r\n#]*)",
        area,
    )
    # Only a literal YAML boolean false is provably non-cancelling.
    # Quoted "false" is a string and must not be treated as a proof.
    return any(item.strip() != "false" for item in matches)


def _inline_concurrency(area, indent):
    """Inline YAML concurrency maps are ambiguous: never silently ignore them."""
    vals = re.findall(
        r"(?m)^" + re.escape(indent) + r"concurrency:[ \t]*(\{[^\r\n]*\}|[^\r\n#]+)$",
        area,
    )
    return bool(vals)


def risks(text, *, details=False):
    """Conservative parser of jobs and root/job-level cancellation.

    Unknown runner expressions/reusable jobs are possibly self-hosted.
    Unexpected source layouts are denied rather than approved.
    """
    if (not re.search(r"(?m)^on:\s*(?:\[.*\]|\{.*\})?\s*$|^on:\s*$", text)
            or len(re.findall(r"(?m)^jobs:\s*$", text)) != 1
            or len(re.findall(r"(?m)^on:", text)) != 1):
        raise Unverifiable("structure")
    if re.search(r"(?m)^[ ]{0,6}(?:<<:|concurrency:\s*[&*]|runs-on:\s*[&*])", text):
        raise Unverifiable("yaml_alias")
    # YAML permits whitespace before the colon in a mapping key; the strict
    # source scanner must deny those layouts rather than miss enabled cancels.
    if re.search(r"(?m)^[ \t]*(?:concurrency|cancel-in-progress)[ \t]+:", text):
        raise Unverifiable("policy_key_whitespace")
    # Nonstandard valid YAML indentation or quoted policy keys are DENY, not PASS.
    if re.search(r"(?m)^[ \t]*[\x27\x22](?:concurrency|cancel-in-progress)[\x27\x22]\s*:", text):
        raise Unverifiable("quoted_key")
    for match in re.finditer(r"(?m)^([ \t]*)cancel-in-progress:", text):
        if match.group(1) not in ("  ", "      "):
            raise Unverifiable("cancel_indentation")
    for match in re.finditer(r"(?m)^([ \t]*)concurrency:", text):
        if match.group(1) not in ("", "    "):
            raise Unverifiable("concurrency_indentation")
    start = re.search(r"(?m)^jobs:\s*$", text)
    tail = text[start.end():]
    next_root = re.search(r"(?m)^[A-Za-z_-][\w-]*:\s*", tail)
    jobs_area = tail[:next_root.start()] if next_root else tail
    outside_jobs = text[:start.start()] + "\n" + (
        tail[next_root.start():] if next_root else ""
    )
    markers = list(JOB.finditer(jobs_area))
    if not markers or len(markers) > 100:
        raise Unverifiable("jobs")
    if len({m.group(1) for m in markers}) != len(markers):
        raise Unverifiable("duplicate_job")
    # Duplicated YAML mapping keys have parser-dependent precedence; do not
    # treat two literal false values as evidence of disabled cancellation.
    if len(re.findall(r"(?m)^concurrency:", outside_jobs)) > 1 or len(
        re.findall(r"(?m)^  cancel-in-progress:", outside_jobs)
    ) > 1:
        raise Unverifiable("duplicate_global_policy")
    global_cancel = _cancel_values(outside_jobs, indent="  ") or _inline_concurrency(outside_jobs, "")
    findings = []
    possible_vps = False
    job_cancel_any = False
    for index, marker in enumerate(markers):
        job = jobs_area[
            marker.end():markers[index + 1].start()
            if index + 1 < len(markers) else None
        ]
        runner = RUNNER.findall("  " + marker.group(1) + ":\n" + job)
        is_hosted = len(runner) == 1 and bool(HOSTED.fullmatch(runner[0].strip()))
        possible_vps = possible_vps or not is_hosted
        if len(re.findall(r"(?m)^    concurrency:", job)) > 1 or len(
            re.findall(r"(?m)^      cancel-in-progress:", job)
        ) > 1:
            raise Unverifiable("duplicate_job_policy")
        job_cancel = _cancel_values(job, indent="      ") or _inline_concurrency(job, "    ")
        job_cancel_any = job_cancel_any or job_cancel
        if (global_cancel or job_cancel) and not is_hosted:
            findings.append("possible_vps_cancellation")
    findings = sorted(set(findings))
    if details:
        return findings, possible_vps, global_cancel or job_cancel_any
    return findings


def _literal_group(value):
    """Return a single bounded literal string; never resolve expressions."""
    token = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", token):
        return token
    if (len(token) >= 3 and token[0] == token[-1] and token[0] in "'\""
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", token[1:-1])):
        return token[1:-1]
    return None


def static_concurrency_groups(text):
    """Literal quoted/unquoted groups, excluding all dynamic expressions."""
    groups = set()
    for match in re.finditer(
        r"(?m)^(?P<indent>|    )concurrency:[ \t]*(?P<value>[^\r\n#]*)$", text
    ):
        indent = match.group("indent")
        value = match.group("value").strip()
        if value.startswith("{"):
            m = re.search(r"(?:^|[{,])\s*group:\s*([^,}]+)\s*[,}]", value)
        elif value:
            continue
        else:
            tail = text[match.end():]
            next_peer = re.search(r"(?m)^" + re.escape(indent) + r"[^ \r\n#]", tail)
            region = tail[:next_peer.start()] if next_peer else tail
            matches = re.findall(r"(?m)^" + re.escape(indent) +
                                 r"  group:[ \t]*([^\r\n#]*)$", region)
            if len(matches) > 1:
                raise Unverifiable("duplicate_group")
            m = matches[0] if matches else None
            if m is not None:
                group = _literal_group(m)
                if group:
                    groups.add(group)
                continue
        if m:
            group = _literal_group(m.group(1))
            if group:
                groups.add(group)
    return groups


def build_vps_group_index(root, base, head):
    """Scan each immutable Git revision once, not once per changed workflow."""
    owners = {}
    for sha in (base, head):
        raw = git(root, "ls-tree", "-r", "--name-only", "-z", sha,
                  "--", ".github/workflows/")
        paths = [p.decode("utf-8", "strict") for p in raw.split(b"\0") if p]
        if len(paths) > 500 or len(paths) != len(set(paths)) or any(
            not WORKFLOW.fullmatch(p) for p in paths
        ):
            raise Unverifiable("collision_inventory")
        for path in paths:
            try:
                other = source(root, sha, path)
                _, possible_vps, _ = risks(other, details=True)
            except (Unverifiable, subprocess.SubprocessError, OSError, UnicodeError) as exc:
                # A positive claim that there is no collision needs a complete
                # old-and-new inventory. Missing source evidence is not safety.
                raise Unverifiable("collision_external_unknown") from exc
            if possible_vps:
                groups = static_concurrency_groups(other)
                for group in groups:
                    owners.setdefault(group, set()).add(path)
                # A previous VPS-capable workflow can select a runtime group
                # dynamically. Absence of a provable literal group is *not*
                # proof that it cannot match the new hosted cancellation group.
                if not groups and re.search(
                    r"(?m)^(?:concurrency:|    concurrency:)", other
                ):
                    owners.setdefault(None, set()).add(path)
    return owners


def literal_group_collision(candidate_path, candidate, exposure_index):
    """Reject exact literal cross-workflow concurrency-group collisions."""
    groups = static_concurrency_groups(candidate)
    # Unknown old/current group values are possible collisions with every
    # literal candidate group; this must remain deny-first.
    if groups and any(owner != candidate_path
                      for owner in exposure_index.get(None, ())):
        return True
    return any(
        any(owner != candidate_path for owner in exposure_index.get(group, ()))
        for group in groups
    )


def evaluate(root, base, head):
    result = []
    exposure_index = None
    for path in changed(root, base, head):
        before = source(root, base, path, missing_ok=True)
        after = source(root, head, path)
        now, _, now_cancels = risks(after, details=True)
        prior_vps = False
        if before is not None:
            _, prior_vps, _ = risks(before, details=True)
        # An already-running old VPS job can be cancelled even if new runner
        # definitions are hosted-only. Check both old and current snapshots.
        collision = False
        if not now and not prior_vps and now_cancels:
            groups = static_concurrency_groups(after)
            if not groups:
                # Dynamic/unknown group identity cannot prove isolation from
                # an independent, possibly running VPS workflow.
                collision = True
            else:
                if exposure_index is None:
                    exposure_index = build_vps_group_index(root, base, head)
                collision = literal_group_collision(path, after, exposure_index)
        if now or (prior_vps and now_cancels) or collision:
            result.append(path)
    return result


def is_manual_only(text):
    """Only an unambiguous workflow_dispatch-only mapping counts as manual."""
    marker = re.search(r"(?m)^on:[ \t]*$", text)
    if not marker:
        return False
    tail = text[marker.end():]
    other_root = re.search(r"(?m)^[A-Za-z_-][\w-]*:", tail)
    region = tail[:other_root.start()] if other_root else tail
    event_lines = re.findall(r"(?m)^  ([A-Za-z_]+):[ \t]*(?:#.*)?$", region)
    return event_lines == ["workflow_dispatch"] and not re.search(
        r"(?m)^  [^ #\r\n]", region.replace("  workflow_dispatch:", "  #event:", 1)
    )


def inventory(root, head):
    """Read-only exact-commit debt census, with unknowns never called safe.

    Return only aggregate values; do not print workflow contents, host identity,
    runner labels, bearer tokens or paths from untrusted repository YAML.
    """
    if not SHA.fullmatch(head):
        raise Unverifiable("inventory_sha")
    raw = git(root, "ls-tree", "-r", "--name-only", "-z", head, "--",
              ".github/workflows/")
    paths = [part.decode("utf-8", "strict") for part in raw.split(b"\0") if part]
    if not paths or len(paths) > 500 or len(paths) != len(set(paths)):
        raise Unverifiable("inventory_size")
    if any(not WORKFLOW.fullmatch(path) for path in paths):
        raise Unverifiable("inventory_path")
    counts = {"scanned": len(paths), "risky": 0, "unknown": 0, "safe": 0,
              "risky_manual_only": 0, "risky_nonmanual_or_unknown": 0}
    for path in paths:
        try:
            text = source(root, head, path)
            findings = risks(text)
        except (Unverifiable, subprocess.SubprocessError, UnicodeError, OSError):
            counts["unknown"] += 1
            continue
        counts["risky" if findings else "safe"] += 1
        if findings:
            bucket = ("risky_manual_only" if is_manual_only(text)
                      else "risky_nonmanual_or_unknown")
            counts[bucket] += 1
    if (sum(counts[k] for k in ("risky", "unknown", "safe")) != counts["scanned"]
            or counts["risky"] != counts["risky_manual_only"] + counts["risky_nonmanual_or_unknown"]):
        raise Unverifiable("inventory_inconsistent")
    return counts


def triage(root, head, limit=12, prefix=None):
    """Deterministic bounded workflow-path shortlist; never dump YAML or tokens.

    A shortlist is an *observation*, not an authorizing remediation queue:
    every path still requires current Notion/PR/branch ownership review.
    """
    if not SHA.fullmatch(head) or not isinstance(limit, int) or not 1 <= limit <= 25:
        raise Unverifiable("triage_arguments")
    if prefix is not None and not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", prefix):
        raise Unverifiable("triage_prefix")
    raw = git(root, "ls-tree", "-r", "--name-only", "-z", head,
              "--", ".github/workflows/")
    paths = [item.decode("utf-8", "strict") for item in raw.split(b"\0") if item]
    if not paths or len(paths) > 500 or len(set(paths)) != len(paths) or any(
        not WORKFLOW.fullmatch(path) for path in paths
    ):
        raise Unverifiable("triage_inventory")
    result = []
    for path in paths:
        if prefix and not path.rsplit("/", 1)[-1].startswith(prefix):
            continue
        try:
            text = source(root, head, path)
            finding = bool(risks(text))
            if finding:
                result.append(("manual_only" if is_manual_only(text)
                               else "automatic_or_unknown", path))
        except (Unverifiable, OSError, UnicodeError, subprocess.SubprocessError):
            result.append(("unknown_source", path))
    # Automatic/unknown first, manual-only second, unreadable last. Never
    # expose source lines, expressions, runner labels or contents.
    priority = {"automatic_or_unknown": 0, "manual_only": 1, "unknown_source": 2}
    result.sort(key=lambda x: (priority[x[0]], x[1]))
    return result[:limit], len(result)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--inventory-head")
    parser.add_argument("--triage-head")
    parser.add_argument("--triage-limit", type=int, default=12)
    parser.add_argument("--triage-prefix")
    parser.add_argument("--repo-root", type=Path,
                        default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    if args.triage_head is not None:
        try:
            if any(v is not None for v in (args.base, args.head, args.inventory_head)):
                raise Unverifiable("mixed_modes")
            entries, total = triage(args.repo_root, args.triage_head,
                                    args.triage_limit, args.triage_prefix)
        except (Unverifiable, subprocess.SubprocessError, OSError, UnicodeError):
            print("VPS_CANCELLATION_TRIAGE=DENY reason=unverifiable_source")
            print("VPS_MUTATION_AUTHORIZED=false")
            return 2
        print(f"VPS_CANCELLATION_TRIAGE=OBSERVED sha={args.triage_head} "
              f"total={total} shown={len(entries)}")
        for category, relative in entries:
            print(f"OBSERVED {category} {relative}")
        print("VPS_MUTATION_AUTHORIZED=false")
        return 0
    if args.inventory_head is not None:
        try:
            if args.triage_prefix is not None:
                raise Unverifiable("unexpected_triage_filter")
            if args.base is not None or args.head is not None:
                raise Unverifiable("mixed_modes")
            counts = inventory(args.repo_root, args.inventory_head)
        except (Unverifiable, subprocess.SubprocessError, OSError, UnicodeError):
            print("VPS_CANCELLATION_INVENTORY=DENY reason=unverifiable_source")
            print("VPS_MUTATION_AUTHORIZED=false")
            return 2
        print(f"VPS_CANCELLATION_INVENTORY=OBSERVED sha={args.inventory_head} "
              + " ".join(f"{key}={counts[key]}" for key in
                         ("scanned", "safe", "risky", "unknown", "risky_manual_only",
                          "risky_nonmanual_or_unknown")))
        print("VPS_MUTATION_AUTHORIZED=false")
        return 0
    try:
        if args.triage_prefix is not None:
            raise Unverifiable("unexpected_triage_filter")
        if args.base is None or args.head is None:
            raise Unverifiable("missing_base_head")
        risky = evaluate(args.repo_root, args.base, args.head)
    except (Unverifiable, subprocess.SubprocessError, OSError, UnicodeError):
        print("VPS_CANCELLATION_RATCHET=DENY reason=unverifiable_source")
        print("VPS_MUTATION_AUTHORIZED=false")
        return 2
    print(f"VPS_CANCELLATION_RATCHET={'DENY' if risky else 'PASS'} risky_changed={len(risky)}")
    for path in risky[:25]:
        print(f"DENY {path}")
    print("VPS_MUTATION_AUTHORIZED=false")
    return int(bool(risky))


if __name__ == "__main__":
    sys.exit(main())
