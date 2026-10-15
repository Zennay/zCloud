# Remote shell ingestion: source-only no-new-debt gate (15 Oct 2026)

Scope: zCloud deploy-ops #633; proposed in [PR #1466](https://github.com/Zennay/zCloud/pull/1466). This is a **non-authorizing source tripwire**, not an enforcement mechanism for runtime or deployment.

## Protected pattern

A newly added or modified GitHub Actions workflow containing a literal `run:` command with `curl` or `wget` piped into `sh`, `bash`, `zsh`, `/bin/bash`, `/usr/bin/bash`, `sudo -E /usr/bin/bash` or `/usr/bin/env bash` is rejected. The same applies to `bash <(curl ...)`. The scanner reads committed Git objects, not a mutable working-tree file list. It requires exact immutable base/head commit SHA, valid ancestry, bounded blob sizes and no symlink/gitlink workflow entries. Changed previously risky workflows cannot reuse the historical exception merely by being relocated into a different job or privilege context.

An **unchanged** historical workflow with a matched risk remains counted but is not considered new debt. That exception is purely for gradual remediation; it does not establish safety.

## Run offline

```bash
python3 -m unittest discover -s tests -p 'test_zcloud_remote_shell_ratchet_20261015_w1.py' -v
python3 scripts/zcloud_remote_shell_ratchet_20261015_w1.py --repo . --base <exact-40-hex-main> --head <exact-40-hex-head>
```

Exit: **0** = complete source proof and no *new matched* risks; **1** = matched new risk; **2** = unknown or incomplete source proof. Output includes only numerical counts and all-false mutation/release/deployment flags. It deliberately never prints URLs, script bodies, environment variables or arbitrary Git errors.

## Limitations and gate

- **Not a full YAML parser or shell execution analyzer.** Escaped/aliased mapping keys, complex flow YAML, indirect `uses` actions, heredocs, variables, base64 payloads, script downloads to files, shell substitutions beyond the named pattern and alternate fetch tools may bypass this detector.
- A PR can change its own scanner or workflow. Running on a same-repository owner PR provides a test signal, **not tamperproof branch protection**. Independent diff review and protected merge conditions are required before reliance.
- GitHub-hosted checkout uses immutable PR head and does not persist credentials; the job has read-only repository permission. It neither contacts the VPS nor invokes any deploy/recovery/manual writer.
- A green source job is **not** proof that the VPS listener, worker generation, SQLite, Firefox, deploy or protected-main release gate works. Self-hosted runner #1326 and production writer locks #1447 have separate owners. Release decisions remain with #580 / PWQ-41 / #1089.
- No automatic merge or direct-main mutation. Keep draft pending independent review and exact-head full hosted regression.

Owners should review changed PR file inventory and current main before integration, and never treat these counters as permission to retry production.
