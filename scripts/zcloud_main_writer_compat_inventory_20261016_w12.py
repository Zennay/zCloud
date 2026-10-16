#!/usr/bin/env python3
"""Read-only, SHA-bound compatibility inventory for zCloud main protection.

Static indications are deliberately NOT evidence that protection is safe to enable.
No GitHub API, network calls, workflow execution, writes, or release authority.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path

MAX_WORKFLOWS = 512
MAX_BLOB_BYTES = 512 * 1024
MAX_TREE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}\Z")
SAFE_PATH = re.compile(r"\.github/workflows/[A-Za-z0-9_./+-]{1,190}\Z")

# Signals are review hints. In particular, a contents:write grant is NOT a
# finding that a job ran or wrote main; a missing signal does NOT prove safety.
# Git global options can sit between "git" and "push".
GIT_PUSH = (r"\bgit\s+(?:(?:-c|-C|--git-dir|--work-tree)\s+\S+\s+|"
            r"--(?:git-dir|work-tree)=\S+\s+|-\S+\s+)*push\b")
SIGNALS = (
    ("direct_main_ref", re.compile(
        GIT_PUSH + r"[^\r\n]{0,240}"
        r"(?:HEAD:main\b|refs/heads/main\b|\borigin\s+main\b|:main\b)", re.I)),
    ("other_git_push", re.compile(GIT_PUSH, re.I)),
    ("git_refs_api", re.compile(
        r"(?:/git/refs(?:/heads/main)?\b|"
        r"\b(?:updateRef|createRef|deleteRef|forceUpdateRef)\s*\(|"
        r"\bgh\s+api\b[^\r\n]{0,160}\brefs\b)", re.I)),
    ("pull_request_merge", re.compile(
        r"\bgh\s+pr\s+merge\b|"
        r"\bgithub\.rest\.pulls\.merge\b|"
        r"/pulls/[^\s\"']{1,100}/merge\b", re.I)),
    ("contents_write", re.compile(r"\bcontents\s*:\s*write\b", re.I)),
    ("write_all", re.compile(r"\bpermissions\s*:\s*write-all\b", re.I)),
    ("github_script", re.compile(r"\bactions/github-script@", re.I)),
)
STRONG = {"direct_main_ref", "git_refs_api", "pull_request_merge"}


class InventoryError(Exception):
    """Stable redacted error only; do not include Git or file contents."""


def git(repo: Path, *args: str, max_output: int = MAX_TREE_BYTES) -> bytes:
    """Bound child stdout *while reading*, not after subprocess.run has buffered it.

    Git tree entries are attacker-controlled. The old capture-then-check path could
    allocate far more than max_output; a stopped stdout or orphan child could also
    exhaust the hosted job budget. Never follow Git redirects or invoke shell.
    """
    # Reject inherited GIT_DIR, GIT_WORK_TREE, GIT_CONFIG_COUNT and
    # other caller-supplied overrides of the pinned source repository.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
                "GIT_PAGER": "cat"})
    process = None
    output = bytearray()
    deadline = time.monotonic() + 20.0
    try:
        process = subprocess.Popen(
            ["git", "-C", str(repo), *args], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, start_new_session=True,
        )
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    raise InventoryError("git_timeout")
                ready = selector.select(min(remain, 0.25))
                if not ready:
                    continue
                chunk = os.read(process.stdout.fileno(), min(65536, max_output - len(output) + 1))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > max_output:
                    raise InventoryError("git_output_too_large")
        remain = deadline - time.monotonic()
        if remain <= 0:
            raise InventoryError("git_timeout")
        if process.wait(timeout=remain) != 0:
            raise InventoryError("git_read_failed")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InventoryError("git_unavailable") from exc
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            if process.stdout is not None:
                process.stdout.close()
    result = bytes(output)
    return result.strip() if args[0] == "rev-parse" else result

def logical_shell_lines(content: str):
    """Join shell backslash continuations and preserve first physical line number.

    The source is bounded at 512 KiB and never executed. This only handles
    a common shell idiom; indirection and external actions still require review.
    """
    lines = content.splitlines()
    index = 0
    while index < len(lines):
        start = index + 1
        current = lines[index]
        if current.lstrip().startswith("#") or not current.strip():
            index += 1
            continue
        while current.rstrip().endswith("\\") and index + 1 < len(lines):
            next_line = lines[index + 1]
            if next_line.lstrip().startswith("#"):
                break
            current = current.rstrip()[:-1] + " " + next_line.lstrip()
            index += 1
        yield start, current
        index += 1


def pinned(repo: Path, expected: str) -> None:
    if not SHA.fullmatch(expected):
        raise InventoryError("invalid_expected_sha")
    head = git(repo, "rev-parse", "--verify", "HEAD", max_output=128).decode("ascii", "replace")
    main = git(repo, "rev-parse", "--verify", "refs/heads/main", max_output=128).decode("ascii", "replace")
    if head != expected or main != expected:
        raise InventoryError("main_sha_drift")


def inventory(repo: Path, expected: str) -> dict:
    """Never uses working-tree YAML or the remote; all blobs read from pinned Git."""
    pinned(repo, expected)
    root = git(repo, "ls-tree", expected, ".github/workflows", max_output=512)
    if not re.fullmatch(rb"040000 tree [0-9a-f]{40}\t\.github/workflows\n", root):
        raise InventoryError("missing_workflow_tree")
    raw = git(repo, "ls-tree", "-rz", "--full-tree", expected,
              ".github/workflows", max_output=MAX_TREE_BYTES)
    entries = [item for item in raw.split(b"\0") if item]
    if not entries or len(entries) > MAX_WORKFLOWS * 4:
        raise InventoryError("workflow_tree_out_of_bounds")
    workflows = []
    total_bytes = 0
    paths = set()
    for item in entries:
        try:
            meta, raw_name = item.split(b"\t", 1)
            mode, kind, raw_sha = meta.split(b" ")
            path = raw_name.decode("ascii")
            blob_sha = raw_sha.decode("ascii")
        except (UnicodeError, ValueError) as exc:
            raise InventoryError("invalid_tree_entry") from exc
        if not path.startswith(".github/workflows/"):
            raise InventoryError("out_of_scope_tree_path")
        if not path.endswith((".yml", ".yaml")):
            continue
        if not SAFE_PATH.fullmatch(path) or "//" in path or "/../" in path or "/./" in path:
            raise InventoryError("unsafe_workflow_path")
        if path in paths or mode not in (b"100644", b"100755") or kind != b"blob":
            raise InventoryError("unsafe_workflow_tree")
        paths.add(path)
        if len(paths) > MAX_WORKFLOWS or not SHA.fullmatch(blob_sha):
            raise InventoryError("workflow_count_or_hash")
        size_raw = git(repo, "cat-file", "-s", blob_sha, max_output=64)
        try:
            size = int(size_raw)
        except ValueError as exc:
            raise InventoryError("invalid_blob_size") from exc
        if size < 0 or size > MAX_BLOB_BYTES:
            raise InventoryError("workflow_blob_out_of_bounds")
        total_bytes += size
        if total_bytes > MAX_TOTAL_BYTES:
            raise InventoryError("workflow_total_out_of_bounds")
        contents = git(repo, "cat-file", "blob", blob_sha, max_output=MAX_BLOB_BYTES)
        if len(contents) != size or b"\0" in contents:
            raise InventoryError("invalid_workflow_blob")
        try:
            content = contents.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise InventoryError("invalid_workflow_encoding") from exc
        # splitlines() removes extra separators (including vertical tab):
        # validate the original decoded blob before any line normalization.
        if any((ord(char) < 32 and char not in "\t\r\n") or
               0x7f <= ord(char) <= 0x9f for char in content):
            raise InventoryError("invalid_workflow_controls")
        signals = []
        for lineno, line in logical_shell_lines(content):
            for name, regex in SIGNALS:
                if regex.search(line):
                    signals.append({"line": lineno, "kind": name})
                    if len(signals) > 160:
                        raise InventoryError("signal_count_out_of_bounds")
        if signals:
            kinds = {signal["kind"] for signal in signals}
            workflows.append({
                "path": path,
                "source_sha256": hashlib.sha256(contents).hexdigest(),
                "classification": ("direct_writer_candidate" if kinds & STRONG
                                   else "capability_or_indirect_writer_candidate"),
                "signals": signals,
                "disposition": "manual_review_required",
            })
    pinned(repo, expected)
    strong = sum(row["classification"] == "direct_writer_candidate" for row in workflows)
    return {
        "schema": "zcloud-main-writer-compat-v1",
        "main_sha": expected,
        "static_scan_complete": True,
        "workflows_scanned": len(paths),
        "candidate_count": len(workflows),
        "direct_writer_candidate_count": strong,
        "no_static_signal_count": len(paths) - len(workflows),
        "candidates": sorted(workflows, key=lambda x: x["path"]),
        "historical_activity_verified": False,
        "github_branch_protection_verified": False,
        "pr_branch_ownership_verified": False,
        "safe_to_enable_branch_protection": False,
        "production_mutation_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline main writer compatibility audit")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--expected-main-sha", required=True)
    parser.add_argument("--format", choices=("json", "markdown", "csv"), default="json")
    args = parser.parse_args(argv)
    try:
        result = inventory(Path(args.repo), args.expected_main_sha)
    except InventoryError as exc:
        if args.format == "markdown":
            print("## Mainbescherming: GEEN VRIJGAVE\n\n"
                  "Inventarisatie mislukt (" + str(exc) + "). "
                  "Herstel de lokale invoer of Git-bron; vraag daarna een nieuwe "
                  "exact-SHA controle. Geen wijzigingen aan productie.")
        else:
            print(json.dumps({"ok": False, "error": str(exc),
                              "safe_to_enable_branch_protection": False,
                              "production_mutation_authorized": False}, sort_keys=True))
        return 2
    if args.format == "csv":
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(["main_sha", "workflow_path", "source_sha256",
                         "signal_lines_and_kinds", "priority",
                         "owner", "reviewed_by", "decision", "evidence_url",
                         "review_verified", "branch_protection_authorized"])
        for candidate in result["candidates"]:
            signal_pairs = "|".join(str(signal["line"]) + ":" + signal["kind"]
                                    for signal in candidate["signals"])
            writer.writerow([result["main_sha"], candidate["path"],
                             candidate["source_sha256"], signal_pairs,
                             "P1" if candidate["classification"] == "direct_writer_candidate"
                             else "P2", "", "", "", "", "false", "false"])
        return 0
    if args.format == "markdown":
        print("## zCloud main-schrijvers — alleen-lezen broninventaris\n")
        print("Vaste main SHA: " + result["main_sha"])
        print("Workflows gecontroleerd: " + str(result["workflows_scanned"]))
        print("Mogelijke schrijvers: " + str(result["candidate_count"]))
        print("Sterke signalen: " + str(result["direct_writer_candidate_count"]))
        print("Zonder herkenbaar signaal: " + str(result["no_static_signal_count"]))
        print("\n**GEEN VRIJGAVE VOOR BRANCHBESCHERMING.** "
              "Er zijn geen runtime-activiteit, branch-eigenaren, review of "
              "GitHub-governanceregels bewezen.\n")
        for candidate in result["candidates"]:
            kinds = ", ".join(sorted({signal["kind"] for signal in candidate["signals"]}))
            print("- " + candidate["path"] + " — " + kinds +
                  "; eigenaar moet bepalen: migreren / uitfaseren / false positive.")
    else:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
