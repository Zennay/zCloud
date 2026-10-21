"""Private Markdown renderer for the read-only zCloud VPS preflight."""
from __future__ import annotations

import re


def _md(value: object) -> str:
    if value is None:
        return "unknown"
    text = str(value).replace("\r", " ").replace("\n", " ")
    special = r"\\*_{}\[\]()#+.!|<>" + chr(96)
    return re.sub("([" + special + "])", r"\\\1", text)


def render(report: dict) -> str:
    """Render a human handoff; this is not release permission or an owner claim."""
    if (type(report) is not dict or report.get("mode") != "read_only"
            or report.get("release_authorized") is not False
            or type(report.get("findings")) is not list
            or type(report.get("release_gates")) is not list
            or type(report.get("owner_handoff")) is not list):
        raise ValueError("invalid non-authorizing VPS preflight receipt")
    result = report.get("result", "unknown")
    labels = {"blocked": "BLOCKED", "partial": "PARTIAL",
              "preflight_clear": "LOCAL PREFLIGHT CLEAR"}
    lines = [
        "# zCloud VPS preflight — zSentinel",
        "",
        f"**Resultaat:** {labels.get(result, 'ONBEKEND')}  ",
        f"**Bronmoment (UTC):** {_md(report.get('observed_at', 'unknown'))}  ",
        "**Lokale systeemstart/checkout-binding:** " +
            _md((report.get("source_scope") or {}).get("state", "unknown")) +
            " (niet onafhankelijk geattesteerd)  ",
        "**Modus:** alleen lezen · **Release toegestaan:** nee",
        "",
        "## Checkout en runner",
        "",
        "| Signaal | Waarneming |",
        "|---|---|",
    ]
    checkout = report.get("checkout") or {}
    runner = report.get("runner") or {}
    resources = report.get("resources") or {}
    for key, value in (
        ("Branch", checkout.get("branch", "unknown")),
        ("Lokale HEAD", checkout.get("sha", "unknown")),
        ("Onafhankelijk verwachte main-HEAD", checkout.get("expected_main_sha", "unknown")),
        ("Gewijzigde tracked bestanden", checkout.get("tracked_changes", "unknown")),
        ("Untracked bestanden", checkout.get("untracked_entries", "unknown")),
        ("Runner unit", runner.get("unit", "unknown")),
        ("Runner unit state", runner.get("unit_state", "unknown")),
        ("systemd manager", runner.get("unit_manager", "unknown")),
        ("Listener count", len(runner.get("listener_pids", []))),
        ("Runner.Worker count", runner.get("worker_process_count", "unknown")),
        ("Vrij RAM (%)", resources.get("available_ram_pct", "unknown")),
        ("Swap gebruikt (%)", resources.get("swap_used_pct", "unknown")),
        ("Vrije disk bytes", resources.get("disk_free_bytes", "unknown")),
        ("10 GiB trainingsreserve-tekort (bytes)", resources.get("training_reserve_gap_bytes", "unknown")),
    ):
        lines.append(f"| {_md(key)} | {_md(value)} |")
    pressure = report.get("host_pressure")
    if pressure is not None:
        lines += ["", "## CPU-, geheugen- en I/O-druk", "",
                  "| Signaal | Waarneming |", "|---|---|"]
        for key, value in (
            ("Bronstatus", pressure.get("state", "unknown")),
            ("Logical CPUs", pressure.get("cpu_count", "unknown")),
            ("Load / CPU (1 minuut)", pressure.get("load_per_cpu", "unknown")),
            ("CPU PSI some avg10 (%)", pressure.get("cpu_some_avg10_pct", "unknown")),
            ("Memory PSI full avg10 (%)", pressure.get("memory_full_avg10_pct", "unknown")),
            ("I/O PSI full avg10 (%)", pressure.get("io_full_avg10_pct", "unknown")),
        ):
            lines.append(f"| {_md(key)} | {_md(value)} |")
        lines.append("")
        lines.append("Dit is hostbrede, momentane vraag; geen bewijs welke worker de oorzaak is.")
    for section, key, fields in (
        ("Extern archief", "archive_readiness", (
            ("Mount", "mount"), ("Status", "state"), ("Filesystem", "mount_type"),
            ("rclone read-only", "rclone_read_only"),
            ("Schrijven toegestaan", "archive_write_authorized"),
            ("Overdracht uitgevoerd", "transfer_performed"))),
        ("Bescherming lopend werk", "worker_safety", (
            ("Workerbewijs", "state"), ("Claimed queue rows", "queued_claims"),
            ("Durable claims", "durable_claims"),
            ("Fresh generating slots", "fresh_generating_slots"),
            ("Event sample compleet", "events_sample_complete"),
            ("Safe idle bewezen", "safe_idle_verified"))),
    ):
        data = report.get(key)
        if data is None:
            continue
        lines += ["", "## " + section, "", "| Signaal | Waarneming |", "|---|---|"]
        for label, field in fields:
            lines.append("| " + _md(label) + " | " + _md(data.get(field, "unknown")) + " |")
        lines.append("Geen workeronderbreking, archiefwrite of claim-/queuewijziging is geautoriseerd.")
    storage = report.get("storage_inventory")
    if storage is not None:
        lines += ["", "## Begrensde opslaginventaris", ""]
        for row in storage.get("measured", []):
            lines.append("- " + _md(row.get("category", "unknown")) +
                         ": " + _md(row.get("size_gib", "unknown")) + " GiB")
        if storage.get("incomplete"):
            lines.append("Onvolledig/niet gemeten: " + _md(", ".join(storage["incomplete"])))
        lines.append("Scope: " + _md(storage.get("scope", "unknown")) +
                     "; geen bestanden gewijzigd of verwijderd.")
    comparison = report.get("comparison")
    if comparison is not None:
        lines += ["", "## Verandering sinds vorige bronopname", "",
                  "- Baselineversheid: " + _md(comparison.get("snapshot_freshness", "unknown")),
                  "- Leeftijd baseline (seconden): " + _md(comparison.get("snapshot_age_seconds", "unknown")),
                  "- Bronbinding vorige/huidige meting: " + _md(comparison.get("source_scope_comparison", "unknown")),
                  "- Bron SHA-256 (exact gelezen vorige JSON): " + _md(comparison.get("baseline_sha256", "unknown")),
                  "- Bron bytes: " + _md(comparison.get("baseline_bytes", "unknown")),
                  "- Bronlees-integriteit: " + _md(comparison.get("baseline_integrity", "unknown")),
                  "- Herstelconclusies toegestaan: " + _md(comparison.get("resolution_credit_allowed", "unknown")),
                  ""]
        for key, label in (("new_findings", "Nieuw"),
                           ("resolved_findings", "Verdwenen in bron"),
                           ("recovery_unverified_findings", "Herstel niet geverifieerd"),
                           ("still_present", "Nog aanwezig")):
            lines.append("- " + label + ": " + _md(", ".join(comparison.get(key, [])) or "geen"))
        trend = comparison.get("host_pressure_trend")
        if trend is not None:
            lines.append("CPU/PSI-trend: " + _md(trend.get("status", "unknown")) +
                         " · " + _md(trend.get("trend", "unknown")) +
                         " (twee bronopnames, geen doorlopend toezicht).")
            for metric in trend.get("metrics", []):
                lines.append("- " + _md(metric.get("label", metric.get("metric", "unknown"))) +
                             ": " + _md(metric.get("previous", "unknown")) +
                             " → " + _md(metric.get("current", "unknown")) +
                             " (Δ " + _md(metric.get("delta", "unknown")) + ")")
    lines += ["", "## Bevindingen", ""]
    if report["findings"]:
        for finding in report["findings"]:
            lines.append("- **" + _md(finding.get("severity", "unknown")).upper() +
                         " · " + _md(finding.get("code", "unknown")) + ":** " +
                         _md(finding.get("detail", "")))
    else:
        lines.append("- Geen lokale bevindingen geregistreerd; dit autoriseert geen release.")
    lines += ["", "## Eigenaarshandoff", ""]
    if report["owner_handoff"]:
        for card in report["owner_handoff"]:
            lines += [
                "- **" + _md(card.get("code", "unknown")) + " — " +
                _md(card.get("status", "unknown")) + " / " +
                _md(card.get("trend", "unknown")) + "**",
                "  - Eigenaar: " + _md(card.get("owner", "unknown")),
                "  - Probleem: " + _md(card.get("problem", "")),
                "  - Actie: " + _md(card.get("action", "")),
                "  - Vereist bewijs: " + _md("; ".join(card.get("required_evidence", [])) or "niet gespecificeerd"),
                "  - Bevindingen: " + _md(", ".join(card.get("finding_codes", [])) or "geen bevestigd"),
            ]
    else:
        lines.append("- Geen eigenaarshandoff beschikbaar; actuele eigenaar blijft onbekend.")
    lines += ["", "## Geordende hersteladviezen", ""]
    for action in report.get("recovery_priorities", []):
        lines += [
            "- **" + _md(action.get("code", "unknown")) + " — " +
            _md(action.get("owner", "unknown")) + "**",
            "  - Reden: " + _md(action.get("reason", "")),
            "  - Volgende stap: " + _md(action.get("next_action", "")),
            "  - Hostwijziging: " + _md(action.get("changes_host", "unknown")) +
            " · autorisatie vereist: " + _md(action.get("authorization_required", "unknown")),
        ]
    lines += ["", "## Releasebeslisbord", "",
              "| Gate | Status | Eigenaar | Betekenis |",
              "|---|---|---|---|"]
    for gate in report["release_gates"]:
        lines.append("| " + _md(gate.get("gate", "unknown")) +
                     " | " + _md(gate.get("status", "unknown")) +
                     " | " + _md(gate.get("owner", "unknown")) +
                     " | " + _md(gate.get("explanation", "")) + " |")
    lines += [
        "", "**Belangrijk:** dit rapport voert geen herstel uit en bewijst geen "
        "Notion-claim, vrije worker, vereiste branchregels, onafhankelijke review, "
        "exact-head CI-vrijgave of productieacceptatie.",
        "Behoud lopende claims en volg de bestaande bevoegde release-eigenaar.",
        "",
    ]
    output = "\n".join(lines)
    if len(output.encode("utf-8")) > 512 * 1024:
        raise ValueError("VPS preflight Markdown exceeds output limit")
    return output
