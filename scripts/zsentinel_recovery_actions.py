"""Deterministic, advisory zSentinel recovery actions from private desk facts.

Never performs actions. No claim, release, runner, network or filesystem authority.
The three-line operator items derive from observed enums/counts, not task prose.
"""
from __future__ import annotations

MAX_ACTIONS = 9

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
    shadowed = _number(packet.get("runtime_active_filtered_by_eligibility"))
    if shadowed:
        result.append(action(
            "runtime-eligibility-source-gap", "urgent",
            f"{shadowed} lopende taak/taken hebben eligible!=1 en zijn mogelijk onzichtbaar voor de serverplanner.",
            "Laat de bestaande servereigenaar de live in-flight scopebron en de huidige claimhouder vergelijken.",
            "HOLD_RUNTIME_SOURCE_GAP",
        ))
    missing_scope = _number(packet.get("active_write_scopes_not_declared"))
    if missing_scope:
        result.append(action(
            "active-scope-unknown", "urgent",
            f"{missing_scope} actieve herstelopdrachten missen expliciete bestands- of capabilitiescope.",
            "Laat hun huidige eigenaren de scopes en PR-overlap vastleggen; start geen nieuwe parallelle lane.",
            "HOLD_PARALLEL_ADMISSION",
        ))
    missing_claim_scope = _number(packet.get("active_claim_scopes_not_declared"))
    if missing_claim_scope:
        result.append(action(
            "active-claim-scope-unknown", "urgent",
            f"{missing_claim_scope} geldige lokale leases missen een bestands-/capabilityscope.",
            "Laat de bestaande claimeigenaar het schrijfbereik bevestigen; geef geen nieuwe schrijflane vrij.",
            "HOLD_CLAIM_SCOPE_RECONCILIATION",
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
    if any(
        lane.get("serialized_unscoped_queue_admitted") is True
        for lane in packet.get("lanes", [])[:4]
    ):
        result.append(action(
            "serialized-unscoped-writer", "review",
            "Een wachtende herstelopdracht zonder schrijfscope heeft alleen seriële ruimte.",
            "Laat de taak starten uitsluitend na externe claim-, eigenaar- en resourcecontrole; geen parallelle schrijver.",
            "SERIAL_CANDIDATE_NOT_AUTHORIZED",
        ))
    # Runner /proc only corroborates local process presence. It cannot prove
    # GitHub runner registration, labels, job assignment or successful CI.
    local_proc = packet.get("runner_processes")
    if isinstance(local_proc, dict):
        families = local_proc.get("families") or {}
        cloud = families.get("zCloud", {}) if isinstance(families, dict) else {}
        listener_count = _number(cloud.get("listeners"))
        if listener_count:
            result.append(action(
                "zcloud-listener-presence", "review",
                "Op de VPS is een zCloud Runner.Listener-proces waargenomen.",
                "Controleer de bestaande GitHub-jobs op runner_id, labels en werkelijke start; "
                "procesaanwezigheid is geen bewijs van geslaagde CI.",
                "GITHUB_RUNNER_ASSIGNMENT_NOT_OBSERVED",
            ))
        elif _number(cloud.get("workers")) > 0:
            result.append(action(
                "zcloud-worker-without-listener-observation",
                "urgent" if local_proc.get("source_complete") is True else "review",
                "Er draait een zCloud Runner.Worker, maar de lokale snapshot ziet geen zCloud-listener.",
                "Behoud de bestaande worker/job; vraag de bevoegde eigenaar de listener en "
                "GitHub runner-toewijzing onafhankelijk te controleren zonder blanket restart.",
                "ACTIVE_WORKER_PROTECTED_LISTENER_UNKNOWN",
            ))
        elif local_proc.get("source_complete") is True:
            result.append(action(
                "zcloud-listener-absent", "urgent",
                "De volledige lokale processnapshot toont geen zCloud-listener.",
                "Laat de bevoegde runtime-eigenaar de service en GitHub-runnerregistratie inspecteren; "
                "onderbreek geen actieve worker.",
                "RUNNER_OWNER_INSPECTION_REQUIRED",
            ))
        else:
            result.append(action(
                "zcloud-proc-incomplete", "review",
                "De afgeschermde procfs-bron toont geen verifieerbare zCloud-listener.",
                "Vergelijk met de eigenaar zijn bestaande serviceproces en een GitHub-jobreceipt.",
                "RUNNER_PRESENCE_NOT_CONCLUSIVE",
            ))
        active_workers = [
            name for name, details in families.items()
            if isinstance(details, dict) and _number(details.get("workers")) > 0
        ]
        if active_workers:
            # No process IDs, command lines, raw paths, tokens or owner IDs leave
            # the bounded probe. Report the aggregate without taking ownership.
            result.append(action(
                "runner-worker-in-progress", "review",
                f"{len(active_workers)} runnerfamilie(s) hebben lokaal een Runner.Worker-proces.",
                "Behoud bestaande jobs en inspecteer hun voortgang zonder serviceherstart of queue-drain.",
                "ACTIVE_RUNNER_WORK_PROTECTED",
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
        # Build eligible P0 cards before the final bounded, safety-aware
        # selection; otherwise earlier advisory cards can starve open issues.
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
    # An idle lane is *not* an available new writer while another active
    # queue task or local claim has an unknown actual write scope. Do not
    # suggest a supposedly independent handoff on incomplete ownership data.
    admission_clear = (
        packet.get("source_complete") is True
        and packet.get("ownership_reconciliation_required") is False
        and packet.get("active_cross_lane_coverage_complete") is True
        and _number(packet.get("active_write_scopes_not_declared")) == 0
        and _number(packet.get("active_claim_scopes_not_declared")) == 0
    )
    if admission_clear and not any(
        lane.get("id") == "product-remediation" and lane.get("status") != "idle"
        for lane in packet.get("lanes", [])[:4]
    ):
        result.append(action(
            "unoccupied-product-lane", "info",
            "De lokale productherstelbaan toont geen actieve taakhouder.",
            "Bekijk onafhankelijk productwerk na controle van actuele externe claims en bestandscope.",
            "ADVISORY_NOT_OWNER_FREE",
        ))
    # Enforce the existing nine-card ceiling after ranking, not while
    # constructing the list. A verified local worker presence must never
    # disappear behind generic review/queue messages in a busy incident.
    protected = {
        "runner-worker-in-progress",
        "zcloud-worker-without-listener-observation",
    }
    def display_rank(item):
        identifier = item["id"]
        if identifier in protected:
            return 0
        if item["severity"] == "urgent":
            return 1
        if identifier.startswith("p0-project-"):
            return 2
        if item["severity"] == "review":
            return 3
        return 4
    return sorted(result, key=display_rank)[:MAX_ACTIONS]
