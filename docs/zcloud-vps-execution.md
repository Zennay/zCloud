# zCloud shared VPS execution lane

All zCloud projects use the same VPS-first execution route for operations that require a shell, SSH, package installation, service control, builds, tests, deployment or filesystem changes.

The transport is the GitHub Actions self-hosted runner on vps-bb300bba (runner user ubuntu). A project prompt must dispatch or resume work through this runner/queue and must not treat a missing direct SSH/MCP connection as a blocker before the route has been attempted.

The runner can execute commands available to its Linux account and can use its configured SSH keys for other targets. It is not a permanent interactive SSH terminal: long-lived work belongs in a bounded job, systemd service or timer. Every operation returns a workflow/run identifier, exit code and sanitized evidence.

Destructive or broad changes require explicit confirmation. Secrets stay in runner/GitHub secret storage and never enter prompts, command arguments or logs.
