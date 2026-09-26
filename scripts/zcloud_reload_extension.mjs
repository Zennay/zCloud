#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

export const DEFAULT_ADDON_ID = 'project-runner-v07@local';

export function parseDebuggerPort(processList) {
  for (const line of String(processList || '').split('\n')) {
    if (!line.includes('firefox') || !line.includes('start-debugger-server')) continue;
    const match = line.match(/(?:^|\s)-{1,2}start-debugger-server\s+(\d+)(?:\s|$)/);
    if (match) return Number(match[1]);
  }
  throw new Error('Geen actieve Firefox debugger-server gevonden');
}

export function remoteModulePath(home = process.env.HOME || '/home/ubuntu') {
  return process.env.ZCLOUD_WEB_EXT_REMOTE ||
    home + '/.local/lib/node_modules/web-ext/lib/firefox/remote.js';
}

export async function reloadProjectRunner({
  addonId = process.env.ZCLOUD_ADDON_ID || DEFAULT_ADDON_ID,
  processList = null,
  home = process.env.HOME || '/home/ubuntu',
} = {}) {
  const ps = processList ?? execFileSync('ps', ['-eo', 'args='], {encoding: 'utf8'});
  const port = parseDebuggerPort(ps);
  const modulePath = remoteModulePath(home);
  const { connect } = await import(pathToFileURL(modulePath).href);
  const remote = await connect(port);
  try {
    const addon = await remote.getInstalledAddon(addonId);
    await remote.reloadAddon(addonId);
    return {ok: true, port, addon_id: addon.id};
  } finally {
    remote.disconnect();
  }
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  reloadProjectRunner()
    .then(result => console.log('\n' + JSON.stringify(result)))
    .catch(error => {
      console.error(error?.stack || String(error));
      process.exitCode = 1;
    });
}
