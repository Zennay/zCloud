#!/usr/bin/env python3
"""One-command zSentinel cold-data archive shortlist for several workspaces.

Only bounded read-only metadata, per-workspace independent failure reporting,
create-only private output. No copy/delete or runner, queue, claim, DB mutation.
"""
from __future__ import annotations
import argparse
import json
from datetime import datetime,timezone
from html import escape
from pathlib import Path
from zsentinel_disk_hotspots import observe as scan
from zsentinel_archive_readiness import observe as archive, public_summary
from zsentinel_private_output import write_private_output

SCHEMA = "zsentinel-archive-portfolio-v1"
MAX_WORKSPACES = 4

def observe_many(workspaces: list[Path], target: Path, *,
                 min_age_days=7, max_entries=2000, max_depth=2, max_seconds=2):
    if (type(workspaces) is not list or not 1 <= len(workspaces) <= MAX_WORKSPACES
            or len({str(x) for x in workspaces}) != len(workspaces)
            or any(not isinstance(x, Path) or not x.is_absolute() for x in workspaces)
            or not isinstance(target, Path) or not target.is_absolute()
            or type(max_entries) is not int or not 1 <= max_entries <= 5000
            or type(max_depth) is not int or not 0 <= max_depth <= 3
            or type(max_seconds) is not int or not 1 <= max_seconds <= 5):
        raise ValueError("bounded explicit project workspaces required")
    rows = []
    for workspace in workspaces:
        try:
            report = scan(workspace, max_entries=max_entries,
                          max_depth=max_depth,max_seconds=max_seconds)
            owner_report = archive(report, target, min_age_days=min_age_days)
            public = public_summary(owner_report)
            rows.append({
                "workspace": str(workspace),
                "coverage": "partial" if report["coverage"] == "partial" else "sample_bounded",
                "source_observed_at_utc": report["observed_at_utc"],
                "status": "metadata_only",
                "source_entries_observed": report["scanned_entries"],
                "source_directories_unscanned": report["directories_unscanned"],
                "sample_large_file_candidates": report["large_files_seen_lower_bound"],
                "archive_candidate_hints": owner_report["candidate_count"],
                "archive_receipt": owner_report,
                "public_receipt": public,
            })
        except (ValueError, OSError, KeyError, TypeError, OverflowError):
            rows.append({
                "workspace": str(workspace),
                "status": "unavailable_or_refused",
                "coverage": "unavailable",
                "archive_receipt": None,
                "public_receipt": None,
                "archive_candidate_hints": None,
            })
    observed = [r for r in rows if r["status"] == "metadata_only"]
    return {
        "schema": SCHEMA,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "workspace_count": len(rows),
        "observed_workspace_count": len(observed),
        "unavailable_workspace_count": len(rows) - len(observed),
        "sampled_candidate_hints_lower_bound": sum(
            r["archive_candidate_hints"] for r in observed),
        "entries": rows,
        "coverage": "partial_by_design",
        "safe_archive_copy_authorized": False,
        "source_delete_authorized": False,
        "files_written_or_transferred": False,
        "runner_or_claim_modified": False,
        "archive_target_write_or_integrity_verified": False,
    }

def markdown(data):
    if (type(data) is not dict or data.get("schema") != SCHEMA
            or data.get("files_written_or_transferred") is not False
            or data.get("safe_archive_copy_authorized") is not False
            or type(data.get("entries")) is not list
            or not 1 <= len(data["entries"]) <= MAX_WORKSPACES):
        raise ValueError("invalid archive portfolio")
    lines = ["# zSentinel | portfolio naar extern archief", "",
             "Bronmetadata; GEEN archiveringen, kopieen, deletes of claims.",
             "Workspace-waarnemingen: " + str(data["observed_workspace_count"]) +
             "/" + str(data["workspace_count"]) +
             ". Verschillende bronvensters zijn geen atomaire snapshot.",
             "Metadata-only mogelijke oude data-bestanden in bekeken samples: " +
             str(data["sampled_candidate_hints_lower_bound"]) +
             ". Dit is geen veilige migratie-lijst of volledigheidsgarantie.", ""]
    for row in data["entries"]:
        lines.append("## " + row["workspace"].replace(chr(96), "'").replace("|","/"))
        if row["status"] == "metadata_only":
            from zsentinel_archive_readiness import markdown as render
            lines.append(render(row["archive_receipt"]))
        else:
            lines.append("Bron geweigerd of onbereikbaar; eigenaar moet mount/workspace controleren.")
    lines.extend(["", "Alle bronbestanden blijven ongewijzigd; "
                  "per workspace blijft schrijver, retentie, backup en datarecht onbekend.",
                  "Een bevoegde eigenaar regelt onafhankelijk gevalideerde kopie "
                  "en herstel alvorens afzonderlijk brondata op te ruimen."])
    return "\n".join(lines) + "\n"

def html(data):
    return (
        '<!doctype html><html lang="nl"><meta charset="utf-8">'
        '<meta name="referrer" content="no-referrer">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; '
        'style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>zSentinel portfolio archiefscan</title>'
        '<style>body{font:16px system-ui;background:#0b1220;color:#f8fafc;'
        'max-width:1000px;line-height:1.6;margin:2rem auto;padding:1rem}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;'
        'border:1px solid #64748b;border-radius:14px;padding:1.2rem}</style>'
        '<main><h1>Portfolio | archiefroute voor eigenaar</h1><pre>' +
        escape(markdown(data)) + '</pre></main></html>')

def public_summary_portfolio(data):
    if (data.get("schema") != SCHEMA or data.get("files_written_or_transferred") is not False
            or data.get("source_delete_authorized") is not False
            or type(data.get("entries")) is not list
            or not 1 <= len(data["entries"]) <= MAX_WORKSPACES):
        raise ValueError("invalid archive portfolio summary")
    return {
        "schema": "zsentinel-archive-portfolio-summary-v1",
        "sampled_workspaces": len(data["entries"]),
        "observed_workspaces": data["observed_workspace_count"],
        "unavailable_workspaces": data["unavailable_workspace_count"],
        "candidate_hints_not_approved": data["sampled_candidate_hints_lower_bound"],
        "private_workspace_names_disclosed": False,
        "source_disk_space_reclaimed": False,
        "files_copied_or_deleted": False,
        "source_coverage": "partial_by_design",
    }

def main():
    p=argparse.ArgumentParser(description="Bounded cross-project archive-readiness, no writes except explicit report")
    p.add_argument("--workspace",type=Path,required=True,action="append")
    p.add_argument("--archive-root",required=True,type=Path)
    p.add_argument("--min-age-days",type=int,default=7)
    p.add_argument("--max-entries",type=int,default=2000)
    p.add_argument("--max-depth",type=int,default=2)
    p.add_argument("--max-seconds",type=int,default=2)
    p.add_argument("--format",choices=("html","json","md"),default="html")
    p.add_argument("--output",required=True,type=Path)
    args=p.parse_args()
    try:
        data=observe_many(args.workspace,args.archive_root,min_age_days=args.min_age_days,
                          max_entries=args.max_entries,max_depth=args.max_depth,
                          max_seconds=args.max_seconds)
        output=(json.dumps(data,sort_keys=True,ensure_ascii=True,indent=2)
                if args.format=="json" else markdown(data)
                if args.format=="md" else html(data))
        write_private_output(args.output,output,args.format)
        print("Private portfolio archive review created; no project data moved or deleted.")
        return 0
    except (OSError,ValueError,TypeError,KeyError,OverflowError):
        p.exit(2,"zSentinel portfolio archive source unavailable; no changes made.\n")

if __name__=="__main__":
    raise SystemExit(main())
