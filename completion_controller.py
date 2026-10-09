"""Worker Completion Controller.

Classifies open GitHub pull-request / branch work per project, prefers
finishing that work over starting new roadmap work, and tracks the
evidence-backed completion metrics (commits, terminal CI results, merges,
deploys, stagnation) that the portfolio dashboard surfaces.

This module is intentionally I/O-light and GitHub-API-free: it classifies
whatever PR/check-run payloads a caller hands it (a GitHub Actions step with
the repo's own GITHUB_TOKEN, a worker preflight, a CLI backfill) and persists
the result in the shared SQLite database. Keeping GitHub calls out of this
module keeps classification unit-testable without a network and keeps a
single place that owns the "is this safe to merge / should a worker pick
this up next" decision, instead of duplicating that judgement per caller.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3

_TERMINAL_FAILURE_CONCLUSIONS = {"failure", "timed_out", "action_required", "startup_failure"}
_TERMINAL_OK_CONCLUSIONS = {"success", "neutral", "skipped"}
_NON_TERMINAL_STATUSES = {"queued", "in_progress", "pending", "waiting", "requested"}

# Phrases a worker or reviewer used in a PR body/title to say "do not merge
# this yet" in plain prose, independent of any label or status field. Several
# real zCloud PRs (e.g. #580) only express this in the description. Treated
# as a hard auto-merge veto regardless of what checks/mergeable_state say.
_DO_NOT_MERGE_MARKERS = (
    "do not merge",
    "don't merge",
    "keep draft",
    "not ready to merge",
    "niet mergen",
    "niet klaar om te mergen",
)

# Labels a classified PR can carry. "stale" and "conflicting" and
# "ci_failing" and "needs_review" and "draft_in_progress" are all forms of
# *unfinished work* a worker should resume before opening anything new.
# "mergeable" is terminal-ready; "superseded" is a human/worker judgement
# call surfaced for review, never auto-closed by this module.
UNFINISHED_LABELS = {
    "conflicting",
    "ci_failing",
    "needs_review",
    "draft_in_progress",
    "stale",
    "possible_duplicate",
}

_MAX_EVIDENCE_BYTES = 12000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def init_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS pr_state(
            project_id TEXT NOT NULL,
            repo TEXT NOT NULL,
            pr_number INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            html_url TEXT NOT NULL DEFAULT '',
            head_sha TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            auto_merge_eligible INTEGER NOT NULL DEFAULT 0,
            draft INTEGER NOT NULL DEFAULT 0,
            mergeable_state TEXT NOT NULL DEFAULT '',
            pr_updated_at TEXT NOT NULL DEFAULT '',
            checked_at TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(project_id,repo,pr_number)
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS pr_state_project_label ON pr_state(project_id,label,checked_at)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS completion_metrics(
            project_id TEXT PRIMARY KEY,
            tasks_started INTEGER NOT NULL DEFAULT 0,
            tasks_completed INTEGER NOT NULL DEFAULT 0,
            commits_total INTEGER NOT NULL DEFAULT 0,
            ci_success_total INTEGER NOT NULL DEFAULT 0,
            ci_failure_total INTEGER NOT NULL DEFAULT 0,
            ci_cancelled_total INTEGER NOT NULL DEFAULT 0,
            merges_total INTEGER NOT NULL DEFAULT 0,
            deploys_total INTEGER NOT NULL DEFAULT 0,
            last_material_progress_at TEXT,
            updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS completion_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS completion_events_project_ts ON completion_events(project_id,created_at)"
    )


# ---------------------------------------------------------------------------
# Classification (pure; no I/O)
# ---------------------------------------------------------------------------

def _checks_summary(checks: list[dict] | None) -> dict:
    checks = checks or []
    total = len(checks)
    pending = 0
    failing = 0
    ok = 0
    for check in checks:
        status = str(check.get("status") or "").lower()
        conclusion = str(check.get("conclusion") or "").lower()
        if status in _NON_TERMINAL_STATUSES or not conclusion:
            pending += 1
        elif conclusion in _TERMINAL_FAILURE_CONCLUSIONS:
            failing += 1
        elif conclusion in _TERMINAL_OK_CONCLUSIONS:
            ok += 1
        else:
            # Unknown conclusion string: treat conservatively as pending
            # rather than silently counting it as green.
            pending += 1
    return {"total": total, "pending": pending, "failing": failing, "ok": ok}


def _has_do_not_merge_marker(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _DO_NOT_MERGE_MARKERS)


def classify_pr(pr: dict, checks: list[dict] | None = None, *, stale_after_seconds: int = 7 * 24 * 3600, now_value: str | None = None) -> dict:
    """Classify a single PR payload (GitHub REST pull-request shape) plus its
    check-runs into one label with a human reason and an auto_merge_eligible
    bit. Pure function: same inputs always produce the same classification,
    so this is fully unit-testable without a GitHub token.
    """
    draft = bool(pr.get("draft"))
    mergeable_state = str(pr.get("mergeable_state") or "unknown").lower()
    body = str(pr.get("body") or "")
    title = str(pr.get("title") or "")
    do_not_merge = _has_do_not_merge_marker(body) or _has_do_not_merge_marker(title)
    summary = _checks_summary(checks)

    reference = _parse_dt(now_value) or datetime.now(timezone.utc)
    updated = _parse_dt(pr.get("updated_at")) or reference
    age_seconds = max(0, int((reference - updated).total_seconds()))
    is_stale = age_seconds > max(3600, int(stale_after_seconds))

    requested_reviewers = pr.get("requested_reviewers") or []
    review_decision = str(pr.get("review_decision") or "").upper()
    changes_requested = review_decision == "CHANGES_REQUESTED"

    label = "mergeable"
    reason = "all signals green"

    if mergeable_state == "dirty":
        label, reason = "conflicting", "merge conflicts with base branch"
    elif changes_requested:
        label, reason = "needs_review", "a reviewer requested changes"
    elif summary["failing"] > 0:
        label, reason = "ci_failing", f"{summary['failing']} check run(s) concluded failure/timed_out"
    elif draft:
        label, reason = "draft_in_progress", "pull request is still a draft"
    elif summary["pending"] > 0:
        label, reason = "ci_failing", f"{summary['pending']} check run(s) still pending"
    elif mergeable_state in ("blocked",):
        label, reason = "needs_review", "blocked: required review or status check outstanding"
    elif requested_reviewers and not review_decision:
        label, reason = "needs_review", "review requested, not yet decided"

    if is_stale and label not in ("conflicting",):
        # Staleness is the stronger signal for "a worker should look at this
        # before opening something new" even when CI happens to be green,
        # because green CI against a week-old head is not current evidence.
        label, reason = "stale", f"no update in {age_seconds // 3600}h (base reclassified as stale)"

    auto_merge_eligible = (
        label == "mergeable"
        and mergeable_state == "clean"
        and not draft
        and not do_not_merge
        and summary["failing"] == 0
        and summary["pending"] == 0
        and summary["total"] > 0
        and not changes_requested
    )

    return {
        "label": label,
        "reason": reason,
        "auto_merge_eligible": bool(auto_merge_eligible),
        "do_not_merge_marker": bool(do_not_merge),
        "checks": summary,
        "age_seconds": age_seconds,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _serialize_evidence(evidence: dict | None) -> str:
    payload = json.dumps(evidence or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(payload.encode("utf-8")) > _MAX_EVIDENCE_BYTES:
        payload = json.dumps({"truncated": True}, separators=(",", ":"))
    return payload


def record_pr_state(
    connection: sqlite3.Connection,
    project_id: str,
    repo: str,
    pr_number: int,
    pr: dict,
    classification: dict,
    *,
    checked_at: str | None = None,
) -> dict:
    checked_at = checked_at or _now()
    connection.execute(
        """INSERT INTO pr_state(
            project_id,repo,pr_number,title,html_url,head_sha,label,reason,
            auto_merge_eligible,draft,mergeable_state,pr_updated_at,checked_at,evidence_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(project_id,repo,pr_number) DO UPDATE SET
            title=excluded.title,html_url=excluded.html_url,head_sha=excluded.head_sha,
            label=excluded.label,reason=excluded.reason,auto_merge_eligible=excluded.auto_merge_eligible,
            draft=excluded.draft,mergeable_state=excluded.mergeable_state,
            pr_updated_at=excluded.pr_updated_at,checked_at=excluded.checked_at,
            evidence_json=excluded.evidence_json""",
        (
            str(project_id), str(repo), int(pr_number),
            str(pr.get("title") or "")[:300], str(pr.get("html_url") or "")[:400],
            str(pr.get("head", {}).get("sha") or pr.get("head_sha") or "")[:80],
            str(classification["label"]), str(classification.get("reason") or "")[:500],
            1 if classification.get("auto_merge_eligible") else 0,
            1 if pr.get("draft") else 0,
            str(pr.get("mergeable_state") or "")[:40],
            str(pr.get("updated_at") or ""),
            checked_at,
            _serialize_evidence({"pr": {k: pr.get(k) for k in ("number", "state", "updated_at")}, "classification": classification}),
        ),
    )
    row = connection.execute(
        "SELECT * FROM pr_state WHERE project_id=? AND repo=? AND pr_number=?",
        (str(project_id), str(repo), int(pr_number)),
    ).fetchone()
    return dict(row)


def drop_missing_pr_state(connection: sqlite3.Connection, project_id: str, repo: str, open_pr_numbers) -> int:
    """Remove rows for PRs that are no longer open (merged/closed elsewhere)
    so a stale 'conflicting' row does not keep blocking new work forever."""
    open_pr_numbers = {int(n) for n in open_pr_numbers}
    rows = connection.execute(
        "SELECT pr_number FROM pr_state WHERE project_id=? AND repo=?", (str(project_id), str(repo))
    ).fetchall()
    to_delete = [int(row["pr_number"]) for row in rows if int(row["pr_number"]) not in open_pr_numbers]
    for pr_number in to_delete:
        connection.execute(
            "DELETE FROM pr_state WHERE project_id=? AND repo=? AND pr_number=?",
            (str(project_id), str(repo), pr_number),
        )
    return len(to_delete)


def open_unfinished_work(connection: sqlite3.Connection, project_id: str) -> list[dict]:
    """Return classified PRs that represent unfinished work for this project,
    highest-priority-to-resume first: conflicting/ci_failing/needs_review
    ahead of draft_in_progress ahead of stale, since those are closer to
    done and a worker can plausibly land them quickly."""
    order = {"conflicting": 0, "ci_failing": 1, "needs_review": 2, "draft_in_progress": 3, "possible_duplicate": 4, "stale": 5}
    rows = connection.execute(
        "SELECT * FROM pr_state WHERE project_id=? AND label IN ({})".format(
            ",".join("?" for _ in UNFINISHED_LABELS)
        ),
        (str(project_id), *sorted(UNFINISHED_LABELS)),
    ).fetchall()
    items = [dict(row) for row in rows]
    items.sort(key=lambda item: (order.get(item["label"], 9), item["checked_at"]))
    return items


def mergeable_prs(connection: sqlite3.Connection, project_id: str | None = None) -> list[dict]:
    if project_id:
        rows = connection.execute(
            "SELECT * FROM pr_state WHERE project_id=? AND label='mergeable' AND auto_merge_eligible=1",
            (str(project_id),),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM pr_state WHERE label='mergeable' AND auto_merge_eligible=1"
        ).fetchall()
    return [dict(row) for row in rows]


def completion_queue_criteria(project_name: str, pr_row: dict) -> str:
    """Build a portfolio_queue completion_criteria string that tells a worker
    to finish a specific, already-open PR instead of starting fresh work."""
    label = pr_row.get("label")
    pr_number = pr_row.get("pr_number")
    reason = pr_row.get("reason") or ""
    html_url = pr_row.get("html_url") or ""
    base = (
        f"Finish existing {project_name} pull request #{pr_number} ({html_url}) before starting any new "
        f"roadmap work. Current classification: {label} — {reason}. "
    )
    if label == "conflicting":
        base += "Rebase or merge the base branch into this PR's branch, resolve every conflict, and push a green head."
    elif label == "ci_failing":
        base += "Diagnose the failing/pending checks on the current head and push a fix; do not open a parallel PR for the same change."
    elif label == "needs_review":
        base += "Address the requested review changes (or outstanding required check) on this PR's existing branch."
    elif label == "draft_in_progress":
        base += "Bring this draft to a genuinely mergeable state (tests green, no TODOs) or explicitly mark in the PR why it must stay draft, then mark the underlying queue item done."
    elif label == "possible_duplicate":
        base += "Confirm whether this duplicates other open work; if so close the redundant one with a one-line explanation, otherwise continue it."
    else:
        base += "Investigate why this PR has been inactive and either land it or close it with a reason."
    base += " Do not create a new pull request for this task; keep working on the existing branch."
    return base


# ---------------------------------------------------------------------------
# Progress / completion metrics
# ---------------------------------------------------------------------------

_EVENT_COUNTER_COLUMN = {
    "commit": "commits_total",
    "ci_success": "ci_success_total",
    "ci_failure": "ci_failure_total",
    "ci_cancelled": "ci_cancelled_total",
    "merge": "merges_total",
    "deploy": "deploys_total",
}

_MATERIAL_PROGRESS_KINDS = {"commit", "ci_success", "merge", "deploy"}


def _ensure_metrics_row(connection: sqlite3.Connection, project_id: str) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO completion_metrics(project_id,updated_at) VALUES(?,?)",
        (str(project_id), _now()),
    )


def record_task_started(connection: sqlite3.Connection, project_id: str) -> None:
    _ensure_metrics_row(connection, project_id)
    connection.execute(
        "UPDATE completion_metrics SET tasks_started=tasks_started+1,updated_at=? WHERE project_id=?",
        (_now(), str(project_id)),
    )


def record_task_completed(connection: sqlite3.Connection, project_id: str) -> None:
    _ensure_metrics_row(connection, project_id)
    connection.execute(
        "UPDATE completion_metrics SET tasks_completed=tasks_completed+1,updated_at=? WHERE project_id=?",
        (_now(), str(project_id)),
    )


def record_progress_event(
    connection: sqlite3.Connection,
    project_id: str,
    kind: str,
    *,
    detail: str = "",
    source_url: str = "",
    amount: int = 1,
) -> dict:
    if kind not in _EVENT_COUNTER_COLUMN:
        raise ValueError(f"unknown completion event kind {kind!r}")
    amount = max(1, int(amount))
    _ensure_metrics_row(connection, project_id)
    column = _EVENT_COUNTER_COLUMN[kind]
    ts = _now()
    connection.execute(
        f"UPDATE completion_metrics SET {column}={column}+?,updated_at=? WHERE project_id=?",
        (amount, ts, str(project_id)),
    )
    if kind in _MATERIAL_PROGRESS_KINDS:
        connection.execute(
            "UPDATE completion_metrics SET last_material_progress_at=? WHERE project_id=?",
            (ts, str(project_id)),
        )
    connection.execute(
        "INSERT INTO completion_events(project_id,kind,detail,source_url,created_at) VALUES(?,?,?,?,?)",
        (str(project_id), kind, str(detail or "")[:500], str(source_url or "")[:400], ts),
    )
    row = connection.execute(
        "SELECT * FROM completion_metrics WHERE project_id=?", (str(project_id),)
    ).fetchone()
    return dict(row)


def material_completion_rate(connection: sqlite3.Connection, project_id: str | None = None) -> dict:
    if project_id:
        rows = connection.execute(
            "SELECT * FROM completion_metrics WHERE project_id=?", (str(project_id),)
        ).fetchall()
    else:
        rows = connection.execute("SELECT * FROM completion_metrics").fetchall()
    started = sum(int(row["tasks_started"] or 0) for row in rows)
    completed = sum(int(row["tasks_completed"] or 0) for row in rows)
    rate = (completed / started) if started else None
    return {
        "project_id": project_id,
        "tasks_started": started,
        "tasks_completed": completed,
        "material_completion_rate": rate,
    }


def stagnation_report(connection: sqlite3.Connection, threshold_seconds: int = 24 * 3600, now_value: str | None = None) -> list[dict]:
    reference = _parse_dt(now_value) or datetime.now(timezone.utc)
    rows = connection.execute("SELECT * FROM completion_metrics").fetchall()
    stagnant = []
    for row in rows:
        last = _parse_dt(row["last_material_progress_at"])
        if last is None:
            age_seconds = None
        else:
            age_seconds = int((reference - last).total_seconds())
        if age_seconds is None or age_seconds > threshold_seconds:
            stagnant.append({
                "project_id": row["project_id"],
                "last_material_progress_at": row["last_material_progress_at"],
                "stagnant_seconds": age_seconds,
            })
    stagnant.sort(key=lambda item: (item["stagnant_seconds"] is not None, item["stagnant_seconds"] or 0), reverse=True)
    return stagnant


def completion_status(connection: sqlite3.Connection) -> dict:
    """Full dashboard payload: per-project unfinished work, metrics and the
    portfolio-wide Material Completion Rate."""
    rows = connection.execute("SELECT DISTINCT project_id FROM pr_state").fetchall()
    metric_rows = connection.execute("SELECT * FROM completion_metrics").fetchall()
    project_ids = sorted({str(r["project_id"]) for r in rows} | {str(r["project_id"]) for r in metric_rows})
    projects = {}
    for project_id in project_ids:
        unfinished = open_unfinished_work(connection, project_id)
        metrics_row = next((dict(r) for r in metric_rows if r["project_id"] == project_id), None)
        projects[project_id] = {
            "unfinished_work": unfinished,
            "unfinished_count": len(unfinished),
            "mergeable_ready": mergeable_prs(connection, project_id),
            "metrics": metrics_row,
        }
    return {
        "time": _now(),
        "projects": projects,
        "portfolio": material_completion_rate(connection),
        "stagnation": stagnation_report(connection),
    }
