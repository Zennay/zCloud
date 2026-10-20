#!/usr/bin/env python3
"""Private offline, interactive human handoff for zSentinel's merge-first inbox.

Render only prevalidated source-bound cards; no API calls, persistence, claims,
work scheduling, automatic approval or release permissions.
"""
from __future__ import annotations

from html import escape
import hashlib
import json
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
        other_prs = row.get("other_existing_pr_candidates_in_sample", [])
        if type(other_prs) is not list or len(other_prs) > 4:
            raise ValueError("unbounded alternative PR candidates")
        other_seen = set()
        alt_items = []
        for other in other_prs:
            if type(other) is not dict:
                raise ValueError("invalid alternative PR source")
            alt_url = other.get("url")
            gate_state = other.get("gate_state")
            if (type(alt_url) is not str or PR.fullmatch(alt_url) is None
                    or alt_url.split("/pull/", 1)[0] != row["issue_url"].split("/issues/", 1)[0]
                    or alt_url == pr or alt_url in other_seen
                    or gate_state not in (
                        "not_observed", "observed_failed", "observed_pending",
                        "observed_partial_no_release_permission")
                    or type(other.get("file_overlap_observed")) is not bool
                    or type(other.get("source_movement_observed")) is not bool
                    or other.get("merge_authorized") is not False
                    or other.get("owner_verified") is not False):
                raise ValueError("untrusted alternative PR gate or authority")
            other_seen.add(alt_url)
            alt_items.append((alt_url, gate_state, other))
        full = row.get("all_existing_pr_candidates_in_sample")
        observed_count = row.get("existing_pr_count_in_sample")
        if (type(full) is not list or len(full) > 5
                or type(observed_count) is not int
                or observed_count != len(full)
                or observed_count != len(alt_items) + int(pr is not None)
                or any(type(item) is not dict or type(item.get("url")) is not str
                       for item in full)
                or {item["url"] for item in full}
                != (other_seen | ({pr} if pr else set()))):
            raise ValueError("issue PR candidates not consistent with saved source")
        if (row.get("other_existing_prs_with_observed_failed_gates")
                != sum(state == "observed_failed" for _, state, _ in alt_items)
                or row.get("other_existing_prs_with_observed_pending_gates")
                != sum(state == "observed_pending" for _, state, _ in alt_items)):
            raise ValueError("issue PR gate totals contradict saved source")
        def h(value):
            return escape(value, quote=True)
        alt_html = ""
        if alt_items:
            extra = "".join(
                "<li><a target='_blank' rel='noopener noreferrer' href='" +
                h(url) + "'>PR " + h(url.rsplit("/", 1)[-1]) + " ↗</a> · " +
                h(state) +
                (" · bestandsoverlap" if data["file_overlap_observed"] else "") +
                (" · bronbeweging" if data["source_movement_observed"] else "") +
                "</li>"
                for url, state, data in alt_items
            )
            alt_html = ("<details class='other-prs'><summary>Andere bestaande PRs (" +
                        str(len(alt_items)) +
                        ") · afzonderlijke eigenaar- en CI-beoordeling</summary><ul>" +
                        extra + "</ul></details>")
        pr_link = (
            "<a rel='noopener noreferrer' target='_blank' href='" + h(pr) +
            "'>Bestaande PR ↗</a>" if pr else
            "<span>Geen bestaande PR bevestigd in bron</span>"
        )
        alternatives_failed = sum(state == "observed_failed"
                                  for _, state, _ in alt_items)
        alternatives_pending = sum(state == "observed_pending"
                                   for _, state, _ in alt_items)
        note = ("Bestaande PRs in bron: " + str(len(alt_items) + int(bool(pr))) +
                (" · andere met mislukte checks: " + str(alternatives_failed)
                 if alternatives_failed else "") +
                (" · andere met wachtende checks: " + str(alternatives_pending)
                 if alternatives_pending else "") +
                " · PR-bestanden: " +
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
            "' data-prs='" + h(" ".join(item[0] for item in alt_items)) +
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
            "<p class='links'>" + pr_link + "</p>" +
            alt_html +
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
    visible_issues = {card["issue_url"] for card in cards[:max_rows]}
    grouped = []
    for group in board.get("pr_groups", []):
        pr = group.get("pr_url") if type(group) is dict else None
        linked = group.get("linked_issue_urls_in_source") if type(group) is dict else None
        if (type(pr) is not str or PR.fullmatch(pr) is None
                or group.get("highest_priority") not in ORDER
                or group.get("fixes_all_linked_issues_verified") is not False
                or group.get("merge_authorized") is not False
                or type(linked) is not list or not 1 <= len(linked) <= MAX_CARDS
                or any(type(url) is not str or ISSUE.fullmatch(url) is None
                       or url.split("/issues/", 1)[0] != pr.split("/pull/", 1)[0]
                       for url in linked)):
            raise ValueError("untrusted existing PR issue-group evidence")
        primary_urls = group.get("primary_issue_urls_in_source", [])
        if (type(primary_urls) is not list
                or len(primary_urls) > len(linked)
                or len(set(primary_urls)) != len(primary_urls)
                or any(url not in linked for url in primary_urls)
                or type(group.get("primary_issue_count_observed")) is not int
                or group["primary_issue_count_observed"] != len(primary_urls)):
            raise ValueError("invalid primary vs secondary PR issue mapping")
        shown_links = [url for url in linked if url in visible_issues]
        if not shown_links:
            continue
        primary_visible = sum(url in visible_issues for url in primary_urls)
        refs = "".join("<li><a rel='noopener noreferrer' target='_blank' href='" +
                       escape(url, quote=True) + "'>Issue " +
                       escape(url.rsplit("/", 1)[-1]) + "</a></li>"
                       for url in shown_links)
        grouped.append(
            "<li><strong>" + escape(group["highest_priority"]) + "</strong> · "
            "<a rel='noopener noreferrer' target='_blank' href='" +
            escape(pr, quote=True) + "'>PR " +
            escape(pr.rsplit("/", 1)[-1]) + "</a> · " +
            str(len(shown_links)) +
            " gekoppelde issuevermelding(en) in deze beperkte overdracht; " +
            str(primary_visible) + " als primaire reviewkandidaat"
            "<details><summary>Bekijk bronissues (geen fixbewijs)</summary><ul>" +
            refs + "</ul></details></li>"
        )
    groups_panel = (
        "<section class='ticket'><h2>Bestaande PR's als één werkgroep</h2>"
        "<p>Iedere bestaande PR-kandidaat staat hier, ook als een andere PR "
        "primair bij het issue werd gekozen. Gedeelde issuevermeldingen zijn "
        "géén fixbewijs, eigenaarschap of mergevrijgave.</p>"
        "<ol>" + "".join(grouped) + "</ol></section>" if grouped else "")
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
const tickets=Array.from(document.querySelectorAll('article.ticket[data-issue]'));
const search=document.getElementById('q'), priority=document.getElementById('priority');
const project=document.getElementById('project'), state=document.getElementById('state');
const visible=document.getElementById('visible');
const labels={open:'Nog te bekijken',owner:'Wacht op bevoegde eigenaar',
  coordination:'Afstemming nodig',followup:'Opvolging genoteerd'};
function refresh(){
 const q=search.value.trim().toLocaleLowerCase('nl');
 let count=0;
 for(const t of tickets){
  const text=[t.dataset.title,t.dataset.issue,t.dataset.pr,t.dataset.prs,
   t.dataset.project,t.dataset.category,t.dataset.next].join(' ').toLocaleLowerCase('nl');
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
  const additional=t.dataset.prs.trim()?t.dataset.prs.trim().split(' '):[];
  if(additional.length){
   parts.push('   Andere bestaande PRs (geen eigenaars- of mergebewijs):');
   for(const alt of additional.slice(0,4))parts.push('   - [Open PR]('+alt+')');
  }
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
document.getElementById('session-save').addEventListener('click',function(){
 const rows=tickets.map(function(t){return {
  issue:t.dataset.issue,
  status:t.querySelector('.local-state').value,
  note:t.querySelector('textarea').value.slice(0,700)
 };});
 const data=JSON.stringify({
  schema:'zsentinel-local-handoff-v1',
  source_token:document.body.dataset.sourceToken,
  saved_at:new Date().toISOString(),
  entries:rows,
  merge_authorized:false,claim_authorized:false,remote_writes_performed:false
 },null,2);
 const blob=new Blob([data+'\n'],{type:'application/json;charset=utf-8'});
 const url=URL.createObjectURL(blob);const a=document.createElement('a');
 a.href=url;a.download='zsentinel-prive-sessie.json';a.click();
 window.setTimeout(function(){URL.revokeObjectURL(url);},3000);
});
document.getElementById('session-import').addEventListener('change',async function(event){
 const input=event.currentTarget, file=input.files&&input.files[0];
 if(!file)return;
 try{
  if(file.size>65536||file.size<10)throw new Error('Privébestand is ongeldig of te groot');
  const restored=JSON.parse(await file.text());
  if(!restored||restored.schema!=='zsentinel-local-handoff-v1'||
     restored.source_token!==document.body.dataset.sourceToken||
     restored.merge_authorized!==false||restored.claim_authorized!==false||
     restored.remote_writes_performed!==false||
     !Array.isArray(restored.entries)||restored.entries.length!==tickets.length)
    throw new Error('Deze sessie hoort niet bij dezelfde bronopname');
  const allowed=new Set(Object.keys(labels));
  for(let i=0;i<tickets.length;i++){
    const item=restored.entries[i];
    if(!item||item.issue!==tickets[i].dataset.issue||!allowed.has(item.status)||
       typeof item.note!=='string'||item.note.length>700)
      throw new Error('Sessie bevat geen betrouwbare handmatige opvolging');
  }
  for(let i=0;i<tickets.length;i++){
    tickets[i].querySelector('.local-state').value=restored.entries[i].status;
    tickets[i].querySelector('textarea').value=restored.entries[i].note;
  }
  refresh();
  document.getElementById('session-status').textContent=
   'Privésessie hersteld, alleen lokaal in dit browservenster.';
 }catch(error){
  document.getElementById('session-status').textContent=
   'Import geweigerd: '+(error instanceof Error?error.message:'onbekende bron');
 }finally{input.value='';}
});
window.addEventListener('beforeunload',function(e){
 if(tickets.some(t=>t.querySelector('textarea').value.trim()||
   t.querySelector('.local-state').value!=='open')){
  e.preventDefault();e.returnValue='';
 }
});
refresh();
})();</script>"""
    # Exact saved source identity; local notes cannot silently attach
    # to different observations or reordered PR/issue sets.
    source_identity = {
        "issue_time": str(ts_issue), "pr_time": str(ts_pr),
        "visible_rows": [
            (row["issue_url"], row["candidate_existing_pr_url"],
             row.get("candidate_exact_head_gate_state"),
             tuple((candidate["url"], candidate["gate_state"],
                    candidate["file_overlap_observed"],
                    candidate["source_movement_observed"])
                   for candidate in row.get("other_existing_pr_candidates_in_sample", [])))
            for row in cards[:max_rows]
        ],
    }
    source_token = hashlib.sha256(json.dumps(
        source_identity, ensure_ascii=True, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
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
        "</style></head><body data-source-token='" + source_token +
        "'><h1>zSentinel · merge-eerst werkbak</h1>"
        "<p>Geobserveerde P0/P1-productissues, bestaande PRs en menselijke vervolgstappen. "
        "Geen automatische claim, issue-afhandeling, CI, review, merge, runnerherstel of deploy. "
        "Notities en statussen blijven lokaal; bewaar een privé-sessiebestand om later precies dezelfde bron te hervatten.</p>"
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
        "<button id='export' type='button'>Download privéoverdracht (Markdown)</button>"
        "<button id='session-save' type='button'>Bewaar lokale sessie (JSON)</button>"
        "<label>Heropen lokale sessie<input id='session-import' type='file' "
        "accept='.json,application/json'></label></div>"
        "<p id='visible' aria-live='polite'></p>"
        "<p id='session-status' aria-live='polite'></p>" + hidden +
        "<main>" + groups_panel + "".join(output) + "</main>" + script + "</body></html>"
    )
