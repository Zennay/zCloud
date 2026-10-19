#!/usr/bin/env python3
"""zSentinel private incident follow-up desk from an existing read-only decision.

Generate a self-contained offline operator view; local human dispositions are
neither recovery receipts nor claim/merge/dispatch authority. No network I/O.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from html import escape

IDENT = re.compile(r"^[a-f0-9]{32}$")
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
PROBLEM = re.compile(r"^[A-Za-z0-9_]{1,120}$")
STATUSES = frozenset(("human_action_required", "read_only_diagnosis_required"))
MAX_INCIDENTS = 200
MAX_TEXT = 600


def _stamp(value):
    if type(value) is not str or len(value) > 45:
        raise ValueError("untrusted incident datetime")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("untrusted incident datetime") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("incident datetime lacks zone")
    if dt > datetime.now(timezone.utc) + timedelta(minutes=2):
        raise ValueError("incident timestamp beyond tolerated source clock skew")
    return dt.astimezone(timezone.utc).isoformat()


def _text(value, limit=MAX_TEXT):
    if (type(value) is not str or not 1 <= len(value) <= limit or
            any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("unsafe incident field")
    return value


def _source(decision):
    if (type(decision) is not dict
            or type(decision.get("schema_version")) is not int
            or decision["schema_version"] != 1
            or any(decision.get(k) is not False for k in (
                "mutations_performed", "queue_claims_changed", "ci_retried",
                "deployment_authorized", "watch_delivery_performed"))):
        raise ValueError("incident desk requires non-authorizing decision")
    raw = decision.get("incidents")
    if type(raw) is not list or len(raw) > MAX_INCIDENTS:
        raise ValueError("unbounded incident inventory")
    source_time = _stamp(decision.get("observed_at_utc"))
    items = []
    seen = set()
    for entry in raw:
        if type(entry) is not dict:
            raise ValueError("malformed incident")
        key = entry.get("incident_id")
        pid = entry.get("project_id")
        kind = entry.get("problem")
        if (type(key) is not str or not IDENT.fullmatch(key) or key in seen or
                type(pid) is not str or not SLUG.fullmatch(pid) or
                type(kind) is not str or not PROBLEM.fullmatch(kind) or
                entry.get("severity") not in ("P0", "P1", "P2", "P3") or
                type(entry.get("human_escalation")) is not bool or
                entry.get("status") not in STATUSES):
            raise ValueError("noncanonical incident identity")
        seen.add(key)
        first = _stamp(entry.get("first_seen"))
        last = _stamp(entry.get("last_seen"))
        # Review target may be in the future; parse it separately.
        due = entry.get("target_review_by")
        if type(due) is not str or len(due) > 45:
            raise ValueError("invalid review target")
        try:
            due_dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid review target") from exc
        if due_dt.tzinfo is None or due_dt.utcoffset() is None:
            raise ValueError("review target missing timezone")
        if first > last or last > datetime.fromisoformat(source_time):
            raise ValueError("incident receipt time inconsistent")
        items.append({
            "id": key, "project": pid, "priority": entry["severity"],
            "problem": kind, "owner": _text(entry.get("owner"), 100),
            "action": _text(entry.get("action"), 600),
            "status": entry["status"],
            "human": entry["human_escalation"],
            "first": first, "last": last,
            "review_by": due_dt.astimezone(timezone.utc).isoformat(),
        })
    items.sort(key=lambda x: (
        ("P0", "P1", "P2", "P3").index(x["priority"]),
        not x["human"], x["review_by"], x["id"]))
    canonical = json.dumps({"time": source_time, "incidents": items},
                           ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return {"observed_at": source_time, "incidents": items,
            "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "source_partial": True, "authorizes_actions": False}


def write_private_html(path, rendered):
    """Create-only private static operator artifact; never overwrite an export."""
    target = Path(path)
    parent = target.parent
    if (not isinstance(rendered, str) or not rendered.startswith("<!doctype html>")
            or target.suffix.lower() != ".html"
            or not parent.is_dir() or parent.is_symlink()
            or not hasattr(os, "O_NOFOLLOW")):
        raise ValueError("private desk requires an existing safe directory and .html output")
    info = parent.stat()
    if (info.st_uid != os.geteuid() or
            stat.S_IMODE(info.st_mode) & 0o022):
        raise ValueError("private desk directory must be owned and not group/world writable")
    raw = rendered.encode("utf-8")
    if len(raw) > 1_000_000:
        raise ValueError("bounded private desk HTML required")
    fd, temporary = tempfile.mkstemp(prefix=".zsentinel-desk-", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link existing safe temporary into a new *absent* target.
        # Fails if target already exists (regular file or symlink).
        os.link(temporary, target)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def review_html(decision):
    """One offline HTML view + bounded private operator handoff in browser."""
    source = _source(decision)
    payload = json.dumps(source, ensure_ascii=True, separators=(",", ":"))
    payload = (payload.replace("<", "\\u003c").replace(">", "\\u003e")
               .replace("&", "\\u0026"))
    html = r"""<!doctype html><html lang="nl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy"
 content="default-src 'none';script-src 'unsafe-inline';style-src 'unsafe-inline';
 connect-src 'none';img-src 'none';form-action 'none';object-src 'none';base-uri 'none'">
<title>zSentinel | Privé incidentenbalie</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,sans-serif}
*{box-sizing:border-box}body{background:#0d1420;color:#eff4fb;max-width:1160px;margin:auto;padding:clamp(16px,3vw,36px)}
a{color:#bde7ff}h1{font-size:clamp(28px,4vw,42px);letter-spacing:-.03em}h2{font-size:21px}
p{line-height:1.55;color:#d2dce8}.banner{background:#2a2924;border-left:4px solid #ffcc85;padding:12px 16px;border-radius:8px}
.meta{color:#adc2d6;font-size:13px}.row{display:flex;align-items:end;gap:12px;flex-wrap:wrap}
label{display:grid;gap:6px;margin:12px 0}select,input,textarea,button{font:inherit}
select,input,textarea{border:1px solid #66809d;background:#18283d;color:white;border-radius:7px;padding:9px}
textarea{min-height:96px;width:100%;resize:vertical}button{border:1px solid #94c7ea;background:#b9e4ff;color:#10243b;padding:10px 16px;border-radius:8px;font-weight:700;cursor:pointer}
button:focus-visible,select:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid #fff0a1;outline-offset:2px}
.panel{background:#172538;border:1px solid #415c79;border-radius:12px;margin:13px 0;padding:16px}
.panel.human{border-left:5px solid #ffcb8e}.panel.wait{border-left:5px solid #90bddf}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.scroll{max-height:70vh;overflow:auto}
.tag{display:inline-block;border:1px solid #9eafc6;border-radius:16px;padding:2px 7px;font-size:12px;margin-right:5px}
.line{margin:9px 0}.line strong{display:inline-block;min-width:84px}
small{color:#bbcbdc}code{overflow-wrap:anywhere}#detail{min-height:320px}
@media(max-width:760px){.grid{grid-template-columns:1fr}.scroll{max-height:320px}}
@media print{body{color:black;background:white}.controls,.grid>div:first-child{display:none}.grid{display:block}}
</style>
<header><small>zCLOUD / zSENTINEL / READ-ONLY SOURCE</small>
<h1>Vliegende keep · incidentenbalie</h1>
<p>Een lokaal overzicht om concrete blokkades menselijk op te volgen. De bron is één begrensde momentopname;
de actieknoppen veranderen geen echte incidenten of claims.</p>
<p class="banner"><strong>GEEN herstel- of uitvoerrechten.</strong>
Statuskeuzes en notities bestaan alleen in dit browsertabblad totdat je een privé-overdracht downloadt.
Geen workflow, GitHub-write, calendar push, Notion-write, lease of deploy.</p>
<p class="meta" id="freshness"></p></header>
<div class="controls row">
<label>Project<select id="project"><option value="">Alle projecten</option></select></label>
<label>Ernst<select id="priority"><option value="">Alle prioriteiten</option>
<option>P0</option><option>P1</option><option>P2</option><option>P3</option></select></label>
<label>Soort<select id="human"><option value="">Alle blokkades</option>
<option value="yes">Menselijke actie nodig</option><option value="no">Alleen diagnose</option></select></label>
<label>Zoeken<input id="query" type="search" maxlength="80" placeholder="Project of probleem"></label>
</div>
<p id="count" aria-live="polite"></p>
<div class="grid"><div><h2>Actuele bronincidenten</h2><div class="scroll" id="items"></div></div>
<div><h2>Menselijke opvolging</h2><section id="detail" class="panel">
<p>Selecteer een incident. Geen incident geselecteerd betekent niet dat het portfolio gezond is.</p></section>
<section class="panel controls"><h2>Privé-overdracht</h2>
<p>Download alleen je eigen statuskeuzes en notities, exact gekoppeld aan deze momentopname.
Import van een ander bronmoment verandert niets.</p>
<div class="row"><button id="export" type="button">Download overdracht</button>
<label>Heropen exact dezelfde bron <input id="import" type="file" accept=".json,application/json"></label>
<label>Herbruik oude notities (opnieuw beoordelen) <input id="carry" type="file" accept=".json,application/json"></label></div>
<p id="feedback" aria-live="polite"></p></section></div></div>
<footer><p class="meta">Een ontbrekend incident kan door incomplete GitHub-bron, rate-limit,
scope of tijdvenster komen. Een lokale opvolgstatus bewijst geen eigenaargoedkeuring
of systeemherstel. Bronstatus blijft ongewijzigd.</p></footer>
<script>
"use strict";
const source=__SOURCE__;
const $=id=>document.getElementById(id);
const choices=["nog-niet-beoordeeld","eigenaar-benaderen","in-behandeling-bij-eigenaar",
               "extern-geparkeerd","bron-opnieuw-verifieren"];
const notes=Object.create(null);
let selected=null;
const el=(tag,txt,cls)=>{const n=document.createElement(tag);if(txt!==undefined)n.textContent=txt;if(cls)n.className=cls;return n};
function incidentLabel(x){return x.priority+" · "+x.project+" · "+x.problem}
function isFresh(){
 const delta=Date.now()-Date.parse(source.observed_at);
 const msg=(delta<0?"Systeemklok wijkt af;":delta>600000?"OUDER DAN 10 MINUTEN;":"Momentopname;");
 $("freshness").textContent=msg+" bron "+source.observed_at+"; dekking altijd gedeeltelijk. Bron-ID "+source.fingerprint.slice(0,12)+".";
}
for(const p of [...new Set(source.incidents.map(i=>i.project))].sort()){
 const opt=el("option",p);opt.value=p;$("project").append(opt);
}
function filtered(){const q=$("query").value.trim().toLowerCase();return source.incidents.filter(x=>
 (!$("project").value||x.project===$("project").value)&&
 (!$("priority").value||x.priority===$("priority").value)&&
 (!$("human").value||(x.human?"yes":"no")===$("human").value)&&
 (!q||(x.project+" "+x.problem+" "+x.owner).toLowerCase().includes(q)));
}
function render(){
 $("items").replaceChildren();
 const rows=filtered();$("count").textContent=rows.length+" incidenten binnen huidig filter (bronmaximaal "+source.incidents.length+").";
 if(!rows.length)$("items").append(el("p","Geen bronincidenten in dit filter. Geen all-clear."));
 for(const x of rows){
   const b=el("button",incidentLabel(x));b.style.width="100%";b.style.marginBottom="8px";b.style.textAlign="left";
   b.style.background=selected===x.id?"#e6f5ff":"#c4e6fc";
   b.onclick=()=>{selected=x.id;render()};
   const wrapper=el("div");wrapper.append(b);
   wrapper.append(el("small",x.human?"Eigenaaractie vereist":"Read-only diagnose · geen automatische actie"));
   $("items").append(wrapper);
 }
 const x=source.incidents.find(v=>v.id===selected);
 const box=$("detail");box.replaceChildren();
 if(!x){box.append(el("p","Selecteer een bronincident om de menselijke opvolging te bekijken."));return}
 const hd=el("h3",incidentLabel(x));box.append(hd);
 box.append(el("p","MENS NODIG: "+(x.human?"JA":"NEE, alleen veilige diagnose")+" · Owner: "+x.owner,"meta"));
 for(const [label,value] of [["Probleem",x.problem],["Actie",x.action],["Bronstatus",x.status]]){
   const p=el("p",undefined,"line");p.append(el("strong",label));p.append(document.createTextNode(" "+value));box.append(p)
 }
 box.append(el("p","Review uiterlijk "+x.review_by+" (adviserend; geen vastgelegde afspraak).","meta"));
 const sl=el("label","Mijn opvolgstatus (alleen privé)");const sel=el("select");
 for(const c of choices){const o=el("option",c.replaceAll("-"," "));o.value=c;sel.append(o)}
 sel.value=notes[x.id]?.status||choices[0];
 sel.onchange=()=>{if(!notes[x.id])notes[x.id]={status:choices[0],note:""};notes[x.id].status=sel.value;$("feedback").textContent="Lokale status aangepast, niet doorgestuurd."};
 sl.append(sel);box.append(sl);
 const nl=el("label","Mijn privénotitie (maximaal 800 tekens)");
 const ta=el("textarea");ta.maxLength=800;ta.value=notes[x.id]?.note||"";
 ta.oninput=()=>{if(!notes[x.id])notes[x.id]={status:choices[0],note:""};notes[x.id].note=ta.value;$("feedback").textContent="Notitie alleen in dit tabblad."};
 nl.append(ta);box.append(nl);
 box.append(el("small","Dit verandert NIET de incidentstatus, CI, PR, eigenaar of queue."));
}
for(const id of ["project","priority","human","query"])$(id).addEventListener(id==="query"?"input":"change",render);
function download(obj){
 const str=JSON.stringify(obj,null,2);if(str.length>100000)throw Error("Overdracht te groot");
 const url=URL.createObjectURL(new Blob([str+"\n"],{type:"application/json"}));
 const a=el("a");a.href=url;a.download="zsentinel-private-human-handoff.json";
 document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
$("export").onclick=()=>{try{
 const entries=Object.keys(notes).filter(k=>source.incidents.some(v=>v.id===k)).sort().map(id=>({id,status:notes[id].status,note:notes[id].note}));
 for(const r of entries)if(!choices.includes(r.status)||typeof r.note!=="string"||r.note.length>800||/[\u0000-\u0009\u000B-\u001F\u007F]/u.test(r.note))throw Error("Ongeldige notitie");
 download({schema:"zsentinel-human-followup-v1",source_sha256:source.fingerprint,source_at:source.observed_at,
         read_only:true,action_authorized:false,incident_count:source.incidents.length,entries});
 $("feedback").textContent="Privébestand aangeboden; er is niets verstuurd.";
 }catch(_){$("feedback").textContent="Export geweigerd; controleer lokale gegevens."}};
$("import").onchange=async event=>{
 try{
  const file=event.target.files?.[0];if(!file||file.size>100000)throw Error("size");
  const raw=await file.text();const data=JSON.parse(raw);
  if(!data||data.schema!=="zsentinel-human-followup-v1"||data.source_sha256!==source.fingerprint||
     data.source_at!==source.observed_at||data.read_only!==true||data.action_authorized!==false||
     data.incident_count!==source.incidents.length||!Array.isArray(data.entries)||
     data.entries.length>source.incidents.length)throw Error("source");
  const fresh=Object.create(null);
  for(const row of data.entries){
    if(!row||typeof row.id!=="string"||!source.incidents.some(x=>x.id===row.id)||
       row.id in fresh||!choices.includes(row.status)||typeof row.note!=="string"||
       row.note.length>800||/[\u0000-\u0009\u000B-\u001F\u007F]/u.test(row.note))throw Error("entry");
    fresh[row.id]={status:row.status,note:row.note};
  }
  for(const key of Object.keys(notes))delete notes[key];
  for(const key of Object.keys(fresh))notes[key]=fresh[key];
  $("feedback").textContent="Privé-overdracht geopend; bron-ID exact gelijk. Geen externe actie.";
  render();
 }catch(_){$("feedback").textContent="Import geweigerd: geen exacte betrouwbare overdracht voor deze bron."}
 finally{event.target.value=""}
};
// Import notes from an older saved source ONLY with explicit user confirmation.
// Prior local statuses must never become current owner/recovery evidence.
$("carry").onchange=async event=>{
 try{
  const file=event.target.files?.[0];
  if(!file||file.size>100000)throw Error("size");
  const data=JSON.parse(await file.text());
  const older=Date.parse(data?.source_at);
  const current=Date.parse(source.observed_at);
  if(!data||data.schema!=="zsentinel-human-followup-v1"||
     typeof data.source_sha256!=="string"||!/^[0-9a-f]{64}$/.test(data.source_sha256)||
     data.source_sha256===source.fingerprint||!Number.isFinite(older)||
     older>=current||!Array.isArray(data.entries)||data.entries.length>200||
     data.read_only!==true||data.action_authorized!==false||
     !Number.isSafeInteger(data.incident_count)||data.incident_count<0||data.incident_count>200)
    throw Error("source");
  const prior=Object.create(null),matching=[];
  for(const row of data.entries){
    if(!row||typeof row.id!=="string"||!/^[a-f0-9]{32}$/.test(row.id)||
       row.id in prior||!choices.includes(row.status)||
       typeof row.note!=="string"||row.note.length>800||
       /[\u0000-\u0009\u000B-\u001F\u007F]/u.test(row.note))throw Error("entry");
    prior[row.id]=row;
    if(row.note.trim()&&source.incidents.some(x=>x.id===row.id))
      matching.push(row);
  }
  if(!matching.length)throw Error("no-match");
  const message="Er zijn "+matching.length+" oude notities bij identieke incident-ID's. "+
   "Alleen de notities worden overgenomen, nooit eerdere eigenaar-/herstelstatus. "+
   "Iedere overgenomen status wordt 'bron opnieuw verifiëren'. Wil je dit bewust doen?";
  if(!window.confirm(message)){
    $("feedback").textContent="Oude notities niet overgenomen.";
    return;
  }
  const prefix="HISTORISCHE NOTITIE "+data.source_at+"\n";
  for(const row of matching){
    const note=(prefix+row.note).slice(0,800);
    notes[row.id]={status:"bron-opnieuw-verifieren",note};
  }
  $("feedback").textContent=matching.length+" historische notities overgenomen als opnieuw te verifiëren. Geen externe status gewijzigd.";
  render();
 }catch(_){$("feedback").textContent="Oude overdracht geweigerd: bron, tijd, incident-ID of notities niet bruikbaar.";}
 finally{event.target.value="";}
};
isFresh();render();
</script></html>"""
    return html.replace("__SOURCE__", payload)
