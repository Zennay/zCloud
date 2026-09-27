# zSSH

zSSH is the security-first remote operations plugin being built as a zCloud subproject.

## Current milestone

**M1 — Safe local execution proof.**

This branch proves the smallest safe foundation:

- remote MCP endpoint at `/mcp`;
- development bearer authentication;
- `zssh_server_info`, `zssh_exec_readonly`, `zssh_exec`, `zssh_read_file`, and `zssh_write_file`;
- non-root startup guard;
- command timeout and output limits;
- configured filesystem roots;
- secret redaction in tool output and audit data;
- JSONL audit trail;
- raw shell disabled by default; command classification is audit/UX metadata, not the security boundary;
- Node unit tests for classification, redaction, and path boundaries.

## Architecture direction

```
ChatGPT plugin
    |
    v
zSSH remote MCP gateway
    |
    v
pairing / auth / policy
    |
    v
paired target agent
    |
    +-- Linux user
    +-- scoped sudo
    +-- files / systemd / git
```

M0 runs the gateway and execution adapter together to keep the proof small. M2 splits the target agent boundary and adds pairing/revocation. Production must not depend on storing users' SSH private keys in the gateway.

## Local development

Requires Node 20+.

```bash
cd zssh
npm install
cp .env.example .env
# export values from .env in your preferred way
npm test
npm start
```

The server binds to `127.0.0.1` by default. Put TLS/reverse proxy or a development tunnel in front of it rather than binding the M0 process directly to the public internet.

## Policy

`zssh_exec_readonly` is the M1 safe execution path. It never invokes a shell: the server accepts only a fixed executable allowlist and validates every argument separately. Raw `zssh_exec` remains disabled by default and only activates with `ZSSH_EXEC_MODE=full` on an explicitly trusted/disposable target.

`classifyCommand()` remains audit/UX metadata only and is not an authorization boundary. Production hardening still requires a dedicated service account, scoped sudo/capabilities, stronger approval semantics, rate limiting, OAuth-compatible user auth, agent pairing, and review against current ChatGPT plugin requirements.

## ChatGPT integration status

OpenAI's current plugin documentation uses remote MCP over streamable HTTP. Public submission requires a stable public HTTPS endpoint. Development bearer auth in M0 is temporary; production authentication and public submission are later milestones.

## Canonical project docs

- Project HQ: https://app.notion.com/p/3e89e19ac955811a9008d420e3e2a634
- Handoff: https://app.notion.com/p/3e89e19ac95581639bdcdc9daeb37ae8
