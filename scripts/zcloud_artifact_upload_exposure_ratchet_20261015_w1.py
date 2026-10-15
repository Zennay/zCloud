#!/usr/bin/env python3
"""Fail-closed check for unsafe CI artifact uploads in changed workflow sources.

This only evaluates source, never invokes GitHub APIs or issues deploy authority.
Unchanged historical workflow blobs are intentionally grandfathered.
"""
from __future__ import annotations
import argparse
import os
import re
import selectors
import signal
import subprocess
import sys
import time
import unicodedata

SHA = re.compile(r"[a-f0-9]{40}\Z")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml\Z")
ACTION_MANIFEST = re.compile(r"(?:[A-Za-z0-9_.-]+/)*action\.ya?ml\Z")
USES = re.compile(r"^(\s*)-?\s*uses\s*:\s*['\"]?actions/upload-artifact@[^\s'\"]+['\"]?\s*(?:#.*)?$", re.I)
WITH = re.compile(r"^(\s*)with\s*:\s*(?:#.*)?$", re.I)
KEY = re.compile(r"^(\s*)([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$")
PRIVATE = re.compile(r"(?:^|[/\\])(?:\.env(?:\.[^/\\]*)?|\.git|\.ssh|secrets?|credentials?|\.npmrc|id_rsa|tokens?)(?:\.[^/\\]+)?(?:[/\\]|$)", re.I)
BROAD = re.compile(r"(?:^|\s)(?:\.|\./|\*|\*\*|/|~/)(?:\s|$)|[\$\{\}\[\]]|\*\*|[!~]", re.I)


class Denied(ValueError):
    pass


def git(*args: str, cap: int = 1200000) -> bytes:
    """Bound output while receiving it, and tear down the whole process group."""
    process = None
    poller = None
    succeeded = False
    try:
        env = os.environ.copy()
        env["GIT_NO_REPLACE_OBJECTS"] = "1"
        env["GIT_OPTIONAL_LOCKS"] = "0"
        process = subprocess.Popen(["git", "-c", "core.pager=cat", *args],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=env, start_new_session=True)
        assert process.stdout is not None
        poller = selectors.DefaultSelector()
        poller.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + 10
        chunks = []
        seen = 0
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise Denied("git_deadline")
            if not poller.select(remain):
                raise Denied("git_deadline")
            piece = os.read(process.stdout.fileno(), min(65536, cap - seen + 1))
            if not piece:
                break
            seen += len(piece)
            if seen > cap:
                raise Denied("git_output_unbounded")
            chunks.append(piece)
        if process.wait(timeout=max(0.001, deadline - time.monotonic())):
            raise Denied("git_source_unavailable")
        succeeded = True
        return b"".join(chunks)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Denied("git_unavailable") from e
    finally:
        if poller is not None:
            poller.close()
        if process is not None:
            # A Git wrapper may exit while a forked child holds stdout open.
            if not succeeded:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.wait()
            if process.stdout is not None:
                process.stdout.close()


def workflow_paths(base: str, head: str) -> list[str]:
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise Denied("invalid_revision")
    if git("rev-parse", "HEAD").decode().strip() != head:
        raise Denied("head_mismatch")
    # Apply the SAME bounded, no-replace-object Git reader to ancestry.
    # A bare subprocess.run here otherwise inherits altered Git object
    # replacement/configuration and lacks the streaming process-group guard.
    ancestor = git("merge-base", base, head, cap=120).decode("ascii", "strict").strip()
    if ancestor != base:
        raise Denied("invalid_ancestry")
    paths = []
    for raw in git("diff", "--name-only", "-z", "--diff-filter=ACMRT", base, head,
                   "--", cap=100000).split(b"\0"):
        if not raw:
            continue
        # Local relative uses references can point to action.yml anywhere.
        # Skip unrelated paths before ASCII decoding so Unicode filenames
        # unrelated to workflow source are left intact.
        is_manifest_name = (raw in (b"action.yml", b"action.yaml") or
                            raw.endswith((b"/action.yml", b"/action.yaml")))
        if not (is_manifest_name or raw.startswith((
                b".github/workflows/", b".github/actions/", b"actions/"))):
            continue
        try:
            name = raw.decode("ascii", "strict")
        except UnicodeError as e:
            raise Denied("ambiguous_workflow_path") from e
        if len(name) > 512:
            raise Denied("source_path_too_long")
        if name.startswith(".github/workflows/"):
            if not WORKFLOW.fullmatch(name):
                raise Denied("ambiguous_workflow_path")
        elif ACTION_MANIFEST.fullmatch(name):
            pass
        elif name.startswith((".github/actions/", "actions/")):
            # Deny opaque source entries, but do not parse normal scripts.
            entry = git("ls-tree", "-z", head, "--", name, cap=1500)
            if not entry.startswith((b"100644 blob ", b"100755 blob ")) or not entry.endswith(name.encode() + b"\0"):
                raise Denied("opaque_local_action_entry")
            continue
        else:
            raise Denied("ambiguous_action_manifest_path")
        paths.append(name)
    if len(paths) > 200 or len(paths) != len(set(paths)):
        raise Denied("workflow_inventory_unbounded")
    return paths


def read_workflow(commit: str, path: str) -> str:
    mode = git("ls-tree", "-z", commit, "--", path, cap=1500)
    if not mode.startswith((b"100644 blob ", b"100755 blob ")) or not mode.endswith(path.encode() + b"\0"):
        raise Denied("non_regular_workflow_blob")
    data = git("show", f"{commit}:{path}", cap=600000)
    if b"\0" in data or len(data) > 550000:
        raise Denied("unsafe_workflow_bytes")
    try:
        return data.decode("utf-8", "strict")
    except UnicodeError as e:
        raise Denied("unsafe_workflow_bytes") from e


def examine(text: str) -> None:
    # YAML accepts some invisible format/bidi and separator code points that
    # a line-oriented lexical parser cannot safely treat as ordinary letters.
    if any(unicodedata.category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
           and c not in "\r\n\t" for c in text):
        raise Denied("unsafe_workflow_bytes")
    # YAML double-quoted escapes are resolved before GitHub identifies an Action.
    # Detect encoded uses values even when the source lacks the literal action name.
    encoded = re.compile(r"\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8})")
    for line in text.splitlines():
        m = re.match(r"^\s*(?:-\s*)?(?:uses|['\"]uses['\"])\s*:\s*(.*)$", line, re.I)
        if m:
            raw = m.group(1).split(" #", 1)[0].strip()
            if encoded.search(raw):
                raise Denied("encoded_action_reference")
            # YAML folds block scalars and double-quoted backslash-newline
            # continuations before resolving the action name. A literal-only
            # scan could miss an upload-artifact name split across lines.
            if (not raw or raw[0] in ">|&*!{[" or "\\" in raw or
                    (raw[0] in "'\"" and (len(raw) < 2 or raw[-1] != raw[0]))):
                raise Denied("ambiguous_action_reference")
        if re.match(r'^\s*(?:-\s*)?"[^"\n]*\\(?:x|u|U)[^"\n]*"\s*:', line):
            raise Denied("encoded_mapping_key")
    if "upload-artifact" not in text.lower():
        return
    if re.search(r"(?m)^\s*(?:<<\s*:|(?:[\w-]+\s*:\s*)?[&*!]{1,2}[\w-]+)", text):
        raise Denied("ambiguous_yaml_upload")
    lines = text.splitlines()
    uploads = [i for i, line in enumerate(lines) if USES.match(line)]
    if not uploads:
        raise Denied("unrecognized_upload_step")
    for i in uploads:
        m = USES.match(lines[i])
        assert m
        uses_indent = len(m.group(1))
        # A step may start "- uses: ..." or "- name: ...\n  uses: ...".
        # In the named-step form, "with:" is a sibling of "uses:", not a
        # deeper child. Treat the enclosing '-' list item as the boundary.
        direct_with_indent = None
        if lines[i].lstrip().startswith("-"):
            step_indent = uses_indent
        else:
            step_indent = uses_indent - 1
            direct_with_indent = uses_indent
        with_indent = None
        before_with_indent = None
        field_indent = None
        fields = {}
        for line in lines[i+1:]:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            if indent <= step_indent:
                break
            with_match = WITH.match(line)
            if with_match and with_indent is None:
                candidate_indent = len(with_match.group(1))
                if (direct_with_indent is not None and
                        candidate_indent != direct_with_indent):
                    raise Denied("nested_upload_with")
                if (before_with_indent is not None and
                        candidate_indent != before_with_indent):
                    raise Denied("nested_upload_with")
                with_indent = candidate_indent
                continue
            if with_indent is None:
                # A later 'with' nested inside env/defaults is not a top-level
                # Action input; only a direct sibling may satisfy path.
                prior = KEY.match(line)
                if prior and indent > step_indent:
                    before_with_indent = (indent if before_with_indent is None
                                          else min(indent, before_with_indent))
                continue
            if with_match:
                # Duplicate YAML mappings can replace the earlier safe values.
                raise Denied("duplicate_upload_with")
            if indent <= with_indent:
                break
            key = KEY.match(line)
            if not key or len(key.group(1)) <= with_indent:
                raise Denied("ambiguous_upload_value")
            if field_indent is None:
                field_indent = indent
            elif indent != field_indent:
                raise Denied("nested_upload_option")
            k = key.group(2).lower()
            if k in fields:
                raise Denied("duplicate_upload_option")
            fields[k] = key.group(3).split(" #", 1)[0].strip().strip("\"'")
        if with_indent is None or "path" not in fields:
            raise Denied("missing_artifact_path")
        if fields.get("include-hidden-files", "false").lower() != "false":
            raise Denied("hidden_files_enabled_or_ambiguous")
        paths = fields["path"].split(",")
        for value in paths:
            path = value.strip().strip("\"'")
            if (not path or ambiguous_artifact_path(path) or BROAD.search(path) or PRIVATE.search(path) or
                path.startswith(("-", "/", "\\")) or ".." in path.split("/") or
                any(c.isspace() or c in "|>&;" + chr(96) + "#:" for c in path)):
                raise Denied("secret_or_broad_artifact_path")


def ambiguous_artifact_path(value: str) -> bool:
    """Disallow upload globs, encoded paths, hidden indirections and secret-like names."""
    if len(value) > 200 or any(char in value for char in ("\\", "*", "?", "%")):
        return True
    if value.endswith("/") or "//" in value:
        return True
    if any(ord(char) < 32 or ord(char) > 126 for char in value):
        return True
    if re.search(r"(?:secret|credential|token|privatekey|password|passwd|\.env|id_rsa|\.npmrc)", value, re.I):
        return True
    # A narrow filename can still be a private key or cloud credential.
    if re.search(r"\.(?:pem|p12|pfx|jks|keystore|key|asc)\Z", value, re.I):
        return True
    # Credential stores that are easy to mistake for innocuous JSON/config
    # when uploaded through a narrow, non-wildcard artifact file path.
    protected_roots = {".aws", ".kube", ".azure", ".gnupg", ".gcloud",
                       ".docker", ".terraform", ".npm", ".m2", ".config"}
    parts = [part.lower() for part in value.split("/")]
    if any(part in protected_roots for part in parts):
        return True
    if parts[-1] in {".netrc", ".pypirc", ".git-credentials",
                     "id_ed25519", "id_ecdsa", "id_dsa", "gradle.properties"}:
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        paths = workflow_paths(args.base, args.head)
        for path in paths:
            examine(read_workflow(args.head, path))
    except Denied as e:
        print("DENY " + str(e), file=sys.stderr)
        return 2
    print(f"PASS: {len(paths)} changed workflow(s) inspected; mutation_authorized=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
