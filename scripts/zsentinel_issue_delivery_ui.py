#!/usr/bin/env python3
"""Pure offline presentation pieces for the standalone product issue board.

No API calls, browser storage, telemetry or authenticated browser requests.
Source data is escaped by the caller; browser user notes stay in the tab.
"""
from __future__ import annotations

from html import escape


def controls(projects):
    if not isinstance(projects, list) or not 1 <= len(projects) <= 18:
        raise ValueError("untrusted project selector")
    options = "".join('<option value="' + escape(pid, quote=True) +
                      '">' + escape(pid) + '</option>' for pid in projects)
    return (
        '<section class="operator" aria-label="Persoonlijk productwerkoverzicht">'
        '<h2>Filter & volg lokaal op</h2><p id="issue-freshness" '
        'class="notice" role="status" aria-live="polite"></p>'
        '<p>Alle filters en eigen notities blijven '
        'alleen in dit browsertabblad. Niets wordt opgeslagen of verzonden.</p>'
        '<div class="filters"><label>Zoeken <input id="issue-search" type="search" '
        'maxlength="80" placeholder="Titel of project"></label>'
        '<label>Project <select id="issue-project"><option value="">Alle</option>' +
        options + '</select></label>'
        '<label>Prioriteit <select id="issue-priority"><option value="">Alle</option>'
        '<option>P0</option><option>P1</option><option>P2</option>'
        '<option value="Unspecified">Onbekend</option></select></label>'
        '<label>Route <select id="issue-route"><option value="">Alle</option>'
        '<option value="authorized_infrastructure_owner_required">Menselijke infrablokkade</option>'
        '<option value="review_existing_pr_first">Bestaande PR eerst</option>'
        '<option value="verify_ownership_and_scope">Eerst eigendom/scope</option>'
        '<option value="check_assignee_and_current_claim">Assignee controleren</option>'
        '</select></label><label>Bronverandering <select id="issue-progress">'
        '<option value="">Alle items</option>'
        '<option value="new">Nieuw in steekproef (niet nieuw issue)</option>'
        '<option value="changed">PR-/prioriteitsbron veranderd</option>'
        '<option value="unchanged">Ook eerder waargenomen</option>'
        '<option value="sourceunknown">Onvolledige bronvergelijking</option>'
        '</select></label></div>'
        '<p id="issue-visible-count" role="status" aria-live="polite"></p>'
        '<h3>Bewuste privé-overdracht</h3><p>De export bevat alleen de momenteel '
        'zichtbare issues plus de lokaal ingevulde opvolgstatus en notities. '
        'Bekijk het bestand vóór je het deelt; een statuskeuze is geen echte '
        'claim, herstel of toestemming.</p>'
        '<button type="button" id="preview-issues">Voorvertoning maken</button> '
        '<button type="button" id="export-issues" disabled>Download gecontroleerde .md</button>'
        '<pre id="issue-handoff" aria-live="polite"></pre>'
        '<h3>Privé-opvolgnotities meenemen</h3>'
        '<p>Na voorvertoning kun je afzonderlijk maximaal 24 lokale '
        'opvolgkeuzes/notities exporteren als JSON, later opnieuw importeren '
        'voor exact dezelfde issue-URL’s. Dit is geen echte workerclaim. '
        'Oude notities worden nooit automatisch met een bronstatus samengevoegd.</p>'
        '<button type="button" id="export-followups" disabled>'
        'Download privénotities (.json)</button> '
        '<label>Import eigen privénotities (.json) '
        '<input id="import-followups" type="file" accept=".json,application/json"></label>'
        '<p id="followup-feedback" role="status" aria-live="polite"></p>'
        '<h3>Voorbereid werkpakket voor een bevoegde worker</h3>'
        '<p>Vink alleen concrete, niet-infrastructurele productissues aan '
        '(maximaal 12) en maak een machineleesbare conceptopdracht. '
        'Controleer eigenaar, bestandsscope, bestaande PR en required CI vóór '
        'een bevoegde worker iets mag claimen. De knop dispatcht NIETS.</p>'
        '<button type="button" id="preview-packet">Conceptwerkpakket bekijken</button> '
        '<button type="button" id="download-packet" disabled>Download concept .json</button>'
        '<p id="packet-feedback" role="status" aria-live="polite"></p>'
        '<pre id="packet-preview"></pre></section>'
    )


INTERACTIVE_CSS = """
.filters{display:flex;flex-wrap:wrap;gap:14px}
.filters label{display:grid;gap:5px;min-width:190px;flex:1}
.operator input,.operator select,.issue textarea,.issue select{
    font:inherit;background:#172432;border:1px solid #89a6b6;color:#fff;
    padding:8px;border-radius:6px}
.issue textarea{display:block;width:100%;min-height:58px;margin-top:7px;resize:vertical}
.issue .followup{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
.issue .followup label{display:grid;gap:5px;flex:1;min-width:180px}
.operator button{font:inherit;padding:9px 12px;border:1px solid #bce9ff;
    border-radius:7px;background:#bce9ff;color:#102031;font-weight:700;cursor:pointer}
.operator button:disabled{opacity:.5;cursor:not-allowed}
.operator pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:300px;
    overflow:auto;border:1px solid #627a90;padding:12px}
.operator :focus-visible,.issue :focus-visible{outline:3px solid #fff0a1;outline-offset:2px}
@media print{.operator,.followup{display:none!important}}
"""


# Static trusted script: only textContent / DOM form values for user content,
# never innerHTML, storage, automatic network requests or hidden downloads.
INTERACTIVE_JS = r"""
"use strict";
const boardItems=Array.from(document.querySelectorAll("article.issue[data-issue-url]"));
const input=id=>document.getElementById(id);
const preview=input("issue-handoff");
const download=input("export-issues");
const followupDownload=input("export-followups");
const followupImport=input("import-followups");
const feedback=input("followup-feedback");
const packetPreview=input("packet-preview");
const packetDownload=input("download-packet");
const packetFeedback=input("packet-feedback");
let packetGeneration=0;
let lastPacket=null;
const followupValues=["nog-niet-beoordeeld","eigenaar-benaderen",
  "bron-opnieuw-controleren","extern-geparkeerd"];
let previewVersion=0;
let latestPreview=null;
function freshness(){
  const stamp=Date.parse(document.body.dataset.observedAt||"");
  const difference=Date.now()-stamp;
  const stale=!Number.isFinite(difference)||difference>10*60*1000||
    difference<-(2*60*1000);
  input("issue-freshness").textContent=stale?
    "VEROUDERDE OF ONBETROUWBARE MOMENTOPNAME · Controleer GitHub/claims opnieuw voordat je actie onderneemt.":
    "RECENT BRONMOMENT · Nog altijd slechts een steekproef, geen eigendoms- of herstelbewijs.";
  return stale;
}
function visibleItems(){
  const q=input("issue-search").value.trim().toLowerCase();
  const project=input("issue-project").value;
  const priority=input("issue-priority").value;
  const route=input("issue-route").value;
  const progress=input("issue-progress").value;
  return boardItems.filter(item=>
    (!q||item.dataset.search.includes(q))&&
    (!project||item.dataset.project===project)&&
    (!priority||item.dataset.priority===priority)&&
    (!route||item.dataset.route===route)&&
    (!progress||item.dataset.change===progress));
}
function invalidatePacket(){
  packetGeneration++;
  lastPacket=null;
  packetDownload.disabled=true;
  packetPreview.textContent="Selectie of bron veranderd. Bekijk het werkpakket opnieuw.";
}
function invalidatePreview(){
  invalidatePacket();
  previewVersion++;
  latestPreview=null;
  download.disabled=true;
  followupDownload.disabled=true;
  preview.textContent="Bronfilters of notities gewijzigd. Maak een nieuwe voorvertoning.";
}
function refresh(){
  const show=new Set(visibleItems());
  for(const item of boardItems)item.hidden=!show.has(item);
  input("issue-visible-count").textContent=show.size+
    " zichtbare productissues uit een begrensde GitHub-steekproef. Geen leegtebewijs.";
  for(const section of document.querySelectorAll("main section[data-priority-group]")){
    const shown=section.querySelectorAll("article.issue:not([hidden])").length;
    const label=section.querySelector("[data-visible-group-count]");
    if(label)label.textContent=shown+" zichtbaar";
  }
  invalidatePreview();
}
for(const id of ["issue-search","issue-project","issue-priority","issue-route","issue-progress"]){
  input(id).addEventListener("input",refresh);
}
for(const item of boardItems){
  const picker=item.querySelector("[data-work-packet]");
  if(picker)picker.addEventListener("change",invalidatePacket);
  item.querySelector("[data-local-followup]").addEventListener("change",invalidatePreview);
  item.querySelector("[data-local-note]").addEventListener("input",invalidatePreview);
}
function markdownSafe(text){
  return String(text).replace(/[\\`*_{}\[\]()>#+.!|-]/g, "\\$&").replace(/[\r\n]+/g," ").slice(0,350);
}
input("preview-packet").addEventListener("click",()=>{
  const selected=visibleItems().filter(item=>
    item.querySelector("[data-work-packet]")?.checked);
  if(!selected.length||selected.length>12){
    invalidatePacket();
    packetFeedback.textContent="Selecteer 1 t/m 12 zichtbare productissues. Infrastructuur-P0 is uitgesloten.";
    return;
  }
  const seen=new Set();
  const entries=[];
  for(const item of selected){
    const project=item.dataset.project;
    const raw=item.dataset.issueUrl;
    const parts=new URL(raw);
    const tokens=parts.pathname.split("/").filter(Boolean);
    if(parts.hostname!=="github.com"||parts.protocol!=="https:"||
       tokens.length!==4||tokens[0]!=="Zennay"||tokens[2]!=="issues"||
       !/^[1-9][0-9]*$/.test(tokens[3])||seen.has(raw)||
       item.dataset.route==="authorized_infrastructure_owner_required"){
      invalidatePacket();
      packetFeedback.textContent="Onbetrouwbare of dubbele issuebron; geen werkpakket.";
      return;
    }
    seen.add(raw);
    const prs=[];
    for(const a of item.querySelectorAll('p a[href*="/pull/"]')){
      const dest=new URL(a.href);
      const p=dest.pathname.split("/").filter(Boolean);
      if(dest.hostname==="github.com"&&dest.protocol==="https:"&&
         p.length===4&&p[0]==="Zennay"&&p[1]===tokens[1]&&
         p[2]==="pull"&&/^[1-9][0-9]*$/.test(p[3]))
         prs.push(dest.href);
    }
    entries.push({
      project_id:project,issue_url:raw,title:item.dataset.title.slice(0,180),
      priority:item.dataset.priority,next_step:item.dataset.route,
      source_observed_at:item.dataset.sourceObservedAt||
        document.body.dataset.observedAt,
      source_window:item.dataset.sourceWindow||null,
      existing_prs_unverified:prs.slice(0,3),
      active_claim_verified:false,file_scope_verified:false,
      issue_fixed:false,dispatch_authorized:false,merge_authorized:false
    });
  }
  const payload={
    schema:"zsentinel-product-work-packet-draft-v1",
    created_at:new Date().toISOString(),
    source_time_oldest:document.body.dataset.observedAt,
    source_stale:freshness(),
    requires_current_claim_file_ci_review_check:true,
    owner_verified:false,permission_to_dispatch:false,
    permission_to_merge:false,claims_taken:false,writes_performed:false,
    items:entries
  };
  lastPacket={version:packetGeneration,payload};
  packetPreview.textContent=JSON.stringify(payload,null,2);
  packetFeedback.textContent=entries.length+
    " conceptitem(s); bron en bevoegdheid moeten nog gecontroleerd worden.";
  packetDownload.disabled=false;
});
packetDownload.addEventListener("click",()=>{
  if(!lastPacket||lastPacket.version!==packetGeneration)return;
  if(freshness()&&!lastPacket.payload.source_stale){
    invalidatePacket();
    packetFeedback.textContent="Bron is intussen ouder dan tien minuten; opnieuw bekijken.";
    return;
  }
  const data=JSON.stringify(lastPacket.payload,null,2)+"\n";
  const blob=new Blob([data],{type:"application/json;charset=utf-8"});
  const url=URL.createObjectURL(blob);
  try{
    const link=document.createElement("a");
    link.href=url;link.download="zsentinel-concept-werkpakket.json";
    document.body.append(link);link.click();link.remove();
  }finally{setTimeout(()=>URL.revokeObjectURL(url),30000);}
});
input("preview-issues").addEventListener("click",()=>{
  const version=previewVersion;
  const stale=freshness();
  const lines=["# zSentinel · bewuste privé productoverdracht","",
    "Bronmoment: "+String(document.body.dataset.observedAt||"ONBEKEND"),
    stale?"LET OP: HISTORISCH BRONMOMENT; huidige claims, PR-head en CI opnieuw verifiëren.":"Momentopname was recent bij voorvertoning; kan na export verouderen.",
    "Alleen huidig gefilterde items; bronbewijs, eigendom en release NIET geverifieerd.",
    "Geen claim, retry, merge of deploy uitgevoerd.",""];
  for(const item of visibleItems()){
    const note=item.querySelector("[data-local-note]").value.trim();
    const followup=item.querySelector("[data-local-followup]").value;
    lines.push("## "+markdownSafe(item.dataset.priority)+" · "+
      markdownSafe(item.dataset.title));
    lines.push("- Issue: "+item.dataset.issueUrl);
    lines.push("- Project: "+markdownSafe(item.dataset.project));
    lines.push("- Bronroute: "+markdownSafe(item.dataset.route));
    if(item.dataset.change!=="not-compared"){
      lines.push("- Verandering t.o.v. eerdere beperkte bron: "+
                 markdownSafe(item.dataset.change)+
                 " (nooit sluitings- of eigendomsbewijs)");
    }
    lines.push("- Eigen opvolgkeuze (geen echte status): "+markdownSafe(followup));
    if(note) lines.push("- Mijn privénotitie: "+markdownSafe(note));
    for(const anchor of item.querySelectorAll('p a[href*="/pull/"]')){
      const address=new URL(anchor.href);
      const segments=address.pathname.split("/").filter(Boolean);
      if(address.protocol==="https:"&&address.hostname==="github.com"&&
         segments.length===4&&segments[0]==="Zennay"&&
         /^[A-Za-z0-9_.-]+$/.test(segments[1])&&segments[2]==="pull"&&
         /^[1-9][0-9]*$/.test(segments[3])){
        lines.push("- Bestaande PR-verwijzing (geen oplossing bewezen): "+address.href);
      }
    }
    const receipt=item.querySelector("details");
    if(receipt){
      const summary=receipt.textContent.trim().replace(/\s+/g," ").slice(0,650);
      lines.push("- Reeds gelezen PR-bronactie: "+markdownSafe(summary));
    }
    lines.push("- Eigenaar/CI/merge: ONBEKEND of niet geautoriseerd","");
  }
  if(!visibleItems().length)lines.push("Geen issues zichtbaar. Dit bewijst NIET dat er geen problemen zijn.");
  latestPreview={version,body:lines.join("\n"),staleAtPreview:stale};
  preview.textContent=latestPreview.body;
  download.disabled=false;
  followupDownload.disabled=false;
});
followupDownload.addEventListener("click",()=>{
  if(!latestPreview||latestPreview.version!==previewVersion)return;
  if(freshness()&&!latestPreview.staleAtPreview){
    invalidatePreview();
    feedback.textContent="Bron is verouderd; maak een nieuwe voorvertoning.";
    return;
  }
  // Separate user-controlled local save, never an API/claim writer.
  const entries=boardItems.map(item=>({
    issue_url:item.dataset.issueUrl,
    project_id:item.dataset.project,
    followup:item.querySelector("[data-local-followup]").value,
    note:item.querySelector("[data-local-note]").value.slice(0,300)
  }));
  const receipt={
    schema:"zsentinel-private-followups-v1",
    source_observed_at:document.body.dataset.observedAt,
    exported_at:new Date().toISOString(),
    entries,
    external_writes_performed:false,
    owner_verified:false,
    claims_taken:false
  };
  const body=JSON.stringify(receipt,null,2)+"\\n";
  const blob=new Blob([body],{type:"application/json;charset=utf-8"});
  const url=URL.createObjectURL(blob);
  try{
    const link=document.createElement("a");
    link.href=url; link.download="zsentinel-prive-opvolgnotities.json";
    document.body.append(link);link.click();link.remove();
  }finally{setTimeout(()=>URL.revokeObjectURL(url),30000);}
});
followupImport.addEventListener("change",async ()=>{
  const file=followupImport.files&&followupImport.files[0];
  followupImport.value="";
  if(!file)return;
  try{
    if(file.size<1||file.size>65536)throw Error("Bestand groter dan 64 KiB of leeg");
    const saved=JSON.parse(await file.text());
    if(!saved||saved.schema!=="zsentinel-private-followups-v1"||
       saved.external_writes_performed!==false||
       saved.owner_verified!==false||saved.claims_taken!==false||
       typeof saved.source_observed_at!=="string"||
       saved.source_observed_at.length>50||
       !Array.isArray(saved.entries)||saved.entries.length>24){
      throw Error("Geen compatibele begrensde privénotities");
    }
    const byIssue=new Map(boardItems.map(item=>[item.dataset.issueUrl,item]));
    const seen=new Set();
    const entries=[];
    for(const record of saved.entries){
      if(!record||typeof record.issue_url!=="string"||
         record.issue_url.length>240||
         typeof record.project_id!=="string"||
         !followupValues.includes(record.followup)||
         typeof record.note!=="string"||record.note.length>300||
         seen.has(record.issue_url))throw Error("Ongeldige of dubbele issue-notitie");
      seen.add(record.issue_url);
      const item=byIssue.get(record.issue_url);
      if(item){
        if(item.dataset.project!==record.project_id)
          throw Error("Notitie heeft andere projectidentiteit");
        entries.push({item,record});
      }
    }
    if(!entries.length){
      feedback.textContent="Geen overlappende issue-URL in dit bronvenster; niets geïmporteerd.";
      return;
    }
    if(!window.confirm("Importeer "+entries.length+
      " eigen notities in dit TABBLAD? Dit verandert geen GitHub/Notion/workerstatus.")){
      feedback.textContent="Import geannuleerd. Niets gewijzigd.";
      return;
    }
    for(const {item,record} of entries){
      item.querySelector("[data-local-followup]").value=record.followup;
      item.querySelector("[data-local-note]").value=record.note;
    }
    invalidatePreview();
    feedback.textContent=entries.length+" persoonlijke notities lokaal hersteld; "+
      "eerder bronmoment "+saved.source_observed_at+
      ". Eigendom/issuefix blijft ONBEKEND.";
  }catch(error){
    feedback.textContent="Privé-import geweigerd: "+String(error.message).slice(0,100);
  }
});
download.addEventListener("click",()=>{
  if(!latestPreview||latestPreview.version!==previewVersion)return;
  if(freshness()&&!latestPreview.staleAtPreview){
    invalidatePreview();
    preview.textContent="Bron werd intussen historisch. Maak een nieuwe, expliciet als historisch gemarkeerde voorvertoning.";
    return;
  }
  const blob=new Blob([latestPreview.body],{type:"text/markdown;charset=utf-8"});
  const url=URL.createObjectURL(blob);
  try{
    const link=document.createElement("a");
    link.href=url; link.download="zsentinel-productoverdracht.md";
    document.body.append(link); link.click();link.remove();
  }finally{setTimeout(()=>URL.revokeObjectURL(url),30000);}
});
refresh();
freshness();
setInterval(freshness,60*1000);
"""


def script_tag():
    return "<script>" + INTERACTIVE_JS + "</script>"


def packet_selector():
    return ('<label class="work-packet"><input type="checkbox" '
            'data-work-packet> Voorbereiden als conceptwerkpakket '
            '(GEEN claim)</label>')


def followup_controls():
    return (
        '<div class="followup"><label>Mijn opvolgkeuze'
        '<select data-local-followup>'
        '<option value="nog-niet-beoordeeld">Nog niet beoordeeld</option>'
        '<option value="eigenaar-benaderen">Eigenaar benaderen</option>'
        '<option value="bron-opnieuw-controleren">Bron opnieuw controleren</option>'
        '<option value="extern-geparkeerd">Extern geparkeerd</option>'
        '</select></label><label>Mijn notitie (niet opgeslagen)'
        '<textarea data-local-note maxlength="300" placeholder="Eigen vervolgstap">'
        '</textarea></label></div>'
    )
