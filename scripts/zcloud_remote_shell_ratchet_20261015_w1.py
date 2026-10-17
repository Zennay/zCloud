#!/usr/bin/env python3
"""Offline fail-closed Git-tree ratchet for directly piped remote shell in Actions.

This is a narrow source-risk tripwire, NOT a YAML parser, runtime security
boundary or authorization to deploy. Unmodified workflow debt is grandfathered.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
SCALAR = re.compile(r"""^(?P<indent>\s*)(?:-\s+)?(?P<key>run|["\x27]run["\x27])\s*:\s*(?P<value>.*)$""")
FLOW_RUN = re.compile(r"""(?:\{|,)\s*(?:run|["\x27]run["\x27])\s*:\s*(?P<script>"(?:\\.|[^"])*"|\x27(?:\x27\x27|[^\x27])*\x27|[^,}\]]+)""")
# Wrappers can change the process name without changing remote-code execution.
# Bounded, explicit prefixes keep this a conservative source-pattern check.
# Explicit bounded sudo user and env assignment forms are real interpreter
# wrappers: curl | sudo -u root bash and wget | env LANG=C sh execute bytes.
# Ignore arbitrary shell metacharacter expansion and dynamic variable values.
RUN_USER = r"[A-Za-z_][A-Za-z0-9_.-]{0,63}"
SUDO_WRAPPER = (
    r"sudo(?:\s+-[A-Za-z]+)*"
    + rf"(?:\s+(?:-u\s+{RUN_USER}|--user(?:=|\s+){RUN_USER}))?"
    + r"(?:\s+--)?"
)
ENV_WRAPPER = (
    r"(?:/usr/bin/)?env(?:\s+-[A-Za-z]+)*"
    + r"(?:\s+[A-Za-z_][A-Za-z0-9_]*=[^\s|;]{1,128}){0,3}"
)
SCRIPT_WRAPPER = (
    rf"(?:(?:{SUDO_WRAPPER}|{ENV_WRAPPER}|"
    r"(?:/usr/bin/)?timeout\s+[0-9]+(?:\.[0-9]+)?|nohup|command|exec)\s+){0,3}"
)
# Include alternate POSIX-compatible shells and BusyBox applets that read
# downloaded stdin as executable instructions (not merely bash/sh/zsh).
SHELL_CONSUMERS = r"(?:busybox\s+(?:sh|ash)|bash|sh|zsh|dash|ksh|mksh|ash|fish)"
SCRIPT_EXEC = SCRIPT_WRAPPER + r"(?:/usr/local/bin/|/usr/bin/|/bin/)?(?:" + SHELL_CONSUMERS + r"|python(?:[23](?:\.\d+)?)?|pypy3?|node(?:js)?|ruby|perl|php)\b"
# Remote bytes may traverse bounded pass-through/decoder stages before a shell.
# Keep the allowed stage names explicit to avoid spanning unrelated commands.
PASS_THROUGH = r"(?:tee|cat|sed|base64|tr|awk|gunzip|gzip)\b[^\n|;]*\|\s*"
# Bash/POSIX shell can execute pipeline consumers in a subshell or command
# group. A pipe into '( bash )' or '{ sh; }' is still remote code execution.
# Only an opening group *immediately before* the interpreter is admitted here.
PIPE_GROUP = r"(?:\(\s*|\{\s*)?"
REMOTE = re.compile(
    r"\b(?:curl|wget)\b[^\n|;]*\|\s*" + rf"(?:{PASS_THROUGH}){{0,2}}" + PIPE_GROUP + SCRIPT_EXEC,
    re.I,
)
SUBSTITUTION = re.compile(SCRIPT_EXEC + r"\s+<\(\s*(?:curl|wget)\b", re.I)
SHELL_COMMAND_SUBST = re.compile(SCRIPT_EXEC + r"""\s+-c\s+["\x27]?\$\(\s*(?:curl|wget)\b""", re.I)
REMOTE_STDIN = re.compile(
    SCRIPT_EXEC + r"""(?:\s+-s|\s+-)?\s*(?:<\s*<\(|<<<\s*["\x27]?\s*\$\()\s*(?:curl|wget)\b""", re.I
)
# Shell eval executes command-substituted response as code, while source/. read
# directly from process substitution. Both are remote execution even though no
# network stream is visibly piped into an interpreter.
SHELL_EVAL_REMOTE = re.compile(
    r"""(?<![\w])eval\s+["']?\s*\$\(\s*(?:curl|wget)\b""", re.I
)
SHELL_SOURCE_PROCESS = re.compile(
    r"""(?<!\S)(?:source|\.)\s+<\(\s*(?:curl|wget)\b""", re.I
)
# Obvious download-to-local-file then immediate execution is code ingestion
# without a pipe. A bounded literal same-path backreference prevents guessing
# whether a differently named local file came from this fetch. Does not claim
# to model aliases, chmod/rewrite steps, shell variables or arbitrary files.
DOWNLOAD_THEN_EXEC = re.compile(
    r"\b(?:curl|wget)\b[^\n;|]{0,512}?"
    # Combined short options like wget -qO or curl -fsSLo are common.
    # Long flags remain explicitly bounded and cannot consume arbitrary text.
    r"(?:\s-[A-Za-z]{0,8}[oO]\s+|\s--(?:output|output-document)(?:=|\s+))"
    r"(?P<downloaded>(?:\./|/tmp/|/var/tmp/)?[A-Za-z0-9._/-]{1,180})"
    r"[^\n;|]{0,512}?"
    r"(?:\s*&&\s*|\s*;\s*|\s*\n\s*)"
    + rf"(?:{SCRIPT_EXEC}|source\b|\.)" + r"\s+(?P=downloaded)(?![A-Za-z0-9._/-])",
)
# A shell stdout redirection is an alternative to -o/--output:
# curl URL > local.sh && sh local.sh. Count only bounded literal identical
# targets, without following aliases, variable substitution or file rewrites.
REDIRECT_THEN_EXEC = re.compile(
    r"\b(?:curl|wget)\b[^\n;|><]{0,512}?\s+>\s*"
    r"(?P<redirected>(?:\./|/tmp/|/var/tmp/)?[A-Za-z0-9._/-]{1,180})"
    r"[^;\n|><]{0,256}?"
    r"(?:\s*&&\s*|\s*;\s*|\s*\n\s*)"
    + rf"(?:{SCRIPT_EXEC}|source\b|\.)"
    + r"\s+(?P=redirected)(?![A-Za-z0-9._/-])",
)
# Literal shell variable populated from remote substitution and immediately
# evaluated as code is remote execution too: payload=$(curl URL) && eval "$payload".
# Only an unmodified exact literal variable can match; this is not shell
# data-flow analysis and cannot infer aliases, exports or interim mutations.
REMOTE_VARIABLE_EVAL = re.compile(
    r"\b(?P<variable>[A-Za-z_][A-Za-z0-9_]{0,63})\s*=\s*"
    r"""["']?\$\(\s*(?:curl|wget)\b[^)\n;]{0,512}\)["']?"""
    r"\s*(?:&&|;|\n)\s*"
    r"""(?:eval\s+|(?:bash|sh|zsh|dash)\s+-c\s+)["']?\$\{?"""
    r"(?P=variable)" + r"""\}?["']?(?![A-Za-z0-9_])""",
)
# PowerShell has a separate direct download-and-evaluate vocabulary. Both
# Invoke-RestMethod/Invoke-WebRequest and their literal aliases irm/iwr can
# pipe remote text directly into iex (Invoke-Expression), or be nested inside
# an iex call. No PowerShell AST or alias redefinition is inferred here.
PS_DOWNLOAD = r"(?:Invoke-RestMethod|Invoke-WebRequest|irm|iwr)"
PS_EVAL = r"(?:Invoke-Expression|iex)"
POWERSHELL_PIPE_EVAL = re.compile(
    rf"\b{PS_DOWNLOAD}\b[^\n;|]{{0,512}}\|\s*{PS_EVAL}\b",
    re.I,
)
POWERSHELL_INVOKE_EVAL = re.compile(
    rf"\b{PS_EVAL}\b\s*\(\s*{PS_DOWNLOAD}\b",
    re.I,
)
# A downloaded local executable is not always started via bash/script.py.
# After a literal download, direct ./script execution, optionally preceded
# by chmod +x on the same literal name, consumes remote code as well.
# Do not trace moves, shell expansions, aliases or intermediate rewrites.
DOWNLOADED_DIRECT_EXEC = re.compile(
    r"\b(?:curl|wget)\b[^\n;|]{0,512}?"
    r"(?:\s-[A-Za-z]{0,8}[oO]\s+|\s--(?:output|output-document)(?:=|\s+)|\s*>\s*)"
    r"(?P<executable>(?:\./|/tmp/|/var/tmp/)?[A-Za-z0-9._/-]{1,180})"
    r"[^\n;|]{0,512}?"
    r"(?:\s*&&\s*|\s*;\s*|\s*\n\s*)"
    r"(?:chmod\s+[A-Za-z0-9+,-]{1,16}\s+(?P=executable)"
    r"(?:\s*&&\s*|\s*;\s*|\s*\n\s*))?"
    r"(?:\./)?(?P=executable)(?![A-Za-z0-9._/-])",
)
OPAQUE_RUN = re.compile(r"""^\s*(?:\*[A-Za-z_][\w.-]*|![^\s#]+)(?:\s|$)""")
# Escaped YAML mapping keys and explicit question-mark run keys have semantics
# outside the narrow source reader. Conservatively reject newly introduced
# ambiguity instead of silently reporting an unexamined run step as safe.
ESCAPED_MAPPING_KEY = re.compile(
    r"""^\s*(?:-\s+)?["'][^"'\n]*\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8})[^"'\n]*["']\s*:"""
)
EXPLICIT_RUN_KEY = re.compile(r"""^\s*(?:-\s+)?\?\s*(?:"run"|'run'|run)(?:\s*:|\s*$)""")
# Inline flow maps can hide run via an escaped double-quoted key:
# steps: [{"r\u0075n": "curl ... | bash"}]. A single-quoted key is literal.
FLOW_ESCAPED_MAPPING_KEY = re.compile(
    r"""(?:\{|,)\s*"[^"\n]*\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8})[^"\n]*"\s*:"""
)
# YAML double-quoted run scalars decode escapes before Actions executes shell.
# A \u007c / \x7c can therefore become a real pipeline even if raw Git
# bytes contain no literal '|'. Deny newly changed opaque execution source.
# We deliberately flag other control escapes rather than trying to emulate
# the whole YAML 1.2 scalar decoder with regexes.
OPAQUE_QUOTED_ESCAPE = re.compile(
    r"""\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|[nrt0abefv\\])"""
)
MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 512, 1024 * 1024, 32 * 1024 * 1024
# Bound even the *whole* ls-tree response, before the per-file source cap.
MAX_GIT_OUTPUT_BYTES = 64 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 25



class SourceError(ValueError):
    pass


def git(repo: Path, *argv: str) -> bytes:
    """Read trusted Git-object bytes with a hard aggregate output/deadline cap.

    Checking source-file counts *after* subprocess.run collected the entire
    ls-tree output was insufficient to prevent a large Git tree exhausting
    hosted scanner memory. Never return arbitrary Git stderr to the operator.
    """
    command = ["git", "-C", str(repo), *argv]
    deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    try:
        chunks: list[bytes] = []
        total = 0
        with selectors.DefaultSelector() as selector:
            assert process.stdout is not None
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, GIT_TIMEOUT_SECONDS)
                ready = selector.select(remaining)
                if not ready:
                    raise subprocess.TimeoutExpired(command, GIT_TIMEOUT_SECONDS)
                for key, _ in ready:
                    piece = os.read(key.fileobj.fileno(), 65536)
                    if not piece:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(piece)
                    if total > MAX_GIT_OUTPUT_BYTES:
                        raise SourceError("Excessive Git source evidence")
                    chunks.append(piece)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, GIT_TIMEOUT_SECONDS)
        if process.wait(timeout=remaining) != 0:
            raise SourceError("Git evidence unavailable or invalid")
        return b"".join(chunks)
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait()
        finally:
            # Popen stdout is a Python-owned buffered file descriptor;
            # selector.unregister does not close it. Close on success,
            # nonzero exit, timeout and over-limit exception alike.
            if process.stdout is not None:
                process.stdout.close()


def verify_commit(repo: Path, sha: str) -> None:
    if not SHA.fullmatch(sha):
        raise SourceError("Expected immutable 40-character lower-case commit SHA")
    got = git(repo, "rev-parse", "--verify", sha + "^{commit}").decode().strip()
    if got != sha:
        raise SourceError("Resolved Git object differs from requested commit")


def committed_workflows(repo: Path, sha: str) -> dict[str, bytes]:
    verify_commit(repo, sha)
    # Also inspect repository-local composite Action entrypoints: their run: 
    # steps execute through `uses: ./path`, outside .github/workflows.
    entries = git(repo, "ls-tree", "-r", "-z", "--full-tree", sha).split(b"\0")
    found: dict[str, bytes] = {}
    total = 0
    for entry in entries:
        if not entry:
            continue
        try:
            meta, raw_path = entry.split(b"\t", 1)
            mode, typ, object_id = meta.decode("ascii").split(" ")
            path = raw_path.decode("utf-8", "strict")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SourceError("Malformed Git tree entry") from exc
        # Recursive Git tree listing omits directory nodes but emits a
        # symlink/gitlink at the directory's exact path. Reject those control
        # ancestors even if another valid composite Action remains in the tree.
        if path in (".github", ".github/actions", ".github/workflows"):
            raise SourceError("Actions control directory replaced by a non-tree")
        workflow_tree = path.startswith(".github/workflows/")
        action_source = path in ("action.yml", "action.yaml") or path.endswith(("/action.yml", "/action.yaml"))
        workflow_source = workflow_tree and path.endswith((".yml", ".yaml"))
        # A symlink in Actions control source is never a trusted local entrypoint.
        if mode in ("120000", "160000") and (workflow_tree or action_source or path.startswith(".github/actions/")):
            raise SourceError("Symlinked or gitlinked Actions source rejected")
        if not workflow_source and not action_source:
            continue
        if mode not in ("100644", "100755") or typ != "blob" or not SHA.fullmatch(object_id):
            raise SourceError("Unsafe Actions source tree entry")
        if path in found or len(found) >= MAX_FILES:
            raise SourceError("Duplicate or excessive Actions source inventory")
        size = git(repo, "cat-file", "-s", object_id).decode().strip()
        if not size.isdecimal() or int(size) > MAX_FILE_BYTES:
            raise SourceError("Oversized or invalid workflow blob")
        total += int(size)
        if total > MAX_TOTAL_BYTES:
            raise SourceError("Excessive workflow bytes")
        data = git(repo, "cat-file", "blob", object_id)
        if len(data) != int(size) or b"\0" in data:
            raise SourceError("Inconsistent or binary workflow blob")
        data.decode("utf-8", "strict")
        found[path] = data
    if not found:
        raise SourceError("No committed GitHub Actions source evidence")
    return found


def closed_quoted_run(value: str) -> bool:
    """An unescaped matching closing quote is required for YAML quoted scalars."""
    if not value or value[0] not in ("'", '"'):
        return True
    quote = value[0]
    count = 0
    escaped = False
    for character in value:
        if character == quote and (quote == "'" or not escaped):
            count += 1
        if character == "\\" and not escaped:
            escaped = True
        else:
            escaped = False
    return count % 2 == 0


def extract_run_scripts(yaml: str) -> list[str]:
    """Scan literal run scalars; no full YAML semantic claim."""
    lines = yaml.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = SCALAR.match(line)
        i += 1
        if line.lstrip().startswith("#"):
            continue
        if ESCAPED_MAPPING_KEY.match(line) or EXPLICIT_RUN_KEY.match(line):
            blocks.append("*ambiguous-mapping-key")
        if FLOW_ESCAPED_MAPPING_KEY.search(line):
            blocks.append("*ambiguous-flow-mapping-key")
        # Compact flow maps are valid GitHub Actions YAML too, e.g.
        # steps: [{run: "curl -fsSL https://host/script | bash"}].
        # This conservative source heuristic deliberately does not claim
        # complete flow-YAML parsing.
        for flow in FLOW_RUN.finditer(line):
            blocks.append(flow.group("script"))
        if not m:
            continue
        raw = m.group("value").strip()
        if raw.startswith("#"):
            continue
        if raw.startswith(("|", ">")):
            indent = len(m.group("indent"))
            body = []
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                    break
                if nxt.strip() and not nxt.lstrip().startswith("#"):
                    body.append(nxt.strip())
                i += 1
            # Folded YAML (>) joins line breaks into spaces; literal (|) keeps them.
            blocks.append(" ".join(body) if raw.startswith(">") else "\n".join(body))
        elif raw:
            if raw.startswith(('"', "'")) and not closed_quoted_run(raw):
                # Quoted multiline YAML scalars fold continuation newlines to
                # spaces. A new remote-shell pipe must not evade a line reader.
                indent = len(m.group("indent"))
                parts = [raw]
                while i < len(lines) and not closed_quoted_run(" ".join(parts)):
                    continuation = lines[i]
                    if continuation.strip() and len(continuation) - len(continuation.lstrip()) <= indent:
                        raise SourceError("Unterminated quoted YAML run scalar")
                    parts.append(continuation.strip())
                    i += 1
                if not closed_quoted_run(" ".join(parts)):
                    raise SourceError("Unterminated quoted YAML run scalar")
                blocks.append(" ".join(parts))
            else:
                # A plain YAML run value terminates at a whitespace-prefixed
                # '#'; fully quoted run values preserve that character. Avoid
                # false source debt from a comment after a harmless command.
                if raw[0] in ('"', "'"):
                    blocks.append(raw)
                else:
                    # YAML folds indented continuations of a *plain* run scalar.
                    # A shell pipe split after curl's '|' must be scanned as
                    # the actual concatenated command, not only its first line.
                    indent = len(m.group("indent"))
                    parts = [raw.split(" #", 1)[0]]
                    while i < len(lines):
                        continuation = lines[i]
                        if not continuation.strip():
                            i += 1
                            continue
                        deeper = len(continuation) - len(continuation.lstrip())
                        if deeper <= indent:
                            break
                        if not continuation.lstrip().startswith("#"):
                            parts.append(continuation.strip().split(" #", 1)[0])
                        i += 1
                    blocks.append(" ".join(parts))
    return blocks


def findings(data: bytes) -> int:
    text_value = data.decode("utf-8", "strict")
    matched = 0
    for script in extract_run_scripts(text_value):
        command = re.sub(r"\\\s*\n\s*", " ", script)
        matched += len(REMOTE.findall(command)) + len(SUBSTITUTION.findall(command))
        matched += len(SHELL_COMMAND_SUBST.findall(command))
        matched += len(REMOTE_STDIN.findall(command))
        matched += len(DOWNLOAD_THEN_EXEC.findall(command))
        matched += len(REDIRECT_THEN_EXEC.findall(command))
        matched += len(REMOTE_VARIABLE_EVAL.findall(command))
        matched += len(POWERSHELL_PIPE_EVAL.findall(command))
        matched += len(POWERSHELL_INVOKE_EVAL.findall(command))
        matched += len(DOWNLOADED_DIRECT_EXEC.findall(command))
        matched += len(SHELL_EVAL_REMOTE.findall(command))
        matched += len(SHELL_SOURCE_PROCESS.findall(command))
        # YAML aliases/tags can hide a shell script outside the literal run scalar.
        # Never claim a new opaque run value is harmless on source evidence alone.
        matched += bool(OPAQUE_RUN.match(command))
        # Only double-quoted YAML scalars interpret these escapes. Literal
        # single-quoted YAML text is not silently upgraded to a shell pipe.
        matched += bool(command.startswith('"') and OPAQUE_QUOTED_ESCAPE.search(command))
    return matched


@dataclass(frozen=True)
class Result:
    policy: str
    baseline_findings: int
    candidate_findings: int
    new_risky_workflows: int
    source_complete: bool
    release_authorized: bool = False
    deployment_authorized: bool = False
    mutation_performed: bool = False


def audit(repo: Path, base: str, head: str) -> Result:
    before = committed_workflows(repo, base)
    after = committed_workflows(repo, head)
    git(repo, "merge-base", "--is-ancestor", base, head)
    head_bad = {p for p, v in after.items() if findings(v)}
    # Whole-workflow identity: relocating pre-existing code to elevated context
    # cannot reuse the old debt exception.
    delta = {p for p in head_bad if before.get(p) != after[p]}
    return Result("zcloud-remote-shell-no-new-debt-v1",
                  sum(findings(v) for v in before.values()),
                  sum(findings(v) for v in after.values()), len(delta), True)


def parse_operator_result(raw: str) -> object:
    """Strictly decode bounded scanner JSON: duplicates cannot rewrite safety fields."""
    if type(raw) is not str or len(raw.encode("utf-8")) > 8192:
        raise SourceError("Invalid source result size")
    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise SourceError("Duplicate result field")
            result[key] = value
        return result
    def reject_constant(_: str) -> object:
        raise SourceError("Nonfinite JSON result denied")
    try:
        return json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=reject_constant)
    except (ValueError, UnicodeError) as exc:
        raise SourceError("Untrustworthy JSON result") from exc


def operator_summary(report: object, exit_code: int) -> str:
    """Render only bounded numerical CI evidence; never a release decision."""
    if type(exit_code) is not int or exit_code not in (0, 1, 2):
        raise SourceError("Invalid scan exit status")
    if not isinstance(report, dict):
        raise SourceError("Invalid source result shape")
    expected = {
        "policy", "baseline_findings", "candidate_findings",
        "new_risky_workflows", "source_complete", "release_authorized",
        "deployment_authorized", "mutation_performed",
    }
    incomplete_required = {
        "policy", "new_risky_workflows", "source_complete",
        "release_authorized", "deployment_authorized", "mutation_performed",
    }
    if exit_code == 2:
        if (not incomplete_required.issubset(report)
                or not set(report).issubset(expected)):
            raise SourceError("Incomplete result schema invalid")
    elif set(report) != expected:
        raise SourceError("Complete result schema invalid")
    if report.get("policy") != "zcloud-remote-shell-no-new-debt-v1":
        raise SourceError("Unknown result policy")
    for authority in ("release_authorized", "deployment_authorized", "mutation_performed"):
        if report.get(authority) is not False:
            raise SourceError("Source evidence cannot authorize mutation")
    if exit_code == 2:
        if report.get("source_complete") is not False or report.get("new_risky_workflows") is not None:
            raise SourceError("Incomplete evidence falsely reported complete")
        if report.get("baseline_findings") is not None or report.get("candidate_findings") is not None:
            raise SourceError("Incomplete source cannot publish confident counts")
        return ("### zCloud broncontrole — Onvolledig bewijs\n\n"
                "Bronvergelijking niet afgerond. Geen productieautorisatie.\n")
    if report.get("source_complete") is not True:
        raise SourceError("Complete scan not proven")
    counters = tuple(report.get(k) for k in (
        "baseline_findings", "candidate_findings", "new_risky_workflows"
    ))
    if any(type(v) is not int or not 0 <= v <= 10_000_000 for v in counters):
        raise SourceError("Invalid or excessive source counters")
    baseline, candidate, new = counters
    if new > candidate or (exit_code == 0 and new != 0) or (exit_code == 1 and new == 0):
        raise SourceError("Contradictory scan outcome")
    status = "Geen nieuwe bekende shell-risico's" if exit_code == 0 else "Nieuw risico gedetecteerd"
    return (f"### zCloud broncontrole — {status}\n\n"
            f"Bestaande: {baseline}; kandidaat: {candidate}; nieuw: {new}.\n\n"
            "Geen productieautorisatie. Broncontrole is geen VPS- of releasebewijs.\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", default=".")
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    a = p.parse_args(argv)
    try:
        v = audit(Path(a.repo).resolve(), a.base, a.head)
    except (SourceError, OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        print(json.dumps({"policy": "zcloud-remote-shell-no-new-debt-v1",
                          "source_complete": False, "new_risky_workflows": None,
                          "release_authorized": False,
                          "deployment_authorized": False,
                          "mutation_performed": False}, sort_keys=True))
        return 2
    print(json.dumps(vars(v), sort_keys=True))
    return 1 if v.new_risky_workflows else 0


if __name__ == "__main__":
    raise SystemExit(main())
