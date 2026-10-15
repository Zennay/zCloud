#!/usr/bin/env python3
"""Read-only no-new-debt check for disabled TLS verification in changed workflows.

This is an offline source ratchet, NOT a deployment authorization, runtime
inspection, GitHub branch-protection rule, or proof that legacy debt is safe.
"""
import argparse
from collections import Counter
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
DANGERS = {
    "curl_insecure": re.compile(r"\bcurl\s+[^\n]*(?:\s--insecure\b|\s-[A-Za-z]*k[A-Za-z]*\b)", re.I),
    "wget_no_certificate": re.compile(r"\bwget\s+[^\n]*--no-check-certificate\b", re.I),
    "git_ssl_verify_disabled": re.compile(r"\bgit\s+-c\s+http\.sslverify\s*=\s*(?:false|0)\b", re.I),
    "git_ssl_no_verify": re.compile(r"\bGIT_SSL_NO_VERIFY\s*[:=]\s*['\"]?(?:1|true)\b", re.I),
    "node_tls_bypass": re.compile(r"\bNODE_TLS_REJECT_UNAUTHORIZED\s*[:=]\s*['\"]?0\b", re.I),
    "python_tls_bypass": re.compile(r"\bPYTHONHTTPSVERIFY\s*[:=]\s*['\"]?0\b", re.I),
    "npm_tls_bypass": re.compile(r"\b(?:npm|yarn)\s+config\s+set\s+strict-ssl\s+false\b", re.I),
    "npm_env_tls_bypass": re.compile(r"\bnpm_config_strict_ssl\s*[:=]\s*['\"]?false\b", re.I),
    "python_requests_bypass": re.compile(r"\bverify\s*=\s*False\b"),
}


class Unverifiable(Exception):
    pass


def git(root, *args):
    try:
        p = subprocess.run(
            ["git", *args], cwd=root, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unverifiable("git_unavailable") from exc
    if p.returncode or len(p.stdout) > 210_000:
        raise Unverifiable("git_read_failed")
    return p.stdout


def workflow_changes(root, base, head):
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise Unverifiable("invalid_sha")
    data = git(root, "diff", "--name-status", "--no-renames",
               "--diff-filter=AM", "-z", base, head, "--",
               ".github/workflows/")
    fields = data.split(b"\0")
    if fields[-1] != b"" or len(fields[:-1]) % 2:
        raise Unverifiable("invalid_diff_inventory")
    fields = fields[:-1]
    if len(fields) > 400:
        raise Unverifiable("too_many_changed_workflows")
    found = []
    for status, raw in zip(fields[::2], fields[1::2]):
        try:
            path = raw.decode("ascii", "strict")
        except UnicodeDecodeError as exc:
            raise Unverifiable("non_ascii_path") from exc
        if status not in (b"A", b"M") or not WORKFLOW.fullmatch(path):
            raise Unverifiable("unexpected_workflow_entry")
        found.append((status, path))
    if len({p for _, p in found}) != len(found):
        raise Unverifiable("duplicate_workflow_entry")
    return found


def source(root, sha, path):
    record = git(root, "ls-tree", sha, "--", path).decode("utf-8", "strict")
    if not record.startswith("100644 blob ") or record.count("\n") != 1:
        raise Unverifiable("invalid_workflow_mode")
    value = git(root, "show", f"{sha}:{path}")
    if len(value) > 100_000 or b"\x00" in value:
        raise Unverifiable("invalid_workflow_blob")
    try:
        return value.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise Unverifiable("invalid_workflow_encoding") from exc


def insecure_counts(text):
    if len(text) > 100_000:
        raise Unverifiable("oversized_source")
    # Treat shell backslash-newline continuations as a single logical line.
    joined = re.sub(r"\\\r?\n[ \t]*", "", text)
    joined = "\n".join(line for line in joined.splitlines()
                       if not line.lstrip().startswith("#"))
    return Counter({name: len(pattern.findall(joined))
                    for name, pattern in DANGERS.items()})


def evaluate(root, base, head):
    problems = []
    changes = workflow_changes(root, base, head)
    for status, path in changes:
        after = insecure_counts(source(root, head, path))
        before = Counter() if status == b"A" else insecure_counts(source(root, base, path))
        for key in sorted(DANGERS):
            if after[key] > before[key]:
                problems.append(f"{path}:{key}:new={after[key]-before[key]}")
    return changes, problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        changes, problems = evaluate(args.root, args.base, args.head)
    except (Unverifiable, OSError, UnicodeError, ValueError):
        print("ZCLOUD_TLS_RATCHET=DENY reason=unverifiable_source")
        print("PRODUCTION_MUTATION_AUTHORIZED=false")
        return 1
    print(f"ZCLOUD_TLS_RATCHET={'DENY' if problems else 'PASS'} scanned={len(changes)}")
    for problem in problems[:30]:
        print(f"DENY {problem}")
    if len(problems) > 30:
        print("DENY additional_findings_redacted")
    print("PRODUCTION_MUTATION_AUTHORIZED=false")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
