#!/usr/bin/env python3
"""Self-contained, script-only-in-document offline router history workbench.

Never fetches, persists browser notes, or provides any claim/release controls.
"""
from __future__ import annotations

import json


def render_html(report):
    if (not isinstance(report, dict)
            or report.get("schema") != "zsentinel-private-router-timeline-v1"
            or report.get("owner_claim_verified") is not False
            or report.get("merge_authorized") is not False
            or report.get("recovery_verified") is not False
            or report.get("writes_performed") is not False
            or not isinstance(report.get("items"), list)
            or len(report["items"]) > 400):
        raise ValueError("untrusted or authorizing historical timeline")
    data = json.dumps(report, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)
    # Source labels are data, never HTML/script. Prevent closing JSON <script>.
    data = (data.replace("<", "\\u003c")
                .replace(">", "\\u003e").replace("&", "\\u0026"))
    template = r"""<!doctype html><html lang="nl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy"
 content="default-src 'none';connect-src 'none';form-action 'none';
 object-src 'none';img-src 'none';base-uri 'none';
 script-src 'unsafe-inline';style-src 'unsafe-inline'">
<title>zSentinel · Historische incidenttijdlijn</title>
<style>
:root{font-family:system-ui,-apple-system,sans-serif;color-scheme:dark;background:#0c1422;color:#ecf4ff}
*{box-sizing:border-box}body{max-width:1260px;margin:0 auto;padding:clamp(1rem,3vw,2rem)}
h1{font-size:clamp(1.6rem,4vw,2.4rem)}h2{font-size:1.12rem}
p{line-height:1.5;overflow-wrap:anywhere}small{color:#b9cce1}
header{padding-bottom:1rem}section,article{border-radius:12px;border:1px solid #475c76;padding:1rem;background:#18293d;margin:.7rem 0}
.warning{border-left:5px solid #ffbe6c}.statgrid,.filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.8rem}
.stat{background:#132236}.stat strong{font-size:1.5rem;display:block}
label{display:grid;gap:.35rem;font-weight:600}
input,select,button{font:inherit;background:#22364e;border:1px solid #708cab;color:inherit;border-radius:8px;padding:.6rem}button{cursor:pointer;margin:.3rem .4rem .3rem 0}button:disabled{cursor:not-allowed;opacity:.45}
input:focus-visible,select:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid #c4f190}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,390px),1fr));gap:.8rem}
.card{border-left:5px solid #8fbbe0}.card.unknown{border-left-color:#ffbe6c}
.chips{display:flex;gap:.4rem;flex-wrap:wrap}.chip{border:1px solid #8097b3;border-radius:25px;padding:.2rem .55rem;font-size:.8rem}
details{padding:.5rem 0}summary{cursor:pointer}li{line-height:1.5;margin-bottom:.5rem;overflow-wrap:anywhere}
</style>
<header><small>ZCLOUD · zSentinel · READ ONLY</small>
<h1>Historische incidenttijdlijn</h1>
<p>Meerdere opgeslagen bronmomenten vergelijken zonder VPS, GitHub of queue te benaderen.</p>
<section class="warning"><strong>HISTORISCH ARCHIEF — GEEN HERSTELBEWIJS</strong>
<p>Geen actuele claims, CI-resultaten, releases of runnerherstel geverifieerd.
Een incident dat in de laatste gedeeltelijke waarneming ontbreekt, is <strong>niet bewezen opgelost</strong>.
Deze werkbak kan geen claim, job, merge of systeemdienst veranderen.</p></section></header>
<section id="stats" class="statgrid" aria-label="Geobserveerde samenvatting"></section>
<section aria-label="Bronvensters"><h2>Historische bronmomenten</h2>
<div id="sources"></div></section>
<section class="warning" aria-label="Historische SQLite-wachtrijdruk">
<h2>Wachtrij en claimpogingen · alleen bronvergelijking</h2>
<p>Afwijkingen tussen opgeslagen, volledige bronmomenten; claimpogingen zijn geen
mislukte runs en een daling is geen geverifieerd herstel. Bronhiaat = ONBEKEND.
Geen claims vrijgeven, jobs herstarten of automatische retries uitvoeren.</p>
<div id="queueStats" class="statgrid"></div>
<div id="queueWindows"></div>
<div id="queueDeltas"></div>
<p>Voor menselijke beoordeling zonder runner- of queueacties:</p>
<button id="queuePreview">Bekijk privé wachtrijoverdracht</button>
<button id="queueDownload" disabled>Download wachtrijoverdracht als Markdown</button>
<p id="queueFeedback" aria-live="polite">Geen overdracht gemaakt.</p>
<pre id="queueBrief" style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:280px;overflow:auto"></pre>
</section>
<section class="warning" aria-label="Historische worker-voortgang">
<h2>Workergebeurtenissen tussen bronmomenten</h2>
<p>Vergelijkt alleen dezelfde oorspronkelijke SQLite-workerbron en dezelfde geobserveerde slots.
Nieuwe browsergeneratie-events zijn <strong>geen</strong> bewijs van productlevering, geldige
lease, herstelde worker of merge. Ontbrekende slots en bronwisselingen blijven ONBEKEND.</p>
<div id="workerMetrics" class="statgrid"></div>
<div id="workerHistory"></div>
<h3>Aanhoudende eigenaarcontroles uit twee bronvensters</h3>
<p>Dit zijn herhaalde <em>classificaties</em>, niet noodzakelijk twee aparte fouten.
Actuele claim, lease en productlevering zijn niet bewezen.</p>
<div id="workerFollowup"></div>
<p>Menselijke overdracht zonder uitvoerbevoegdheid:</p>
<button id="workerPreview">Bekijk privé worker-opvolging</button>
<button id="workerDownload" disabled>Download worker-overdracht als Markdown</button>
<p id="workerFeedback" aria-live="polite">Geen historische worker-overdracht gegenereerd.</p>
<pre id="workerBrief" style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:290px;overflow:auto"></pre>
</section>
<section class="filters" aria-label="Filters">
<label>Zoeken<input id="search" type="search" placeholder="Incident, project, eigenaar of advies"></label>
<label>Hoogste historisch waargenomen prioriteit<select id="severity"><option value="">Alle</option><option>P0</option><option>P1</option><option>P2</option><option>P3</option></select></label>
<label>Laatste steekproef<select id="presence"><option value="">Alle</option><option value="seen">Wel gezien</option><option value="missing">Niet gezien: opvolgen</option></select></label>
<label>Actietype<select id="human"><option value="">Alle</option><option value="yes">Menselijke actie</option><option value="no">Overig</option></select></label>
</section>
<section aria-label="Privé menselijke overdracht">
<h2>Menselijke opvolgoverdracht uit de geselecteerde bronkaarten</h2>
<p>Alleen historisch waargenomen P0/P1-acties. Eerst privé voorvertonen,
daarna handmatig downloaden. Geen verzending, agenda, eigenaar-ACK, claim of herstelactie.</p>
<button id="preview">Bekijk privéoverdracht</button>
<button id="download" disabled>Download geselecteerd Markdown</button>
<p id="feedback" aria-live="polite">Nog geen voorvertoning. Alle bronvensters blijven partieel.</p>
<pre id="brief" style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:340px;overflow:auto"></pre>
</section>
<p id="count" aria-live="polite"></p><main id="cards" class="cards"></main>
<script type="application/json" id="report">__SOURCE_JSON__</script>
<script>
"use strict";
const $=id=>document.getElementById(id),data=JSON.parse($("report").textContent);
function el(tag,cls,value){
 const x=document.createElement(tag);
 if(cls)x.className=cls;
 if(value!==undefined)x.textContent=String(value);
 return x;
}
function line(label,value){
 const p=el("p");p.append(el("strong",null,label+": "),el("span",null,value));return p;
}
function stat(label,value){
 const e=el("section","stat");e.append(el("small",null,label),el("strong",null,value));return e;
}
$("stats").append(
 stat("Bronvensters",data.source_count),
 stat("Unieke incidenten in steekproef",data.unique_incidents_sampled),
 stat("Meerdere keren gezien",data.multi_window_observed),
 stat("Verdween en later opnieuw gezien",data.disappeared_then_reobserved),
 stat("Niet in laatste steekproef: opvolgen",data.not_observed_in_final_sample_needs_reconciliation),
 stat("Getoonde kaarten",data.visible_incidents),
 stat("Afgekapt",data.rows_truncated?"JA, onzichtbare incidenten bestaan":"nee")
);
const queue=data.queue_pressure_history;
$("queueStats").append(
 stat("Vergelijkbare bronparen",queue.compared_pairs),
 stat("Bronhiaten / niet vergelijkbaar",queue.unknown_pairs),
 stat("Actuele lease- of herstelstatus","NIET GECONTROLEERD")
);
for(const w of queue.windows){
 const q=w.queue;
 $("queueWindows").append(line(
  "Wachtrijbron "+w.window,
  q.coverage==="not_supplied"?"NIET GELEZEN — geen nulclaim":(
   q.sampled_at_utc+" · "+q.coverage+
   (q.coverage==="current" ?
    " · wachtenden "+q.totals.queued_items+
    " · hoge claimpogingen "+q.totals.high_claim_attempt_items+
    " · verlopen lease-rijen "+q.totals.expired_owner_lease_rows
    :" · BRON ONVOLLEDIG / HISTORISCH; niet als herstelbewijs gebruiken")
  )
 ));
}
for(const delta of queue.adjacent_comparisons){
 const label="Venster "+delta.from_window+" → "+delta.to_window;
 const changes=delta.deltas;
 $("queueDeltas").append(line(label,
  changes===null?"ONBEKEND · één of beide bronsamples ontbreken of zijn onvergelijkbaar":
  "wachtenden "+(changes.queued_items>=0?"+":"")+changes.queued_items+
  ", hoge claimpogingen "+(changes.high_claim_attempt_items>=0?"+":"")+changes.high_claim_attempt_items+
  ", verlopen lease-rijen "+(changes.expired_owner_lease_rows>=0?"+":"")+changes.expired_owner_lease_rows+
  (delta.owner_review_required ? " · EIGENAAR CONTROLEERT BRON": " · niet gelijk aan herstel")
 ));
 const details=el("details");
 details.append(el("summary",null,label+" · per project en prioriteit"));
 if(delta.project_deltas===null){
  details.append(el("p",null,"ONBEKEND: projectbron ontbrak of was afgekapte steekproef. Geen nulaantallen."));
 }else if(!delta.project_deltas.length){
  details.append(el("p",null,"Geen actieve projectgroepen in twee complete bronmomenten; geen herstelbewijs."));
 }else{
  const list=el("ol");
  for(const group of delta.project_deltas){
   const signed=n=>(n>=0?"+":"")+n;
   list.append(el("li",null,
    group.priority+" · "+group.project_id+" · wachtenden "+signed(group.queued_delta)+
    " · hoge claimpogingen "+signed(group.high_claim_attempts_delta)+
    " · verlopen lease-rijen "+signed(group.expired_owner_lease_rows_delta)+
    " · veranderingen ≠ retries of vrijgegeven taken"));
  }
  details.append(list);
 }
 $("queueDeltas").append(details);
}
let queuePreview=null;
function queueMarkdown(){
 const history=data.queue_pressure_history;
 if(!history||!Array.isArray(history.adjacent_comparisons))throw Error("source");
 const out=[
  "# zSentinel · historische eigenaarsoverdracht wachtrijdruk","",
  "> Alle wachtrijdata komen uit oude, gedeeltelijke bronvensters.",
  "> Pogingen om taken te claimen zijn geen mislukte jobs, geen retrytoestemming.",
  "> Afwezige data, lagere aantallen of verlopen leases betekenen geen herstel.",
  "> Geen mutaties, merges, leases of productierechten.","",
  "- Eerste routerbron: "+data.first_source_observed_at_utc,
  "- Laatste routerbron: "+data.last_source_observed_at_utc,
  "- Bron SHA256: "+data.sources.map(x=>x.normalized_source_sha256).join(", "),
  "- Vergelijkbare vensters: "+history.compared_pairs,
  "- Niet-vergelijkbare vensters: "+history.unknown_pairs,"",
 ];
 for(const row of history.adjacent_comparisons){
  const title="## Vensters "+row.from_window+" → "+row.to_window;
  out.push(title);
  if(row.deltas===null){
   out.push("- Status: BRON ONBEKEND. Eigenaar verifieert originele queue/claims opnieuw.","");
   continue;
  }
  const deltas=row.deltas;
  out.push("- Wachtenden: "+deltas.queued_items,
   "- Hoge claimpogingen: "+deltas.high_claim_attempt_items,
   "- Verlopen lease-rijen: "+deltas.expired_owner_lease_rows,
   "- Status: geobserveerd AGGREGAAT; geen jobherstel of leasevrijgave.");
  if(row.project_deltas===null){
   out.push("- Projectbron onvolledig; geen fictieve projectnullen.");
  }else{
   for(const g of row.project_deltas){
    if(g.queued_delta===0&&g.high_claim_attempts_delta===0&&
       g.expired_owner_lease_rows_delta===0)continue;
    out.push("- "+g.priority+" "+g.project_id+
     " · wachtenden "+g.queued_delta+
     " · hoge claimpogingen "+g.high_claim_attempts_delta+
     " · verlopen lease-rijen "+g.expired_owner_lease_rows_delta+
     " (eigenaar onderzoekt; GEEN automatische retry)");
   }
  }
  out.push("");
 }
 const raw=out.join("\n");
 if(new TextEncoder().encode(raw).byteLength>125000)throw Error("too large");
 return raw;
}
$("queuePreview").onclick=()=>{
 try{
  const content=queueMarkdown();
  queuePreview={source:data.sources.map(x=>x.normalized_source_sha256).join(","),content};
  $("queueBrief").textContent=content;
  $("queueDownload").disabled=false;
  $("queueFeedback").textContent="Privé voorvertoning klaar. Controleer oorspronkelijke bronmomenten en claims.";
 }catch(_){
  queuePreview=null;$("queueDownload").disabled=true;
  $("queueBrief").textContent="";
  $("queueFeedback").textContent="Wachtrijoverdracht niet beschikbaar voor deze bron.";
 }
};
$("queueDownload").onclick=()=>{
 try{
  if(!queuePreview||queuePreview.content!==queueMarkdown()||
     queuePreview.source!==data.sources.map(x=>x.normalized_source_sha256).join(","))
   throw Error("stale source");
  const url=URL.createObjectURL(new Blob([queuePreview.content],{type:"text/markdown;charset=utf-8"}));
  const link=el("a");link.href=url;link.download="zsentinel-historische-wachtrijdruk.md";
  document.body.append(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
  $("queueFeedback").textContent="Privébronoverdracht aangeboden; niets verzonden of geautoriseerd.";
 }catch(_){
  queuePreview=null;$("queueDownload").disabled=true;
  $("queueFeedback").textContent="Bron of inhoud niet actueel; maak een nieuwe voorvertoning.";
 }
};
const workers=data.worker_continuity_history;
$("workerMetrics").append(
 stat("Oorspronkelijke workerbronnen",workers.source_windows),
 stat("Onvolledige of gewijzigde bronparen",workers.unknown_or_partial_pairs),
 stat("Nieuw geobserveerde generatiesignalen",workers.observed_generation_signal_pairs),
 stat("Herhaald geobserveerde eigenaarcontroles",workers.persistent_observed_owner_review_count),
 stat("Productlevering bewezen","NEE")
);
if(!workers.persistent_observed_owner_review.length){
 $("workerFollowup").append(el("p",null,
  "Geen herhaalde eigenaarcontroles in de vergelijkbare deelsamples. Dit bewijst niet dat alle workers gezond zijn."));
}else{
 const followups=el("ol");
 for(const row of workers.persistent_observed_owner_review){
  followups.append(el("li",null,
   "P"+row.priority+" · "+row.project_id+" worker "+row.worker_slot+
   " · vensters "+row.from_window+" → "+row.to_window+
   " · voorheen "+row.previous_classification+" · later "+row.later_classification+
   " · huidige eigenaar moet zelf verifiëren: "+row.owner_followup+
   " · geen automatische claim of herstel"));
 }
 $("workerFollowup").append(followups);
}
for(const pair of workers.adjacent_comparisons){
 const detail=el("details");
 detail.append(el("summary",null,"Vensters "+pair.from_window+" → "+pair.to_window+
  " · "+pair.comparison+" · "+pair.workers_compared+" dezelfde slots"));
 if(!pair.entries.length){
  detail.append(el("p",null,"Geen vergelijkbare workerbron of slots: ONBEKEND. Geen herstel of vrije claim."));
 }else{
  const list=el("ol");
  for(const worker of pair.entries){
   list.append(el("li",null,worker.project_id+" · slot "+worker.worker_slot+
    " · "+worker.classification+" · bronactie: "+worker.later_owner_followup+
    (worker.worker_state_changed?" · laatste classificatie veranderd, niet bewezen hersteld":"")+
    " · geen claim- of mergeautorisatie"));
  }
  detail.append(list);
 }
 $("workerHistory").append(detail);
}
let lastWorkerBrief=null;
function renderWorkerBrief(){
 const out=[
  "# zSentinel · historische menselijke worker-opvolging","",
  "> NIET ACTUEEL. Nieuwe browser-events zijn geen productlevering, geldige lease of geverifieerd herstel.",
  "> Ongeziene of gewijzigde slots zijn ONBEKEND. Geen restart, claim, dispatcheractie of merge.","",
  "- Eerste bron: "+data.first_source_observed_at_utc,
  "- Laatste bron: "+data.last_source_observed_at_utc,
  "- Bron SHA-256: "+data.sources.map(s=>s.normalized_source_sha256).join(", "), "",
  "## Herhaald geobserveerde eigenaarcontroles, geen herstelbewijs", ""
 ];
 for(const row of workers.persistent_observed_owner_review){
  out.push("- "+row.project_id+" worker "+row.worker_slot+
   " · vensters "+row.from_window+" → "+row.to_window+
   " · classificatie laatst "+row.later_classification+
   " · eigenaarcontrole "+row.owner_followup+
   " · niet als bewezen defect of claimoverdracht interpreteren.");
 }
 out.push("");
 for(const pair of workers.adjacent_comparisons){
  out.push("## Vensters "+pair.from_window+" → "+pair.to_window,
   "- Bronvergelijking: "+pair.comparison);
  if(!pair.entries.length)out.push("- Geen betrouwbaar vergelijkbaar workerslot; eigenaar controleert de actuele claimbron.");
  for(const row of pair.entries){
   out.push("- "+row.project_id+" slot "+row.worker_slot+
    " · "+row.classification+
    " · bronadvies "+row.later_owner_followup+
    " · na later moment "+row.later_worker_state+
    " · productlevering NIET bewezen.");
  }
  out.push("");
 }
 const content=out.join("\n");
 if(new TextEncoder().encode(content).byteLength>130000)throw Error("bounded-handoff");
 return content;
}
$("workerPreview").onclick=()=>{
 try{
  const content=renderWorkerBrief();
  lastWorkerBrief={identity:data.sources.map(s=>s.normalized_source_sha256).join(","),content};
  $("workerBrief").textContent=content;
  $("workerDownload").disabled=false;
  $("workerFeedback").textContent="Voorvertoning gereed. Verifieer eigenaar, CI en voortgang via actuele bronnen.";
 }catch(_){
  lastWorkerBrief=null;$("workerDownload").disabled=true;
  $("workerBrief").textContent="";
  $("workerFeedback").textContent="Historische worker-overdracht niet beschikbaar.";
 }
};
$("workerDownload").onclick=()=>{
 try{
  if(!lastWorkerBrief||
      lastWorkerBrief.identity!==data.sources.map(s=>s.normalized_source_sha256).join(",")||
      lastWorkerBrief.content!==renderWorkerBrief())throw Error("stale-source");
  const url=URL.createObjectURL(new Blob([lastWorkerBrief.content],
   {type:"text/markdown;charset=utf-8"}));
  const a=el("a");a.href=url;a.download="zsentinel-historische-worker-opvolging.md";
  document.body.append(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
  $("workerFeedback").textContent="Lokale overdracht aangeboden; geen externe actie uitgevoerd.";
 }catch(_){lastWorkerBrief=null;$("workerDownload").disabled=true;
  $("workerFeedback").textContent="Overdracht niet actueel; maak een nieuwe voorvertoning.";
 }
};
for(const source of data.sources){
 $("sources").append(line("Venster "+source.index,
 source.observed_at_utc+" · "+source.observed_incident_count+
 " in gedeeltelijke steekproef (P0 "+source.observed_p0_count+
 ", P1 "+source.observed_p1_count+", menselijke actie "+source.observed_human_only_count+
 ") · SHA256(normalized) "+source.normalized_source_sha256));
}
function filteredItems(){
 const q=$("search").value.trim().toLowerCase(),s=$("severity").value,
       presence=$("presence").value,human=$("human").value;
 return data.items.filter(r=>
   (!s||r.highest_observed_priority===s)&&
   (!presence||(presence==="seen" ? r.observed_in_final_sample : !r.observed_in_final_sample))&&
   (!human||(human==="yes" ? r.human_last_observed : !r.human_last_observed))&&
   (!q||[r.id,r.problem,r.project,r.last_owner_label_unverified,r.last_action_advisory]
       .some(v=>v.toLowerCase().includes(q))));
}
let lastPreview=null;
function invalidate(){
 lastPreview=null;$("download").disabled=true;
 $("brief").textContent="";
 $("feedback").textContent="Selectie veranderd. Bekijk eerst opnieuw de privéoverdracht.";
}
function render(){
 const filtered=filteredItems();
 invalidate();
 $("count").textContent=filtered.length+" kaarten van "+data.visible_incidents+
 " getoonde bronkaarten. Gefilterde of ontbrekende kaarten betekenen nooit opgelost.";
 const frag=document.createDocumentFragment();
 for(const row of filtered){
  const article=el("article","card"+(row.observed_in_final_sample?"":" unknown"));
  const chips=el("div","chips");
  chips.append(
   el("span","chip",row.last_priority),el("span","chip",row.project),
   el("span","chip",row.observed_in_final_sample?"In laatst gelezen bron":"Niet gezien: herstel ONBEKEND"));
  if(row.highest_observed_priority!==row.last_priority){
   chips.append(el("span","chip","Historisch "+row.highest_observed_priority));
  }
  article.append(chips,el("h2",null,row.problem),
   line("Incident",row.id),line("Gelezen momenten",row.observed_windows+"/"+data.source_count),
   line("Bronstatus",row.last_status),line("Eigenaarlabel, niet geverifieerd",row.last_owner_label_unverified),
   line("Verdwenen en opnieuw gezien (bronhiaten)",row.disappeared_and_reappeared_sample_gaps+
      " · geen bewijs dat het probleem tussentijds hersteld was"),
   line("Vervolgadvies",row.last_action_advisory),
   line("Beoordelingsdoel uit bron",row.last_original_review_target));
  const details=el("details"),summary=el("summary",null,"Toon bronhistorie / veranderingen");
  details.append(summary);
  if(row.observed_field_changes)details.append(
   el("p",null,"Bronvelden veranderden tussen waarnemingen; geen bewijs van eigenaarsovername."));
  const list=el("ol");
  for(const event of row.events){
   const item=el("li",null,"Venster "+event.window+" · "+event.source_at+
    " · "+event.priority+" · "+event.status+" · eigenaarlabel "+event.owner_label);
   list.append(item);
  }
  details.append(list);article.append(details);frag.append(article);
 }
 $("cards").replaceChildren(frag);
}
function md(value){
 let result=String(value).replace(/\r/g,"").replace(/\n/g," ");
 for(const char of ["\\","<",">","[","]","#","*","_","|",String.fromCharCode(96),"!"]){
  result=result.split(char).join("\\"+char);
 }
 return result;
}
function scope(){
 return JSON.stringify({
  source:data.sources.map(s=>s.normalized_source_sha256),
  selection:["search","severity","presence","human"].map(id=>$(id).value),
  cards:filteredItems().map(r=>r.id)
 });
}
function makeBrief(){
 const selected=filteredItems().filter(r=>r.human_last_observed&&
  ["P0","P1"].includes(r.highest_observed_priority));
 if(!selected.length)throw Error("no-human");
 const out=[
  "# zSentinel · historisch menselijk hersteloverzicht","",
  "> NIET ACTUEEL. Geen goedkeuring voor ownerclaims, retries, releases of merges.",
  "> Ontbrekende bronkaarten zijn niet opgelost. Gedeeltelijke sampling.","",
  "- Oorspronkelijk eerste bronmoment: "+data.first_source_observed_at_utc,
  "- Oorspronkelijk laatste bronmoment: "+data.last_source_observed_at_utc,
  "- Bronidentiteit: "+data.sources.map(s=>s.normalized_source_sha256).join(", "),
  "- Gebaseerd op "+selected.length+" geselecteerde historische P0/P1 kaarten.",
  ""
 ];
 for(const r of selected){
  out.push("## "+md(r.highest_observed_priority+" · "+r.project+" · "+r.problem),
   "- Incident: "+r.id,
   "- Laatste bronwaarneming: "+r.events[r.events.length-1].source_at,
   "- Aanwezig in laatst geobserveerde bron: "+(r.observed_in_final_sample?"JA":"NEE, niet opgelost bewezen"),
   "- Aantal waargenomen bronvensters: "+r.observed_windows,
   "- Historische bronhiaten: "+r.disappeared_and_reappeared_sample_gaps,
   "- Eigenaarlabel, niet geverifieerd: "+md(r.last_owner_label_unverified),
   "- Bronadvies: "+md(r.last_action_advisory),
   "- Oorspronkelijke beoordelingsdatum: "+r.last_original_review_target,
   "- Bronstatus: "+md(r.last_status),"");
 }
 out.push("Geen verzending, herstelactie, CI-dispatch, claim of merge uitgevoerd.","");
 const result=out.join("\n");
 if(new TextEncoder().encode(result).byteLength>300000)throw Error("too-big");
 return result;
}
$("preview").onclick=()=>{
 try{
  const content=makeBrief();
  lastPreview={scope:scope(),content};
  $("brief").textContent=content;
  $("download").disabled=false;
  $("feedback").textContent="Voorvertoning klaar. Controleer de inhoud voordat je privé exporteert.";
 }catch(_){invalidate();$("feedback").textContent="Geen passende historische menselijke P0/P1 kaarten in dit filter.";}
};
$("download").onclick=()=>{
 try{
  if(!lastPreview||lastPreview.scope!==scope()||lastPreview.content!==makeBrief())
   throw Error("stale-preview");
  const url=URL.createObjectURL(new Blob([lastPreview.content],{type:"text/markdown;charset=utf-8"}));
  const link=el("a");link.href=url;link.download="zsentinel-historische-menselijke-opvolging.md";
  document.body.append(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
  $("feedback").textContent="Privéoverdracht aangeboden. Niets verzonden of goedgekeurd.";
 }catch(_){invalidate();$("feedback").textContent="Bron of selectie gewijzigd; maak opnieuw een voorvertoning.";}
};
for(const id of ["search","severity","presence","human"]){
 $(id).addEventListener(id==="search"?"input":"change",render);
}
render();
</script></html>"""
    if template.count("__SOURCE_JSON__") != 1:
        raise ValueError("ambiguous offline timeline source placeholder")
    return template.replace("__SOURCE_JSON__", data)
