import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm, symlink } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { classifyCommand, redactSecrets, resolveAllowedPath, runSafeProgram, isMainEntry } from "../server.mjs";

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


test("safe runner blocks programs outside the hard allowlist", async () => {
  const result = await runSafeProgram("sh", ["-c", "echo nope"], process.cwd(), 1);
  assert.equal(result.ok, false);
  assert.equal(result.blocked, true);
  assert.match(result.error, /allowlist/);
});

test("safe runner executes an allowlisted program without a shell", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "zssh-run-test-"));
  const previous = process.env.ZSSH_ALLOWED_ROOTS;
  process.env.ZSSH_ALLOWED_ROOTS = root;
  try {
    const result = await runSafeProgram("whoami", [], root, 5);
    assert.equal(result.ok, true);
    assert.equal(result.program, "whoami");
    assert.ok(result.stdout.trim().length > 0);
    assert.equal(result.timed_out, false);
    assert.equal(result.output_limited, false);
  } finally {
    if (previous === undefined) delete process.env.ZSSH_ALLOWED_ROOTS;
    else process.env.ZSSH_ALLOWED_ROOTS = previous;
    await rm(root, { recursive: true, force: true });
  }
});


test("main-entry detection follows symlinks used by atomic live releases", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "zssh-entry-test-"));
  const link = path.join(root, "server-current.mjs");
  const realServer = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..", "server.mjs");
  try {
    await symlink(realServer, link);
    assert.equal(isMainEntry(link), true);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});


test("safe runner defaults cwd to the first configured allowed root", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "zssh-default-cwd-"));
  const previousRoots = process.env.ZSSH_ALLOWED_ROOTS;
  process.env.ZSSH_ALLOWED_ROOTS = root;
  try {
    const result = await runSafeProgram("pwd", [], undefined, 5);
    assert.equal(result.ok, true);
    assert.equal(result.stdout.trim(), root);
  } finally {
    if (previousRoots === undefined) delete process.env.ZSSH_ALLOWED_ROOTS;
    else process.env.ZSSH_ALLOWED_ROOTS = previousRoots;
    await rm(root, { recursive: true, force: true });
  }
});
