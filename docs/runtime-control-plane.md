# zCloud runtime control plane

zCloud separates project product truth from runtime truth.

## Sources of truth

- `projects.json`: presentation/product registry (name, goal, milestones, repo and Notion links).
- `project-contracts.json`: canonical runtime contract (autonomy, queue/lane mode, AI cap, compute class/pool, soft CPU/memory budget).
- SQLite `portfolio_queue` + `task_claims`: executable work and collision ownership.
- SQLite `project_state_receipts`: latest evidence-backed execution state.
- SQLite `resource_leases`: portfolio compute admission.
- GitHub: executable source + CI/workflow evidence.
- Notion: mission, decisions, roadmap and handoff.

## Project State Receipts

A receipt records the smallest useful state handoff after material work:

- project id;
- phase;
- last material action;
- canonical commit;
- CI/runtime status;
- blocker;
- next executable gate;
- source + observed timestamp;
- machine-readable evidence.

When a receipt exists, `/api/status` prefers its phase and next gate over stale registry text. Registry values remain the fallback before a project has a valid receipt.

CLI example:

```bash
python3 scripts/zcloud_runtime.py receipt \
  --project cloud \
  --phase "Runtime control-plane rollout" \
  --action "Merged receipt storage" \
  --commit "$GITHUB_SHA" \
  --ci-status success \
  --next-gate "Deploy exact main" \
  --source github-actions \
  --evidence-json '{"run_id":"123"}'
```

## Resource Governor

The governor is admission control, not a dashboard hint.

Current pools:

- `protected`: zCloud/zSSH control-plane capacity;
- `build`: bounded app build/test work;
- `heavy`: one shared slot across FTMO, HaxLab and LightUp heavy work;
- `disabled`: no autonomous compute admission.

Acquire before a governed workload and release in an unconditional cleanup/finally step:

```bash
OWNER="${GITHUB_RUN_ID:-manual}-${GITHUB_JOB:-job}"
python3 scripts/zcloud_runtime.py acquire --project ftmo --owner "$OWNER"
trap 'python3 scripts/zcloud_runtime.py release --project ftmo --owner "$OWNER" || true' EXIT
```

Exit code 75 means the pool is currently occupied; callers must queue/retry rather than bypass admission.

## Migration rule

New projects are fail-closed. An active project is incomplete until it has an explicit runtime contract. Runtime code must not silently invent autonomy or compute defaults for an unregistered project.

The compatibility `autonomy-policy.json` is now an empty metadata-only placeholder. Production runtime reads use `project-contracts.json`; only explicitly redirected test/recovery paths may still provide an isolated legacy policy. A regression test prevents runtime truth from being reintroduced into the placeholder.

## Worker Completion Controller

`portfolio_queue_audit`'s refill step prefers resuming a project's own
unfinished GitHub PR work over handing out a brand-new roadmap package. See
[`worker-completion-controller.md`](worker-completion-controller.md) for the
classification rules, the `/api/completion` API, and the auto-merge
guardrails.
