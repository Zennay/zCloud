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
        "Re-run the PR-less branch ownership snapshot",
        "abort",
        "Re-run the serialized-writer snapshot immediately before any live dispatch.",
        "Re-run both #866 open-PR and #924 PR-less branch ownership checks",
        "Never fall back to a stale earlier `clear` result.",
        "branch without an open PR",
        "Do not reinterpret missing evidence as approval.",
    ]

    for marker in required_markers:
        assert marker in text, f"missing fail-closed deploy-ops marker: {marker}"


def test_deploy_ops_release_runbook_orders_admission_before_live_dispatch():
    text = RUNBOOK.read_text(encoding="utf-8")

    ordered_markers = [
        "PR #920 has landed",
        "PR #866 has landed",
        "PR #924 has landed",
        "A fresh bounded serialized-writer snapshot reports:",
        "Current `main` SHA is re-read immediately before the claim.",
        "A fresh open-PR ownership snapshot is collected with the #866 auditor",
        "A fresh PR-less branch snapshot is collected",
        "Re-run the serialized-writer snapshot immediately before any live dispatch.",
        "Re-run both #866 open-PR and #924 PR-less branch ownership checks",
        "Perform only the bounded cancellation proof described by #712.",
    ]

    positions = [text.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions), "deploy-ops admission sequence must stay fail-closed and ordered"
