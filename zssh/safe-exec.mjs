import { spawn } from "node:child_process";

const ALLOWLIST = new Map([
  ["pwd", new Set()],
  ["whoami", new Set()],
  ["id", new Set(["-u", "-g", "-un", "-gn"])],
  ["uname", new Set(["-a", "-s", "-r", "-m", "-n"])],
  ["uptime", new Set()],
  ["hostname", new Set()],
  ["date", new Set()],
  ["df", new Set(["-h", "-P"])],
  ["free", new Set(["-h", "-m"])]
]);

export function validateReadonlyCommand(program, args = []) {
  if (typeof program !== "string" || !ALLOWLIST.has(program)) {
    throw new Error("program is not in the zSSH read-only allowlist");
  }
  if (!Array.isArray(args)) throw new Error("args must be an array");
  const allowed = ALLOWLIST.get(program);
  for (const arg of args) {
    if (typeof arg !== "string") throw new Error("all args must be strings");
    if (!allowed.has(arg)) throw new Error("argument is not allowed for " + program);
  }
  return { program, args: [...args] };
}

export function spawnReadonlyCommand(program, args, options = {}) {
  const validated = validateReadonlyCommand(program, args);
  return spawn(validated.program, validated.args, {
    ...options,
    shell: false,
    stdio: ["ignore", "pipe", "pipe"]
  });
}
