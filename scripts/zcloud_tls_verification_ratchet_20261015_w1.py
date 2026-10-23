#!/usr/bin/env python3
"""Read-only no-new-debt check for disabled TLS verification in changed workflows and local Actions.

This is an offline source ratchet, NOT a deployment authorization, runtime
inspection, GitHub branch-protection rule, or proof that legacy debt is safe.
"""
import argparse
from collections import Counter
import os
import select
import signal
import time
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOW = re.compile(r"(?:\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml|(?:[A-Za-z0-9_.-]+/)*action\.ya?ml)\Z")
DANGERS = {
    "curl_insecure": re.compile(r"\bcurl\b[^\n]*[ \t]+(?:--insecure\b|-[A-Za-z]*k[A-Za-z]*\b)", re.I),
    "wget_no_certificate": re.compile(r"\bwget\s+[^\n]*--no-check-certificate\b", re.I),
    # Match both one-shot -c and persistent git config overrides, including
    # per-origin http.<url>.sslVerify (may appear after unrelated git flags).
    "git_ssl_verify_disabled": re.compile(
        r"\bgit\b[^\n]*?(?:http(?:\.[^\s=]+)?\.sslverify\s*=\s*(?:false|0)\b|"
        r"\bconfig\s+(?:--[a-z-]+\s+)*http(?:\.[^\s=]+)?\.sslverify\s+(?:false|0)\b)", re.I),
    "powershell_skip_certificate": re.compile(
        r"\b(?:Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b[^\n]*?-SkipCertificateCheck\b", re.I),
    "node_reject_unauthorized": re.compile(r"\brejectUnauthorized\s*[:=]\s*false\b", re.I),
    "python_ssl_cert_none": re.compile(r"\b(?:ssl\.)?CERT_NONE\b", re.I),
    "git_ssl_no_verify": re.compile(r"\bGIT_SSL_NO_VERIFY\s*[:=]\s*['\"]?(?:1|true)\b", re.I),
    "node_tls_bypass": re.compile(r"\bNODE_TLS_REJECT_UNAUTHORIZED\s*[:=]\s*['\"]?0\b", re.I),
    "python_tls_bypass": re.compile(r"\bPYTHONHTTPSVERIFY\s*[:=]\s*['\"]?0\b", re.I),
    "npm_tls_bypass": re.compile(r"\b(?:npm|yarn)\s+config\s+set\s+strict-ssl\s+false\b", re.I),
    "npm_env_tls_bypass": re.compile(r"\bnpm_config_strict_ssl\s*[:=]\s*['\"]?false\b", re.I),
    "python_requests_bypass": re.compile(r"\bverify\s*=\s*False\b"),
}


class Unverifiable(Exception):
    pass


def git(root, *args, deadline_seconds=15.0):
    """Bound both Git output and wall time, reaping inherited stdout holders.

    A subprocess can exit while its child keeps stdout open. Killing only that
    parent leaves an orphan and blocks communicate(). Use a new process group,
    short bounded pipe reads, and a monotonic deadline to fail closed.
    """
    limit = 210_000
    try:
        process = subprocess.Popen(
            ["git", *args], cwd=root, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        raise Unverifiable("git_unavailable") from exc
    deadline = time.monotonic() + deadline_seconds
    output = bytearray()
    success = False
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Unverifiable("git_timeout")
            if not select.select([process.stdout], [], [], remaining)[0]:
                raise Unverifiable("git_timeout")
            chunk = os.read(process.stdout.fileno(),
                            min(8192, limit + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > limit:
                raise Unverifiable("git_output_too_large")
        if process.wait(timeout=max(0.001, deadline - time.monotonic())):
            raise Unverifiable("git_read_failed")
        success = True
        return bytes(output)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unverifiable("git_read_failed") from exc
    finally:
        if not success:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.stdout.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def workflow_changes(root, base, head):
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise Unverifiable("invalid_sha")
    data = git(root, "diff", "--name-status", "--no-renames",
               "--diff-filter=AMT", "-z", base, head, "--",
               ".github/workflows/", ":(glob)**/action.yml",
               ":(glob)**/action.yaml")
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
        if status not in (b"A", b"M", b"T") or not WORKFLOW.fullmatch(path):
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


def insecure_occurrences(text):
    if len(text) > 100_000:
        raise Unverifiable("oversized_source")
    # Compare complete unsafe statements, not merely occurrence counts.
    # Replacing an old curl -k destination with a new one is new debt.
    joined = re.sub(r"\\\r?\n[ \t]*", "", text)
    occurrences = Counter()
    # Git honors GIT_CONFIG_KEY_N + GIT_CONFIG_VALUE_N pairs as an alternative
    # to command-line -c/config. Check the same index, not an unrelated value.
    indexed_git_config = {}
    for line in joined.splitlines():
        statement = line.strip()
        if not statement or statement.startswith("#"):
            continue
        for name, pattern in DANGERS.items():
            number = len(pattern.findall(statement))
            if number:
                occurrences[(name, statement)] += number
        assignment = re.search(
            r"\bGIT_CONFIG_(KEY|VALUE)_([0-9]{1,3})\s*[:=]\s*['\"]?([^'\"\s#]+)",
            statement, re.I)
        if assignment:
            kind, index, value = assignment.groups()
            key = (index, kind.upper())
            if key in indexed_git_config and indexed_git_config[key] != value:
                raise Unverifiable("ambiguous_git_config_env")
            indexed_git_config[key] = value
    for (index, kind), key in indexed_git_config.items():
        if kind != "KEY":
            continue
        value = indexed_git_config.get((index, "VALUE"), "")
        if (re.fullmatch(r"http(?:\.[^\s=]+)?\.sslverify", key, re.I)
                and value.lower() not in {"true", "1"}):
            occurrences[("git_ssl_verify_environment",
                         f"GIT_CONFIG_KEY_{index}={key};GIT_CONFIG_VALUE_{index}={value}")] += 1
    return occurrences


def insecure_counts(text):
    occurrences = insecure_occurrences(text)
    keys = set(DANGERS) | {kind for kind, _ in occurrences}
    return Counter({name: sum(n for (kind, _), n in occurrences.items()
                               if kind == name) for name in keys})


def evaluate(root, base, head):
    problems = []
    changes = workflow_changes(root, base, head)
    for status, path in changes:
        after = insecure_occurrences(source(root, head, path))
        before = Counter() if status == b"A" else insecure_occurrences(source(root, base, path))
        for (name, statement), number in sorted(after.items()):
            increase = number - before[(name, statement)]
            if increase > 0:
                problems.append(f"{path}:{name}:new={increase}")
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
