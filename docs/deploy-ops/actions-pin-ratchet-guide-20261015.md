# GitHub Actions pin-drift gate — 15 October 2026

Scope: #633, source-only independent deploy-ops slice. The new policy only compares immutable Git trees of GitHub Actions workflows. It does not edit existing workflows, invoke VPS jobs, cancel other runs, merge PRs or authorize a release.

## Problem and behavior

A workflow may use a mutable reference like actions/checkout@v4. An upstream tag can be changed later. This ratchet allows existing debt in the immutable base revision but refuses newly introduced references whose exact token is not pinned to a 40-character Git SHA. A floating reference changed to another floating tag counts as new debt. Local repository actions (./...) and Docker SHA-256 digests are exempt; Docker tags and unknown/dynamic expressions remain debt.

* Baseline: github.event.pull_request.base.sha. Candidate: github.event.pull_request.head.sha. Both are exact commits, not moving branches.
* Sources are read from Git objects, not a mutable worktree. Symlink/nonregular workflows, invalid bytes, excessive files and ambiguous evidence are refused.
* Debt identity is a multiset of workflow path, classification and SHA-256 of the raw reference. Duplicate actions are counted, so an additional unpinned invocation is not concealed by a matching old one.
* JSON output has only bounded path, line and hash prefix. It never includes raw action references. Exit 0: no new debt; 1: new debt; 2: incomplete/unsafe evidence. All outcomes permanently deny deployment and release authority.
* Hosted-only PR workflow, contents:read, pinned credential-free checkout, no privileged dispatch, SSH or production write.
* Initial PR bootstrap uses the candidate script because the trusted base does not contain this script yet. This is advisory bootstrap evidence, NOT independent security review or approval. After integration, subsequent PRs read the scanner from the exact base commit.
* Ambiguous YAML aliases, step reuse, inline flow-map merges, quoted uses-keys and non-regular workflow directory ancestry are now rejected rather than silently accepted. Inline shell literals and comments remain ignored where confidently distinguished. The source scanner is still not a complete YAML parser or publisher provenance validator; independent review remains required. A 40-character commit SHA is not by itself publisher authorization.
* After the full scanner finishes, a separate GitHub-hosted shell gate rechecks the current canonical main SHA via a read-only remote Git query. If main advanced, disappeared or cannot be fetched, proof fails, rather than treating a stale green check as current acceptance. Three real temporary-Git integration cases cover unchanged, moved and missing main. This is still not a production authorization check.
* This does not replace runner recovery #1326, three writer locks #1447, existing source owners, or serialized production gate #580/PWQ-41 and #1089.

## Operator usage

From a local Git repository with the base and candidate commits already fetched:

    python3 scripts/zcloud_actions_pin_ratchet_20261015.py --base BASE_40_HEX_SHA --head HEAD_40_HEX_SHA

Keep the PR DRAFT and unmerged until exact-head full regression, owner/branch collision proof and independent policy review. A green hosted source result cannot authorize production promotion.
