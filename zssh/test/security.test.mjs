import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { classifyCommand, redactSecrets, resolveAllowedPath } from "../server.mjs";
import { validateReadonlyCommand } from "../safe-exec.mjs";

test("classifies read-only commands", () => {
  assert.equal(classifyCommand("systemctl status nginx"), "read_only");
  assert.equal(classifyCommand("git status"), "read_only");
  assert.equal(classifyCommand("uptime"), "read_only");
});

test("classifies mutation and destructive commands", () => {
  assert.equal(classifyCommand("touch hello.txt"), "mutation");
  assert.equal(classifyCommand("rm -rf /tmp/demo"), "destructive");
  assert.equal(classifyCommand("systemctl stop nginx"), "destructive");
  assert.equal(classifyCommand("git reset --hard HEAD~1"), "destructive");
});

test("redacts common secrets", () => {
  const value = redactSecrets("token=abc123 Authorization: Bearer eyJ.secret password=hunter2");
  assert.match(value, /token=\[REDACTED\]/);
  assert.match(value, /Bearer \[REDACTED\]/);
  assert.match(value, /password=\[REDACTED\]/);
  assert.doesNotMatch(value, /abc123|hunter2|eyJ\.secret/);
});

test("allowed path gate accepts inside root and rejects outside root", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "zssh-test-"));
  const file = path.join(root, "ok.txt");
  await writeFile(file, "ok");
  const previous = process.env.ZSSH_ALLOWED_ROOTS;
  process.env.ZSSH_ALLOWED_ROOTS = root;
  try {
    assert.equal(await resolveAllowedPath(file), file);
    await assert.rejects(() => resolveAllowedPath("/etc/hosts"), /outside allowed roots/);
  } finally {
    if (previous === undefined) delete process.env.ZSSH_ALLOWED_ROOTS;
    else process.env.ZSSH_ALLOWED_ROOTS = previous;
    await rm(root, { recursive: true, force: true });
  }
});

test("read-only execution uses an exact program and argument allowlist", () => {
  assert.deepEqual(validateReadonlyCommand("uname", ["-a"]), { program: "uname", args: ["-a"] });
  assert.deepEqual(validateReadonlyCommand("df", ["-h"]), { program: "df", args: ["-h"] });
  assert.throws(() => validateReadonlyCommand("rm", ["-rf", "/"]), /not in the zSSH read-only allowlist/);
  assert.throws(() => validateReadonlyCommand("uname", ["; touch /tmp/pwned"]), /argument is not allowed/);
  assert.throws(() => validateReadonlyCommand("df", ["/etc"]), /argument is not allowed/);
});
