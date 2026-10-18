# v2 composer reliability — safe idle repair for actual browser worker

## Delivered source behaviour

Current `main` worker is the **single-tab v2** browser engine, not the old 1.3.x userscript path. The old [PR #948](https://github.com/Zennay/zCloud/pull/948) edits the now-skipped legacy path. This work instead updates **both** `public/zcloud-worker-v2-engine.js` and the byte-identical embedded engine in `public/zcloud-worker.user.js`.

When the browser's assigned ChatGPT tab has an **active, queue-bound, assignment-ready worker** but its composer DOM is missing, the actual v2 poll loop:

1. Holds local sending without attempting repeated `fill()` or posting a fresh prompt.
2. Gives the page a 45-second observed grace period, checked by the existing 30-second polling heartbeat (no extra timer or background job).
3. Preserves any sending, pending, generating, existing chat route, previously sent prompt, or in-flight SPA transition.
4. Only **before the first prompt was sent**, on a blank `/` new-chat route with the existing worker ownership intact, requests **one** existing same-tab New chat UI action, never a reload, `location.assign`, another browser tab, or an unbounded retry.
5. If the route is an existing conversation or recovery cannot run, sends a bounded `composer-missing-manual-recovery-required` or `composer-missing-operator-action-required` event for the operator instead of discarding the conversation.
6. Resets the missing episode when the composer returns or the queue assignment changes, reporting `composer-restored` on real recovery. No claim epoch, lease or priority is minted by browser observations.

This is *fail-closed and minimally invasive*. A missing composer inside an already active conversation needs human/operator action; it is not automatically restored. The browser never treats an event as permission to take over an active Notion/GitHub claim.

## Integration boundaries

This version keeps the existing `2.0.3` userscript/Firefox-extension version contract unchanged. Updating the code in a GitHub PR **does not** prove it was installed in the browser, and old installed clients may require an authorized version-coordinated refresh once the exact-head CI/review/release gates allow it. Do not silently change `@version` without coordinating the extension's `VIOLENTMONKEY_REQUIRED_VERSION`, distribution and existing acceptance contracts.

The PR does **not** include test files, trigger manual QA, touch production SQLite, run browser tabs, dispatch GitHub jobs, restart services or alter the VPS. Existing mandatory exact-head CI and secure serialized deployment requirements remain unchanged.

Original source owner [PR #948](https://github.com/Zennay/zCloud/pull/948) is left intact and must be reconciled as superseded **only after** this v2-based implementation has been accepted by the maintainer.
