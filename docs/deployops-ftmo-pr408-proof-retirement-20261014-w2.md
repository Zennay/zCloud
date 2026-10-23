# Retirement: FTMO PR408 provider-boundary proof (14 October 2026)

## Exact source and lifecycle evidence

- Removed **only** `.github/workflows/ftmo-pr408-provider-boundary-proof.yml`
  from zCloud's registered Actions workflow inventory.
- The old one-shot workflow was bound to the immutable historical FTMO
  candidate `98c033b2b571cea2313f20bee31820286132226f`. It still exposed
  `workflow_dispatch` and a self-hosted `[self-hosted, zcloud, vps]` proof job.
- [Zennay/Ftmo PR #408](https://github.com/Zennay/Ftmo/pull/408) is
  **closed and merged**, with final head
  `eac3d115741a60c9bb9004636d69b34a3ca81ed2` and merge commit
  `2b8c7c457ba62f724dae89c0765a72b6ce9b90f1`. The old proof target
  is **not** the final merged PR head.
- Latest observed FTMO `main` at selection:
  `b84e9904df806ac41a35dacda7c38af7721d9e84`.
- At branch creation zCloud `main` was
  `8b0f91478b9d6024dcf35f207ad2a1c784d3170c`.
- Open PR title/body searches for the exact workflow file returned no
  claimed owner; an exact `ftmo-pr408` branch search returned none.
  Source-file lookup for the historical candidate SHA found only the
  retired workflow. This is a **bounded ownership survey**, not an
  assertion that every historical repository reference is known.

## Why remove instead of re-dispatch

The old workflow would clone an obsolete FTMO checkout, install Python
dependencies and execute a long (15-minute) targeted proof using the shared
zCloud VPS runner. It could not certify today's FTMO `main`, zCloud's
live production deployment or the stalled P0 Actions listener. Keeping its
manual trigger would preserve an unnecessary route for historical work to
occupy scarce VPS capacity.

Deletion is scoped to this one retired workflow. There is **no attempt to
cancel already queued or running jobs**; deletion does not stop historical
runs. No FTMO repository, provider, market data, trading or production
runtime files are modified.

## Gates and boundaries

- New acceptance proof is **GitHub-hosted only**. It confirms the historical
  trigger is gone and runs regression fixtures; it never checks out source
  on the VPS.
- zCloud production owner #580/PWQ-41/#1089, P0 runner owner
  [#1326](https://github.com/Zennay/zCloud/issues/1326)/#1317,
  and pending FTMO research runner activity are untouched.
- Never interpret green hosted CI as live runner restoration or deployment.
- Should PR408 historical evidence ever be needed, consult its immutable
  source commit and original Actions evidence under existing access; do
  **not** re-enable an on-demand VPS workflow to reconstruct old history.
