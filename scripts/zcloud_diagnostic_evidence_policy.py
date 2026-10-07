#!/usr/bin/env python3
"""Guard heavy diagnostic evidence so new capture stays failure/manual gated."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

WORKFLOW_DIR = ".github/workflows"

COMMAND_PREFIX = r"^[ \t]*(?:run:[ \t]*)?(?:\([ \t]*)?"

HEAVY_PATTERNS = {
    "screenshot_capture": (
        re.compile(COMMAND_PREFIX + r"(?:sudo[ \t]+(?:-n[ \t]+)?)?(?:scrot|gnome-screenshot)(?:[ \t]|$)", re.I | re.M),
        re.compile(COMMAND_PREFIX + r"(?:sudo[ \t]+(?:-n[ \t]+)?)?import[ \t]+-(?:window|screen)\b", re.I | re.M),
        re.compile(r"(?i)\b(?:Page\.captureScreenshot|captureVisibleTab)\b"),
        re.compile(COMMAND_PREFIX + r"ffmpeg\b[^\n]*\bx11grab\b", re.I | re.M),
    ),
    "raw_journal_dump": (
        re.compile(COMMAND_PREFIX + r"(?:sudo[ \t]+-n[ \t]+)?journalctl\b", re.I | re.M),
    ),
    "service_status_dump": (
        re.compile(COMMAND_PREFIX + r"(?:sudo[ \t]+-n[ \t]+)?systemctl\b[^\n]*\bstatus\b", re.I | re.M),
    ),
    "process_commandline_dump": (
        re.compile(COMMAND_PREFIX + r"ps\b[^\n]*(?:\bcmd\b|\bargs\b)", re.I | re.M),
        re.compile(COMMAND_PREFIX + r"pgrep\b[^\n]*-[^\n]*a", re.I | re.M),
    ),
    "debug_archive": (
        re.compile(COMMAND_PREFIX + r"tar\b[^\n]*(?:\blog\b|\bdiag\b|/var/log)", re.I | re.M),
    ),
}

ERROR_GATE = re.compile(r"(?i)\b(?:failure|cancelled)\s*\(\s*\)")


@dataclass(frozen=True)
class Finding:
    path: str
    step: str
    kinds: tuple[str, ...]
    gate: str

    @property
    def signature(self) -> str:
        canonical = "|".join((self.path, self.step, ",".join(self.kinds)))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def bounded(self) -> dict:
        return {
            "path": self.path,
            "step": self.step[:120],
            "kinds": list(self.kinds),
            "gate": self.gate,
            "signature": self.signature,
        }


def workflow_events(text: str) -> set[str]:
    lines = text.splitlines()
    start = None
    inline = ""
    for index, line in enumerate(lines):
        match = re.match(r"^on:\s*(.*)$", line)
        if match:
            start = index
            inline = match.group(1).strip()
            break
    if start is None:
        return set()
    if inline:
        return {event for event in re.findall(r"[A-Za-z_]+", inline) if event != "on"}
    events: set[str] = set()
    for line in lines[start + 1 :]:
        if line and not line[0].isspace():
            break
        match = re.match(r"^\s{2}([A-Za-z_][A-Za-z0-9_-]*):", line)
        if match:
            events.add(match.group(1))
    return events


def manual_only(text: str) -> bool:
    events = workflow_events(text)
    return events == {"workflow_dispatch"}


def iter_step_blocks(text: str) -> Iterable[tuple[str, str]]:
    lines = text.splitlines()
    starts: list[tuple[int, int, str]] = []
    pattern = re.compile(r"^(?P<indent>\s*)-\s+(?P<key>name|run|uses|if):\s*(?P<value>.*)$")
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if not match or len(match.group("indent")) < 4:
            continue
        key = match.group("key")
        value = match.group("value").strip().strip("'\"")
        name = value if key == "name" and value else f"<unnamed@{index + 1}>"
        starts.append((index, len(match.group("indent")), name))

    for pos, (start, indent, name) in enumerate(starts):
        end = len(lines)
        for next_start, next_indent, _ in starts[pos + 1 :]:
            if next_indent <= indent:
                end = next_start
                break
        block = "\n".join(lines[start:end])
        yield name, block


def heavy_kinds(block: str) -> tuple[str, ...]:
    found = [
        kind
        for kind, patterns in HEAVY_PATTERNS.items()
        if any(pattern.search(block) for pattern in patterns)
    ]
    return tuple(sorted(found))


def scan_workflow(path: str, text: str) -> list[Finding]:
    is_manual_only = manual_only(text)
    findings: list[Finding] = []
    for step_name, block in iter_step_blocks(text):
        kinds = heavy_kinds(block)
        if not kinds:
            continue
        if ERROR_GATE.search(block):
            gate = "error_gated"
        elif is_manual_only:
            gate = "manual_only"
        else:
            gate = "ungated"
        findings.append(Finding(path=path, step=step_name, kinds=kinds, gate=gate))
    return findings


def scan_tree(root: Path) -> list[Finding]:
    workflows = root / WORKFLOW_DIR
    if not workflows.exists():
        return []
    findings: list[Finding] = []
    for path in sorted((*workflows.glob("*.yml"), *workflows.glob("*.yaml"))):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"unsafe workflow path: {path}")
        findings.extend(
            scan_workflow(path.relative_to(root).as_posix(), path.read_text(encoding="utf-8"))
        )
    return findings


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git command failed: {' '.join(args[:2])}")
    return proc.stdout


def scan_git_ref(repo: Path, ref: str) -> list[Finding]:
    names = _git(repo, "ls-tree", "-r", "--name-only", ref, "--", WORKFLOW_DIR)
    findings: list[Finding] = []
    for raw in names.splitlines():
        path = raw.strip()
        if not path.endswith((".yml", ".yaml")):
            continue
        text = _git(repo, "show", f"{ref}:{path}")
        findings.extend(scan_workflow(path, text))
    return findings


def ungated(findings: Iterable[Finding]) -> list[Finding]:
    return [finding for finding in findings if finding.gate == "ungated"]


def compare_findings(base: Iterable[Finding], head: Iterable[Finding]) -> list[Finding]:
    base_signatures = {finding.signature for finding in ungated(base)}
    return sorted(
        (finding for finding in ungated(head) if finding.signature not in base_signatures),
        key=lambda finding: finding.signature,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--base-ref")
    parser.add_argument("--head-ref")
    parser.add_argument("--fail-on-new-ungated", action="store_true")
    parser.add_argument("--fail-on-ungated", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if bool(args.base_ref) != bool(args.head_ref):
            raise RuntimeError("--base-ref and --head-ref must be supplied together")
        if args.base_ref:
            base = scan_git_ref(args.repo, args.base_ref)
            head = scan_git_ref(args.repo, args.head_ref)
            new_ungated = compare_findings(base, head)
            findings = head
            baseline_ungated = len(ungated(base))
        else:
            findings = scan_tree(args.repo)
            new_ungated = ungated(findings)
            baseline_ungated = 0
    except (OSError, UnicodeError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True))
        return 2

    payload = {
        "ok": not new_ungated,
        "workflow_findings": len(findings),
        "ungated": len(ungated(findings)),
        "baseline_ungated": baseline_ungated,
        "new_ungated": [item.bounded() for item in new_ungated],
        "policy": "heavy-diagnostics-must-be-error-gated-or-manual-only-v1",
    }
    if args.json:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        print(
            "ZCLOUD_DIAGNOSTIC_EVIDENCE_POLICY "
            f"findings={payload['workflow_findings']} ungated={payload['ungated']} "
            f"baseline_ungated={baseline_ungated} new_ungated={len(new_ungated)}"
        )
        for finding in new_ungated:
            print(
                "NEW_UNGATED "
                f"path={finding.path} step={finding.step[:120]!r} "
                f"kinds={','.join(finding.kinds)}"
            )

    if args.fail_on_new_ungated and new_ungated:
        return 1
    if args.fail_on_ungated and ungated(findings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
