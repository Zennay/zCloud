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

def _psi_owner_context(pressure):
    """Bounded explanation of local 60-second stalls, not a job diagnosis."""
    psi = pressure.get("psi")
    if not isinstance(psi, dict) or psi.get("status") != "OBSERVED":
        return " Kernelwachttijden (PSI) niet volledig waargenomen."
    resources = psi.get("resources") or {}
    candidates = []
    labels = (
        ("memory", "full", "geheugen: volledige stilstand"),
        ("io", "full", "I/O: volledige stilstand"),
        ("cpu", "some", "CPU: deels wachtend"),
        ("memory", "some", "geheugen: deels wachtend"),
        ("io", "some", "I/O: deels wachtend"),
    )
    for source, scope, description in labels:
        try:
            value = resources[source]["measurements"][scope]["avg60_pct"]
        except (KeyError, TypeError):
            continue
        if type(value) in (float, int) and 0 <= value <= 100:
            candidates.append((value, description))
    if not candidates:
        return " Kernelwachttijden (PSI) onbekend."
    # Full memory/I/O stalls are more severe than partial waits even when
    # their raw percentage is smaller. Do not hide full 70% memory stalls
    # behind a higher "some" percentage in a one-line owner handoff.
    full = [
        (value, label) for value, label in candidates
        if "volledige stilstand" in label and value >= 1
    ]
    percent, reason = max(full if full else candidates, key=lambda item: item[0])
    window = psi.get("window_signal")
    window_note = (
        " De 10s-kerneldruk ligt beduidend hoger dan de 300s-bron;"
        " dit is een venstervergelijking, geen prognose."
        if window == "SHORT_WINDOW_PRESSURE_HIGHER" else
        " De 10s- en 300s-vensters lopen uiteen;"
        " de trendrichting is niet eenduidig."
        if window == "MIXED_WINDOWS" else ""
    )
    return (
        f" Kernel-PSI (60s): {reason} {percent:.2f}%; "
        "bronbrede stall, geen bewijs van een specifieke GitHub-job."
        + window_note
    )


def _runner_pressure_context(packet):
    """Correlate only two LOCAL observations; never infer remote job status."""
    proc = packet.get("runner_processes")
    if not isinstance(proc, dict):
        return " Runnerprocessen niet bemonsterd."
    families = proc.get("families")
    if not isinstance(families, dict):
        return " Runnerprocesbron onbekend."
    cloud = families.get("zCloud")
    if not isinstance(cloud, dict):
        return " zCloud-runnerbron ontbreekt."
    listeners = _number(cloud.get("listeners"))
    workers = _number(cloud.get("workers"))
    if listeners:
        return (
            " Er is lokaal een zCloud-listener gezien; dit bevestigt geen "
            "GitHub-registratie, toewijzing of succesvolle job."
        )
    if workers:
        return (
            " Er is lokaal een zCloud-worker zonder waargenomen listener; "
            "onderbreek de actieve job niet."
        )
    if proc.get("source_complete") is True:
        return (
            " De complete lokale processnapshot zag geen zCloud-listener; "
            "remote runnerregistratie blijft apart te verifiëren."
        )
    return " De lokale runnerprocesbron is onvolledig."


def suggested_actions(packet):
    """Return a bounded prioritized list; observation is never authorization."""
    result = []
    # Keep queued records with existing worker slots out of any new
    # admission. The private desk deliberately never reveals slot IDs.
    reserved_slots = sum(
        _number(lane.get("deferred_assigned_packages"))
        for lane in packet.get("lanes", [])[:4] if isinstance(lane, dict)
    )
    if reserved_slots:
        result.append(action(
            "queued-worker-slot-owner-check", "urgent",
            f"{reserved_slots} lokale wachtrijtaken zijn al aan een workerslot gekoppeld.",
            "Laat de bestaande runtime- en queuemeester vaststellen of die "
            "reserveringen nog geldig zijn; niet automatisch vrijmaken, "
            "herclaimen of parallel starten.",
            "HOLD_QUEUE_WORKER_SLOT_RESERVED",
        ))
    noneligible = sum(
        _number(lane.get("deferred_ineligible_packages"))
        for lane in packet.get("lanes", [])[:4] if isinstance(lane, dict)
    )
    if noneligible:
        result.append(action(
            "queued-eligibility-owner-check", "review",
            f"{noneligible} lokale wachtrijtaken hebben eligible niet op 1.",
            "Laat de actuele queue-/Notion-eigenaar de eligibilityreden "
            "beoordelen; geen automatische heractivering of nieuwe claim.",
            "HOLD_QUEUE_NOT_ELIGIBLE",
        ))
    deferred_qa = sum(
        _number(lane.get("deferred_qa_packages"))
        for lane in packet.get("lanes", [])[:4]
        if isinstance(lane, dict)
    )
    if deferred_qa:
        result.append(action(
            "qa-product-policy-exclusion", "review",
            f"{deferred_qa} wachtende QA-/validatietaken zijn niet toegelaten tot herstelwerk.",
            "Laat de wachtrijeigenaar deze items apart beheren en geef voorrang aan "
            "open productissues, bestaande PR-integraties en actuele blokkades; "
            "bestaande actieve claims blijven intact.",
            "NO_NEW_QA_WORK_ADMISSION",
        ))
    if packet.get("source_complete") is not True or (
        packet.get("active_cross_lane_coverage_complete") is not True
    ):
        result.append(action(
            "source-partial", "urgent",
            "De lokale herstel- of scopetelling heeft onvolledige brondekking.",
            "Herlees de begrensde bron en herstel ontbrekende/ongeldige metadata met de eigenaar.",
            "HOLD_SOURCE_INCOMPLETE",
        ))
    contract = packet.get("local_contract_evidence")
    if isinstance(contract, dict):
        if contract.get("matches_proposed_profile") == "MISMATCH_LOCAL_FILE":
            result.append(action(
                "local-contract-profile-owner-review", "urgent",
                "Het lokale vierbaanprofiel of de cap van precies vier herstelworkers wijkt af.",
                "Laat de runtime-eigenaar het actuele portfolio-recovery profiel, "
                "ai_worker_cap=4 en de werkelijk geladen service vergelijken. "
                "Behoud bestaande claims; geen automatische opschaling of overname.",
                "HOLD_LOCAL_CONTRACT_PROFILE_MISMATCH",
            ))
        elif contract.get("status") != "OBSERVED_LOCAL_FILE":
            result.append(action(
                "local-contract-unverified-owner-review", "urgent",
                "Het lokale vier-workercontract is niet betrouwbaar geobserveerd.",
                "Laat de runtime-eigenaar het bronbestand en de werkelijk geladen "
                "serviceconfiguratie vaststellen; houd nieuwe schrijvers tegen.",
                "HOLD_LOCAL_CONTRACT_UNVERIFIED",
            ))
    migrated = _number(packet.get("active_legacy_profile_claims_observed"))
    if migrated:
        result.append(action(
            "legacy-profile-owner-migration", "urgent",
            f"{migrated} actieve herstelopdrachten hebben een oud of onbevestigd baanprofiel.",
            "Laat de runtime-eigenaar de werkelijk actieve configuratie, claimhouder "
            "en conflicterende PR-scopes verifiëren; behoud bestaande taken en "
            "migreer pas na geautoriseerde release.",
            "HOLD_LEGACY_PROFILE_MIGRATION",
        ))
    expired_local = _number(packet.get("expired_local_leases_observed"))
    if expired_local:
        result.append(action(
            "expired-local-claim-owner-review", "urgent",
            f"{expired_local} lokale zSentinel-taaklease(s) zijn verlopen.",
            "Verifieer met de oorspronkelijke claimeigenaar en actuele Notion-/queuebron "
            "welke uitvoering nog loopt; geef geen taak aan een andere schrijver.",
            "HOLD_EXPIRED_LOCAL_CLAIM",
        ))
    shadowed = _number(packet.get("runtime_active_filtered_by_eligibility"))
    if shadowed:
        result.append(action(
            "runtime-eligibility-source-gap", "urgent",
            f"{shadowed} lopende taak/taken hebben eligible!=1 en zijn mogelijk onzichtbaar voor de serverplanner.",
            "Laat de bestaande servereigenaar de live in-flight scopebron en de huidige claimhouder vergelijken.",
            "HOLD_RUNTIME_SOURCE_GAP",
        ))
    untrusted_leases = _number(packet.get("untrusted_active_leases_observed"))
    if untrusted_leases:
        result.append(action(
            "active-lease-admission-held", "urgent",
            f"{untrusted_leases} actieve herstelopdrachten hebben geen betrouwbare, lopende queuelease.",
            "Behoud de bestaande worker en laat de runtime-/claimeigenaar beide tijden "
            "en de echte voortgang vergelijken vóór een nieuwe schrijver.",
            "HOLD_NEW_RECOVERY_WRITERS",
        ))
    suspect_windows = _number(
        packet.get("inconsistent_in_flight_queue_claim_windows_observed")
    )
    if suspect_windows:
        result.append(action(
            "inconsistent-in-flight-lease", "urgent",
            f"{suspect_windows} lopende queue-claims hebben een onmogelijke of ontbrekende claimstart.",
            "Laat de huidige claimeigenaar start- en vervaltijd controleren; "
            "vertrouw de toekomstige vervaldatum niet en herclaim niets.",
            "HOLD_CLAIM_TIME_RECONCILIATION",
        ))
    missing_scope = _number(packet.get("active_write_scopes_not_declared"))
    if missing_scope:
        missing_decl = _number(packet.get("active_scope_declarations_missing"))
        invalid_decl = _number(packet.get("active_scope_declarations_invalid_or_empty"))
        result.append(action(
            "active-scope-unknown", "urgent",
            f"{missing_scope} actieve herstelopdrachten zonder bewijs van echte bestandscope: "
            f"{missing_decl} ontbrekend, {invalid_decl} ongeldig of leeg.",
            "Vraag hun huidige eigenaren zelf concrete bestandsbereiken en PR-overlap "
            "vast te leggen. Afgeleide lane-capabilities, wildcards en ongeldige "
            "paden zijn geen bewijs voor een nieuwe parallelle schrijver.",
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
    # Local host memory/swap/load is corroborating evidence only. Resource
    # pressure can explain slow CI/worker throughput; low pressure does not
    # prove cgroup quotas, GitHub admission, free leases or new worker rights.
    pressure = packet.get("host_pressure")
    if isinstance(pressure, dict) and pressure.get("status") in ("HOST_SNAPSHOT_OBSERVED", "HOST_PARTIAL"):
        hint = pressure.get("pressure_hint")
        if hint in ("HIGH_LOCAL_PRESSURE", "ELEVATED_LOCAL_PRESSURE"):
            result.append(action(
                "host-pressure-owner-review",
                "urgent" if hint == "HIGH_LOCAL_PRESSURE" else "review",
                "De lokale host heeft verhoogde RAM-, swap-, CPU- of schijfruimtedruk; "
                "dit is geen bewijs dat een GitHub-job vastzit."
                + _psi_owner_context(pressure)
                + _runner_pressure_context(packet),
                "Behoud bestaande jobs en claims. Vergelijk cgroup-limieten, "
                "runner-toewijzing, procfs-PSI en workerprogressie met de bevoegde eigenaar "
                "vóór capaciteitsverhoging, opslagopschoning of serviceactie.",
                "HOLD_CAPACITY_OWNER_REVIEW",
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
    # Source-limited P0/P1 discovery is an operator inbox, never admission.
    # Previously the desk omitted ALL P1 projects even when no P0 project
    # was waiting. Preserve P0 precedence while always showing a P1 fallback
    # from a *different* project when the bounded source contains one.
    portfolio = [
        item for item in packet.get("portfolio_urgent_local", [])[:13]
        if isinstance(item, dict) and isinstance(item.get("project"), str)
        and 0 < len(item["project"]) <= 32
    ]
    def ordered_band(band):
        key = "queued_" + band
        eligible_key = "eligible_queued_" + band
        return sorted(
            (item for item in portfolio if _number(item.get(key)) > 0),
            key=lambda item: (
                -_number(item.get(key)),
                -_number(item.get(eligible_key)),
                item["project"].casefold(),
            ),
        )

    p0 = ordered_band("p0")
    p1 = ordered_band("p1")
    # The first two P0s retain priority; a distinct P1 may occupy the third
    # slot. When there are no P0s, show up to three highest-impact P1s.
    chosen = [(item, "p0") for item in p0[:2]]
    seen_names = {item["project"].casefold() for item, _ in chosen}
    for item in p1:
        if len(chosen) >= 3:
            break
        if item["project"].casefold() not in seen_names:
            chosen.append((item, "p1"))
            seen_names.add(item["project"].casefold())
    if not p1 and len(chosen) < 3:
        for item in p0[2:]:
            if len(chosen) >= 3:
                break
            chosen.append((item, "p0"))
    for item, band in chosen:
        name = item["project"]
        count = _number(item.get("queued_" + band))
        eligible = min(count, _number(item.get("eligible_queued_" + band)))
        active = _number(item.get("in_flight_" + band))
        label = band.upper()
        result.append(action(
            f"{band}-project-" + name.casefold(),
            "review",
            f"{name}: {count} lokale {label}-taak/taken wachten, "
            f"{eligible} lokaal eligible; {active} reeds in behandeling.",
            "Open bestaande product-PRs en P0/P1 issues; controleer actuele "
            "Notion-/GitHub-claims en bestandsoverlap vóór een eigen reparatie.",
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
        "legacy-profile-owner-migration",
        "local-contract-profile-owner-review",
    }
    def display_rank(item):
        identifier = item["id"]
        if identifier in protected or (
            identifier == "host-pressure-owner-review"
            and item["severity"] == "urgent"
        ):
            return 0
        if item["severity"] == "urgent":
            return 1
        if identifier.startswith(("p0-project-", "p1-project-")):
            return 2
        if item["severity"] == "review":
            return 3
        return 4
    return sorted(result, key=display_rank)[:MAX_ACTIONS]
