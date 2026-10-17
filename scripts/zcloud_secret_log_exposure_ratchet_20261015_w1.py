#!/usr/bin/env python3
"""Deny new CI log/secret exposure in workflow and local Action source.

Conservative source ratchet: newly introduced patterns are denied; this is
not a YAML parser, exhaustive data-flow analysis, or production authorization.
Existing unmodified debt is NOT certified safe. No runtime/network mutations.
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
PATH = re.compile(
    r"(?:\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml|"
    r"(?:[A-Za-z0-9_.-]+/)*action\.ya?ml)\Z")
SIGNS = {
    "shell_xtrace": re.compile(
        r"\bset\s+(?:-[A-Za-z]*x[A-Za-z]*\b|-o\s+xtrace\b)", re.I),
    "shell_debug_execution": re.compile(
        r"\b(?:bash|sh|zsh)\s+-[A-Za-z]*x[A-Za-z]*\b", re.I),
    "print_all_environment": re.compile(
        r"(?:(?<=^)|(?<=[;|]))\s*(?:-?\s*run:\s*)?"
        r"(?:printenv|env)(?=\s*(?:$|[;|>&#]))", re.I),
    "powershell_environment_dump": re.compile(
        r"\b(?:Get-ChildItem|gci|dir|ls)\s+(?:Env:|env\\)(?=\s|$|[|;])", re.I),
    "debug_actions_enabled": re.compile(
        r"\bACTIONS_(?:STEP|RUNNER)_DEBUG\s*[:=]\s*['\"]?(?:true|1)\b", re.I),
    "print_sensitive_context_json": re.compile(
        r"\b(?:echo|printf|Write-Host|Write-Output|print)\b[^\n]*"
        r"\$\{\{\s*toJSON\s*\(\s*(?:secrets|github|env)\s*\)\s*\}\}", re.I),
    "print_indexed_secret_expression": re.compile(
        r"\b(?:echo|printf|Write-Host|Write-Output|print)\b[^\n]*"
        r"\$\{\{\s*secrets\s*\[\s*['\"][A-Za-z_][A-Za-z0-9_]*['\"]\s*\]\s*\}\}", re.I),
    "print_named_secret_env": re.compile(
        r"\bprintenv\s+(?:GITHUB_TOKEN|GH_TOKEN|AWS_SECRET_ACCESS_KEY|"
        r"AZURE_CLIENT_SECRET|NPM_TOKEN|DOCKER_PASSWORD)\b", re.I),
    "print_dotenv_file": re.compile(
        r"\b(?:cat|head|tail|type|Get-Content)\b[^\n]*"
        r"(?:^|[/\s])\.env(?:\.local|\.production|\.staging)?(?=$|[\s;|>\"'])", re.I),
    "print_secrets_expression": re.compile(
        r"\b(?:echo|printf|Write-Host|Write-Output|print)\b[^\n]*"
        r"\$\{\{\s*secrets\.[A-Za-z_][A-Za-z0-9_]*\s*\}\}", re.I),
    "print_secret_environment": re.compile(
        r"\b(?:echo|printf|Write-Host|Write-Output|print)\b[^\n]*"
        r"(?:\$(?:\{)?(?:GITHUB_TOKEN|GH_TOKEN|AWS_SECRET_ACCESS_KEY|"
        r"AZURE_CLIENT_SECRET|NPM_TOKEN|DOCKER_PASSWORD)(?:\}|(?=\W|$))|"
        r"\$env:(?:GITHUB_TOKEN|GH_TOKEN|AWS_SECRET_ACCESS_KEY|"
        r"AZURE_CLIENT_SECRET|NPM_TOKEN|DOCKER_PASSWORD)\b)", re.I),
    "print_credential_file": re.compile(
        r"\b(?:cat|head|tail|type|Get-Content|gc)\b[^\n]*"
        r"(?:\.git-credentials|\.aws/credentials|\.docker/config\.json)\b", re.I),
    "print_github_env_file": re.compile(
        r"\b(?:cat|head|tail|type|Get-Content|gc)\b[^\n]*"
        r"(?:\$(?:\{)?(?:GITHUB_ENV|GITHUB_OUTPUT)(?:\}|(?=\W|$))|\$env:(?:GITHUB_ENV|GITHUB_OUTPUT)\b)", re.I),
}


class Unverifiable(Exception):
    """The source cannot safely be established."""


def git(root, *args, deadline_seconds=15.0):
    """Bound Git wall time/output and kill its process group on failure.

    Git's child processes may hold stdout open after Git exits. Never trust
    subprocess.run(timeout=...) alone to clean up that inherited pipe.
    """
    limit = 220_000
    try:
        process = subprocess.Popen(["git", *args], cwd=root,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL,
                                   start_new_session=True)
    except OSError as exc:
        raise Unverifiable("git_unavailable") from exc
    deadline = time.monotonic() + deadline_seconds
    data = bytearray()
    clean = False
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                raise Unverifiable("git_timeout")
            chunk = os.read(process.stdout.fileno(),
                            min(8192, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                raise Unverifiable("git_output_too_large")
        if process.wait(timeout=max(0.001, deadline - time.monotonic())):
            raise Unverifiable("git_read_failed")
        clean = True
        return bytes(data)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unverifiable("git_io_failed") from exc
    finally:
        if not clean:
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


def changed_paths(root, base, head):
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise Unverifiable("bad_source_sha")
    data = git(root, "diff", "--name-status", "--no-renames",
               "--diff-filter=AMT", "-z", base, head, "--",
               ".github/workflows/", ":(glob)**/action.yml",
               ":(glob)**/action.yaml")
    if not data.endswith(b"\0") and data:
        raise Unverifiable("invalid_git_diff")
    fields = data[:-1].split(b"\0") if data else []
    if len(fields) % 2 or len(fields) > 400:
        raise Unverifiable("invalid_file_inventory")
    inventory = []
    for status, name in zip(fields[::2], fields[1::2]):
        try:
            path = name.decode("ascii", "strict")
        except UnicodeDecodeError as exc:
            raise Unverifiable("non_ascii_path") from exc
        if status not in (b"A", b"M", b"T") or not PATH.fullmatch(path):
            raise Unverifiable("invalid_changed_path")
        inventory.append((status, path))
    if len(inventory) != len({path for _, path in inventory}):
        raise Unverifiable("duplicate_changed_path")
    return inventory


def source(root, revision, path):
    record = git(root, "ls-tree", revision, "--", path).decode("utf-8", "strict")
    if record.count("\n") != 1 or not record.startswith("100644 blob "):
        raise Unverifiable("unsafe_blob_type")
    payload = git(root, "show", f"{revision}:{path}")
    if len(payload) > 100_000 or b"\0" in payload:
        raise Unverifiable("oversize_or_binary_source")
    try:
        return payload.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise Unverifiable("source_not_utf8") from exc


def findings(text):
    if len(text) > 100_000:
        raise Unverifiable("oversized_source")
    joined = re.sub(r"\\\r?\n[ \t]*", "", text)
    matches = Counter()
    for raw in joined.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for kind, pattern in SIGNS.items():
            count = len(pattern.findall(line))
            if count:
                matches[(kind, line)] += count
    return matches


def check(root, base, head):
    problems = []
    inventory = changed_paths(root, base, head)
    for status, path in inventory:
        new = findings(source(root, head, path))
        old = Counter() if status == b"A" else findings(source(root, base, path))
        for (kind, signature), count in sorted(new.items()):
            extra = count - old[(kind, signature)]
            if extra > 0:
                problems.append(f"{path}:{kind}:new={extra}")
    return len(inventory), problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        count, problems = check(args.root, args.base, args.head)
    except (Unverifiable, OSError, UnicodeError, ValueError):
        print("ZCLOUD_SECRET_LOG_RATCHET=DENY reason=unverifiable_source")
        print("PRODUCTION_MUTATION_AUTHORIZED=false")
        return 1
    print(f"ZCLOUD_SECRET_LOG_RATCHET={'DENY' if problems else 'PASS'} scanned={count}")
    for item in problems[:30]:
        print(f"DENY {item}")
    if len(problems) > 30:
        print("DENY additional_findings_redacted")
    print("PRODUCTION_MUTATION_AUTHORIZED=false")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
