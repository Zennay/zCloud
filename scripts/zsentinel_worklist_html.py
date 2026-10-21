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


def source_head_link(pr_url, head):
    """Render only an admitted historical exact commit, never merge approval."""
    if head is None:
        return "<strong>Bron HEAD ONBEKEND</strong>"
    if (type(head) is not str or re.fullmatch(r"[0-9a-f]{40}", head) is None
            or type(pr_url) is not str or PR.fullmatch(pr_url) is None):
        raise ValueError("untrusted saved PR commit identity")
    base = pr_url.split("/pull/", 1)[0]
    return ("<a target='_blank' rel='noopener noreferrer' href='" +
            escape(base + "/commit/" + head, quote=True) +
            "'>Waargenomen bron HEAD <code>" + head[:12] +
            "</code> ↗</a> · geen actuele mergevrijgave")


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
                        "observed_partial_no_release_permission",
                        "issue_source_stale", "source_clock_incoherent")
                    or type(other.get("file_overlap_observed")) is not bool
                    or type(other.get("source_movement_observed")) is not bool
                    or other.get("merge_authorized") is not False
                    or other.get("owner_verified") is not False
                    or (other.get("source_head_observed") is not None
                        and (type(other["source_head_observed"]) is not str
                             or re.fullmatch(r"[0-9a-f]{40}",
                                             other["source_head_observed"]) is None))):
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
                "<br>" + source_head_link(url, data.get("source_head_observed")) +
                "</li>"
                for url, state, data in alt_items
            )
            alt_html = ("<details class='other-prs'><summary>Andere bestaande PRs (" +
                        str(len(alt_items)) +
                        ") · afzonderlijke eigenaar- en CI-beoordeling</summary><ul>" +
                        extra + "</ul></details>")
        primary = next((item for item in full if item["url"] == pr), None)
        primary_head = primary.get("source_head_observed") if primary else None
        if primary_head is not None and (
                type(primary_head) is not str or
                re.fullmatch(r"[0-9a-f]{40}", primary_head) is None):
            raise ValueError("untrusted primary PR source HEAD")
        pr_link = (
            "<a rel='noopener noreferrer' target='_blank' href='" + h(pr) +
            "'>Bestaande PR ↗</a><br>" + source_head_link(pr, primary_head)
            if pr else
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
            "<article class='ticket' tabindex='-1' data-project='" + h(row["project"]) +
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
            "<p class='links'>" + pr_link +
            (" <button class='focus-from-issue' type='button'>"
             "Bekijk alle bronissues van deze PR</button>" if pr else "") +
            "</p>" + alt_html +
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
    visible_project = {card["issue_url"]: card["project"]
                       for card in cards[:max_rows]}
    groups = board.get("pr_groups", [])
    if (type(groups) is not list or len(groups) > MAX_CARDS * 5):
        raise ValueError("unbounded existing PR work groups")
    grouped = []
    group_source = []
    seen_group_prs = set()
    for group in groups:
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
        if pr in seen_group_prs:
            raise ValueError("duplicated PR group")
        seen_group_prs.add(pr)
        primary_urls = group.get("primary_issue_urls_in_source", [])
        if (type(primary_urls) is not list
                or len(primary_urls) > len(linked)
                or len(set(primary_urls)) != len(primary_urls)
                or any(url not in linked for url in primary_urls)
                or type(group.get("primary_issue_count_observed")) is not int
                or group["primary_issue_count_observed"] != len(primary_urls)):
            raise ValueError("invalid primary vs secondary PR issue mapping")
        observed_gate = group.get("group_gate_source_state")
        group_head = group.get("source_head_observed")
        if group_head is not None and (
                type(group_head) is not str or
                re.fullmatch(r"[0-9a-f]{40}", group_head) is None):
            raise ValueError("invalid PR-group source HEAD")
        permitted_gate = {
            "observed_failed", "observed_pending", "not_observed",
            "observed_partial_no_release_permission",
            "issue_source_stale", "source_clock_incoherent",
            "inconsistent_existing_pr_gate_sources",
        }
        human_gate = group.get("human_next_gate_step")
        if (observed_gate not in permitted_gate
                or type(human_gate) is not str or not 1 <= len(human_gate) <= 220
                or type(group.get("any_file_overlap_observed")) is not bool
                or type(group.get("any_historical_movement_observed")) is not bool):
            raise ValueError("untrusted existing PR group gate observation")
        shown_links = [url for url in linked if url in visible_issues]
        if not shown_links:
            continue
        primary_visible = sum(url in visible_issues for url in primary_urls)
        source_projects = {visible_project[url] for url in shown_links}
        if len(source_projects) != 1:
            raise ValueError("PR issue group spans inconsistent local projects")
        group_project = next(iter(source_projects))
        # Each grouped PR must reference the same saved exact-head identity as
        # its visible source-linked issues. A forged or stale group receipt
        # cannot turn an unrelated commit into a credible operator shortcut.
        for issue_row in cards[:max_rows]:
            if issue_row["issue_url"] not in shown_links:
                continue
            matches = [candidate for candidate in
                       issue_row["all_existing_pr_candidates_in_sample"]
                       if candidate["url"] == pr]
            if len(matches) != 1 or matches[0].get("source_head_observed") != group_head:
                raise ValueError("grouped PR source HEAD disagrees with issue evidence")
        group_source.append((
            pr, group["highest_priority"], tuple(linked), tuple(primary_urls),
            observed_gate, human_gate, group_head,
            group["any_file_overlap_observed"],
            group["any_historical_movement_observed"]))
        refs = "".join("<li><a rel='noopener noreferrer' target='_blank' href='" +
                       escape(url, quote=True) + "'>Issue " +
                       escape(url.rsplit("/", 1)[-1]) + "</a></li>"
                       for url in shown_links)
        grouped.append(
            "<li class='pr-work' data-pr='" + escape(pr, quote=True) +
            "' data-project='" + escape(group_project, quote=True) +
            "' data-priority='" + escape(group["highest_priority"], quote=True) +
            "' data-issues='" + escape(" ".join(shown_links), quote=True) +
            "' data-gate='" + escape(observed_gate, quote=True) +
            "'><strong>" + escape(group["highest_priority"]) + "</strong> · "
            "<a rel='noopener noreferrer' target='_blank' href='" +
            escape(pr, quote=True) + "'>PR " +
            escape(pr.rsplit("/", 1)[-1]) + "</a> · " +
            str(len(shown_links)) +
            " gekoppelde issuevermelding(en) in deze beperkte overdracht; " +
            str(primary_visible) + " als primaire reviewkandidaat"
            "<p class='source'>" + source_head_link(pr, group_head) +
            "<br>Exacte-HEAD bron: " + escape(observed_gate) +
            " · " + escape(human_gate) +
            (" · Bestandsoverlap waargenomen" if
             group["any_file_overlap_observed"] else "") +
            (" · Historische bronbeweging" if
             group["any_historical_movement_observed"] else "") +
            " · actuele owner/merge: ONBEKEND</p>"
            "<details><summary>Bekijk bronissues (geen fixbewijs)</summary><ul>" +
            refs + "</ul></details>"
            "<button class='focus-pr' type='button'>Bekijk gekoppelde issues in werkbak</button>"
            "<div class='pr-controls'><label>Privé PR-opvolging"
            "<select class='pr-local-state'>"
            "<option value='open'>Nog te beoordelen</option>"
            "<option value='review'>Wacht op review/CI</option>"
            "<option value='owner'>Wacht op huidige eigenaar</option>"
            "<option value='coordinate'>Afstemming tussen PRs nodig</option>"
            "<option value='followup'>Opvolging vastgelegd</option>"
            "</select></label><label>Privé PR-notitie, geen GitHub-commentaar"
            "<textarea class='pr-note' maxlength='500' rows='2'"
            " placeholder='Menselijke opvolging; geen merge- of eigenaarvrijgave'></textarea>"
            "</label></div></li>"
        )
    groups_panel = (
        "<section class='pr-panel'><h2>Bestaande PR's als één werkgroep</h2>"
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
const prs=Array.from(document.querySelectorAll('.pr-work[data-pr]'));
const search=document.getElementById('q'), priority=document.getElementById('priority');
const project=document.getElementById('project'), state=document.getElementById('state');
const visible=document.getElementById('visible'), prState=document.getElementById('pr-state');
const focusBar=document.getElementById('focus-bar'),focusTitle=document.getElementById('focus-title');
const focusClear=document.getElementById('focus-clear');
const focusNext=document.getElementById('focus-next');
let focusedPr=null,focusIndex=-1;
function linkedToFocus(t){
 return Boolean(focusedPr)&&
   [t.dataset.pr,...t.dataset.prs.trim().split(' ').filter(Boolean)].includes(focusedPr);
}
const prLabels={open:'Nog te beoordelen',review:'Wacht op review/CI',
 owner:'Wacht op huidige eigenaar',coordinate:'Afstemming tussen PRs nodig',
 followup:'Opvolging vastgelegd'};
const labels={open:'Nog te bekijken',owner:'Wacht op bevoegde eigenaar',
  coordination:'Afstemming nodig',followup:'Opvolging genoteerd'};
function refresh(){
 const q=search.value.trim().toLocaleLowerCase('nl');
 let count=0;
 for(const t of tickets){
  const text=[t.dataset.title,t.dataset.issue,t.dataset.pr,t.dataset.prs,
   t.dataset.project,t.dataset.category,t.dataset.next].join(' ').toLocaleLowerCase('nl');
  const show=(!focusedPr||linkedToFocus(t))&&
   (!q||text.includes(q))&&(!priority.value||priority.value===t.dataset.priority)&&
   (!project.value||project.value===t.dataset.project)&&
   (!state.value||state.value===t.querySelector('.local-state').value);
  t.hidden=!show;if(show)count++;
 }
 let prCount=0;
 for(const p of prs){
  const content=[p.dataset.pr,p.dataset.issues,p.dataset.project,
    p.dataset.gate].join(' ').toLocaleLowerCase('nl');
  const show=(!focusedPr||p.dataset.pr===focusedPr)&&(!q||content.includes(q))&&
    (!priority.value||priority.value===p.dataset.priority)&&
    (!project.value||project.value===p.dataset.project)&&
    (!prState.value||prState.value===p.querySelector('.pr-local-state').value);
  p.hidden=!show;if(show)prCount++;
 }
 visible.textContent=count+' van '+tickets.length+' issuekaarten en '+
   prCount+' van '+prs.length+' bestaande PR-groepen zichtbaar';
 focusBar.hidden=!focusedPr;
 if(focusedPr){
   const open=tickets.filter(t=>linkedToFocus(t)&&
     t.querySelector('.local-state').value==='open');
   focusTitle.textContent='PR '+focusedPr.split('/').pop()+
     ': '+count+' gekoppelde bronissues in dit dossier, '+open.length+
     ' nog te bekijken. Status is alleen lokaal; geen fix- of mergebewijs.';
   focusNext.disabled=open.length===0;
 }
}
function focusOnPr(url){
 const target=prs.find(p=>p.dataset.pr===url);
 if(!target)return;
 focusedPr=url;focusIndex=-1;
 search.value='';priority.value='';project.value='';state.value='';prState.value='';
 refresh();
 focusBar.scrollIntoView({block:'start'});
 focusClear.focus();
}
for(const p of prs)p.querySelector('.focus-pr').addEventListener('click',()=>focusOnPr(p.dataset.pr));
for(const t of tickets){
 const button=t.querySelector('.focus-from-issue');
 if(button)button.addEventListener('click',()=>focusOnPr(t.dataset.pr));
}
focusClear.addEventListener('click',()=>{
 focusedPr=null;focusIndex=-1;refresh();document.getElementById('q').focus();
});
focusNext.addEventListener('click',()=>{
 const open=tickets.filter(t=>linkedToFocus(t)&&
   t.querySelector('.local-state').value==='open'&&!t.hidden);
 if(!open.length)return;
 focusIndex=(focusIndex+1)%open.length;
 open[focusIndex].scrollIntoView({block:'start'});
 open[focusIndex].focus();
});
for(const c of [search,priority,project,state,prState])c.addEventListener('input',refresh);
for(const t of tickets)t.querySelector('.local-state').addEventListener('change',refresh);
for(const p of prs)p.querySelector('.pr-local-state').addEventListener('change',refresh);
document.getElementById('reset').addEventListener('click',function(){
 focusedPr=null;focusIndex=-1;search.value='';priority.value='';project.value='';state.value='';prState.value='';refresh();
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
 if(prs.length)parts.push('## Bestaande PRs: één handmatige opvolging per PR','',
  'Groepskoppelingen zijn bronverwijzingen, geen eigenaars-, review- of fixbewijs.','');
 for(const p of prs){
  const url=p.dataset.pr;
  parts.push('- [Bestaande PR]('+url+') · '+safeText(p.dataset.priority)+
   ' · '+safeText(p.dataset.project));
  const issues=p.dataset.issues.trim()?p.dataset.issues.trim().split(' '):[];
  for(const issue of issues)parts.push('  - [Bronissue]('+issue+')');
  parts.push('  - Waargenomen gatebron: '+safeText(p.dataset.gate)+
   ' (geen autorisatie)');
  parts.push('  - Handmatige PR-status: '+prLabels[p.querySelector('.pr-local-state').value]);
  const note=p.querySelector('.pr-note').value.trim();
  if(note)parts.push('  - Privé PR-opvolging: '+safeText(note));
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
 const prRows=prs.map(function(p){return {
  pr:p.dataset.pr,
  status:p.querySelector('.pr-local-state').value,
  note:p.querySelector('.pr-note').value.slice(0,500)
 };});
 const data=JSON.stringify({
  schema:'zsentinel-local-handoff-v2',
  source_token:document.body.dataset.sourceToken,
  saved_at:new Date().toISOString(),
  entries:rows, pr_entries:prRows,
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
  if(file.size>524288||file.size<10)throw new Error('Privébestand is ongeldig of te groot');
  const restored=JSON.parse(await file.text());
  const oldSession=restored&&restored.schema==='zsentinel-local-handoff-v1'&&
    restored.source_token===document.body.dataset.legacySourceToken;
  const currentSession=restored&&restored.schema==='zsentinel-local-handoff-v2'&&
    restored.source_token===document.body.dataset.sourceToken;
  if(!restored||(!oldSession&&!currentSession)||
     restored.merge_authorized!==false||restored.claim_authorized!==false||
     restored.remote_writes_performed!==false||
     !Array.isArray(restored.entries)||restored.entries.length!==tickets.length||
     (currentSession&&(!Array.isArray(restored.pr_entries)||
       restored.pr_entries.length!==prs.length))||
     (oldSession&&Object.prototype.hasOwnProperty.call(restored,'pr_entries')))
    throw new Error('Deze sessie hoort niet bij dezelfde bronopname');
  const allowed=new Set(Object.keys(labels));
  for(let i=0;i<tickets.length;i++){
    const item=restored.entries[i];
    if(!item||item.issue!==tickets[i].dataset.issue||!allowed.has(item.status)||
       typeof item.note!=='string'||item.note.length>700)
      throw new Error('Sessie bevat geen betrouwbare handmatige opvolging');
  }
  if(currentSession){
   const prAllowed=new Set(Object.keys(prLabels));
   for(let i=0;i<prs.length;i++){
     const item=restored.pr_entries[i];
     if(!item||item.pr!==prs[i].dataset.pr||!prAllowed.has(item.status)||
        typeof item.note!=='string'||item.note.length>500)
       throw new Error('Sessie bevat ongeldige PR-opvolging');
   }
  }
  for(let i=0;i<tickets.length;i++){
    tickets[i].querySelector('.local-state').value=restored.entries[i].status;
    tickets[i].querySelector('textarea').value=restored.entries[i].note;
  }
  if(currentSession){
   for(let i=0;i<prs.length;i++){
     prs[i].querySelector('.pr-local-state').value=restored.pr_entries[i].status;
     prs[i].querySelector('.pr-note').value=restored.pr_entries[i].note;
   }
  }
  refresh();
  document.getElementById('session-status').textContent=oldSession?
   'Oude issuenotities exact op bron hersteld; PR-groepsnotities blijven ongewijzigd.':
   'Privésessie inclusief PR-opvolging hersteld, alleen lokaal in dit browservenster.';
 }catch(error){
  document.getElementById('session-status').textContent=
   'Import geweigerd: '+(error instanceof Error?error.message:'onbekende bron');
 }finally{input.value='';}
});
window.addEventListener('beforeunload',function(e){
 if(tickets.some(t=>t.querySelector('textarea').value.trim()||
   t.querySelector('.local-state').value!=='open')||
    prs.some(p=>p.querySelector('.pr-note').value.trim()||
    p.querySelector('.pr-local-state').value!=='open')){
  e.preventDefault();e.returnValue='';
 }
});
refresh();
})();</script>"""
    # Exact saved source identity; local notes cannot silently attach
    # to different observations or reordered PR/issue sets.
    source_identity = {
        "issue_time": str(ts_issue), "pr_time": str(ts_pr),
        "visible_pr_groups": group_source,
        "visible_rows": [
            (row["issue_url"], row["candidate_existing_pr_url"],
             row.get("candidate_exact_head_gate_state"),
             tuple((candidate["url"], candidate["gate_state"],
                    candidate.get("source_head_observed"),
                    candidate["file_overlap_observed"],
                    candidate["source_movement_observed"])
                   for candidate in row.get("all_existing_pr_candidates_in_sample", [])))
            for row in cards[:max_rows]
        ],
    }
    source_token = hashlib.sha256(json.dumps(
        source_identity, ensure_ascii=True, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    # Keep old issue-only private session notes importable exclusively when
    # their exact original issue/PR/gate source token still matches.
    legacy_identity = {
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
    legacy_token = hashlib.sha256(json.dumps(
        legacy_identity, ensure_ascii=True, sort_keys=True,
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
        ".ticket[hidden]{display:none}.ticket:focus{outline:3px solid #f0c976;scroll-margin:90px}"
        ".ticket header{display:flex;gap:12px;"
        "align-items:center}.priority{font-weight:bold;background:#3a4d66;padding:2px 8px}"
        ".pr-panel{border:1px solid #70879f;border-radius:10px;padding:16px;"
        "margin:18px 0;background:#172338}.pr-panel ol{padding-left:24px}"
        ".pr-work{padding:12px;border-bottom:1px solid #526a83}"
        ".pr-work[hidden],.focus-bar[hidden]{display:none}"
        ".pr-controls{display:grid;grid-template-columns:minmax(170px,260px) 1fr;"
        "gap:14px;margin:12px 0}.pr-note{width:100%;box-sizing:border-box}"
        ".controls{display:grid;grid-template-columns:minmax(170px,260px) 1fr;"
        "gap:16px}.controls textarea{width:100%;box-sizing:border-box;resize:vertical}"
        ".source,.warning{color:#ebce9b}.links a{margin-right:10px}"
        ".focus-bar{position:sticky;top:0;background:#293950;border:2px solid #ebce9b;"
        "padding:12px;border-radius:9px;z-index:2;display:flex;align-items:center;"
        "gap:12px;flex-wrap:wrap}"
        "@media(max-width:640px){.controls,.pr-controls{grid-template-columns:1fr}}"
        "</style></head><body data-source-token='" + source_token +
        "' data-legacy-source-token='" + legacy_token +
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
        "<label>PR-opvolging<select id='pr-state'>"
        "<option value=''>Alle PR-statussen</option>"
        "<option value='open'>Nog te beoordelen</option>"
        "<option value='review'>Wacht op review/CI</option>"
        "<option value='owner'>Wacht op huidige eigenaar</option>"
        "<option value='coordinate'>Afstemming tussen PRs nodig</option>"
        "<option value='followup'>Opvolging vastgelegd</option></select></label>"
        "<button id='reset' type='button'>Wis filters</button>"
        "<button id='export' type='button'>Download privéoverdracht (Markdown)</button>"
        "<button id='session-save' type='button'>Bewaar lokale sessie (JSON)</button>"
        "<label>Heropen lokale sessie<input id='session-import' type='file' "
        "accept='.json,application/json'></label></div>"
        "<p id='visible' aria-live='polite'></p>"
        "<p id='session-status' aria-live='polite'></p>"
        "<div id='focus-bar' class='focus-bar' role='status' hidden>"
        "<strong id='focus-title'></strong>"
        "<button id='focus-next' type='button'>Volgende nog te bekijken issue</button>"
        "<button id='focus-clear' type='button'>Terug naar alle bronissues</button></div>"
        "<p>Filters en PR-focus veranderen de privé-export niet: deze bevat alle bronkaarten.</p>" + hidden +
        "<main>" + groups_panel + "".join(output) + "</main>" + script + "</body></html>"
    )
