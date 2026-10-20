#!/usr/bin/env python3
"""Private offline, interactive human handoff for zSentinel's merge-first inbox.

Render only prevalidated source-bound cards; no API calls, persistence, claims,
work scheduling, automatic approval or release permissions.
"""
from __future__ import annotations

from html import escape
import re

from zsentinel_issue_action_board import MAX_CARDS, ORDER, PR, ISSUE


def render_html(board, max_rows=20):
    if (type(board) is not dict
            or board.get("schema") != "zsentinel-merge-first-human-inbox-v1"
            or any(board.get(key) is not False for key in (
                "current_claims_verified", "merge_authorized", "claim_authorized",
                "automatic_dispatch", "release_authorized", "writes_performed"))
            or type(board.get("cards")) is not list
            or type(board.get("issue_count")) is not int
            or not 0 <= board["issue_count"] <= MAX_CARDS
            or len(board["cards"]) != board["issue_count"]
            or type(max_rows) is not int or not 1 <= max_rows <= MAX_CARDS):
        raise ValueError("untrusted or unbounded private operator inbox")
    cards = board["cards"]
    output = []
    for pos, row in enumerate(cards[:max_rows], 1):
        if (type(row) is not dict or row.get("priority") not in ORDER
                or type(row.get("issue_url")) is not str
                or ISSUE.fullmatch(row["issue_url"]) is None
                or type(row.get("issue")) is not str
                or type(row.get("project")) is not str
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", row["project"]) is None
                or type(row.get("title")) is not str
                or not 1 <= len(row["title"]) <= 180
                or type(row.get("category_nl")) is not str
                or not 1 <= len(row["category_nl"]) <= 180
                or type(row.get("human_next_step_nl")) is not str
                or not 1 <= len(row["human_next_step_nl"]) <= 650
                or type(row.get("pr_file_scope_sample_complete")) is not bool
                or type(row.get("issue_reference_sample_complete")) is not bool
                or row.get("claim_authorized") is not False
                or row.get("release_authorized") is not False):
            raise ValueError("invalid issue row in operator inbox")
        pr = row.get("candidate_existing_pr_url")
        if pr is not None and (
                type(pr) is not str or PR.fullmatch(pr) is None
                or pr.split("/pull/", 1)[0] != row["issue_url"].split("/issues/", 1)[0]):
            raise ValueError("cross-repository candidate PR in operator inbox")
        def h(value):
            return escape(value, quote=True)
        pr_link = (
            "<a rel='noopener noreferrer' target='_blank' href='" + h(pr) +
            "'>Bestaande PR ↗</a>" if pr else
            "<span>Geen bestaande PR bevestigd in bron</span>"
        )
        note = ("PR-bestanden: " +
                ("volledig binnen steekproef" if row["pr_file_scope_sample_complete"]
                 else "ONVOLLEDIG") +
                " · Issueverwijzingen: " +
                ("volledig binnen steekproef" if row["issue_reference_sample_complete"]
                 else "ONVOLLEDIG"))
        output.append(
            "<article class='ticket' data-project='" + h(row["project"]) +
            "' data-priority='" + h(row["priority"]) +
            "' data-issue='" + h(row["issue_url"]) +
            "' data-pr='" + h(pr or "") +
            "' data-title='" + h(row["title"]) +
            "' data-category='" + h(row["category_nl"]) +
            "' data-next='" + h(row["human_next_step_nl"]) + "'>"
            "<header><span class='priority'>" + h(row["priority"]) + "</span> "
            "<span>" + h(row["project"]) + "</span> <small>#" +
            str(pos) + "</small></header><h2><a rel='noopener noreferrer' "
            "target='_blank' href='" + h(row["issue_url"]) + "'>" +
            h(row["title"]) + "</a></h2>"
            "<p><strong>" + h(row["category_nl"]) + "</strong></p>"
            "<p>" + h(row["human_next_step_nl"]) + "</p>"
            "<p class='links'>" + pr_link + "</p>"
            "<p class='source'>" + h(note) +
            " · actuele CI/review: " +
                h(row["candidate_exact_head_gate_state"]) +
                " · actuele owner/merge: ONBEKEND</p>"
            "<div class='controls'><label>Lokale vervolgstatus"
            "<select class='local-state'>"
            "<option value='open'>Nog te bekijken</option>"
            "<option value='owner'>Wacht op bevoegde eigenaar</option>"
            "<option value='coordination'>Afstemming nodig</option>"
            "<option value='followup'>Opvolging genoteerd</option>"
            "</select></label><label>Privé werknotitie (alleen dit browservenster)"
            "<textarea maxlength='700' rows='2' placeholder='Handmatige afspraak, geen automatische claim'></textarea>"
            "</label></div></article>"
        )
    if len(cards) > max_rows:
        hidden = "<p class='warning'>ONVOLLEDIGE overdracht: " + str(len(cards)-max_rows) + (
            " issues buiten deze begrensde pagina. Gebruik --worklist-limit "
            "voor een grotere, afzonderlijke export.</p>"
        )
    else:
        hidden = ""
    ts_issue = board.get("observed_issue_source_at_utc")
    ts_pr = board.get("observed_pr_source_at_utc")
    # Timestamps are inert text; historical snapshots never license work.
    issue_stamp = escape(str(ts_issue)[:60])
    pr_stamp = escape(str(ts_pr)[:60])
    script = r"""<script>
(function(){
'use strict';
const tickets=Array.from(document.querySelectorAll('.ticket'));
const search=document.getElementById('q'), priority=document.getElementById('priority');
const project=document.getElementById('project'), state=document.getElementById('state');
const visible=document.getElementById('visible');
const labels={open:'Nog te bekijken',owner:'Wacht op bevoegde eigenaar',
  coordination:'Afstemming nodig',followup:'Opvolging genoteerd'};
function refresh(){
 const q=search.value.trim().toLocaleLowerCase('nl');
 let count=0;
 for(const t of tickets){
  const text=[t.dataset.title,t.dataset.issue,t.dataset.pr,t.dataset.project,
   t.dataset.category,t.dataset.next].join(' ').toLocaleLowerCase('nl');
  const show=(!q||text.includes(q))&&(!priority.value||priority.value===t.dataset.priority)&&
   (!project.value||project.value===t.dataset.project)&&
   (!state.value||state.value===t.querySelector('.local-state').value);
  t.hidden=!show;if(show)count++;
 }
 visible.textContent=count+' van '+tickets.length+' bronkaarten zichtbaar';
}
for(const c of [search,priority,project,state])c.addEventListener('input',refresh);
for(const t of tickets)t.querySelector('.local-state').addEventListener('change',refresh);
document.getElementById('reset').addEventListener('click',function(){
 search.value='';priority.value='';project.value='';state.value='';refresh();
});
function safeText(value){
 return String(value||'').replace(/[\r\n\t]+/g,' ').replace(/([\\*_{}\[\]()|<>!#\x60~])/g,'\\$1').slice(0,720);
}
document.getElementById('export').addEventListener('click',function(){
 const parts=['# zSentinel · private handmatige opvolging','',
  'Issuebron UTC: '+safeText(document.getElementById('issue-time').textContent),
  'PR-bron UTC: '+safeText(document.getElementById('pr-time').textContent),
  'Geen claim-, merge-, CI-, review- of deployautorisatie.',''];
 for(const [index,t] of tickets.entries()){
  const issue=t.dataset.issue;
  parts.push(String(index+1)+'. ['+safeText(t.dataset.title)+']('+issue+') · '+
   safeText(t.dataset.project)+' · '+safeText(t.dataset.priority));
  parts.push('   Bestaande PR: '+(t.dataset.pr?'[Open PR]('+t.dataset.pr+')':'niet gezien in steekproef'));
  parts.push('   Bronroute: '+safeText(t.dataset.category));
  parts.push('   Aanbevolen stap: '+safeText(t.dataset.next));
  parts.push('   Handmatige status: '+labels[t.querySelector('.local-state').value]);
  const note=t.querySelector('textarea').value.trim();
  if(note)parts.push('   Lokale operatornotitie: '+safeText(note));
  parts.push('');
 }
 parts.push('Statussen zijn uitsluitend handmatig, lokaal en niet op GitHub/Notion vastgelegd.');
 const blob=new Blob([parts.join('\n')+'\n'],{type:'text/markdown;charset=utf-8'});
 const url=URL.createObjectURL(blob);const a=document.createElement('a');
 a.href=url;a.download='zsentinel-prive-opvolging.md';a.click();
 window.setTimeout(function(){URL.revokeObjectURL(url);},3000);
});
window.addEventListener('beforeunload',function(e){
 if(tickets.some(t=>t.querySelector('textarea').value.trim()||
   t.querySelector('.local-state').value!=='open')){
  e.preventDefault();e.returnValue='';
 }
});
refresh();
})();</script>"""
    project_names = sorted({r["project"] for r in cards[:max_rows]})
    p_opts = "".join("<option value='" + escape(name, quote=True) + "'>" +
                     escape(name) + "</option>" for name in project_names)
    return (
        "<!doctype html><html lang='nl'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='referrer' content='no-referrer'>"
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; "
        "script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; "
        "img-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'\">"
        "<title>zSentinel · merge-eerst werkbak</title>"
        "<style>body{font:16px/1.55 system-ui;background:#111927;color:#eff5fc;"
        "max-width:1120px;margin:auto;padding:18px}h1{font-size:1.6rem}"
        "h2{font-size:1.15rem}a{color:#a6d7ff}a:focus-visible,button:focus-visible,"
        "input:focus-visible,textarea:focus-visible,select:focus-visible{outline:3px solid #f0c976}"
        ".filters{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin:18px 0}"
        "label{display:grid;gap:4px}input,select,textarea,button{font:inherit;"
        "background:#233248;color:white;border:1px solid #8595ac;border-radius:6px;"
        "padding:10px}button{cursor:pointer}.ticket{border:1px solid #526a83;"
        "background:#1b293c;border-radius:10px;padding:16px;margin:14px 0}"
        ".ticket[hidden]{display:none}.ticket header{display:flex;gap:12px;"
        "align-items:center}.priority{font-weight:bold;background:#3a4d66;padding:2px 8px}"
        ".controls{display:grid;grid-template-columns:minmax(170px,260px) 1fr;"
        "gap:16px}.controls textarea{width:100%;box-sizing:border-box;resize:vertical}"
        ".source,.warning{color:#ebce9b}.links a{margin-right:10px}"
        "@media(max-width:640px){.controls{grid-template-columns:1fr}}"
        "</style></head><body><h1>zSentinel · merge-eerst werkbak</h1>"
        "<p>Geobserveerde P0/P1-productissues, bestaande PRs en menselijke vervolgstappen. "
        "Geen automatische claim, issue-afhandeling, CI, review, merge, runnerherstel of deploy. "
        "Notities en statussen blijven in dit tabblad tot export; er wordt niets opgeslagen.</p>"
        "<p>Bron issue UTC <span id='issue-time'>" + issue_stamp +
        "</span> · PR UTC <span id='pr-time'>" + pr_stamp + "</span></p>"
        "<div class='filters'><label>Zoeken<input id='q' type='search'></label>"
        "<label>Prioriteit<select id='priority'><option value=''>Alles</option>"
        "<option>P0</option><option>P1</option><option>P2</option>"
        "<option>Unspecified</option></select></label>"
        "<label>Project<select id='project'><option value=''>Alles</option>" +
        p_opts + "</select></label>"
        "<label>Handmatige status<select id='state'><option value=''>Alles</option>"
        "<option value='open'>Nog te bekijken</option>"
        "<option value='owner'>Wacht op eigenaar</option>"
        "<option value='coordination'>Afstemming nodig</option>"
        "<option value='followup'>Opvolging genoteerd</option></select></label>"
        "<button id='reset' type='button'>Wis filters</button>"
        "<button id='export' type='button'>Download privéoverdracht (Markdown)</button></div>"
        "<p id='visible' aria-live='polite'></p>" + hidden +
        "<main>" + "".join(output) + "</main>" + script + "</body></html>"
    )
