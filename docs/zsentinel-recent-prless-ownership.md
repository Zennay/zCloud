# zSentinel: recent PR-less branch ownership (#870)

The existing `zcloud_deploy_prless_inventory_20261013_w1.py` provides a
complete, exact-main, double-read inventory of GitHub branches and open PR
heads. The new `zcloud_recent_prless_ownership.py` **reuses** that inventory
instead of creating a second, inconsistently paginated PR ownership crawler.

## What operators get

For each bounded slice of up to 12 PR-less branch heads, the command obtains
immutable Git commit timestamp evidence. It classifies heads older than seven
days as `historical` (not active owners), and for newer heads obtains an
exact-main changed-file compare. Renames include their old and new paths.
Optional candidate paths are checked for directory/file overlap.

The result is bounded JSON with ref, SHA, source timestamp, classification,
ahead count, changed paths and conflicting paths. It never exposes commit
messages, PR bodies, prompt text or credentials. No role, lease, merge/release
authorization or active ownership is inferred from a branch reference.

All remote calls are read-only GitHub GETs. A full inventory is read and
reconciled before **and after** scanning. Duplicate/partial inventories,
malformed timestamps, future clocks, oversized compares, missing commit
provenance or provider errors yield a fail-closed nonzero result.
A `coverage_complete:false` slice cannot clear work ownership on its own.

## CLI

Pass the freshly verified exact main SHA and optional repository-relative paths:

```sh
python3 scripts/zcloud_recent_prless_ownership.py <CURRENT_MAIN_SHA> \
  --offset 0 --limit 12 \
  --owned-path server.py \
  --owned-path scripts/
```

Subsequent slices use `--offset 12`, `--offset 24`, etc. A full decision
requires review of every slice against one unchanged source head; multiple
separate calls do **not** jointly create an automatic lease or proof of stable
global coverage. If the inventory grows too large, the GitHub budget is
exhausted or any source fails, stop and require owner coordination.

This is the branch-only complement to [#866](https://github.com/Zennay/zCloud/issues/866)
(open PR overlap) and [#870](https://github.com/Zennay/zCloud/issues/870)
(recent PR-less ownership). The existing exact-head owner PR gate remains
separate. Nothing here writes to queues, claims, Git refs, PRs, VPS runtime,
databases or services.

## Release and execution gates

Source is not running in production. It was not executed on the VPS. A later
permanent-runner integration must require exact-head trusted owner and
same-repository identity before checking out or executing repository code,
with canonical `[self-hosted,zcloud,vps]` runner verification and existing
mandatory CI/review. No standalone QA, regression or test suite was added
or run in this product-source change.
