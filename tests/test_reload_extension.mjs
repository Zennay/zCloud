import assert from 'node:assert/strict';
import {
  DEFAULT_ADDON_ID,
  parseDebuggerPort,
  remoteModulePath,
} from '../scripts/zcloud_reload_extension.mjs';

assert.equal(DEFAULT_ADDON_ID, 'project-runner-v07@local');
assert.equal(
  parseDebuggerPort('/snap/firefox/firefox -start-debugger-server 44157 -foreground -profile /tmp/profile'),
  44157,
);
assert.equal(
  parseDebuggerPort('noise\n/usr/bin/firefox --start-debugger-server 6000 -foreground\nmore noise'),
  6000,
);
assert.throws(
  () => parseDebuggerPort('/usr/bin/firefox -foreground'),
  /Geen actieve Firefox debugger-server/,
);
assert.equal(
  remoteModulePath('/home/test'),
  '/home/test/.local/lib/node_modules/web-ext/lib/firefox/remote.js',
);

console.log('Safe Firefox addon reload helper checks passed.');
