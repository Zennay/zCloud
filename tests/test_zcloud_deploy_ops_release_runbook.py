from pathlib import Path


RUNBOOK = Path("docs/zcloud-deploy-ops-release-runbook.md")


def test_deploy_ops_release_runbook_is_fail_closed():
    text = RUNBOOK.read_text(encoding="utf-8")

    required_markers = [
        "PR #580 / PWQ-41",
        "PR #576",
        "PR #920 has landed",
        "inventory_complete=true",
        "status=clear",
        "Current `main` SHA is re-read immediately before the claim.",
        "changed-file ownership",
        "abort",
        "Re-run the serialized-writer snapshot immediately before any live dispatch.",
        "Never fall back to a stale earlier `clear` result.",
        "Do not reinterpret missing evidence as approval.",
    ]

    for marker in required_markers:
        assert marker in text, f"missing fail-closed deploy-ops marker: {marker}"
