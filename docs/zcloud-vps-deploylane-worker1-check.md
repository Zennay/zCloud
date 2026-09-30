# zCloud VPS deploylane Worker 1 execution note

Queue item: `cloud-permanent-vps-first-deploylane`

This iteration verified the permanent VPS-first deployment path through GitHub:

- Repository route: `Zennay/zCloud`
- Deploy workflow: `.github/workflows/zcloud-vps-deploy.yml`
- Runner target: `self-hosted`
- Queue claim: `cloud-permanent-vps-first-deploylane`

Current state:

- Workflow structure supports guarded VPS promotion.
- Production green evidence still requires a fresh successful production deploy run.
- No completion claim is made until that evidence exists.
