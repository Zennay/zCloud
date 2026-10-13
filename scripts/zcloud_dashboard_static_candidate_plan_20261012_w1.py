#!/usr/bin/env python3
"""Read-only static-dashboard candidate plan; NEVER authorizes VPS promotion.

The comparison baseline is caller-provided Git history, NOT an attestation that
these bytes are deployed. The plan cannot establish live server.js/API
compatibility, safe idle, active generations, or a serialized release lease.
No filesystem, SQLite, browser, runner or GitHub mutating operations are made.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from html.parser import HTMLParser
import re
import subprocess
from urllib.parse import urlsplit

SHA = re.compile(r"[0-9a-f]{40}\Z")
STATIC_ALLOWLIST = frozenset((
    "public/index.html",
    "public/app.js",
    "public/design-system.css",
    "public/enhancements.css",
))
MAX_ASSET_BYTES = 3 * 1024 * 1024
MAX_TOTAL_BYTES = 5 * 1024 * 1024
MAX_DIFF_PATHS = 128


class PlanError(ValueError):
    """An untrusted or incompatible proposed static release."""


def git(root: Path, *argv: str, timeout: int = 15) -> bytes:
    try:
        result = subprocess.run(
            ["git", *argv],
            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, check=True,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise PlanError("git_identity_or_object_unavailable") from None
    return result.stdout


def require_sha(value: str) -> str:
    if type(value) is not str or not SHA.fullmatch(value):
        raise PlanError("invalid_commit_sha")
    return value


def blob_at(root: Path, sha: str, path: str) -> str | None:
    output = git(root, "ls-tree", "-z", sha, "--", path)
    if not output:
        return None
    rows = output.split(b"\0")
    if len(rows) != 2 or rows[-1] != b"":
        raise PlanError("ambiguous_git_tree_entry")
    try:
        mode_type_blob, actual = rows[0].split(b"\t", 1)
        mode, kind, blob = mode_type_blob.decode("ascii").split(" ")
    except (ValueError, UnicodeError):
        raise PlanError("malformed_git_tree_entry") from None
    if actual != path.encode("utf-8") or mode != "100644" or kind != "blob":
        raise PlanError("non_regular_dashboard_asset")
    return require_sha(blob)


def validate_commit_pair(root: Path, baseline: str, candidate: str) -> tuple[str, str]:
    """Pin two real related Git commits without trusting a live deploy state."""
    baseline = require_sha(baseline)
    candidate = require_sha(candidate)
    if baseline == candidate:
        raise PlanError("no_commit_delta")
    if git(root, "rev-parse", "HEAD").decode("ascii").strip() != candidate:
        raise PlanError("candidate_does_not_equal_checked_out_head")
    for sha in (baseline, candidate):
        if git(root, "cat-file", "-t", sha).strip() != b"commit":
            raise PlanError("revision_not_commit")
    try:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", baseline, candidate],
            cwd=root, timeout=15, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=True,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        raise PlanError("baseline_not_ancestor") from None
    return baseline, candidate


def plan(root: Path, baseline: str, candidate: str) -> dict:
    baseline, candidate = validate_commit_pair(root, baseline, candidate)
    raw = git(root, "diff", "--name-status", "--no-renames", "-z",
              baseline, candidate, "--")
    fields = raw.split(b"\0")
    if fields[-1:] != [b""]:
        raise PlanError("unterminated_git_diff")
    fields.pop()
    if not fields or len(fields) % 2:
        raise PlanError("no_or_malformed_git_diff")
    if len(fields) // 2 > MAX_DIFF_PATHS:
        raise PlanError("too_many_changed_paths")
    changed: list[tuple[str, str]] = []
    for index in range(0, len(fields), 2):
        try:
            state = fields[index].decode("ascii")
            path = fields[index + 1].decode("utf-8")
        except UnicodeError:
            raise PlanError("invalid_diff_encoding") from None
        if path not in STATIC_ALLOWLIST or state not in {"A", "M"}:
            raise PlanError("non_static_or_deleted_change")
        if any(existing == path for existing, _ in changed):
            raise PlanError("duplicate_diff_path")
        changed.append((path, state))

    result_assets = []
    total = 0
    for path, status in sorted(changed):
        base_blob = blob_at(root, baseline, path)
        new_blob = blob_at(root, candidate, path)
        if new_blob is None or (status == "M" and base_blob is None):
            raise PlanError("missing_or_unexpected_static_blob")
        if status == "A" and base_blob is not None:
            raise PlanError("unexpected_static_addition")
        try:
            size = int(git(root, "cat-file", "-s", new_blob).strip())
        except (ValueError, UnicodeError):
            raise PlanError("invalid_static_size") from None
        if size < 0 or size > MAX_ASSET_BYTES or total + size > MAX_TOTAL_BYTES:
            raise PlanError("static_byte_budget_exceeded")
        raw_bytes = git(root, "cat-file", "blob", new_blob)
        if len(raw_bytes) != size or b"\0" in raw_bytes:
            raise PlanError("untrusted_static_asset_bytes")
        try:
            raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise PlanError("non_utf8_static_asset") from None
        total += size
        result_assets.append({
            "path": path,
            "change": status,
            "git_blob_sha": new_blob,
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "bytes": size,
        })

    return {
        "schema": "zcloud-dashboard-static-candidate-plan-v1",
        "baseline_sha": baseline,
        "candidate_sha": candidate,
        "baseline_proven_deployed": False,
        "changed_static_assets_only": True,
        "static_diff_eligible": True,
        "live_runtime_compatibility_proven": False,
        "sqlite_ownership_proven": False,
        "production_status_proven": False,
        "release_authorized": False,
        "promotion_authorized": False,
        "total_bytes": total,
        "assets": result_assets,
    }


# The currently integrated cockpit UI publishes this three-file bundle. The
# optional design-system CSS is included only when it is a normal Git blob.
SNAPSHOT_REQUIRED = (
    "public/index.html",
    "public/app.js",
    "public/enhancements.css",
)



class _IndexDependencyLinks(HTMLParser):
    """Track active executable/style references without executing source."""

    INERT_CONTAINERS = frozenset(("template", "noscript"))

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[tuple[str, bool]] = []
        self.styles: list[tuple[str, bool]] = []
        self._inert_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.INERT_CONTAINERS:
            self._inert_stack.append(tag)
            return
        if tag not in {"script", "link"}:
            return
        fields = {}
        for key, value in attrs:
            if key in fields:
                raise PlanError("duplicate_index_attribute")
            fields[key] = value
        active = not self._inert_stack
        if tag == "script" and "src" in fields:
            script_type = (fields.get("type") or "").strip().lower()
            active = active and script_type in {"", "module", "text/javascript",
                                                 "application/javascript"} and "nomodule" not in fields
            self.scripts.append((fields["src"] or "", active))
        elif tag == "link" and "stylesheet" in (fields.get("rel") or "").lower().split():
            tokens = (fields.get("rel") or "").lower().split()
            media = (fields.get("media") or "all").strip().lower()
            active = active and "alternate" not in tokens and "disabled" not in fields
            active = active and media in {"", "all", "screen"}
            self.styles.append((fields.get("href") or "", active))

    def handle_endtag(self, tag: str) -> None:
        if tag in self.INERT_CONTAINERS and self._inert_stack:
            if tag != self._inert_stack[-1]:
                raise PlanError("malformed_inert_index_structure")
            self._inert_stack.pop()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def close(self) -> None:
        super().close()
        if self._inert_stack:
            raise PlanError("malformed_inert_index_structure")


def index_bundle_versions(raw_html: bytes) -> dict[str, str]:
    """Require unambiguous, cache-pinned app and cockpit CSS references.

    Referenced versions are informational only; they cannot attest browser
    cache state, runtime responses or successful production deployment.
    """
    parser = _IndexDependencyLinks()
    try:
        parser.feed(raw_html.decode("utf-8"))
        parser.close()
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, PlanError):
            raise
        raise PlanError("malformed_index_asset_reference") from None
    revisions: dict[str, str] = {}
    for asset, urls in (
        ("app.js", parser.scripts),
        ("enhancements.css", parser.styles),
    ):
        possible: list[tuple[str, bool]] = []
        for url, active in urls:
            try:
                path = urlsplit(url).path
            except ValueError:
                raise PlanError("malformed_index_asset_reference") from None
            if path.rsplit("/", 1)[-1] == asset:
                possible.append((url, active))
        if not possible:
            raise PlanError("missing_index_asset_reference")
        if len(possible) != 1:
            raise PlanError("ambiguous_index_asset_reference")
        value, active = possible[0]
        if not active:
            raise PlanError("inactive_index_asset_reference")
        match = re.fullmatch(r"/" + re.escape(asset) + r"\?v=([A-Za-z0-9][A-Za-z0-9._-]{0,31})", value)
        if not match:
            raise PlanError("untrusted_index_asset_reference")
        revisions[asset] = match.group(1)
    return revisions


def snapshot(root: Path, revision: str) -> dict:
    """Inventory immutable Git tree bytes, NEVER read live or authorize publish.

    This is a source-revision manifest, NOT a verified VPS / deployed manifest.
    Callers may compare its digests to separately authorized live observations.
    """
    revision = require_sha(revision)
    if git(root, "rev-parse", "HEAD").decode("ascii").strip() != revision:
        raise PlanError("candidate_does_not_equal_checked_out_head")
    if git(root, "cat-file", "-t", revision).strip() != b"commit":
        raise PlanError("revision_not_commit")
    result = []
    total = 0
    index_html = None
    required = set(SNAPSHOT_REQUIRED)
    for path in sorted(STATIC_ALLOWLIST):
        blob = blob_at(root, revision, path)
        if blob is None:
            if path in required:
                raise PlanError("required_dashboard_asset_missing")
            continue
        try:
            size = int(git(root, "cat-file", "-s", blob).strip())
        except (ValueError, UnicodeError):
            raise PlanError("invalid_static_size") from None
        if size < 0 or size > MAX_ASSET_BYTES or total + size > MAX_TOTAL_BYTES:
            raise PlanError("static_byte_budget_exceeded")
        raw = git(root, "cat-file", "blob", blob)
        if len(raw) != size or b"\x00" in raw:
            raise PlanError("untrusted_static_asset_bytes")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            raise PlanError("non_utf8_static_asset") from None
        total += size
        if path == "public/index.html":
            index_html = raw
        result.append({
            "path": path,
            "git_blob_sha": blob,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": size,
        })
    if index_html is None:
        raise PlanError("required_dashboard_asset_missing")
    referenced_versions = index_bundle_versions(index_html)
    return {
        "schema": "zcloud-dashboard-git-snapshot-v1",
        "index_asset_versions": referenced_versions,
        "index_references_pinned": True,
        "source_revision_sha": revision,
        "source_bytes_only": True,
        "runtime_bytes_observed": False,
        "baseline_proven_deployed": False,
        "production_status_proven": False,
        "live_runtime_compatibility_proven": False,
        "sqlite_ownership_proven": False,
        "release_authorized": False,
        "promotion_authorized": False,
        "total_bytes": total,
        "assets": result,
    }



def _cache_version_evidence(root: Path, baseline: str, candidate: str) -> tuple[dict, dict, dict]:
    """Read bounded baseline blobs and complete candidate bundle from Git only."""
    after = snapshot(root, candidate)
    before_index = blob_at(root, baseline, "public/index.html")
    if before_index is None:
        raise PlanError("baseline_index_asset_reference_untrusted")
    try:
        size = int(git(root, "cat-file", "-s", before_index).strip())
        if not 0 <= size <= MAX_ASSET_BYTES:
            raise PlanError("baseline_index_asset_reference_untrusted")
        raw = git(root, "cat-file", "blob", before_index)
        if len(raw) != size or b"\0" in raw:
            raise PlanError("baseline_index_asset_reference_untrusted")
        before_versions = index_bundle_versions(raw)
    except (PlanError, ValueError, UnicodeError):
        raise PlanError("baseline_index_asset_reference_untrusted") from None

    before_assets = {}
    for name in ("app.js", "enhancements.css"):
        identity = blob_at(root, baseline, "public/" + name)
        if identity is None:
            raise PlanError("baseline_dashboard_asset_missing")
        before_assets[name] = identity
    return after, before_versions, before_assets


def verify_cache_bust(root: Path, baseline: str, candidate: str) -> dict:
    """Optional strict *static-only* Git cache-version admission, never deploy."""
    proposal = plan(root, baseline, candidate)
    after, before_versions, _ = _cache_version_evidence(root, baseline, candidate)
    changed = {item["path"] for item in proposal["assets"]}
    for name in ("app.js", "enhancements.css"):
        if "public/" + name in changed and before_versions[name] == after["index_asset_versions"][name]:
            raise PlanError("cache_revision_not_bumped")
    return {
        **proposal,
        "cache_bust_checked": True,
        "cache_bust_verified": True,
        "baseline_index_asset_versions": before_versions,
        "candidate_index_asset_versions": after["index_asset_versions"],
        "runtime_bytes_observed": False,
        "release_authorized": False,
        "promotion_authorized": False,
    }


def audit_cache_pins(root: Path, baseline: str, candidate: str) -> dict:
    """Audit cache pins across *mixed* source changes without accepting a release.

    This read-only observation intentionally DOES NOT inspect or admit a mixed
    production diff. It cannot establish a safe deploy, publisher lease or
    browser cache behaviour. False coherence is a failure, never a permission.
    """
    baseline, candidate = validate_commit_pair(root, baseline, candidate)
    after, before_versions, old_blobs = _cache_version_evidence(root, baseline, candidate)
    after_assets = {item["path"]: item for item in after["assets"]}
    assessments = []
    for name in ("app.js", "enhancements.css"):
        path = "public/" + name
        new_blob = after_assets[path]["git_blob_sha"]
        changed = old_blobs[name] != new_blob
        bumped = before_versions[name] != after["index_asset_versions"][name]
        assessments.append({
            "path": path,
            "blob_changed": changed,
            "version_changed": bumped,
            "baseline_version": before_versions[name],
            "candidate_version": after["index_asset_versions"][name],
            "baseline_git_blob_sha": old_blobs[name],
            "candidate_git_blob_sha": new_blob,
            "cache_pin_coherent": not changed or bumped,
        })
    good = all(item["cache_pin_coherent"] for item in assessments)
    return {
        "schema": "zcloud-dashboard-cache-pin-audit-v1",
        "baseline_sha": baseline,
        "candidate_sha": candidate,
        "cache_pins_coherent": good,
        "reason": "cache_pin_versions_consistent" if good else "changed_asset_pin_unchanged",
        "source_bytes_only": True,
        "structural_static_diff_not_evaluated": True,
        "static_diff_eligible": False,
        "runtime_bytes_observed": False,
        "baseline_proven_deployed": False,
        "production_status_proven": False,
        "sqlite_ownership_proven": False,
        "live_runtime_compatibility_proven": False,
        "release_authorized": False,
        "promotion_authorized": False,
        "assets": assessments,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--baseline-sha")
    parser.add_argument("--candidate-sha")
    parser.add_argument("--snapshot-sha")
    parser.add_argument("--check-cache-bust", action="store_true",
                        help="Require changed JS/CSS to bump index.html cache versions; no deploy")
    parser.add_argument("--audit-cache-pins", action="store_true",
                        help="Audit Git asset revisions even across mixed commits; never promote")
    args = parser.parse_args(argv)
    try:
        if args.check_cache_bust and args.audit_cache_pins:
            raise PlanError("incompatible_modes")
        if args.snapshot_sha is not None:
            if args.check_cache_bust or args.audit_cache_pins:
                raise PlanError("incompatible_modes")
            if args.baseline_sha is not None or args.candidate_sha is not None:
                raise PlanError("incompatible_modes")
            evidence = snapshot(args.root, args.snapshot_sha)
        else:
            if args.baseline_sha is None or args.candidate_sha is None:
                raise PlanError("baseline_and_candidate_required")
            if args.audit_cache_pins:
                evidence = audit_cache_pins(args.root, args.baseline_sha, args.candidate_sha)
            elif args.check_cache_bust:
                evidence = verify_cache_bust(args.root, args.baseline_sha, args.candidate_sha)
            else:
                evidence = plan(args.root, args.baseline_sha, args.candidate_sha)
    except PlanError as exc:
        if args.snapshot_sha is not None:
            schema = "zcloud-dashboard-git-snapshot-v1"
        elif args.audit_cache_pins:
            schema = "zcloud-dashboard-cache-pin-audit-v1"
        else:
            schema = "zcloud-dashboard-static-candidate-plan-v1"
        denied = {
            "schema": schema,
            "static_diff_eligible": False,
            "runtime_bytes_observed": False,
            "baseline_proven_deployed": False,
            "production_status_proven": False,
            "sqlite_ownership_proven": False,
            "live_runtime_compatibility_proven": False,
            "release_authorized": False,
            "promotion_authorized": False,
            "reason": str(exc),
        }
        if args.audit_cache_pins:
            denied.update(
                cache_pins_coherent=False,
                structural_static_diff_not_evaluated=True,
                source_bytes_only=True,
            )
        print(json.dumps(denied, sort_keys=True))
        return 2
    print(json.dumps(evidence, sort_keys=True))
    if args.audit_cache_pins and not evidence["cache_pins_coherent"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
