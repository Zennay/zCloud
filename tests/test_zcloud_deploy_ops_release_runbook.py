from pathlib import Path


RUNBOOK = Path("docs/zcloud-deploy-ops-release-runbook.md")


def test_deploy_ops_release_runbook_is_fail_closed():
    text = RUNBOOK.read_text(encoding="utf-8")

    required_markers = [
        "PR #580 / PWQ-41",
        "PR #576",
        "PR #920 has landed",
        "PR #924 has landed",
        "PR-less branch ownership",
        "inventory_complete=true",
        "status=complete",
        "status=clear",
        "complete per-branch changed-file evidence",
        "Current `main` SHA is re-read immediately before the claim.",
        "Open PR changed-file ownership",
        "Re-run the PR-less branch ownership snapshot",
        "abort",
        "Re-run the serialized-writer snapshot immediately before any live dispatch.",
        "Re-run both open-PR and PR-less branch ownership checks",
        "Never fall back to a stale earlier `clear` result.",
        "branch without an open PR",
        "Do not reinterpret missing evidence as approval.",
    ]

    for marker in required_markers:
        assert marker in text, f"missing fail-closed deploy-ops marker: {marker}"
