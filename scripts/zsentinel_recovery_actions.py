"""Deterministic, advisory zSentinel recovery actions from private desk facts.

Never performs actions. No claim, release, runner, network or filesystem authority.
The three-line operator items derive from observed enums/counts, not task prose.
"""
from __future__ import annotations

MAX_ACTIONS = 7

def action(identifier, severity, problem, next_step, status):
    return {
        "id": identifier,
        "severity": severity,
        "problem": problem,
        "next_step": next_step,
        "status": status,
        "action_authorized": False,
    }

def _number(value):
    return value if type(value) is int and 0 <= value <= 1000000 else 0

def suggested_actions(packet):
    """Return a bounded prioritized list; observation is never authorization."""
    result = []
    if packet.get("source_complete") is not True or (
        packet.get("active_cross_lane_coverage_complete") is not True
    ):
        result.append(action(
            "source-partial", "urgent",
            "De lokale herstel- of scopetelling heeft onvolledige brondekking.",
            "Herlees de begrensde bron en herstel ontbrekende/ongeldige metadata met de eigenaar.",
            "HOLD_SOURCE_INCOMPLETE",
        ))
    if packet.get("claimed_without_local_lease_observation") is True:
        result.append(action(
            "owner-not-observed", "urgent",
            "Lopende zSentinel-wachtrijtaken missen lokaal zichtbaar leasebewijs.",
            "Vergelijk de actuele Notion-claims, browserworker en wachtrij met de runtime-eigenaar.",
            "OWNER_CONFIRMATION_REQUIRED",
        ))
    expired = _number(packet.get("expired_in_flight_queue_claims_observed"))
    unknown = _number(packet.get("unknown_in_flight_queue_expiry_observed"))
    if expired or unknown:
        result.append(action(
            "queue-claim-expiry", "urgent",
            f"Bij lopend werk zijn {expired} verlopen en {unknown} onbekende claimtermijnen gezien.",
            "Laat de bestaande claimhouder de claim- en voortgangsstatus vaststellen; niet herclaimen.",
            "HOLD_LEASE_RECONCILIATION",
        ))
    conflicting = [
        lane for lane in packet.get("lanes", [])[:4] if (
            lane.get("parallel_active_warning") is True
            or _number(lane.get("active_cross_lane_scope_conflicts")) > 0
        )
    ]
    if conflicting:
        result.append(action(
            "parallel-ownership", "urgent",
            "Meerdere gelijktijdige opdrachten of overlappende scopes zijn waargenomen.",
            "Stem huidige PR/file-eigenaren af en reserveer bestaande scopes vóór nieuw werk.",
            "HOLD_SCOPE_OWNERSHIP",
        ))
    # Source-limited P0 discovery stays advisory. Eligible is NOT the same as
    # ownership-free and never licenses a dispatch, CI run or merge.
    portfolio = packet.get("portfolio_urgent_local", [])
    priority = [
        item for item in portfolio[:13]
        if _number(item.get("queued_p0")) > 0
    ]
    priority.sort(key=lambda i: (-_number(i.get("queued_p0")), str(i.get("project", ""))))
    for item in priority[:2]:
        if len(result) >= MAX_ACTIONS:
            break
        name = str(item.get("project", ""))[:32]
        count = _number(item.get("queued_p0"))
        eligible = _number(item.get("eligible_queued_p0"))
        result.append(action(
            "p0-project-" + name.lower(),
            "review",
            f"{name}: {count} lokale P0-taak/taken wachten, {eligible} lokaal eligible.",
            "Controleer eerst de actuele open issues, bestaande product-PRs en externe claimhouder.",
            "PR_AND_OWNER_GATE_REQUIRED",
        ))
    # Show one safe independent continuation when release/owner waits.
    if not any(lane.get("id") == "product-remediation"
               and lane.get("status") != "idle" for lane in packet.get("lanes", [])[:4]):
        result.append(action(
            "unoccupied-product-lane", "info",
            "De lokale productherstelbaan toont geen actieve taakhouder.",
            "Bekijk onafhankelijk productwerk na controle van actuele externe claims en bestandscope.",
            "ADVISORY_NOT_OWNER_FREE",
        ))
    return result[:MAX_ACTIONS]
