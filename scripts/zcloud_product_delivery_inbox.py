#!/usr/bin/env python3
"""Private offline operator inbox from an already authorized queue API snapshot.

No network, task claims, retries, CI dispatch, secret printing or runtime writes.
This is a presentation tool, never a recovery decision/authorization service.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_ROWS = 20
PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,55}$")
QUEUE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
KINDS = {
    "active_owner_evidence_gap": ("Ontbrekende claimgegevens", "controleren"),
    "repeated_claim_attempts": ("Herhaald geclaimd", "onderzoeken"),
    "queued_product_lead": ("Onafhankelijk productwerk", "vrij werk"),
}
NEXT_STEPS = {
    "active_owner_evidence_gap": {"owner_reconcile_active_claim_evidence_before_any_new_work"},
    "repeated_claim_attempts": {"review_attempt_history_no_automatic_retry"},
    "queued_product_lead": {
        "check_active_claims_before_any_new_work",
        "owner_reconcile_active_claim_evidence_before_any_new_work",
        "owner_review_attempt_history_no_automatic_retry",
        "verify_claim_scope_capacity_and_owner",
        "owner_rescope_required_for_held_work",
        "no_product_candidate_observed",
    },
}
MISSING_FIELDS = {"worker_slot", "claimed_at", "claim_expires"}
PRIORITIES = {"P0", "P1", "P2", "P3"}

CSS = """
:root{font-family:system-ui,-apple-system,sans-serif;color:#e8ebf4;background:#10131a}
*{box-sizing:border-box}body{margin:0;line-height:1.55}
main{max-width:1050px;margin:auto;padding:28px 16px 72px}
h1{font-size:clamp(1.65rem,3vw,2.5rem);line-height:1.2;margin:.25em 0}
h2{font-size:1.1rem;margin:0 0 .45rem}
p{max-width:78ch}.eyebrow{letter-spacing:.14em;text-transform:uppercase;color:#a6bbfb;font-size:.8rem;font-weight:700}
.subtle{color:#bac3d3}.notice{border:1px solid #626f85;background:#1b2230;padding:14px 18px;border-radius:12px;margin:22px 0}
.warn{border-color:#c8943e;background:#302719}.critical{border-color:#dc7373}
.toolbar{display:grid;grid-template-columns:2fr 1fr 1fr;gap:12px;margin:24px 0}
label{display:grid;gap:4px;font-size:.85rem;color:#cbd2df}
input,select{min-width:0;border:1px solid #6a7486;background:#222938;color:#fff;padding:11px;border-radius:8px;font:inherit}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,285px),1fr));gap:12px}
.card{border:1px solid #475267;background:#1a202b;padding:17px;border-radius:12px;min-width:0;overflow-wrap:anywhere}
.meta{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}.pill{display:inline-block;border:1px solid #747e91;border-radius:22px;padding:2px 9px;font-size:.75rem}
.p0{border-color:#ec8b8b;color:#ffb0b0}.p1{border-color:#f5ca70;color:#f5d89f}.p2{color:#b7c9fc}
.key{font-family:ui-monospace,monospace;color:#d2ddf4;font-size:.88rem;overflow-wrap:anywhere}
small{color:#c2cbd7}.footer{border-top:1px solid #434b59;margin-top:32px;padding-top:16px}
[hidden]{display:none!important}@media(max-width:680px){.toolbar{grid-template-columns:1fr}}
"""
JS = """
const search=document.getElementById('search');
const kind=document.getElementById('kind');
const priority=document.getElementById('priority');
const cards=Array.from(document.querySelectorAll('.card'));
const count=document.getElementById('count');
function apply(){
 const term=search.value.trim().toLocaleLowerCase();
 let visible=0;
 for(const card of cards){
  const allowed=(!kind.value||card.dataset.kind===kind.value)
   &&(!priority.value||card.dataset.priority===priority.value)
   &&(!term||card.textContent.toLocaleLowerCase().includes(term));
  card.hidden=!allowed;
  if(allowed)visible++;
 }
 count.textContent=String(visible)+' van '+String(cards.length)+' zichtbaar';
}
for(const input of [search,kind,priority])input.addEventListener('input',apply);
apply();
"""


def _unique(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("dubbele sleutel in private queue-bron")
        output[key] = value
    return output


def _bounded_private_read(path: Path) -> dict:
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("bron moet een regulier, niet-gelinkt bestand zijn")
        if info.st_uid != os.geteuid() or (info.st_mode & 0o077):
            raise ValueError("private bron moet eigen 0600/0400-bestand zijn")
        if info.st_size > MAX_INPUT_BYTES:
            raise ValueError("private queue-bron is te groot")
        chunks = []
        total = 0
        while True:
            part = os.read(fd, min(65536, MAX_INPUT_BYTES + 1 - total))
            if not part:
                break
            total += len(part)
            if total > MAX_INPUT_BYTES:
                raise ValueError("private queue-bron is te groot")
            chunks.append(part)
        data = json.loads(b"".join(chunks).decode("utf-8"),
                          object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              ValueError("niet-eindig getal in bron")))
    finally:
        os.close(fd)
    if not isinstance(data, dict):
        raise ValueError("ongeldige private queue-root")
    return data


def _safe_int(value, *, minimum=0, maximum=10_000_000_000):
    return type(value) is int and minimum <= value <= maximum


def _bounded_str(value, maximum=160):
    return isinstance(value, str) and len(value) <= maximum


def _source_clock(value):
    if not _bounded_str(value, 96) or not value:
        return "Bronmoment onbekend", "UNKNOWN: ontbrekend bronmoment"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return value, "UNKNOWN: bronklok zonder timezone"
        elapsed = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
        if elapsed < -120:
            return value, "UNKNOWN: bronklok ligt in de toekomst"
        if elapsed > 6 * 3600:
            return value, "HISTORISCH: bronmoment ouder dan zes uur"
        return value, "Recente snapshot: geen bewijs van actuele live-claimstatus"
    except ValueError:
        return "Bronmoment onbekend", "UNKNOWN: ongeldig bronmoment"


def _validate(payload: dict):
    policy = payload.get("delivery_policy")
    if not isinstance(policy, dict):
        raise ValueError("verwacht de geauthenticeerde /api/portfolio-queue delivery_policy")
    if (policy.get("policy") != "product-delivery-2026-10-17"
            or policy.get("source_coverage") != "visible_api_rows_only_not_full_queue"
            or policy.get("operator_workorder_inbox_is_advisory_only") is not True
            or policy.get("not_claimed_or_authorized") is not True
            or policy.get("admission_scope_lease_and_capacity_not_verified") is not True):
        raise ValueError("onvolledig of onbevoegd operator-receipt")
    if type(policy.get("operator_workorder_inbox_truncated")) is not bool:
        raise ValueError("bron mist verplichte truncatie-indicator")
    rows = policy.get("operator_workorder_inbox")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("ongeldige of onbeperkte operator-inbox")
    seen = set()
    clean = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("ongeldig owner-werkorder")
        kind = row.get("kind")
        project, queue = row.get("project_id"), row.get("queue_id")
        priority, step = row.get("priority"), row.get("operator_next_step")
        if (kind not in KINDS or not isinstance(project, str)
                or not PROJECT_ID.fullmatch(project)
                or not isinstance(queue, str) or not QUEUE_ID.fullmatch(queue)
                or priority not in PRIORITIES
                or step not in NEXT_STEPS[kind]
                or row.get("owner_only") is not True
                or row.get("not_claimed_or_authorized") is not True):
            raise ValueError("onbetrouwbare owner-werkordergegevens")
        identity = (project, queue)
        if identity in seen:
            raise ValueError("dubbele taakidentiteit in operator-inbox")
        seen.add(identity)
        detail = ""
        if kind == "active_owner_evidence_gap":
            missing = row.get("missing_fields")
            status = row.get("status")
            if (not isinstance(missing, list) or not 1 <= len(missing) <= 3
                    or len(set(map(str, missing))) != len(missing)
                    or any(field not in MISSING_FIELDS for field in missing)
                    or status not in ("claimed", "running", "verifying")):
                raise ValueError("ongeldig actief claimbewijs")
            detail = "Ontbreekt: " + ", ".join(missing) + " · actieve status: " + status
        elif kind == "repeated_claim_attempts":
            attempts = row.get("claim_attempts_observed")
            capped = row.get("claim_attempts_capped")
            if (not _safe_int(attempts, minimum=3, maximum=1_000_000_000)
                    or type(capped) is not bool
                    or (capped and attempts != 1_000_000_000)
                    or row.get("attempts_are_not_task_failures") is not True):
                raise ValueError("onbetrouwbare herhaalclaim")
            comparator = "minstens " if capped else ""
            detail = ("Geobserveerde claims: " + comparator + str(attempts)
                      + " (geen bewezen fout)")
        else:
            owners = row.get("active_claims_visible")
            if not _safe_int(owners, maximum=1_000_000):
                raise ValueError("ongeldige actieve eigenaarstelling")
            detail = "Al actieve taken in project: " + str(owners)
        clean.append({
            "kind": kind, "project": project, "queue": queue,
            "priority": priority, "step": step, "detail": detail,
        })
    return clean, policy["operator_workorder_inbox_truncated"]


def _render(payload, rows, truncated):
    esc = lambda value: html.escape(str(value), quote=True)
    source, age = _source_clock(payload.get("time"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    count_by_kind = {key: sum(row["kind"] == key for row in rows) for key in KINDS}
    cards = []
    for row in rows:
        title = KINDS[row["kind"]][0]
        cards.append(
            '<article class="card" data-kind="' + esc(row["kind"]) +
            '" data-priority="' + esc(row["priority"]) + '">' +
            '<div class="eyebrow">' + esc(title) + '</div>' +
            '<h2>' + esc(row["project"]) + '</h2>' +
            '<div class="meta"><span class="pill ' + esc(row["priority"].lower()) +
            '">' + esc(row["priority"]) + '</span>' +
            '<span class="pill">Alleen owner</span></div>' +
            '<div class="key">' + esc(row["queue"]) + '</div>' +
            '<p>' + esc(row["detail"]) + '</p>' +
            '<small>Vervolg: ' + esc(row["step"]) +
            '. Geen claim-, retry- of mergebevoegdheid.</small></article>'
        )
    style_hash = base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()
    script_hash = base64.b64encode(hashlib.sha256(JS.encode()).digest()).decode()
    csp = (
        "default-src 'none'; base-uri 'none'; form-action 'none'; "
        "connect-src 'none'; img-src 'none'; object-src 'none'; "
        "style-src 'sha256-" + style_hash + "'; "
        "script-src 'sha256-" + script_hash + "'"
    )
    return (
        '<!doctype html><html lang="nl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="' + esc(csp) + '">'
        '<meta name="referrer" content="no-referrer">'
        '<title>zSentinel · privé herstelwerkbak</title><style>' + CSS +
        '</style></head><body><main>'
        '<p class="eyebrow">zSentinel · alleen lokale operator</p>'
        '<h1>Herstel en onafhankelijk productwerk</h1>'
        '<p class="subtle">Moment van bron: ' + esc(source) +
        ' · gegenereerd: ' + esc(now) + '</p>'
        '<div class="notice warn" role="status"><strong>' + esc(age) +
        '</strong><p>Gedeeltelijke wachtrijbron. Geen live-autorisatie, '
        'geen claimovername, geen automatische retry en geen bewijs dat '
        'een PR op main is gemerged.</p></div>' +
        ('<div class="notice critical"><strong>INBOX AFGEKAPT</strong>'
         '<p>Er zijn meer mogelijke herstelstappen dan deze private selectie toont.</p>'
         '</div>' if truncated else
         '<div class="notice">Inboxselectie niet afgekapt; de onderliggende '
         'queuebron blijft gedeeltelijk.</div>') +
        '<p class="subtle">Geobserveerd: ' +
        ' · '.join(esc(KINDS[key][0] + ": " + str(n)) for key, n in count_by_kind.items()) +
        '</p><section aria-label="Filters" class="toolbar">' +
        '<label>Zoek op project, taak of actie'
        '<input id="search" type="search" autocomplete="off"></label>' +
        '<label>Werksoort<select id="kind"><option value="">Alle soorten</option>' +
        ''.join('<option value="' + esc(key) + '">' + esc(value[0]) + '</option>'
                for key, value in KINDS.items()) +
        '</select></label><label>Prioriteit<select id="priority">'
        '<option value="">Alle prioriteiten</option>' +
        ''.join('<option>' + priority + '</option>' for priority in ("P0", "P1", "P2", "P3")) +
        '</select></label></section><p id="count" role="status"></p>'
        '<section class="cards" aria-label="Operator-werkorders">' +
        ''.join(cards) +
        '</section><p class="footer">Deze statische pagina leest geen internet, '
        'bewaart geen tokens en neemt geen beheersactie. '
        'Vervolgacties zijn uitsluitend voor de bevoegde eigenaar.</p>'
        '<script>' + JS + '</script></main></body></html>\n'
    )



def _render_markdown(payload, rows, truncated):
    """Private text briefing for the responsible owner, not an action command."""
    def clean(value):
        # Escape inline markup even if a formerly fixed field changes later.
        return re.sub(r"([\\*_{}\[\]()#+\-.!|>])", r"\\\1", str(value))

    source, age = _source_clock(payload.get("time"))
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# zSentinel - private hersteloverdracht",
        "",
        "Bronmoment: " + clean(source),
        "Gegenereerd: " + clean(timestamp),
        "",
        "**" + clean(age) + "**",
        "",
        "Deze snapshot is altijd gedeeltelijk, niet live geverifieerd en",
        "geen toestemming voor claim, retry, CI, merge of deploy.",
        "",
    ]
    if truncated:
        lines.extend([
            "**AFGEKAPT: er zijn meer mogelijke werkorders dan hier getoond.**",
            "",
        ])
    if not rows:
        lines.extend(["Geen werkorders in dit begrensde venster.", ""])
    for kind in KINDS:
        group = [row for row in rows if row["kind"] == kind]
        if not group:
            continue
        lines.extend(["## " + clean(KINDS[kind][0]), ""])
        for row in group:
            lines.extend([
                "- **" + clean(row["priority"]) + " / " +
                clean(row["project"]) + "** - " + clean(row["queue"]),
                "  - " + clean(row["detail"]),
                "  - Owner-actie: " + clean(row["step"]),
                "  - Status: alleen handmatige beoordeling, geen overname.",
            ])
        lines.append("")
    lines.append("Gebaseerd op een private JSON-export. "
                 "Bestandsrechten bewijzen de bronherkomst niet.")
    return "\n".join(lines) + "\n"



def _private_create(path: Path, document: str):
    # Bind the final file creation to one held directory descriptor. Checking
    # parent.stat() and opening by an absolute string permits directory-swap
    # races if another local process changes the parent between those steps.
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    directory = os.open(str(path.parent), dir_flags)
    created = False
    try:
        info = os.fstat(directory)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or (info.st_mode & 0o077)):
            raise ValueError("uitvoermap moet eigen private 0700-map zijn")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path.name, flags, 0o600, dir_fd=directory)
        created = True
        try:
            with os.fdopen(fd, "wb") as target:
                target.write(document.encode("utf-8"))
        except Exception:
            # Remove ONLY a file created by this invocation, and only inside
            # the directory whose descriptor remains held.
            if created:
                os.unlink(path.name, dir_fd=directory)
            raise
    finally:
        os.close(directory)


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--input", required=True, type=Path,
                     help="Private 0600 JSON van geautoriseerde /api/portfolio-queue")
    cli.add_argument("--output", required=True, type=Path,
                     help="Nieuw privébestand in eigen 0700 directory")
    cli.add_argument("--format", choices=("html", "markdown"), default="html",
                     help="Offline HTML of Markdown-eigenaarsoverdracht")
    opts = cli.parse_args(argv)
    try:
        payload = _bounded_private_read(opts.input)
        rows, truncated = _validate(payload)
        document = (_render(payload, rows, truncated) if opts.format == "html"
                    else _render_markdown(payload, rows, truncated))
        _private_create(opts.output, document)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        # Do not print paths, source contents, arbitrary exception payload,
        # bearer credentials, or queue IDs in stderr.
        print("zSentinel: private invoer/uitvoer geweigerd; controleer rechten, "
              "broncontract en bestemming.", file=sys.stderr)
        return 2
    print("zSentinel: private offline operatorpagina aangemaakt; "
          "geen runtimeactie uitgevoerd.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
