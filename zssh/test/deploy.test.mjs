import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, symlink } from "node:fs/promises";
import { spawn } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");

test("live installer keeps production raw shell fail-closed and secrets outside repo", async () => {
  const text = await readFile(path.join(ROOT, "deploy", "install-live.sh"), "utf8");
  assert.match(text, /NODE_ENV=production/);
  assert.match(text, /ZSSH_EXEC_MODE=disabled/);
  assert.match(text, /openssl rand -hex 32|randomBytes\(32\)/);
  assert.match(text, /gateway\.env/);
  assert.match(text, /chmod 600 "\$ENV_FILE"/);
  assert.match(text, /live-canary\.mjs/);
  assert.doesNotMatch(text, /ZSSH_DEV_BEARER_TOKEN=[A-Za-z0-9]{20,}/);
  assert.doesNotMatch(text, /\bsudo\b/);
});

test("user service applies restart and baseline sandbox controls", async () => {
  const text = await readFile(path.join(ROOT, "deploy", "zssh.service.in"), "utf8");
  assert.match(text, /Restart=always/);
  assert.match(text, /NoNewPrivileges=true/);
  assert.match(text, /PrivateTmp=true/);
  assert.match(text, /RestrictSUIDSGID=true/);
  assert.match(text, /EnvironmentFile=%h\/\.config\/zssh\/gateway\.env/);
});

test("live MCP canary proves fail-closed raw shell plus safe execution", async () => {
  const text = await readFile(path.join(ROOT, "live-canary.mjs"), "utf8");
  assert.match(text, /zssh_server_info/);
  assert.match(text, /zssh_run_safe/);
  assert.match(text, /exec_mode !== "disabled"/);
  assert.match(text, /program: "whoami"/);
  assert.doesNotMatch(text, /console\.log\([^\n]*token/i);
});


test("server starts when invoked through a release symlink", async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), "zssh-symlink-start-"));
  const link = path.join(tmp, "server.mjs");
  const port = 18000 + (process.pid % 1000);
  await symlink(path.join(ROOT, "server.mjs"), link);

  const child = spawn(process.execPath, [link], {
    env: {
      ...process.env,
      PORT: String(port),
      NODE_ENV: "test",
      ZSSH_EXEC_MODE: "disabled",
      ZSSH_ALLOWED_ROOTS: ROOT,
      ZSSH_AUDIT_LOG: path.join(tmp, "audit.jsonl"),
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  let stdout = "";
  let stderr = "";
  child.stdout.on("data", chunk => { stdout += chunk.toString("utf8"); });
  child.stderr.on("data", chunk => { stderr += chunk.toString("utf8"); });

  try {
    let healthy = false;
    for (let i = 0; i < 40; i += 1) {
      if (child.exitCode !== null) break;
      try {
        const response = await fetch(`http://127.0.0.1:${port}/health`);
        if (response.ok) {
          const body = await response.json();
          healthy = body?.ok === true && body?.service === "zssh";
          if (healthy) break;
        }
      } catch {
        // Server may still be starting.
      }
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.equal(
      healthy,
      true,
      `symlink-started server never became healthy; exit=${child.exitCode}; stdout=${stdout}; stderr=${stderr}`,
    );
  } finally {
    child.kill("SIGTERM");
    await new Promise(resolve => {
      if (child.exitCode !== null) return resolve();
      child.once("exit", resolve);
      setTimeout(resolve, 1000);
    });
    await rm(tmp, { recursive: true, force: true });
  }
});
