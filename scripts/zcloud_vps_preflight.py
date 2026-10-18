#!/usr/bin/env python3
"""Read-only VPS deployment preflight. No service, Git, API or queue mutations."""
from __future__ import annotations

import argparse
from html import escape
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

RUNNER_UNIT = "actions.runner.Zennay-zCloud.zcloud-vps-1.service"


def command(*argv: str, cwd: Path | None = None, timeout: int = 8) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            env={**{key: value for key, value in os.environ.items() if key not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"}}, "GIT_OPTIONAL_LOCKS": "0"},
            check=False,
        )
        return completed.returncode, completed.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def meminfo() -> dict[str, int]:
    result = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, sep, rest = line.partition(":")
            if sep and key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                result[key] = int(rest.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return {}
    return result


def listener_pids() -> list[int]:
    """Only detect the dedicated zCloud Listener; never expose raw process arguments."""
    result = []
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return result
    for proc in entries:
        if not proc.name.isdigit():
            continue
        try:
            if (proc / "comm").read_text().strip() != "Runner.Listener":
                continue
            argv = (proc / "cmdline").read_bytes().split(b"\0")
            program = argv[0].decode("utf-8", "replace").lower() if argv else ""
            if ("zcloud" in program or "zcloud-vps-1" in program) and program.endswith("/runner.listener"):
                result.append(int(proc.name))
        except (OSError, ValueError):
            continue
    return sorted(result)


def service_state() -> str:
    for prefix in (("systemctl", "--user"), ("systemctl",)):
        rc, output = command(*prefix, "show", RUNNER_UNIT, "--property=ActiveState", "--value", timeout=4)
        if rc == 0 and output in {"active", "inactive", "failed", "activating", "deactivating"}:
            return output
    return "unavailable"


def actionable_recovery(findings: list[dict[str, str]]) -> list[dict[str, str]]:
    """Human-readable, non-authorizing recovery ordering; never executes the actions."""
    codes = {entry["code"] for entry in findings}
    recommendations: list[dict[str, str]] = []

    def recommend(code: str, owner: str, why: str, action: str) -> None:
        recommendations.append({
            "code": code, "owner": owner, "reason": why, "next_action": action,
            "changes_host": "no", "authorization_required": "yes",
        })

    if codes & {"LOW_DISK", "SWAP_EXHAUSTED", "LOW_AVAILABLE_MEMORY", "MEMORY_PRESSURE"}:
        recommend("CAPACITY_FIRST", "VPS resource owner",
                  "Insufficient resource headroom can cause repeat OOM crashes or incomplete releases.",
                  "Inspect disk/cache ownership and memory pressure read-only; agree on safe cleanup or resizing before a controlled runner recovery. Never automatically delete or clear swap.")
    if codes & {"ZCLOUD_RUNNER_MISSING", "RUNNER_UNIT_NOT_ACTIVE", "RUNNER_UNIT_UNOBSERVABLE"}:
        recommend("RUNNER_OWNER", "zCloud Actions runner owner (#1326)",
                  "The dedicated listener/registered runner may be unavailable independently of other project runners.",
                  "Inspect the dedicated service and assigned jobs with the authorized runner owner. Restore only under the existing idle and account-identity gates; independently confirm a job was assigned.")
    if codes & {"TRACKED_CHANGES", "UNTRACKED_FILES", "NOT_MAIN", "CHECKOUT_STATUS_UNAVAILABLE"}:
        recommend("WORKTREE_RECONCILE", "zCloud production checkout owner",
                  "Uncommitted work and untracked owner files must not be replaced by a deployment or pull.",
                  "Identify current active edits and ownership; reconcile via the existing serialized release process. Never reset, clean or force-pull this checkout.")
    if codes & {"MAIN_SHA_MISMATCH", "REMOTE_MAIN_UNBOUND", "CHECKOUT_UNAVAILABLE"}:
        recommend("SOURCE_PROVENANCE", "zCloud release owner",
                  "Exact remote-main provenance has not been established for this host.",
                  "Obtain a fresh independent protected-main SHA and compare. Require existing exact-head CI, review and release admission before deployment.")
    if not recommendations:
        recommend("EXTERNAL_RELEASE_GATES", "zCloud release owner",
                  "A local read-only preflight cannot authorize a production release.",
                  "Verify GitHub branch protection, exact-head CI, independent review, serialized writer lock, runner assignment and live acceptance separately.")
    return recommendations


def measure(root: Path, expected_main_sha: str | None = None) -> dict:
    findings: list[dict[str, str]] = []
    def mark(code: str, severity: str, detail: str) -> None:
        findings.append({"code": code, "severity": severity, "detail": detail})

    git_rc, branch = command("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=root)
    sha_rc, sha = command("git", "rev-parse", "HEAD", cwd=root)
    if git_rc or sha_rc or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        mark("CHECKOUT_UNAVAILABLE", "block", "Cannot establish local Git checkout identity.")
        branch, sha = "unknown", None
    if expected_main_sha is None:
        mark("REMOTE_MAIN_UNBOUND", "warn", "No independently verified current-main SHA supplied.")
    elif sha != expected_main_sha:
        mark("MAIN_SHA_MISMATCH", "block", "Local checkout HEAD differs from supplied main SHA.")
    status_rc, tracked = command("git", "status", "--porcelain=v1", "--untracked-files=no", cwd=root)
    untracked_rc, untracked = command("git", "ls-files", "--others", "--exclude-standard", cwd=root)
    tracked_count = len(tracked.splitlines()) if status_rc == 0 and tracked else 0
    untracked_count = len(untracked.splitlines()) if untracked_rc == 0 and untracked else 0
    if status_rc != 0 or untracked_rc != 0:
        mark("CHECKOUT_STATUS_UNAVAILABLE", "block", "Cannot prove the checkout is clean.")
    if tracked_count:
        mark("TRACKED_CHANGES", "block", f"{tracked_count} tracked checkout entries changed; preserve owner edits.")
    if untracked_count:
        mark("UNTRACKED_FILES", "block", f"{untracked_count} untracked checkout entries present; do not overwrite.")
    if branch != "main":
        mark("NOT_MAIN", "block", "Live checkout is not on main.")

    pids = listener_pids()
    unit = service_state()
    if not pids:
        mark("ZCLOUD_RUNNER_MISSING", "block", "Dedicated zCloud Runner.Listener not observed.")
    if unit in {"failed", "inactive"}:
        mark("RUNNER_UNIT_NOT_ACTIVE", "block", f"zCloud runner unit state: {unit}.")
    elif unit == "unavailable":
        mark("RUNNER_UNIT_UNOBSERVABLE", "warn", "Unit identity not readable from this context.")

    memory = meminfo()
    if {"MemTotal", "MemAvailable"} <= memory.keys():
        available_pct = round(100 * memory["MemAvailable"] / max(memory["MemTotal"], 1), 1)
        if available_pct < 10:
            mark("LOW_AVAILABLE_MEMORY", "block", "Available RAM below 10%.")
        elif available_pct < 20:
            mark("MEMORY_PRESSURE", "warn", "Available RAM below 20%.")
    else:
        available_pct = None
        mark("MEMORY_UNOBSERVABLE", "warn", "Cannot read memory accounting.")

    if memory.get("SwapTotal", 0):
        swap_pct = round(100 * (1 - memory.get("SwapFree", 0) / memory["SwapTotal"]), 1)
        if swap_pct >= 95:
            mark("SWAP_EXHAUSTED", "block", "Swap utilisation >=95%.")
    else:
        swap_pct = None

    try:
        volume = os.statvfs(root)
        disk_free_pct = round(100 * volume.f_bavail / max(volume.f_blocks, 1), 1)
        if disk_free_pct < 10:
            mark("LOW_DISK", "block", "Filesystem free space below 10%.")
    except OSError:
        disk_free_pct = None
        mark("DISK_UNOBSERVABLE", "warn", "Cannot read filesystem space.")

    blocked = any(f["severity"] == "block" for f in findings)
    recovery = actionable_recovery(findings)
    return {
        "schema": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only", "release_authorized": False,
        "result": "blocked" if blocked else ("partial" if findings else "preflight_clear"),
        "checkout": {"branch": branch, "sha": sha, "expected_main_sha": expected_main_sha, "tracked_changes": tracked_count,
                     "untracked_entries": untracked_count},
        "runner": {"unit": RUNNER_UNIT, "unit_state": unit, "listener_pids": pids},
        "resources": {"available_ram_pct": available_pct, "swap_used_pct": swap_pct,
                      "disk_free_pct": disk_free_pct},
        "findings": findings,
        "recovery_priorities": recovery,
        "next_step": "Obtain existing owner/release approval; never use this report as deploy authorization.",
    }


def compare_snapshot(path: Path, current: dict) -> dict:
    """Compare code sets only; never trust free-text content from an older report."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 131072:
            raise ValueError("invalid snapshot")
        prior = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(prior, dict) or prior.get("schema") != 1
                or prior.get("mode") != "read_only"
                or prior.get("release_authorized") is not False):
            raise ValueError("invalid snapshot")
        observed_at = prior.get("observed_at")
        if not isinstance(observed_at, str) or len(observed_at) > 64:
            raise ValueError("invalid snapshot")
        previous_time = datetime.fromisoformat(observed_at)
        current_time = datetime.fromisoformat(current["observed_at"])
        if previous_time.tzinfo is None or previous_time.utcoffset() is None or previous_time > current_time:
            raise ValueError("invalid snapshot")
        rows = prior.get("findings")
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("invalid snapshot")
        old: dict[str, str] = {}
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("code"), str)
                    or not re.fullmatch(r"[A-Z0-9_]{3,64}", row["code"])
                    or row["code"] in old
                    or row.get("severity") not in {"block", "warn"}):
                raise ValueError("invalid snapshot")
            old[row["code"]] = row["severity"]
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        raise ValueError("Previous report is not a supported bounded zCloud read-only snapshot.") from None
    new = {item["code"]: item["severity"] for item in current["findings"]}
    changed = sorted(set(old) & set(new))
    return {
        "new_findings": sorted(set(new) - set(old)),
        "resolved_findings": sorted(set(old) - set(new)),
        "still_present": changed,
        "severity_changes": [
            {"code": code, "previous": old[code], "current": new[code]}
            for code in changed if old[code] != new[code]
        ],
        "unchanged": old == new,
        "source": "local_operator_supplied_snapshot",
    }


def render_html(report: dict) -> str:
    """Self-contained, escaped, offline operator view; never embeds raw process arguments."""
    esc = lambda value: escape(str(value), quote=True)
    state = report["result"]
    label = {"blocked": "Deployment blocked", "partial": "Incomplete evidence",
             "preflight_clear": "Local preflight clear"}.get(state, "Unverified")
    counts = {"block": 0, "warn": 0}
    for finding in report["findings"]:
        if finding["severity"] in counts:
            counts[finding["severity"]] += 1

    def item(text: str, value: object) -> str:
        return f"<dt>{esc(text)}</dt><dd>{esc(value)}</dd>"

    findings = "".join(
        f'<li class="finding"><strong>{esc(f["code"])}</strong>'
        f' <span class="{esc(f["severity"])}">{esc(f["severity"].upper())}</span>'
        f'<p>{esc(f["detail"])}</p></li>'
        for f in report["findings"]
    ) or "<li>No local findings recorded. This does not authorize a release.</li>"
    actions = "".join(
        f'<li><strong>{esc(a["code"])}</strong><p class="owner">Owner: {esc(a["owner"])}</p>'
        f'<p>{esc(a["reason"])}</p><p>{esc(a["next_action"])}</p></li>'
        for a in report["recovery_priorities"]
    )
    comparison = report.get("comparison")
    if comparison is None:
        comparison_html = ""
    else:
        def shown(values: list[str]) -> str:
            return ", ".join(esc(x) for x in values) or "None"
        comparison_html = (
            '<section aria-labelledby="diff"><h2 id="diff">Changes since previous report</h2>'
            f'<dl>{item("New findings", shown(comparison["new_findings"]))}'
            f'{item("Resolved findings", shown(comparison["resolved_findings"]))}'
            f'{item("Still present", shown(comparison["still_present"]))}'
            f'{item("Severity changed", shown([x["code"] + ": " + x["previous"] + " → " + x["current"] for x in comparison["severity_changes"]]))}</dl>'
            '<p class="muted">Previous report was locally supplied and not independent runtime attestation.</p></section>'
        )
    checkout = report["checkout"]
    runner = report["runner"]
    resources = report["resources"]
    def val(value: object) -> str:
        return "Not measured" if value is None else str(value)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>zCloud VPS release preflight</title>
<style>
:root {{font: 16px/1.5 system-ui,-apple-system,BlinkMacSystemFont,sans-serif;color:#1f2937;background:#f6f7f9}}
*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:960px;margin:auto;padding:clamp(16px,4vw,42px)}}
header{{border-bottom:1px solid #d6dbe1;padding-bottom:22px}}h1{{font-size:clamp(25px,3vw,38px);line-height:1.15;margin:0 0 10px}}
h2{{font-size:19px;margin:0 0 14px}}p{{margin:6px 0}}.muted{{color:#52606d}}
section{{background:white;margin:20px 0;padding:22px;border:1px solid #e0e5ea;border-radius:12px}}
dl{{display:grid;grid-template-columns:minmax(125px,1fr) minmax(0,2fr);gap:8px 16px;margin:0}}
dt{{color:#52606d}}dd{{margin:0;overflow-wrap:anywhere;font-weight:600}}
ol,ul{{padding-left:22px;margin:0}}li{{margin:0 0 14px}}li:last-child{{margin-bottom:0}}
.finding p{{color:#52606d}}.block{{color:#ae2626}}.warn{{color:#8a6100}}.owner{{font-weight:650}}
.status{{font-size:20px;font-weight:750}}.summary{{display:flex;gap:16px;flex-wrap:wrap}}.summary span{{padding-right:16px}}
.disclaimer{{border-left:4px solid #45556a;padding:12px 16px;background:#eef1f5;border-radius:4px}}
@media(max-width:520px){{dl{{grid-template-columns:1fr;gap:0}}dd{{margin-bottom:12px}}section{{padding:16px}}}}
@media print{{:root{{background:white}}main{{padding:10px}}section{{break-inside:avoid}}}}
</style></head><body><main>
<header><h1>zCloud VPS deployment preflight</h1>
<p class="status">{esc(label)}</p><div class="summary"><span>{counts["block"]} blockers</span>
<span>{counts["warn"]} warnings</span><span>{esc(report["observed_at"])}</span></div>
<p class="muted">Offline, read-only observation. Production release authorization: NO.</p></header>
<section aria-labelledby="overview"><h2 id="overview">Host snapshot</h2><dl>
{item("Checked-out branch", checkout["branch"])}
{item("Local commit SHA", val(checkout["sha"]))}
{item("Expected main SHA", val(checkout["expected_main_sha"]))}
{item("Changed tracked entries", checkout["tracked_changes"])}
{item("Untracked entries", checkout["untracked_entries"])}
{item("Dedicated runner unit", runner["unit"])}
{item("Unit state", runner["unit_state"])}
{item("Dedicated listener count", len(runner["listener_pids"]))}
{item("Available RAM", val(resources["available_ram_pct"]) + "%")}
{item("Swap used", val(resources["swap_used_pct"]) + "%")}
{item("Filesystem free", val(resources["disk_free_pct"]) + "%")}
</dl></section>
{comparison_html}
<section aria-labelledby="findings"><h2 id="findings">Release blockers and warnings</h2><ul>{findings}</ul></section>
<section aria-labelledby="recovery"><h2 id="recovery">Owner-gated recovery priorities</h2><ol>{actions}</ol></section>
<p class="disclaimer"><strong>Not an approval.</strong> This report cannot prove protected-main review,
exact-head CI, GitHub runner registration, active leases or production readiness. Follow the
existing #1326 runner and #580/#1089 serialized release gates. No recovery action was executed.</p>
</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/home/ubuntu/zennay-cloud"))
    parser.add_argument("--json", action="store_true", help="Print bounded machine-readable evidence")
    parser.add_argument("--previous-report", type=Path, default=None,
                        help="Compare with a saved bounded JSON report; never modifies the prior file")
    parser.add_argument("--format", choices=("text", "json", "html"), default="text",
                        help="Output as console text, machine-readable JSON, or self-contained offline HTML")
    parser.add_argument("--expected-main-sha", default=None,
                        help="Exact independently fetched 40-character main commit SHA (no network requests)")
    args = parser.parse_args()
    if args.expected_main_sha and not re.fullmatch(r"[0-9a-f]{40}", args.expected_main_sha):
        parser.error("--expected-main-sha must be a lowercase 40-character Git SHA")
    report = measure(args.root.resolve(), args.expected_main_sha)
    if args.previous_report is not None:
        try:
            report["comparison"] = compare_snapshot(args.previous_report, report)
        except ValueError as exc:
            print(f"PREVIOUS_REPORT_INVALID: {exc}", file=sys.stderr)
            return 2
    if args.json or args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.format == "html":
        print(render_html(report))
    else:
        print(f"zCloud VPS preflight: {report['result'].upper()} (read-only; NOT a release authorization)")
        c = report["checkout"]
        print(f"Checkout: {c['branch']} {c['sha'] or 'unknown'}; tracked={c['tracked_changes']} untracked={c['untracked_entries']}")
        r = report["runner"]
        print(f"Runner: {r['unit_state']}; dedicated listener PIDs: {r['listener_pids']}")
        for f in report["findings"]:
            print(f"[{f['severity'].upper()}] {f['code']}: {f['detail']}")
        if "comparison" in report:
            change = report["comparison"]
            print(f"New findings: {', '.join(change['new_findings']) or 'none'}")
            print(f"Resolved findings: {', '.join(change['resolved_findings']) or 'none'}")
            print(f"Still present: {', '.join(change['still_present']) or 'none'}")
            for entry in change["severity_changes"]:
                print(f"Severity changed: {entry['code']} {entry['previous']} -> {entry['current']}")
        print("Owner-gated recovery priorities:")
        for index, item in enumerate(report["recovery_priorities"], 1):
            print(f"  {index}. {item['code']} [{item['owner']}]: {item['next_action']}")
        print(report["next_step"])
    return 2 if report["result"] == "blocked" else (1 if report["result"] == "partial" else 0)


if __name__ == "__main__":
    sys.exit(main())
