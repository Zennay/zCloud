import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
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
