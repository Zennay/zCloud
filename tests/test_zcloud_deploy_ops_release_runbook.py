from pathlib import Path


RUNBOOK = Path("docs/zcloud-deploy-ops-release-runbook.md")


def test_deploy_ops_release_runbook_is_fail_closed():
    text = RUNBOOK.read_text(encoding="utf-8")

    required_markers = [
        "PR #580 / PWQ-41",
        "PR #576",
        "PR #920 has landed",
        "PR #866 has landed",
        "PR #924 has landed",
        "PR-less branch ownership",
        "inventory_complete=true",
        "status=complete",
        "status=clear",
        "complete per-branch changed-file evidence",
        "Current `main` SHA is re-read immediately before the claim.",
        "#866 auditor",
        "changed-file ownership",
        "All admission evidence belongs to one exact-main transaction.",
        "never compose a writer-window or ownership result from another main revision",
        "scripts/zcloud_serialized_writer_window_audit.py",
        "scripts/zcloud_open_pr_overlap_audit.py",
        "scripts/zcloud_unpr_branch_snapshot.py",
        "scripts/zcloud_unpr_branch_overlap_audit.py",
        "hand-built substitute is not equivalent admission evidence",
        "the write-capable claim is inadmissible",
        "Re-run the PR-less branch ownership snapshot",
        "abort",
        "Re-fetch canonical `main` after exact-head regression",
        "require it to equal the candidate base SHA",
        "abandon this admission transaction and restart from the new exact `main`",
        "Re-run the serialized-writer snapshot",
        "immediately before any live dispatch.",
        "Re-run both #866 open-PR and #924 PR-less branch ownership checks",
        "Never fall back to a stale earlier `clear` result.",
        "branch without an open PR",
        "Do not reinterpret missing evidence as approval.",
    ]

    for marker in required_markers:
        assert marker in text, f"missing fail-closed deploy-ops marker: {marker}"


def test_deploy_ops_release_runbook_safe_order_section_is_strictly_ordered():
    text = RUNBOOK.read_text(encoding="utf-8")

    safe_order = text.split("## Safe order for #712", 1)[1].split("## Parallel-worker rule", 1)[0]

    ordered_markers = [
        "Re-fetch canonical `main` and record the exact SHA.",
        "scripts/zcloud_open_pr_overlap_audit.py",
        "scripts/zcloud_unpr_branch_snapshot.py",
        "scripts/zcloud_unpr_branch_overlap_audit.py",
        "Build the hosted-only contract change on a fresh branch from that exact `main`.",
        "Run exact-head regression.",
        "Re-fetch canonical `main` after exact-head regression",
        "scripts/zcloud_serialized_writer_window_audit.py",
        "Re-run both #866 open-PR and #924 PR-less branch ownership checks",
        "Require `inventory_complete=true` and `status=clear` again",
        "Perform only the bounded cancellation proof described by #712.",
        "Read back the result and record the exact run/commit evidence.",
    ]

    positions = [safe_order.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions), "deploy-ops safe-order section must stay fail-closed and ordered"
