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

## Extended admission coverage (15 October)

Source guard now inspects immutable Git objects for both `.github/workflows/*.yml` and repository-local `action.yml` / `action.yaml` composite entrypoints (including nested directories), so remote-shell pipelines introduced via `uses: ./local/action` are not silently omitted. Git symlinks under `.github/actions` and symlinked Action files are rejected. Source guard detects quoted `run` mapping keys, compact flow-map steps, folded YAML `run: >` blocks, direct remote scripts executed through absolute-path shell binaries, shell `-c "$(curl ...)"` and `sh -c '$(wget ...)'` forms. An opaque `run: *alias` or `run: !tag` is **not assumed safe**, even when no literal remote command appears at the use site. This is intentional conservative rejection of newly edited workflows, not proof that every alias is malicious.

GitHub-hosted admission additionally verifies the remote PR branch ref before **and after** the exact-Git-source comparison; a moved PR head invalidates the source result. The original SHA and immutable main are still checked. Those checks do not grant merge or deploy authority.

**Known limits:** full YAML key decoding (including escaped quoted keys and explicit `?` mappings), multiline flow-map parsing, templated scripts, downloaded intermediate files, third-party non-local actions, alternate download commands, unusual shell encodings and dynamically assembled code are not yet semantically modeled. Focused detection can produce false positives, and green scans must be reviewed alongside independent privileged-workflow checks. Separate #1464/#1465 owners handle interpolation and container-source trust; do not edit their files.

## Multiline quoted YAML admission

GitHub Actions `run: "..."` and `run: '...'` YAML scalars may continue on a later indented line; the command is folded with spaces. The scanner joins those quoted continuations before examining shell execution patterns. Unterminated or indentation-broken quoted `run` values cause fail-closed source-evidence rejection, not a clean result. Inline hashes inside quoted strings remain part of the command rather than being stripped as comments. This remains a **syntactic source check**, not a complete YAML parser or runtime verification.
