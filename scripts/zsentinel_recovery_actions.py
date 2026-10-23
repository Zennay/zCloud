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
    """Bounded explanation of observed kernel stalls, not a job diagnosis."""
    psi = pressure.get("psi")
    if not isinstance(psi, dict) or psi.get("status") not in ("OBSERVED", "PARTIAL"):
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
            if resources[source].get("status") != "OBSERVED":
                continue
            value = resources[source]["measurements"][scope]["avg60_pct"]
        except (KeyError, TypeError):
            continue
        if type(value) in (float, int) and 0 <= value <= 100:
            candidates.append((value, description))
    if not candidates:
        return (
            " Kernelwachttijden (PSI) onbekend; gedeeltelijke bron zonder "
            "bruikbare drukmeting." if psi.get("status") == "PARTIAL"
            else " Kernelwachttijden (PSI) onbekend."
        )
    # Full memory/I/O stalls are more severe than partial waits even when
    # their raw percentage is smaller. Do not hide full stalls behind a
    # higher "some" percentage in a one-line owner handoff.
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
    partial_note = ""
    if psi.get("status") == "PARTIAL":
        basis = [
            source for source in psi.get("pressure_basis", [])
            if source in ("cpu", "memory", "io")
        ]
        if psi.get("pressure_hint") in ("HIGH", "ELEVATED") and basis:
            partial_note = (
                " PSI-brondekking is gedeeltelijk; waargenomen "
                + psi["pressure_hint"] + " druk in " + ", ".join(basis)
                + ". Ontbrekende bronnen blijven UNKNOWN."
            )
        else:
            partial_note = (
                " PSI-brondekking is gedeeltelijk; ontbrekende bronnen "
                "kunnen druk verbergen en blijven UNKNOWN."
            )
    return (
        f" Kernel-PSI (60s): {reason} {percent:.2f}%; "
        "bronbrede stall, geen bewijs van een specifieke GitHub-job."
        + partial_note + window_note
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
    unclassified = _number(proc.get("unclassified_runner_processes_observed"))
    unclassified_note = (
        " Daarnaast zijn " + str(min(unclassified, 20000)) +
        " runnerprocess(en) niet aan een bekende executable-familie gekoppeld; "
        "de snapshot is onvolledig en bewijst geen listener-afwezigheid."
        if unclassified else ""
    )
    service = proc.get("zcloud_service_cgroup")
    if (listeners and isinstance(service, dict)
            and service.get("status") == "OBSERVED_COMPLETE"
            and _number(service.get("listener_in_expected_unit")) == 0
            and _number(service.get("listener_outside_expected_unit")) == listeners):
        return (
            " De waargenomen zCloud-listener valt buiten de verwachte service-cgroup; "
            "dit is alleen lokale procesidentiteit en autoriseert geen herstart."
        )
    if listeners:
        return (
            " Er is lokaal een zCloud-listener gezien; dit bevestigt geen "
            "GitHub-registratie, toewijzing of succesvolle job."
            + unclassified_note
        )
    if workers:
        return (
            " Er is lokaal een zCloud-worker zonder waargenomen listener; "
            "onderbreek de actieve job niet." + unclassified_note
        )
    if proc.get("source_complete") is True:
        return (
            " De complete lokale processnapshot zag geen zCloud-listener; "
            "remote runnerregistratie blijft apart te verifiëren."
        )
    return " De lokale runnerprocesbron is onvolledig." + unclassified_note


def suggested_actions(packet):
    """Return a bounded prioritized list; observation is never authorization."""
    result = []
    # Keep queued records with existing worker slots out of any new
    # admission. The private desk deliberately never reveals slot IDs.
    untrusted_slot_lanes = _number(packet.get("queued_assigned_lanes_untrusted"))
    if untrusted_slot_lanes:
        result.append(action(
            "queued-slot-lane-origin-owner-review", "urgent",
            f"{untrusted_slot_lanes} gereserveerde wachtrijtaken hebben een "
            "onbetrouwbare lane-/profielherkomst.",
            "Behoud bestaande reserveringen; vraag de huidige runtime-eigenaar "
            "om het oorspronkelijke execution_lane record met de echte "
            "slottoewijzing te reconciliëren vóór nieuwe parallelle opdrachten.",
            "HOLD_ASSIGNED_LANE_PROVENANCE",
        ))
    unknown_slot_scopes = _number(packet.get("queued_assigned_scopes_not_declared"))
    if unknown_slot_scopes:
        result.append(action(
            "queued-slot-write-scope-owner-review", "urgent",
            f"{unknown_slot_scopes} gereserveerde wachtrijtaken missen een "
            "expliciete geldige schrijfscope.",
            "Laat de huidige claimeigenaar de werkelijke bestandscope vastleggen; "
            "geen extra schrijfworker, slotvrijgave of claimovername.",
            "HOLD_ASSIGNED_SCOPE_UNKNOWN",
        ))
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
                "Het lokale vierbaanprofiel of de actuele cap van vijf workers wijkt af.",
                "Laat de runtime-eigenaar het actuele portfolio-recovery profiel, "
                "ai_worker_cap=5 en de werkelijk geladen service vergelijken. "
                "Behoud bestaande claims; geen automatische opschaling of overname.",
                "HOLD_LOCAL_CONTRACT_PROFILE_MISMATCH",
            ))
        elif contract.get("status") != "OBSERVED_LOCAL_FILE":
            result.append(action(
                "local-contract-unverified-owner-review", "urgent",
                "Het lokale workercontract is niet betrouwbaar geobserveerd.",
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
    # Local host evidence is corroborating only. Incomplete coverage is
    # itself an owner-review HOLD for new work, even when no known metric
    # crosses a pressure threshold; UNKNOWN must never read as healthy.
    pressure = packet.get("host_pressure")
    if isinstance(pressure, dict):
        pressure_status = pressure.get("status")
        hint = pressure.get("pressure_hint")
        coverage = pressure.get("source_coverage")
        labels = {
            "meminfo": "RAM/swap", "loadavg": "load/CPU",
            "root_disk": "root-schijfruimte/inodes", "psi": "PSI",
        }
        incomplete = []
        if isinstance(coverage, dict):
            for key in ("meminfo", "loadavg", "root_disk", "psi"):
                value = coverage.get(key)
                if value in ("UNKNOWN", "PARTIAL"):
                    incomplete.append(labels[key] + "=" + value)
        coverage_detail = (
            " Onvolledige bronnen: " + ", ".join(incomplete) + "."
            if incomplete else ""
        )
        if (pressure_status in ("HOST_SNAPSHOT_OBSERVED", "HOST_PARTIAL")
                and hint in ("HIGH_LOCAL_PRESSURE", "ELEVATED_LOCAL_PRESSURE")):
            result.append(action(
                "host-pressure-owner-review",
                "urgent" if hint == "HIGH_LOCAL_PRESSURE" else "review",
                "De lokale host heeft verhoogde RAM-, swap-, CPU-, root-schijf- of inodecapaciteitsdruk; "
                "dit is geen bewijs dat een GitHub-job vastzit."
                + _psi_owner_context(pressure)
                + _runner_pressure_context(packet)
                + coverage_detail,
                "Behoud bestaande jobs en claims. Vergelijk cgroup-limieten, "
                "runner-toewijzing, procfs-PSI en workerprogressie met de bevoegde eigenaar "
                "vóór capaciteitsverhoging, opslagopschoning of serviceactie. "
                "Controleer ook alle hierboven ontbrekende of partiële bronnen."
                if incomplete else
                "Behoud bestaande jobs en claims. Vergelijk cgroup-limieten, "
                "runner-toewijzing, procfs-PSI en workerprogressie met de bevoegde eigenaar "
                "vóór capaciteitsverhoging, opslagopschoning of serviceactie.",
                "HOLD_CAPACITY_OWNER_REVIEW",
            ))
        elif pressure_status != "HOST_SNAPSHOT_OBSERVED" or hint == "UNKNOWN":
            detail = (
                coverage_detail
                if coverage_detail else
                " De exacte ontbrekende bron is niet volledig waargenomen."
            )
            result.append(action(
                "host-pressure-source-unknown", "review",
                "Hostcapaciteit is gedeeltelijk of UNKNOWN; geen gemeten drempel "
                "betekent zonder volledige dekking niet dat de host gezond is."
                + detail,
                "Behoud bestaande jobs en claims. Laat de bevoegde hosteigenaar "
                "de ontbrekende RAM/swap-, loadavg-, root-schijf- of PSI-bron "
                "verifiëren voordat nieuwe workers worden toegelaten; geen restart "
                "of claimwijziging op basis van ontbrekende telemetrie.",
                "HOLD_CAPACITY_SOURCE_UNVERIFIED",
            ))
    # Runner /proc only corroborates local process presence. It cannot prove
    # GitHub runner registration, labels, job assignment or successful CI.
    local_proc = packet.get("runner_processes")
    if isinstance(local_proc, dict):
        families = local_proc.get("families") or {}
        cloud = families.get("zCloud", {}) if isinstance(families, dict) else {}
        listener_count = _number(cloud.get("listeners"))
        worker_count = _number(cloud.get("workers"))
        service = local_proc.get("zcloud_service_cgroup")
        service_status = service.get("status") if isinstance(service, dict) else None
        listener_in_unit = _number(service.get("listener_in_expected_unit")) if isinstance(service, dict) else 0
        listener_outside_unit = _number(service.get("listener_outside_expected_unit")) if isinstance(service, dict) else 0
        worker_in_unit = _number(service.get("worker_in_expected_unit")) if isinstance(service, dict) else 0
        worker_outside_unit = _number(service.get("worker_outside_expected_unit")) if isinstance(service, dict) else 0
        listener_mismatch = False
        worker_mismatch = False
        if service_status == "OBSERVED_COMPLETE":
            if (listener_count and listener_in_unit == 0
                    and listener_outside_unit == listener_count):
                result.append(action(
                    "zcloud-listener-service-cgroup-mismatch", "urgent",
                    "De waargenomen zCloud-listener valt buiten de verwachte systemd-service-cgroup.",
                    "Laat de bevoegde runtime-eigenaar de service-/cgroup-identiteit, GitHub-runnerregistratie "
                    "en een echte jobreceipt vergelijken. Deze lokale observatie autoriseert geen herstart.",
                    "HOLD_RUNNER_SERVICE_IDENTITY_RECONCILIATION",
                ))
                listener_mismatch = True
            elif listener_count and listener_in_unit > 0 and listener_outside_unit > 0:
                result.append(action(
                    "zcloud-listener-service-cgroup-mixed", "urgent",
                    "Zowel listeners binnen als buiten de verwachte zCloud-service-cgroup zijn waargenomen.",
                    "Laat de bevoegde runtime-eigenaar de afzonderlijke service-/cgroup-identiteiten en GitHub-jobreceipt "
                    "reconciliëren; beëindig geen proces en herstart de service niet.",
                    "HOLD_MULTIPLE_RUNNER_SERVICE_IDENTITIES",
                ))
                listener_mismatch = True
            if (worker_count and worker_in_unit == 0
                    and worker_outside_unit == worker_count):
                result.append(action(
                    "zcloud-worker-service-cgroup-mismatch", "urgent",
                    "De waargenomen zCloud-worker valt buiten de verwachte systemd-service-cgroup.",
                    "Behoud de actieve worker/job. Laat de bevoegde runtime-eigenaar de service-/cgroup-identiteit "
                    "en GitHub-jobreceipt vergelijken; beëindig of herstart niets op basis van deze observatie.",
                    "HOLD_RUNNER_WORKER_IDENTITY_RECONCILIATION",
                ))
                worker_mismatch = True
            elif worker_count and worker_in_unit > 0 and worker_outside_unit > 0:
                result.append(action(
                    "zcloud-worker-service-cgroup-mixed", "urgent",
                    "Zowel workers binnen als buiten de verwachte zCloud-service-cgroup zijn waargenomen.",
                    "Behoud alle actieve workers en laat de bevoegde runtime-eigenaar de afzonderlijke "
                    "service-/cgroup-identiteiten reconciliëren; beëindig geen proces en herstart niet.",
                    "HOLD_MULTIPLE_RUNNER_SERVICE_IDENTITIES",
                ))
                worker_mismatch = True
        if listener_count and not listener_mismatch:
            result.append(action(
                "zcloud-listener-presence", "review",
                "Op de VPS is een zCloud Runner.Listener-proces waargenomen.",
                "Controleer de bestaande GitHub-jobs op runner_id, labels en werkelijke start; "
                "procesaanwezigheid is geen bewijs van geslaagde CI.",
                "GITHUB_RUNNER_ASSIGNMENT_NOT_OBSERVED",
            ))
        elif not listener_count and worker_count > 0 and not worker_mismatch:
            result.append(action(
                "zcloud-worker-without-listener-observation",
                "urgent" if local_proc.get("source_complete") is True else "review",
                "Er draait een zCloud Runner.Worker, maar de lokale snapshot ziet geen zCloud-listener.",
                "Behoud de bestaande worker/job; vraag de bevoegde eigenaar de listener en "
                "GitHub runner-toewijzing onafhankelijk te controleren zonder blanket restart.",
                "ACTIVE_WORKER_PROTECTED_LISTENER_UNKNOWN",
            ))
        elif not listener_count and not worker_count and local_proc.get("source_complete") is True:
            result.append(action(
                "zcloud-listener-absent", "urgent",
                "De volledige lokale processnapshot toont geen zCloud-listener.",
                "Laat de bevoegde runtime-eigenaar de service en GitHub-runnerregistratie inspecteren; "
                "onderbreek geen actieve worker.",
                "RUNNER_OWNER_INSPECTION_REQUIRED",
            ))
        elif not listener_count and not worker_count:
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
    # The short owner worklist already includes active-only P0 projects,
    # but the top nine incident cards used to show only QUEUED projects.
    # Preserve one explicit active-owner follow-up without inventing a
    # stall, releasing a lease or implying that this work is claimable.
    for entry in packet.get("owner_review_shortlist", [])[:6]:
        if not isinstance(entry, dict) or entry.get("owner_review_kind") != "P0_ACTIVE_OWNER":
            continue
        name = entry.get("project")
        active = _number(entry.get("active_p0"))
        if not isinstance(name, str) or not (0 < len(name) <= 32 and active):
            continue
        result.append(action(
            "p0-active-owner-" + name.casefold(),
            "review",
            f"{name}: {active} lokale P0-taak/taken zijn al in behandeling; "
            "geen nieuwe wachtende P0-taak in deze steekproef.",
            "Controleer met de bestaande eigenaar de actuele voortgang, "
            "claimklok en gekoppelde PR; onderbreek, herclaim of herstart niets. "
            "Actief werk is op zichzelf geen vastgelopen incident.",
            "ACTIVE_P0_OWNER_PROTECTED",
        ))
        break
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
        if identifier.startswith("p0-project-"):
            return 2
        if identifier.startswith("p0-active-owner-"):
            return 3
        if identifier.startswith("p1-project-"):
            return 4
        if item["severity"] == "review":
            return 5
        return 6
    ranked = sorted(result, key=display_rank)
    if len(ranked) <= MAX_ACTIONS:
        return ranked

    # A bounded incident desk must not silently make the tail of the action
    # list disappear. Reserve one visible slot for a machine-readable and
    # human-readable overflow receipt while retaining the eight highest-ranked
    # concrete actions unchanged.
    visible = ranked[:MAX_ACTIONS - 1]
    omitted = ranked[MAX_ACTIONS - 1:]
    severity_counts = {
        level: sum(item.get("severity") == level for item in omitted)
        for level in ("urgent", "review", "info")
    }
    safe_codes = []
    for item in omitted:
        code = item.get("id")
        if (isinstance(code, str) and 0 < len(code) <= 64
                and code[0].isascii() and code[0].isalnum()
                and all(ch.isascii() and (
                    ch.isdigit() or ch == "-"
                    or (ch.isalpha() and ch.islower())
                ) for ch in code)):
            safe_codes.append(code)
    code_sample = safe_codes[:8]
    sample_note = (
        " Voorbeelden: " + ", ".join(code_sample) + "."
        if code_sample else ""
    )
    if len(safe_codes) > len(code_sample):
        sample_note += " De lijst met weggelaten actietypen is zelf begrensd."
    if len(omitted) > len(safe_codes):
        sample_note += " Niet alle weggelaten acties hadden een veilig toonbare code."
    overflow_severity = (
        "urgent" if severity_counts["urgent"] else
        "review" if severity_counts["review"] else "info"
    )
    overflow = action(
        "action-card-overflow",
        overflow_severity,
        f"{len(omitted)} aanvullende herstelactie(s) vallen buiten de "
        f"weergavelimiet van {MAX_ACTIONS}; urgent={severity_counts['urgent']}, "
        f"review={severity_counts['review']}, info={severity_counts['info']}."
        + sample_note,
        "Open het volledige private eigenaarrapport en beoordeel ook de "
        "weggelaten acties. Deze begrensde kaartlijst is niet volledig; "
        "geen actie wordt hierdoor geautoriseerd.",
        "ACTION_CARDS_TRUNCATED_REQUIRES_FULL_REPORT",
    )
    overflow["omitted_count"] = len(omitted)
    overflow["omitted_severity_counts"] = severity_counts
    overflow["omitted_action_codes"] = code_sample
    overflow["omitted_action_codes_truncated"] = len(omitted) > len(code_sample)
    return visible + [overflow]
