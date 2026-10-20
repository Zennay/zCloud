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
input,select{font:inherit;background:#22364e;border:1px solid #708cab;color:inherit;border-radius:8px;padding:.6rem}
input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #c4f190}
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
<section class="filters" aria-label="Filters">
<label>Zoeken<input id="search" type="search" placeholder="Incident, project, eigenaar of advies"></label>
<label>Prioriteit<select id="severity"><option value="">Alle</option><option>P0</option><option>P1</option><option>P2</option><option>P3</option></select></label>
<label>Laatste steekproef<select id="presence"><option value="">Alle</option><option value="seen">Wel gezien</option><option value="missing">Niet gezien: opvolgen</option></select></label>
<label>Actietype<select id="human"><option value="">Alle</option><option value="yes">Menselijke actie</option><option value="no">Overig</option></select></label>
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
for(const source of data.sources){
 $("sources").append(line("Venster "+source.index,
 source.observed_at_utc+" · "+source.observed_incident_count+
 " in gedeeltelijke steekproef (P0 "+source.observed_p0_count+
 ", P1 "+source.observed_p1_count+", menselijke actie "+source.observed_human_only_count+
 ") · SHA256(normalized) "+source.normalized_source_sha256));
}
function render(){
 const q=$("search").value.trim().toLowerCase(),s=$("severity").value,
       presence=$("presence").value,human=$("human").value;
 const filtered=data.items.filter(r=>
   (!s||r.last_priority===s)&&
   (!presence||(presence==="seen" ? r.observed_in_final_sample : !r.observed_in_final_sample))&&
   (!human||(human==="yes" ? r.human_last_observed : !r.human_last_observed))&&
   (!q||[r.id,r.problem,r.project,r.last_owner_label_unverified,r.last_action_advisory]
       .some(v=>v.toLowerCase().includes(q))));
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
for(const id of ["search","severity","presence","human"]){
 $(id).addEventListener(id==="search"?"input":"change",render);
}
render();
</script></html>"""
    if template.count("__SOURCE_JSON__") != 1:
        raise ValueError("ambiguous offline timeline source placeholder")
    return template.replace("__SOURCE_JSON__", data)
