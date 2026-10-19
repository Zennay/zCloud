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
.stack{display:grid;grid-template-columns:minmax(210px,40%) 1fr;gap:16px}
.list{list-style:none;padding:0;margin:0;max-height:68vh;overflow:auto}
.list li{margin:0 0 8px}.list button{display:block;width:100%;text-align:left;background:#1b2b40;border:1px solid #445e7d;color:#f3f7ff}
.list button[aria-current=true]{border-color:#b8e1ff;background:#28445c}
.issue-id{display:block;font-weight:750}.meta{font-size:12px;color:#a2b7d1}
article{padding:18px;border:1px solid #465e7b;border-radius:14px;background:#1a293d;min-height:300px}
details{margin-top:14px}.code{font:12px ui-monospace,monospace;word-break:break-all}
.toolbar{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0}
small{color:#c0cede}.action{font-weight:700}
@media(max-width:690px){.stack{grid-template-columns:1fr}.list{max-height:230px}}
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
</div>
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
</div><p id="status" role="status" aria-live="polite"></p>
<footer><small>Offline. No localStorage, analytics, network requests, GitHub writes, or quiet persistence.
Downloaded notes belong to the operator and are not authorization to act on queues.</small></footer>
<script>
const board = """ + records + """;
const boardSha = """ + json.dumps(board_sha) + """;
const issueList=document.getElementById('issues'),detail=document.getElementById('detail');
const status=document.getElementById('status');
const entries=new Map(board.cards.map(c=>[c.issue,c]));
const permitted=new Set(['investigate','integrate-existing-pr','coordinate-owner','park-blocker','ready-for-new-product-work']);
let selected=null;const handoffs=new Map();
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
 return board.cards.filter(c=>(!p||p===c.priority)&&(!project||project===c.project)&&
   (!q||(c.issue+' '+c.project+' '+c.recommended_next_step).toLowerCase().includes(q)));
}
function drawList(){
 const view=filtered();issueList.replaceChildren();
 view.forEach(c=>{
   const li=elem('li'),b=elem('button');b.type='button';
   b.setAttribute('aria-current',c.issue===selected?'true':'false');
   b.append(elem('span',c.issue,'issue-id'),elem('span',c.priority+' · '+c.project,'meta'));
   b.onclick=()=>{saveSelection();selected=c.issue;drawList();drawDetail()};li.append(b);issueList.append(li);
 });
 document.getElementById('info').textContent=board.cards.length+' bounded issue cards · '+view.length+
   ' shown · observed '+board.observed_at_utc+' · no active-claim authorization';
 if(selected&&!view.some(c=>c.issue===selected)){saveSelection();selected=null;drawDetail()}
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
}
['search','priority','project'].forEach(id=>document.getElementById(id).addEventListener(
 'input',()=>{saveSelection();drawList();drawDetail()}));
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
   handoffs.clear();next.forEach((v,k)=>handoffs.set(k,v));drawDetail();
   status.textContent='Own human handoff restored from this exact original board.';
 }catch{
   status.textContent='Handoff not restored: invalid, foreign or from another board snapshot.';
 }finally{event.currentTarget.value=''}
});
document.getElementById('print').onclick=()=>window.print();
drawList();drawDetail();
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
