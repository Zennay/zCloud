from __future__ import annotations

from .models import Finding


def render_markdown(findings: list[Finding]) -> str:
    lines = ["# LightUp Assessment Findings", ""]
    if not findings:
        lines.append("No findings recorded.")
        return "\n".join(lines) + "\n"

    for finding in findings:
        finding.validate()
        lines.extend(
            [
                f"## {finding.title}",
                f"- ID: `{finding.finding_id}`",
                f"- Severity: **{finding.severity.value}**",
                f"- Target: `{finding.target}`",
                f"- Retest: `{finding.retest_status.value}`",
                "",
                "### Evidence",
            ]
        )
        lines.extend(f"- {item}" for item in finding.evidence or ["No evidence recorded."])
        lines.extend(["", "### Remediation", finding.remediation, ""])
    return "\n".join(lines)
