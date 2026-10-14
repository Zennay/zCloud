#!/usr/bin/env python3
"""Non-authorizing exact-source admission for independent hosted main regression."""
import argparse
import json
import re

REPO = "Zennay/zCloud"
SHA = re.compile(r"\A[0-9a-f]{40}\Z", re.ASCII)


def admission(*, event, repository, ref, actor, triggering_actor, attempt,
              expected_sha, checkout_sha, main_sha, head_repo, stage, upstream_sha=None):
    report = {
        "kind": "zcloud-source-integrity-v1",
        "stage": stage if stage in ("before", "after") else "invalid",
        "source_verified": False,
        "production_verified": False,
        "runner_health_verified": False,
        "authorizes_mutation": False,
        "reason": "DENY",
    }
    if not (
        type(event) is str and event in ("pull_request", "schedule", "workflow_dispatch", "workflow_run")
        and type(repository) is str and repository == REPO
        and type(ref) is str and type(actor) is str
        and type(triggering_actor) is str and type(attempt) is str and attempt == "1"
        and stage in ("before", "after")
        and all(type(s) is str and SHA.fullmatch(s)
                for s in (expected_sha, checkout_sha, main_sha))
        and expected_sha == checkout_sha
    ):
        return report
    if event == "pull_request":
        if head_repo != REPO or ref == "refs/heads/main":
            return report
    elif event == "workflow_run":
        if (ref != "refs/heads/main" or main_sha != expected_sha
                or type(upstream_sha) is not str or not SHA.fullmatch(upstream_sha)
                or upstream_sha == expected_sha):
            return report
    elif ref != "refs/heads/main" or main_sha != expected_sha:
        return report
    elif event == "workflow_dispatch" and (actor != "Zennay" or triggering_actor != "Zennay"):
        return report
    report["source_verified"] = True
    report["reason"] = "EXACT_SOURCE_ONLY"
    return report


def main():
    parser = argparse.ArgumentParser()
    for field in ("event", "repository", "ref", "actor", "triggering-actor",
                  "attempt", "expected-sha", "checkout-sha", "main-sha",
                  "head-repo", "stage"):
        parser.add_argument("--" + field, required=True)
    parser.add_argument("--upstream-sha", default="")
    kwargs = vars(parser.parse_args())
    result = admission(**kwargs)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["source_verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
