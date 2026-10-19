#!/usr/bin/env python3
"""Offline, source-bound zSentinel operator workbench for real product issue delivery.

Only displays previously saved, validated board evidence and human notes.
Cannot grant ownership, trigger CI, merge, deploy or modify worker/queue state.
"""
from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
import os
import stat
from pathlib import Path
import re
import sys

from zsentinel_product_delivery_board import (
    ISSUE, PROJECT, URL, SCHEMAS, MAX_INPUT, unique, write_create_only, when,
)

MAX_CARDS = 60
MAX_REFERENCES = 3
MAX_GAPS = 15
MAX_NOTE = 1000
PRIORITIES = {"P0", "P1", "P2", "Unspecified"}
DECISIONS = ("investigate", "integrate-existing-pr", "coordinate-owner",
             "park-blocker", "ready-for-new-product-work")
HEX = re.compile(r"^[a-f0-9]{64}$")


def read_source_and_sha(path: Path):
    """Validate one stable, bounded regular snapshot and hash the SAME bytes."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("No-follow file opening is unavailable")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= MAX_INPUT:
        raise ValueError("Expected a bounded regular source snapshot")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    try:
        first = os.fstat(fd)
        if not stat.S_ISREG(first.st_mode) or (
                first.st_dev, first.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("Board source identity drift")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(MAX_INPUT + 1)
        last = os.fstat(fd)
        if (len(raw) != first.st_size or len(raw) > MAX_INPUT or
                (first.st_dev, first.st_ino, first.st_size, first.st_mtime_ns) !=
                (last.st_dev, last.st_ino, last.st_size, last.st_mtime_ns)):
            raise ValueError("Board source changed during read")
    finally:
        os.close(fd)
    parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")))
    if type(parsed) is not dict or parsed.get("schema") != SCHEMAS["board"]:
        raise ValueError("Unsupported source board schema")
    return parsed, hashlib.sha256(raw).hexdigest()


def validated_view(source: dict) -> dict:
    if source.get("authorizes_claim") is not False or (
        source.get("authorizes_merge") is not False or
        source.get("authorizes_deploy") is not False or
        source.get("automatic_dispatch_enabled") is not False
    ):
        raise ValueError("Source board unexpectedly authorizes privileged work")
    when(source.get("observed_at_utc"))
    cards = source.get("cards")
    if type(cards) is not list or len(cards) > MAX_CARDS:
        raise ValueError("Source board has unbounded cards")
    chosen = []
    seen = set()
    for card in cards:
        if type(card) is not dict or any(
            card.get(field) is not False for field in
            ("claim_authorized", "merge_authorized", "deploy_authorized")
        ):
            raise ValueError("Board card contains unauthorized action")
        issue, project, priority = (card.get("issue"), card.get("project"),
                                    card.get("priority"))
        if (type(issue) is not str or not ISSUE.fullmatch(issue) or issue in seen
                or type(project) is not str or not PROJECT.fullmatch(project)
                or priority not in PRIORITIES):
            raise ValueError("Noncanonical or duplicate product issue")
        seen.add(issue)
        refs = card.get("open_pr_candidates")
        gaps = card.get("evidence_gaps")
        if (type(refs) is not list or len(refs) > MAX_REFERENCES or
            any(type(ref) is not str or not URL.fullmatch(ref) or
                "/pull/" not in ref for ref in refs)):
            raise ValueError("Untrusted or unbounded candidate PR references")
        if (type(gaps) is not list or len(gaps) > MAX_GAPS or
            any(type(g) is not str or len(g) > 300 for g in gaps)):
            raise ValueError("Untrusted or unbounded evidence gaps")
        next_step = card.get("recommended_next_step")
        if type(next_step) is not str or len(next_step) > 650:
            raise ValueError("Noncanonical recommended next step")
        # Issue identities are constrained to owner/repository#number and
        # rebuilt into GitHub links rather than trusting a stored URL.
        repo, number = issue.rsplit("#", 1)
        chosen.append({
            "issue": issue, "project": project, "priority": priority,
            "url": "https://github.com/" + repo + "/issues/" + number,
            "open_pr_candidates": refs, "evidence_gaps": gaps,
            "recommended_next_step": next_step,
            "issue_coverage": card.get("project_issue_coverage", "unknown"),
            "claim_context": ("reported_observation_only" if
                              card.get("observed_claim_ids") else "not_verified"),
        })
    rows = source.get("project_operations")
    if type(rows) is not list or len(rows) > 60:
        raise ValueError("Source board contains invalid project operations")
    projects = []
    for row in rows:
        if type(row) is not dict or type(row.get("project")) is not str or (
            not PROJECT.fullmatch(row["project"])) or row.get("action_authorized") is not False:
            raise ValueError("Source project operation is not read-only")
        projects.append(row["project"])
    return {"observed_at_utc": source["observed_at_utc"],
            "cards": chosen,
            "projects": sorted(set(projects) | {r["project"] for r in chosen}),
            "global_gate": "live claims, exact-head CI, review and release authority unverified"}


def html_document(view: dict, board_sha: str) -> str:
    records = json.dumps(view, ensure_ascii=True, separators=(",", ":"))
    # The JSON is embedded into an inline script. Escape HTML syntax even if
    # the JSON was formed from malicious public issue titles or operator notes.
    records = records.replace("<", "\\u003c").replace(">", "\\u003e")
    choices = "".join(
        '<option value="' + escape(p, quote=True) + '">' + escape(p) + '</option>'
        for p in view["projects"]
    )
    return """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'; img-src 'none'">
<title>zSentinel · Product Delivery Workbench</title>
<style>
:root{color-scheme:dark}body{font:15px/1.55 system-ui,sans-serif;background:#0e1725;color:#ecf3ff;margin:auto;max-width:1160px;padding:23px 18px}
h1{font-size:clamp(28px,5vw,46px);letter-spacing:-.03em;line-height:1.15}
h2{font-size:20px}p{color:#c4d0e1}a{color:#9fe6ff;text-underline-offset:3px}
label{display:block;margin:12px 0 6px}input,select,textarea,button{font:inherit}
input[type=search],select,textarea{background:#1d2c41;border:1px solid #6a7e9d;border-radius:9px;padding:10px;color:white}
input[type=search]{width:100%;max-width:420px}textarea{width:100%;box-sizing:border-box;resize:vertical}
button{background:#b8e1ff;color:#082036;padding:10px 16px;border:0;border-radius:8px;cursor:pointer;font-weight:650}
button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,a:focus-visible{outline:3px solid #f9db80}
.notice{border-left:4px solid #f4d68c;background:#292d34;padding:12px 18px;border-radius:8px;margin:19px 0}
.filters{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;padding:14px 0}
.filters label{margin:0}.filters select{display:block;min-width:170px}
.portfolio{border:1px solid #445e7d;background:#142238;border-radius:12px;padding:12px 18px;margin:14px 0 20px}
.portfolio dl{display:flex;flex-wrap:wrap;gap:15px;margin:6px 0}.portfolio dt{font-size:12px;color:#b9cce6}.portfolio dd{font-size:19px;font-weight:750;margin:0}
.portfolio ul{columns:2;list-style:none;padding:0;margin:10px 0}.portfolio li{break-inside:avoid;margin:5px 0}
.portfolio small{color:#d5dfed}.brief{border:1px solid #53738e;border-radius:12px;padding:15px;margin:12px 0}
.triage{border:1px solid #5c718c;background:#17283a;border-radius:12px;padding:15px;margin:14px 0}
.triage ol{padding-left:23px;margin:8px 0;max-height:360px;overflow:auto}.triage li{padding:5px 0;overflow-wrap:anywhere}
.triage button{background:#243c56;color:#ecf5ff;border:1px solid #7897b6;padding:6px 9px;margin-right:8px}
.triage small{display:inline-block;margin:2px}
.comparison{border:1px solid #486f87;border-radius:12px;padding:15px;margin:16px 0;background:#132237}
.comparison ol{padding-left:25px;max-height:320px;overflow:auto}.comparison li{margin:7px 0;overflow-wrap:anywhere}
.comparison strong{color:#d7f0ff}
.brief pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:330px;overflow:auto;background:#091627;padding:12px}
.stack{display:grid;grid-template-columns:minmax(210px,40%) 1fr;gap:16px}
.list{list-style:none;padding:0;margin:0;max-height:68vh;overflow:auto}
.list li{margin:0 0 8px}.list button{display:block;width:100%;text-align:left;background:#1b2b40;border:1px solid #445e7d;color:#f3f7ff}
.list button[aria-current=true]{border-color:#b8e1ff;background:#28445c}
.issue-id{display:block;font-weight:750}.meta{font-size:12px;color:#a2b7d1}
article{padding:18px;border:1px solid #465e7b;border-radius:14px;background:#1a293d;min-height:300px}
details{margin-top:14px}.code{font:12px ui-monospace,monospace;word-break:break-all}
.toolbar{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0}
small{color:#c0cede}.action{font-weight:700}
@media(max-width:690px){.stack{grid-template-columns:1fr}.list{max-height:230px}.portfolio ul{columns:1}}
@media print{body{background:white;color:black}.filters,.toolbar,.list{display:none}article{color:black;background:white}.stack{display:block}.notice{color:black;background:white}}
</style>
<header><small>ZCLOUD / zSENTINEL / HUMAN-OPERATED</small>
<h1>Product Delivery Workbench</h1>
<p>Navigate actual P0/P1 product issues, inspect existing PR links and record a human handoff.
Source is a bounded saved zSentinel board, not a live ownership or production feed.</p>
<p class="notice"><strong>No automatic claim, merge, retry, deploy or privileged runner action.</strong>
An apparently free issue is never proof that its ownership is clear. Check live
Notion/queue claims and the exact-head GitHub gates separately before acting.</p>
<p id="info" aria-live="polite"></p></header>
<div class="filters">
<label>Search product issues<input id="search" type="search" placeholder="Issue, project or next step"></label>
<label>Priority<select id="priority"><option value="">All priorities</option>
<option>P0</option><option>P1</option><option>P2</option><option>Unspecified</option></select></label>
<label>Project<select id="project"><option value="">All projects</option>""" + choices + """</select></label>
<label>Human disposition<select id="disposition">
<option value="">All decisions</option>
<option value="investigate">Investigate / not yet reviewed</option>
<option value="integrate-existing-pr">Inspect existing PR</option>
<option value="coordinate-owner">Coordinate owner</option>
<option value="park-blocker">Parked blocker</option>
<option value="ready-for-new-product-work">Candidate only · verify live claims</option>
</select></label>
</div>
<section class="portfolio" aria-label="Snapshot portfolio triage">
<h2>Observed product workload</h2>
<p id="portfolioFreshness">Source freshness is not live-worker availability.</p>
<dl id="portfolioStats"></dl>
<ul id="projectCounts"></ul>
<small>Counts describe only this saved, bounded source snapshot. No checked claim, available worker or release-ready status is inferred.</small>
</section>
<section class="comparison" aria-label="Compare with a previous offline board">
<h2>Compare two saved observations</h2>
<p>Load an earlier private JSON delivery-board snapshot to see new, still-observed
and no-longer-visible issues. A disappearance is <strong>not evidence of a fix</strong>:
source coverage, permissions or filters may have changed.</p>
<label>Earlier source snapshot <input id="baseline" type="file" accept=".json,application/json"></label>
<button id="clearBaseline" type="button" disabled>Clear comparison</button>
<p id="comparisonMeta" aria-live="polite">Only this captured board is loaded; no baseline or live comparison.</p>
<dl id="comparisonStats" class="portfolio"></dl><ol id="comparisonRows"></ol>
</section>
<section class="triage" aria-label="Human-only priority and blocker queue">
<h2>Next-action queue · observed evidence only</h2>
<p>Prioritizes the currently filtered P0/P1 issues and the work you explicitly parked.
No live owner/claim status is fetched or inferred. Recheck GitHub, Notion and release gates before touching code.</p>
<p id="triageCount" aria-live="polite"></p><ol id="triageQueue"></ol>
</section>
<div class="stack">
<nav aria-label="Observed product issues"><ol class="list" id="issues"></ol></nav>
<article id="detail" aria-label="Selected product issue">
<h2>Choose a product issue</h2><p>No issue has been assigned to a worker by this page.</p>
</article>
</div>
<div class="toolbar">
<button id="export" type="button">Download private human handoff</button>
<label>Reopen own handoff<input id="restore" type="file" accept=".json,application/json"></label>
<button id="print" type="button">Print selected issue</button>
</div>
<section class="brief" aria-label="Private project handoff brief">
<h2>Source-bound, human-only project briefing</h2>
<p>Review all visible cards, owner gaps and your human dispositions before exporting
a private Markdown briefing. It includes any private notes you entered: check the
recipient and download destination. It cannot claim, merge, release or contact a project.</p>
<button type="button" id="previewBrief">Preview current filtered briefing</button>
<div id="briefPanel" hidden>
<pre id="briefContent"></pre>
<button type="button" id="downloadBrief" disabled>Download exactly this private briefing (.md)</button>
</div>
</section>
<p id="status" role="status" aria-live="polite"></p>
<footer><small>Offline. No localStorage, analytics, network requests, GitHub writes, or quiet persistence.
Downloaded notes belong to the operator and are not authorization to act on queues.</small></footer>
<script>
const board = """ + records + """;
const boardSha = """ + json.dumps(board_sha) + """;
const baselineSchema = 'zsentinel-product-delivery-triage-v1';
const issuePattern = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+#[1-9][0-9]{0,8}$/;
let baseline=null;
const issueList=document.getElementById('issues'),detail=document.getElementById('detail');
const status=document.getElementById('status');
const entries=new Map(board.cards.map(c=>[c.issue,c]));
const permitted=new Set(['investigate','integrate-existing-pr','coordinate-owner','park-blocker','ready-for-new-product-work']);
let selected=null;const handoffs=new Map();
let viewedBrief=null;
function invalidateBrief(){
 viewedBrief=null;
 document.getElementById('briefPanel').hidden=true;
 document.getElementById('briefContent').textContent='';
 document.getElementById('downloadBrief').disabled=true;
}
function elem(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e}
function link(text,url){const a=elem('a',text);a.href=url;a.rel='noopener noreferrer';a.target='_blank';return a}
function saveSelection(){
 if(!selected)return;
 const decision=document.getElementById('decision'),note=document.getElementById('note');
 if(decision&&note)handoffs.set(selected,{decision:decision.value,note:note.value.slice(0,1000)});
}
function filtered(){
 const q=document.getElementById('search').value.trim().toLowerCase();
 const p=document.getElementById('priority').value;
 const project=document.getElementById('project').value;
 const decision=document.getElementById('disposition').value;
 return board.cards.filter(c=>(!p||p===c.priority)&&(!project||project===c.project)&&
   (!decision||decision===(handoffs.get(c.issue)?.decision||'investigate'))&&
   (!q||(c.issue+' '+c.project+' '+c.recommended_next_step).toLowerCase().includes(q)));
}
function refreshPortfolio(){
 const cards=board.cards;
 const stats=document.getElementById('portfolioStats');
 const projects=document.getElementById('projectCounts');
 stats.replaceChildren();projects.replaceChildren();
 const counts=[
  ['P0 observed',cards.filter(c=>c.priority==='P0').length],
  ['P1 observed',cards.filter(c=>c.priority==='P1').length],
  ['Existing PR references',cards.filter(c=>c.open_pr_candidates.length>0).length],
  ['Evidence gaps',cards.filter(c=>c.evidence_gaps.length>0).length]
 ];
 counts.forEach(([label,count])=>{
  stats.append(elem('dt',label),elem('dd',String(count)));
 });
 board.projects.forEach(project=>{
  const rows=cards.filter(c=>c.project===project);
  if(!rows.length)return;
  const item=elem('li');
  const p0=rows.filter(c=>c.priority==='P0').length;
  const p1=rows.filter(c=>c.priority==='P1').length;
  item.append(elem('strong',project+': '),elem('span',
   rows.length+' observed · '+p0+' P0 · '+p1+' P1'));
  projects.append(item);
 });
 const age=Date.now()-Date.parse(board.observed_at_utc);
 const stale=!Number.isFinite(age)||age<0||age>6*3600*1000;
 document.getElementById('portfolioFreshness').textContent=
  (stale?'Stale or unverifiable saved observation; refresh source externally.':
   'Saved observation within six hours (not a live status).')+
   ' Recorded '+board.observed_at_utc+'.';
}
function refreshTriage(){
 const ranks={P0:0,P1:1,P2:2,Unspecified:3};
 const observations=baseline?new Map(changesSinceBaseline().map(c=>[c.issue,c.kind])):null;
 const weight=kind=>({'new-observation':0,'changed-evidence':1,'still-observed':2})[kind]??3;
 const cards=filtered().slice().sort((a,b)=>
  ranks[a.priority]-ranks[b.priority] ||
  Number(handoffs.get(a.issue)?.decision==='park-blocker')-Number(handoffs.get(b.issue)?.decision==='park-blocker') ||
  (observations?weight(observations.get(a.issue))-weight(observations.get(b.issue)):0) ||
  a.project.localeCompare(b.project) || a.issue.localeCompare(b.issue));
 const urgent=cards.filter(c=>c.priority==='P0'||c.priority==='P1'||handoffs.get(c.issue)?.decision==='park-blocker');
 const list=document.getElementById('triageQueue');list.replaceChildren();
 const parked=urgent.filter(c=>handoffs.get(c.issue)?.decision==='park-blocker').length;
 document.getElementById('triageCount').textContent=urgent.length+
  ' observed priority/parked entries · '+parked+' human-parked · 0 independently authorized actions';
 if(!urgent.length){list.append(elem('li','No P0/P1 or parked entry in this saved filtered snapshot; live backlog unknown.'));return;}
 urgent.forEach(c=>{
  const li=elem('li'),b=elem('button',c.priority+' '+c.issue);b.type='button';
  b.setAttribute('aria-label','Inspect observed issue '+c.issue);
  b.onclick=()=>{saveSelection();invalidateBrief();selected=c.issue;drawList();drawDetail();detail.scrollIntoView({block:'nearest'});};
  const human=handoffs.get(c.issue)?.decision||'investigate';
  const gap=c.evidence_gaps.length?c.evidence_gaps[0]:
   'live owner, exact-head CI and release admission unverified';
  const changed=observations?.get(c.issue);
  li.append(b,elem('small',c.project+' · '+(changed?changed+' · ':'')+human+' · '+line(gap,160)));list.append(li);
 });
}
function sourceInstant(value){
 if(typeof value!=='string'||value.length>45||!/(Z|[+-][0-9]{2}:[0-9]{2})$/.test(value))return null;
 const t=Date.parse(value);
 return Number.isFinite(t)?t:null;
}
function acceptBaseline(data){
 if(!data||Array.isArray(data)||data.schema!==baselineSchema||
    data.authorizes_claim!==false||data.authorizes_merge!==false||
    data.authorizes_deploy!==false||data.automatic_dispatch_enabled!==false||
    !Array.isArray(data.cards)||data.cards.length>60)throw Error('unsafe baseline');
 const earlier=sourceInstant(data.observed_at_utc);
 const current=sourceInstant(board.observed_at_utc);
 if(earlier===null||current===null||earlier>=current||earlier>Date.now()+300000)
   throw Error('baseline must predate current observation');
 const items=new Map();
 for(const c of data.cards){
  if(!c||Array.isArray(c)||typeof c.issue!=='string'||!issuePattern.test(c.issue)||
     items.has(c.issue)||typeof c.project!=='string'||
     !/^[a-z][a-z0-9_-]{0,63}$/.test(c.project)||
     !['P0','P1','P2','Unspecified'].includes(c.priority)||
     c.claim_authorized!==false||c.merge_authorized!==false||c.deploy_authorized!==false||
     !Array.isArray(c.open_pr_candidates)||c.open_pr_candidates.length>3||
     !Array.isArray(c.evidence_gaps)||c.evidence_gaps.length>15)
       throw Error('invalid earlier issue card');
  if(c.open_pr_candidates.some(p=>typeof p!=='string'||!/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/[1-9][0-9]{0,8}$/.test(p))||
     c.evidence_gaps.some(g=>typeof g!=='string'||g.length>300))
       throw Error('invalid earlier evidence');
  items.set(c.issue,{issue:c.issue,project:c.project,priority:c.priority,
   prs:new Set(c.open_pr_candidates),gaps:c.evidence_gaps.length});
 }
 return {observed_at_utc:data.observed_at_utc,items};
}
function changesSinceBaseline(){
 if(!baseline)return [];
 const now=new Map(board.cards.map(c=>[c.issue,c]));
 const result=[];
 for(const c of board.cards){
  const old=baseline.items.get(c.issue);
  if(!old){result.push({kind:'new-observation',issue:c.issue,priority:c.priority});continue;}
  const changed=old.priority!==c.priority||
    old.prs.size!==new Set(c.open_pr_candidates).size||
    c.open_pr_candidates.some(p=>!old.prs.has(p))||
    old.gaps!==c.evidence_gaps.length;
  result.push({kind:changed?'changed-evidence':'still-observed',issue:c.issue,priority:c.priority});
 }
 for(const old of baseline.items.values())if(!now.has(old.issue))
   result.push({kind:'not-observed',issue:old.issue,priority:old.priority});
 return result;
}
function refreshComparison(){
 const meta=document.getElementById('comparisonMeta');
 const stats=document.getElementById('comparisonStats'),list=document.getElementById('comparisonRows');
 stats.replaceChildren();list.replaceChildren();
 document.getElementById('clearBaseline').disabled=!baseline;
 if(!baseline){
  meta.textContent='Only this captured board is loaded; no baseline or live comparison.';
  return;
 }
 const age=Date.parse(board.observed_at_utc)-Date.parse(baseline.observed_at_utc);
 meta.textContent='Earlier '+baseline.observed_at_utc+' → current '+board.observed_at_utc+
  ' · '+(age/3600000).toFixed(1)+' hours between captured observations.'+
  ' Original earlier source SHA256: '+baseline.sha+
  '. No issue resolution or deploy inferred.';
 const events=changesSinceBaseline();
 const names=[['new-observation','New in current snapshot'],
  ['changed-evidence','Changed priority/PR/gap observation'],
  ['still-observed','Still observed'],['not-observed','No longer in current snapshot (NOT resolved)']];
 for(const [kind,title] of names){
  const subset=events.filter(e=>e.kind===kind);
  const dt=elem('dt',title),dd=elem('dd',String(subset.length));stats.append(dt,dd);
  subset.forEach(e=>{
   const li=elem('li');
   li.append(elem('strong',title+' · '),elem('span',e.priority+' '+e.issue));
   if(e.kind!=='not-observed'){
    const b=elem('button','Inspect current issue');b.type='button';
    b.onclick=()=>{saveSelection();invalidateBrief();selected=e.issue;drawList();drawDetail();detail.scrollIntoView({block:'nearest'});};
    li.append(' ',b);
   }
   list.append(li);
  });
 }
}
function line(value,limit=500){
 return String(value??'').replace(/[\u0000-\u001f\u007f]/g,' ').replace(/[<>\x60]/g,'').slice(0,limit).trim();
}
function currentBrief(){
 const rows=filtered().slice().sort((a,b)=>{
  const ranks={'P0':0,'P1':1,'P2':2,'Unspecified':3};
  return ranks[a.priority]-ranks[b.priority] ||
    a.project.localeCompare(b.project) || a.issue.localeCompare(b.issue);
 });
 if(!rows.length)return null;
 const lines=[
  '# zSentinel · PRIVATE human product handoff',
  '',
  'Source SHA256: '+boardSha,
  'Observed at UTC: '+board.observed_at_utc,
  'Issues selected: '+rows.length+' of '+board.cards.length,
  'Comparison baseline: '+(baseline?baseline.sha+' observed '+baseline.observed_at_utc:
   'not loaded; no historical comparison'),
  'Status: observational, claims and releases NOT AUTHORIZED.',
  'MANDATORY: verify active owner/claim, exact-head required CI, security, review and release consent before action.',
  ''
 ];
 let group=null;
 rows.forEach(card=>{
  if(card.project!==group){group=card.project;lines.push('## Project '+line(group,80),'');}
  const human=handoffs.get(card.issue);
  lines.push('### '+line(card.issue,110)+' · '+line(card.priority,20),
    'Original: '+card.url,
    'Human disposition: '+line(human?.decision||'investigate',40)+' (not an owner claim)',
    'Claim context: '+line(card.claim_context,100),
    'Observed PRs: '+(card.open_pr_candidates.length?
      card.open_pr_candidates.join(' | '):'none in bounded snapshot'),
    'Missing evidence: '+(card.evidence_gaps.length?
      card.evidence_gaps.map(g=>line(g,300)).join('; '):'live owner, exact CI and release gates remain unverified'),
    'Recommended investigation: '+line(card.recommended_next_step,650));
  if(human?.note)lines.push('Private human note: '+line(human.note,1000));
  lines.push('');
 });
 if(baseline){
  const diff=changesSinceBaseline();
  lines.push('## Historical source comparison (not resolution evidence)','',
    'Current/previous issue visibility may differ due to coverage, permissions or bounded collectors.',
    ...['new-observation','changed-evidence','still-observed','not-observed'].map(kind=>
     kind+': '+diff.filter(item=>item.kind===kind).map(item=>line(item.issue,110)).join(', ')),
    '');
 }
 lines.push('No live reassignment, dispatch, queue mutation or production readiness is asserted.','');
 return lines.join('\n');
}
function drawList(){
 const view=filtered();issueList.replaceChildren();
 view.forEach(c=>{
   const li=elem('li'),b=elem('button');b.type='button';
   b.setAttribute('aria-current',c.issue===selected?'true':'false');
   b.append(elem('span',c.issue,'issue-id'),elem('span',c.priority+' · '+c.project,'meta'));
   b.onclick=()=>{saveSelection();invalidateBrief();selected=c.issue;drawList();drawDetail()};li.append(b);issueList.append(li);
 });
 document.getElementById('info').textContent=board.cards.length+' bounded issue cards · '+view.length+
   ' shown · observed '+board.observed_at_utc+' · no active-claim authorization';
 if(selected&&!view.some(c=>c.issue===selected)){saveSelection();selected=null;drawDetail()}
 refreshTriage()
}
function drawDetail(){
 detail.replaceChildren();
 if(!selected||!entries.has(selected)){
   detail.append(elem('h2','Select an observed product issue'),elem('p','No action has been authorized.'));
   return;
 }
 const card=entries.get(selected);
 detail.append(elem('small',card.priority+' · '+card.project),
   elem('h2',card.issue),link('Open original GitHub issue ↗',card.url),
   elem('p','Owner status: '+card.claim_context+' · source coverage: '+card.issue_coverage,'meta'),
   elem('h3','Proposed next investigation'),elem('p',card.recommended_next_step));
 const prs=elem('div');prs.append(elem('h3','Existing PR references (not ownership proof)'));
 if(!card.open_pr_candidates.length)prs.append(elem('p','No candidate PR in this bounded snapshot.'));
 card.open_pr_candidates.forEach(u=>prs.append(elem('p')).lastChild.append(link(u,u)));
 detail.append(prs);
 const gaps=elem('div');gaps.append(elem('h3','Missing evidence and gates'));
 const ul=elem('ul');
 card.evidence_gaps.forEach(x=>ul.append(elem('li',x)));
 if(!card.evidence_gaps.length)ul.append(elem('li','Live claim, required review and release rights still need independent verification.'));
 gaps.append(ul);detail.append(gaps);
 const label=elem('label','Operator disposition (non-authorizing)');label.htmlFor='decision';
 const sel=elem('select');sel.id='decision';
 [['investigate','Investigate product issue'],['integrate-existing-pr','Inspect/integrate existing PR'],
  ['coordinate-owner','Coordinate current owner'],['park-blocker','Park external blocker'],
  ['ready-for-new-product-work','Candidate for new product implementation (verify live claims)']]
 .forEach(([v,t])=>{const o=elem('option',t);o.value=v;sel.append(o)});
 const nlabel=elem('label','Private operator note (max 1000 characters)');nlabel.htmlFor='note';
 const note=elem('textarea');note.id='note';note.maxLength=1000;note.rows=5;
 const saved=handoffs.get(selected);sel.value=saved?.decision||'investigate';note.value=saved?.note||'';
 detail.append(label,sel,nlabel,note);
 sel.addEventListener('change',()=>{saveSelection();invalidateBrief();drawList()});
 note.addEventListener('input',()=>{saveSelection();invalidateBrief();refreshTriage()});
}
['search','priority','project','disposition'].forEach(id=>document.getElementById(id).addEventListener(
 'input',()=>{saveSelection();invalidateBrief();drawList();drawDetail()}));
document.getElementById('export').onclick=()=>{
 saveSelection();
 const rows=[...handoffs].filter(([id,v])=>entries.has(id)&&permitted.has(v.decision))
 .map(([issue,value])=>({issue,decision:value.decision,note:value.note.slice(0,1000)}));
 const payload={schema_version:1,kind:'zsentinel-private-human-delivery-handoff',
   source_board_sha256:boardSha,source_observed_at_utc:board.observed_at_utc,
   authorizes_claim:false,authorizes_merge:false,authorizes_deploy:false,entries:rows};
 const uri=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)+'\\n'],{type:'application/json'}));
 const a=elem('a');a.href=uri;a.download='zsentinel-human-handoff.json';a.click();
 setTimeout(()=>URL.revokeObjectURL(uri),1000);
 status.textContent='Private source-bound handoff downloaded. Nothing was submitted.';
};
document.getElementById('baseline').addEventListener('change',async event=>{
 const file=event.currentTarget.files?.[0];if(!file)return;
 try{
  if(file.size<4||file.size>2097152)throw Error('file bounds');
  if(!globalThis.crypto?.subtle)throw Error('SHA-256 unavailable');
  const buf=await file.arrayBuffer();
  const txt=new TextDecoder('utf-8',{fatal:true}).decode(buf);
  const value=JSON.parse(txt);
  const checked=acceptBaseline(value);
  checked.sha=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buf)),
    byte=>byte.toString(16).padStart(2,'0')).join('');
  if(checked.sha===boardSha)throw Error('baseline and current board are identical');
  baseline=checked;invalidateBrief();refreshComparison();refreshTriage();
  status.textContent='Earlier bounded source loaded locally. Missing issues are not proven resolved.';
 }catch{
  status.textContent='Previous source rejected: invalid, unsafe, future, or not earlier than this board.';
 }finally{event.currentTarget.value='';}
});
document.getElementById('clearBaseline').onclick=()=>{
 baseline=null;invalidateBrief();refreshComparison();refreshTriage();
 status.textContent='Earlier source comparison cleared.';
};
document.getElementById('restore').addEventListener('change',async event=>{
 const file=event.currentTarget.files?.[0];if(!file)return;
 try{
   if(file.size<4||file.size>65536)throw Error('size');
   const data=JSON.parse(await file.text());
   if(!data||Array.isArray(data)||data.schema_version!==1||
      data.kind!=='zsentinel-private-human-delivery-handoff'||
      data.source_board_sha256!==boardSha||
      data.source_observed_at_utc!==board.observed_at_utc||
      data.authorizes_claim!==false||data.authorizes_merge!==false||
      data.authorizes_deploy!==false||!Array.isArray(data.entries)||
      data.entries.length>60)throw Error('invalid identity');
   const next=new Map();
   for(const v of data.entries){
     if(!v||Array.isArray(v)||typeof v.issue!=='string'||!entries.has(v.issue)||
         next.has(v.issue)||!permitted.has(v.decision)||
         typeof v.note!=='string'||v.note.length>1000||
         new TextEncoder().encode(v.note).length>4000||
         [...v.note].some(ch=>ch.codePointAt(0)<32&&ch!=='\\n'&&ch!=='\\t'))throw Error('malformed note');
     next.set(v.issue,{decision:v.decision,note:v.note});
   }
   handoffs.clear();next.forEach((v,k)=>handoffs.set(k,v));
   invalidateBrief();drawList();drawDetail();
   status.textContent='Own human handoff restored from this exact original board.';
 }catch{
   status.textContent='Handoff not restored: invalid, foreign or from another board snapshot.';
 }finally{event.currentTarget.value=''}
});
document.getElementById('previewBrief').onclick=()=>{
 saveSelection();
 viewedBrief=currentBrief();
 const panel=document.getElementById('briefPanel');
 panel.hidden=!viewedBrief;
 document.getElementById('briefContent').textContent=viewedBrief||'';
 document.getElementById('downloadBrief').disabled=!viewedBrief;
 status.textContent=viewedBrief?
  'Read the complete private briefing and choose your own destination. No action was sent.':
  'No currently visible issue cards; no briefing produced.';
};
document.getElementById('downloadBrief').onclick=()=>{
 if(!viewedBrief)return;
 saveSelection();
 const refreshed=currentBrief();
 if(refreshed!==viewedBrief){
  invalidateBrief();
  status.textContent='Briefing changed. Preview again before downloading.';
  return;
 }
 const project=document.getElementById('project').value.replace(/[^a-z0-9-]/gi,'-').slice(0,50);
 const uri=URL.createObjectURL(new Blob([viewedBrief],{type:'text/markdown;charset=utf-8'}));
 const a=elem('a');a.href=uri;
 a.download='zsentinel-private-'+(project||'portfolio')+'-handoff.md';a.click();
 setTimeout(()=>URL.revokeObjectURL(uri),1000);
 status.textContent='Private Markdown saved locally. It does not authorize any worker action.';
 invalidateBrief();
};
document.getElementById('print').onclick=()=>window.print();
refreshPortfolio();refreshComparison();drawList();drawDetail();
</script></html>
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True,
                        help="Existing private zSentinel JSON delivery-board snapshot")
    parser.add_argument("--output", type=Path, required=True,
                        help="New private HTML workspace file; never overwrites")
    args = parser.parse_args(argv)
    try:
        if args.board.resolve() == args.output.resolve():
            raise ValueError("Output must not overwrite board evidence")
        source, board_sha = read_source_and_sha(args.board)
        view = validated_view(source)
        write_create_only(args.output, html_document(view, board_sha))
        print(args.output)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("zSentinel workbench refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
