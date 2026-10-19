#!/usr/bin/env python3
"""Bounded GitHub CI discovery for zSentinel's on-demand cockpit.

Uses read-only canonical GitHub GETs, never reruns jobs. Returns at most one
relevant run/job receipt per project, with explicit coverage limitations.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

from zsentinel_product_issue_inbox import bounded_get
from zsentinel_recovery_router import utc_string

UTC=timezone.utc
SHA=re.compile(r"^[a-f0-9]{40}$")
MAX_PROJECTS=3
MAX_RUNS=5
MAX_JOBS=10
MAX_GETS_PER_PROJECT=2
RELEVANT_STATES={"queued","completed","in_progress"}
FAILURES={"failure","timed_out","action_required"}


def parse_time(value):
    if not isinstance(value,str) or len(value)>50:
        raise ValueError("invalid github job clock")
    try:
        dt=datetime.fromisoformat(value.replace("Z","+00:00"))
    except ValueError as exc:
        raise ValueError("invalid github job clock") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("github job clock without timezone")
    return dt.astimezone(UTC)


def discover(registry, selected, token=None):
    """One selected bounded recent workflow run and at most ten jobs / repo."""
    if (not isinstance(selected,list) or not 1<=len(selected)<=MAX_PROJECTS
            or len(set(selected))!=len(selected)
            or any(name not in registry for name in selected)):
        raise ValueError("invalid canonical CI-discovery project selection")
    receipts={}
    coverage=[]
    request_count=0
    for pid in selected:
        repo=registry[pid]
        root=f"https://api.github.com/repos/{repo}"
        try:
            query=urlencode({"event":"pull_request","per_page":MAX_RUNS,"page":1})
            request_count+=1
            result=bounded_get(f"{root}/actions/runs?{query}",token)
            if not isinstance(result,dict) or not isinstance(result.get("workflow_runs"),list):
                raise ValueError("github workflow run shape invalid")
            runs=result["workflow_runs"]
            if len(runs)>MAX_RUNS:
                raise ValueError("github workflow run response exceeds bounds")
            shortlisted=[]
            for run in runs:
                if not isinstance(run,dict):
                    continue
                rid=run.get("id")
                sha=run.get("head_sha")
                status=run.get("status")
                if (type(rid) is not int or not 1<=rid<=2**53-1
                        or not isinstance(sha,str) or not SHA.fullmatch(sha)
                        or status not in RELEVANT_STATES):
                    continue
                linked=run.get("repository")
                if not isinstance(linked,dict) or linked.get("full_name")!=repo:
                    continue
                shortlisted.append((rid,sha,status))
            # Scan one newest relevant run only: partial coverage is explicit.
            if not shortlisted:
                coverage.append({"project_id":pid,"status":"no_relevant_run_in_recent_window",
                                 "authoritative_all_clear":False})
                continue
            run_id,sha,_=shortlisted[0]
            request_count+=1
            query=urlencode({"per_page":MAX_JOBS,"page":1})
            jobs_result=bounded_get(f"{root}/actions/runs/{run_id}/jobs?{query}",token)
            if not isinstance(jobs_result,dict) or not isinstance(jobs_result.get("jobs"),list):
                raise ValueError("github workflow jobs shape invalid")
            jobs=jobs_result["jobs"]
            if len(jobs)>MAX_JOBS:
                raise ValueError("github workflow jobs exceed bounds")
            candidates=[]
            for job in jobs:
                if not isinstance(job,dict):
                    continue
                job_id,actual_run=job.get("id"),job.get("run_id")
                status=job.get("status")
                result_state=job.get("conclusion")
                assigned=job.get("runner_id")
                if (type(job_id) is not int or not 1<=job_id<=2**53-1
                        or actual_run!=run_id or status not in RELEVANT_STATES
                        or (assigned is not None and
                            (type(assigned) is not int or assigned<0))):
                    continue
                created=parse_time(job.get("created_at"))
                observed=datetime.now(UTC)
                if created>observed+timedelta(minutes=2):
                    continue
                if status=="queued" and not assigned and observed-created>=timedelta(minutes=30):
                    importance=0
                elif status=="completed" and result_state in FAILURES:
                    importance=1
                else:
                    continue
                candidates.append((importance,created,job_id,status,result_state,assigned))
            if not candidates:
                coverage.append({"project_id":pid,"status":"no_selected_incident_in_bounded_jobs",
                                 "authoritative_all_clear":False})
                continue
            candidates.sort(key=lambda x:(x[0],x[1],x[2]))
            _,created,job_id,status,result_state,assigned=candidates[0]
            observed=datetime.now(UTC)
            receipt={
                "run_id":run_id,"job_id":job_id,"head_sha":sha,
                "job_status":status,"queued_at":utc_string(created),
                "observed_at":utc_string(observed),
                "runner_assigned":bool(assigned),
                "verified_receipt":"github_bounded_run_jobs_discovery",
            }
            if status=="completed":
                receipt["conclusion"]=result_state
            receipts[pid]=receipt
            coverage.append({"project_id":pid,"status":"selected_one_source_bound_job",
                             "run_id":run_id,"job_id":job_id,
                             "authoritative_all_clear":False})
        except (ValueError,OSError):
            coverage.append({"project_id":pid,"status":"degraded_unavailable",
                             "authoritative_all_clear":False})
    return receipts,{
        "source":"github_canonical_readonly_bounded_discovery",
        "request_count":request_count,
        "maximum_requests":len(selected)*MAX_GETS_PER_PROJECT,
        "max_runs_per_project":MAX_RUNS,
        "max_jobs_per_project":MAX_JOBS,
        "coverage":coverage,
        "full_repo_ci_inventory":False,
        "jobs_restarted":False,
        "runner_mutations":False,
    }
