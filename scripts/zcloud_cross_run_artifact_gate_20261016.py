#!/usr/bin/env python3
"""Read-only, fail-closed workflow/composite-action cross-run artifact ratchet.

This is a conservative source check, not a YAML interpreter or an authorization
for consuming untrusted artifacts. Existing identical legacy steps are recorded
without treating them as safe. No GitHub API write, runner, or deployment access.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
import os
import re
import selectors
import signal
import subprocess
import time
from pathlib import Path

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_USES = re.compile(r"^(?P<indent> *)-?\s*uses:\s*['\"]?(?P<action>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]*download[-_]artifact)@(?P<ref>[^\s'\"#]+)['\"]?\s*(?:#.*)?$", re.IGNORECASE)
_INPUT = re.compile(r"^(?P<indent> *)(?P<key>[\w-]+):\s*(?P<value>.*?)\s*$")
_TARGETS = frozenset({"run-id", "repository", "github-token"})
_DOWNLOAD_ACTION_REF = re.compile(r"\b[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]*download[-_]artifact@", re.IGNORECASE)
# Direct GitHub Actions artifact API routes bypass download-artifact and gh run.
# Deliberately review ANY new endpoint reference in runnable workflow sources:
# dynamically assembled IDs may hide the final /zip URL at authoring time.
_API_ARTIFACT_PATH = re.compile(r"/actions/artifacts/", re.IGNORECASE)

# GitHub CLI downloads bypass actions/download-artifact but have the same
# cross-run artifact trust boundary. A syntactic match requires review, not
# permission to retrieve or execute any artifact.
_GH_RUN_DOWNLOAD = re.compile(
    r"(?<![\w/-])(?:gh|/(?:usr/)?(?:local/)?bin/gh)\s+"
    r"(?:(?:(?:-R|--repo)(?:=\S+|\s+\S+))\s+)*"
    r"run\s+download(?=\s|$|[;&|])", re.IGNORECASE,
)
_MAX_FILE = 256_000
_MAX_WORKFLOWS = 500

class Refusal(ValueError):
    """Source cannot safely be classified."""


def _git(root: Path, *args: str, max_bytes: int = 2_000_000) -> bytes:
    """Read Git output with an actual memory and deadline bound.

    subprocess.run(stdout=PIPE) checks size only AFTER buffering untrusted
    output. An inherited pipe from a child can also leave a consumer stuck.
    A separate process group makes refusal terminate those children as well.
    """
    if max_bytes < 1:
        raise Refusal("invalid_output_budget")
    try:
        proc = subprocess.Popen(
            ["git", "-C", str(root), *args], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError as exc:
        raise Refusal("git_command_unavailable") from exc
    complete = False
    chunks: list[bytes] = []
    total = 0
    deadline = time.monotonic() + 15
    try:
        assert proc.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise Refusal("git_command_timed_out")
                chunk = os.read(proc.stdout.fileno(), min(65_536, max_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise Refusal("git_output_oversized")
                chunks.append(chunk)
        if proc.wait(timeout=max(0.05, deadline - time.monotonic())):
            raise Refusal("git_command_failed")
        complete = True
        return b"".join(chunks)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refusal("git_command_unavailable") from exc
    finally:
        if not complete:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        if proc.stdout is not None:
            proc.stdout.close()


def _inventory(root: Path, sha: str) -> dict[str, tuple[str, str]]:
    """Enumerate workflows AND composite action manifests at arbitrary paths.

    Checking only .github/workflows misses composite action.yml files and
    shell helpers that execute GitHub CLI or REST artifact downloads.
    Git trees are the immutable source of truth; no working-tree traversal.
    """
    data = _git(root, "ls-tree", "-rz", sha, max_bytes=2_000_000)
    result: dict[str, tuple[str, str]] = {}
    workflows = 0
    for item in data.split(b"\x00"):
        if not item:
            continue
        try:
            meta, raw_path = item.split(b"\t", 1)
            mode, kind, oid = meta.decode("ascii").split()
            path = raw_path.decode("utf-8", errors="strict")
        except (ValueError, UnicodeDecodeError) as exc:
            raise Refusal("invalid_git_tree_entry") from exc
        is_workflow = path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))
        is_action = path.rsplit("/", 1)[-1] in {"action.yml", "action.yaml"}
        is_shell = path.endswith((".sh", ".bash"))
        # Extensionless executable scripts are frequently invoked by a job's
        # run step. Ignore non-executable extensionless documentation.
        is_extensionless_executable = mode in {"100644", "100664", "100755"} and not Path(path).suffix
        # Also inspect extensionless regular script blobs: a job can execute
        # a non-executable script with an explicit "bash ./path" invocation.
        # Ordinary README/marker files without a shebang or known script
        # location are skipped only after bounded content inspection.
        # An extensionless symlink can run an otherwise unclassified helper
        # via a shell. Treat it like executable source, then reject its Git
        # symlink mode below instead of silently overlooking the path.
        is_extensionless_symlink = mode == "120000" and not Path(path).suffix
        if not (is_workflow or is_action or is_shell or is_extensionless_executable or is_extensionless_symlink):
            continue
        if path.startswith("/") or "/../" in "/" + path + "/" or not path.strip():
            raise Refusal("unsafe_source_path")
        allowed_modes = {"100644", "100664"} | ({"100755"} if (is_shell or is_extensionless_executable) else set())
        if mode not in allowed_modes or kind != "blob" or not _SHA.fullmatch(oid):
            # Executable shell helpers are ordinary regular files; Git
            # symlinks (120000), submodules and executable YAML stay denied.
            raise Refusal("non_regular_workflow_or_action")
        if path in result:
            raise Refusal("duplicate_source_path")
        result[path] = (oid, mode)
        workflows += int(is_workflow)
        if len(result) > _MAX_WORKFLOWS:
            raise Refusal("source_inventory_too_large")
    if not workflows:
        raise Refusal("no_workflows")
    return result


def _command_risks(lines: list[str], path: str) -> Counter[str]:
    risky: Counter[str] = Counter()
    # Review new direct GitHub CLI artifact downloads as well. Many hosted
    # workflows use inline run blocks rather than an explicit download action.
    # Join bounded shell backslash continuations so a split command is not
    # silently invisible. This is a conservative source indicator.
    for number, source in enumerate(lines):
        if source.lstrip().startswith("#"):
            continue
        logical = source
        following = number + 1
        while logical.rstrip().endswith("\\") and following < len(lines) and following <= number + 8:
            logical = logical.rstrip()[:-1] + " " + lines[following].strip()
            following += 1
        if _GH_RUN_DOWNLOAD.search(logical):
            token = sha256(logical.encode("utf-8")).hexdigest()
            risky[f"{path}:{number+1}:gh-run-download:{token}"] += 1
        if _API_ARTIFACT_PATH.search(logical):
            token = sha256(logical.encode("utf-8")).hexdigest()
            risky[f"{path}:{number+1}:github-api-artifact-path:{token}"] += 1
    return risky


def _risks(text: str, path: str) -> Counter[str]:
    lines = text.splitlines()
    if path.endswith((".sh", ".bash")) or not Path(path).suffix:
        # Shell and extensionless executable sources do not have YAML uses
        # mappings; review only CLI and REST artifact references.
        return _command_risks(lines, path)
    risky: Counter[str] = Counter()
    for index, line in enumerate(lines):
        # Reject indented tabs/ambiguous aliases where a download action is mentioned.
        if _DOWNLOAD_ACTION_REF.search(line) and ("\t" in line[:len(line)-len(line.lstrip())] or "*" in line.strip().split("#")[0]):
            raise Refusal("ambiguous_download_action")
        match = _USES.fullmatch(line)
        if not match:
            # Reject a syntactically unrecognized action reference, rather than
            # silently approving an additional renamed/quoted action invocation.
            if _DOWNLOAD_ACTION_REF.search(line) and not line.lstrip().startswith("#"):
                # Block scalar run scripts may quote the action as inert data;
                # conservatively require human review instead of false approval.
                raise Refusal("unparsed_download_action")
            continue
        indent = len(match.group("indent"))
        # YAML mapping order is not fixed. A preceding 'with:' can supply
        # privileged cross-run inputs even when 'uses:' appears last. Refuse
        # this shape rather than silently treating it as a safe download.
        for preceding in reversed(lines[:index]):
            stripped_pre = preceding.strip()
            if not stripped_pre or stripped_pre.startswith("#"):
                continue
            level_pre = len(preceding) - len(preceding.lstrip(" "))
            if level_pre < indent - 2:
                break
            if stripped_pre.startswith("- ") and level_pre <= indent:
                if stripped_pre.startswith("- with:"):
                    raise Refusal("preceding_artifact_with_block")
                break
            if stripped_pre == "with:" and level_pre == indent:
                raise Refusal("preceding_artifact_with_block")
        with_indent: int | None = None
        fields: dict[str, str] = {}
        for following in lines[index+1:]:
            stripped = following.strip()
            if not stripped or stripped.startswith("#"):
                continue
            level = len(following) - len(following.lstrip(" "))
            if level < indent or (level == indent and stripped.startswith("- ")):
                break
            if with_indent is None:
                if stripped == "with:":
                    with_indent = level
                elif stripped.startswith("with:"):
                    raise Refusal("ambiguous_with_block")
            elif level > with_indent:
                field = _INPUT.fullmatch(following)
                if field is None:
                    raise Refusal("ambiguous_artifact_input")
                key = field.group("key").lower()
                if key in fields:
                    raise Refusal("ambiguous_artifact_input")
                fields[key] = field.group("value").split(" #",1)[0].strip()
            elif stripped == "with:":
                raise Refusal("duplicate_with_block")
            else:
                with_indent = None
        # Unlike the official download action, dawidd6 and its maintained
        # step-security replacement can select the latest previous workflow
        # run even WITHOUT `run_id` or other explicit selectors.
        # All newly added invocations of either must therefore be reviewed.
        is_third_party = match.group("action").lower() != "actions/download-artifact"
        if is_third_party or _TARGETS.intersection(fields):
            # Pin to exact source position as well as fields. Without the
            # location, moving an identical cross-run step between jobs in
            # the SAME workflow would be grandfathered by the old hash.
            # Harmless edits shifting a legacy step require manual review;
            # this conservative no-new-debt check prefers a false refusal.
            raw = "\n".join(lines[index:index+1]) + "\n" + "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
            key = path + ":" + str(index + 1) + ":" + sha256(raw.encode("utf-8")).hexdigest()
            risky[key] += 1
    risky.update(_command_risks(lines, path))
    return risky


def _snapshot(root: Path, sha: str) -> Counter[str]:
    risk: Counter[str] = Counter()
    for path, (oid, mode) in _inventory(root, sha).items():
        content = _git(root, "cat-file", "blob", oid, max_bytes=_MAX_FILE+1)
        if len(content) > _MAX_FILE:
            raise Refusal("oversized_workflow")
        if not Path(path).suffix and mode != "100755":
            script_locations = {"bin", "local-bin", "scripts", "deploy", "ops"}
            in_script_area = bool(script_locations.intersection(Path(path).parts[:-1]))
            if not content.startswith(b"#!") and not in_script_area:
                # Non-executable README and repository marker content do not
                # constitute shell execution evidence. Files in known script
                # directories are still reviewed even without a shebang.
                continue
        try:
            risk.update(_risks(content.decode("utf-8"), path))
        except UnicodeDecodeError as exc:
            raise Refusal("invalid_utf8_workflow") from exc
    return risk



def _risk_source_path(key: str) -> str:
    """Return source path without copying any action arguments or tokens.

    Keys are emitted as path:line:fingerprint for Actions, or as
    path:line:kind:fingerprint for GitHub CLI and API routes. rsplit is
    deliberate: legitimate Git paths may themselves contain a colon.
    """
    if ":gh-run-download:" in key or ":github-api-artifact-path:" in key:
        return key.rsplit(":", 3)[0]
    return key.rsplit(":", 2)[0]


def _new_risk_paths(before: Counter[str], after: Counter[str]) -> list[str]:
    return sorted({
        _risk_source_path(key)
        for key, count in after.items()
        if count > before[key]
    })


def evaluate(root: Path, base: str, head: str) -> dict[str, object]:
    if not _SHA.fullmatch(base) or not _SHA.fullmatch(head) or base == head:
        raise Refusal("invalid_or_identical_sha")
    if _git(root, "rev-parse", "HEAD").decode().strip() != head:
        raise Refusal("candidate_head_mismatch")
    if _git(root, "merge-base", base, head).decode().strip() != base:
        raise Refusal("base_not_ancestor")
    before, after = _snapshot(root, base), _snapshot(root, head)
    new = sum(max(0, count-before[key]) for key, count in after.items())
    new_paths = _new_risk_paths(before, after)
    return {
        "policy": "cross-run-artifact-download-no-new-debt-v1",
        "base": base, "head": head,
        "legacy_findings": sum(before.values()),
        "candidate_findings": sum(after.values()),
        "new_findings": new,
        # Paths only. Never echo matching input lines, tokens or expressions.
        "new_finding_paths": new_paths,
        "ok": new == 0,
        "mutation_performed": False,
        "production_mutation_authorized": False,
        "merge_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        report = evaluate(args.repo_root.resolve(), args.base, args.head)
    except (Refusal, OSError) as exc:
        print(json.dumps({"ok": False, "reason": str(exc) if isinstance(exc, Refusal) else "filesystem_error",
                          "mutation_performed": False, "merge_authorized": False,
                          "production_mutation_authorized": False}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
