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

## 20 October · Terminal conversation-load failure before composer recovery

On the live v2 route an explicit terminal **conversation could not be loaded**
message can render while the composer is absent. The earlier missing-composer
preflight returned early on this condition and could prevent the existing,
separately guarded `failedConversation()` branch in `poll()` from running.
This stranded a known broken conversation even though its recovery rule had
already been implemented.

The embedded engine and its standalone v2 source now **yield to the existing
terminal-error handler** when the explicit load-failure message is present.
The normal safety constraints remain: current queue assignment and worker
identity must be ready, no active generating/sending session is interrupted,
and `newChat()` performs only the existing in-tab UI click, never a reload
or a forced second tab. A missing composer without that explicit error still
uses the normal 45-second bounded grace and one-shot/manual escalation flow.
No new permission, claim, queue retry or server-initiated browser command is
created.

Source commits [01fe678](https://github.com/Zennay/zCloud/commit/01fe6783d0b018e3c4ba37988bb4ca39c8835c3e)
and [a92e887](https://github.com/Zennay/zCloud/commit/a92e88716c5ccf5d38c95f6a4be1a87a73f42352)
update both copies of the v2 engine. This is source-only on existing
[PR #1552](https://github.com/Zennay/zCloud/pull/1552), **not** evidence
that a real browser was restarted, signed, installed or recovered. Required
exact-head CI, independent reviewer, script-versioned distribution and
authorized browser rollout remain mandatory. No QA suites were added or run.


## 20 October · Quote-safe terminal failure and bounded SPA recovery

The original terminal-error detector scanned all visible `main.innerText`, including ordinary chat messages. A user/assistant discussing the words “conversation not found” could therefore cause an **unrequested New chat action** on a still-healthy conversation. Both identical v2 engine copies now treat the message as a terminal page error **only when** there is no visible composer, no visible conversation turn (`data-message-author-role` / `data-testid=conversation-turn`), and at most 2,000 characters of actual error-screen text. Ambiguous pages fail closed without resetting an active chat. This is a conservative DOM heuristic, not authoritative proof that a failed conversation has been recovered.

When a single same-tab New chat click has already been requested, the existing `resetRoute` barrier previously could remain in `new-chat-transition-pending` forever on a failed SPA transition. It now escalates after the existing 45-second composer grace to `new-chat-transition-operator-action-required` while keeping the same block on prompt dispatch. It **does not** click again, reload, navigate, steal another tab, clear queue leases or send a duplicate prompt. The operator must inspect the original tab and current assignment.

The standalone `public/zcloud-worker-v2-engine.js` and its embedded userscript copy are updated together on this existing PR. This remains **unmerged source**, not a browser rollout or live recovery receipt. Existing exact-head CI, independent review, extension distribution/version contract and serialized release authorization still apply; no QA/regression suites, workflow dispatches or worker interruptions were performed.


## 20 October · Visible input takes precedence over hidden responsive duplicates

Some ChatGPT layouts retain a hidden old composer before the currently usable composer in the document. The previous first-match `querySelector` treated the hidden element as authoritative, making a healthy tab appear input-less and potentially entering the missing-composer recovery path. The existing selector priorities now search for a **visible** canonical composer first; only when none are visible does the engine preserve the prior first-match fallback for draft detection. No new DOM action, timer, network permission, worker claim, tab navigation, send or reset is introduced.

Both deployed-source locations (`public/zcloud-worker-v2-engine.js` and the embedded copy inside `public/zcloud-worker.user.js`) were updated together. The source is not deployed merely because this PR exists: version-coordinated authorized distribution, independent review, exact-head CI and protected-main remain required.


### Per-assignment blocker visibility without notification churn

The local `lastBlock` duplicate-event suppression previously persisted across separate verified queue assignments. If a new assignment met the same missing-composer condition as the previous one, its operator could receive **no new** blocker event. `resetComposerRepair(queueId)` now resets the deduplication key **only when** the observed queue ID changes, including a transition to no verified assignment. Repeated polls in the same unchanged assignment still emit at most the original event. This does not create, claim, renew, abandon or mutate any lease, and the repair remains one-shot per verified queue assignment.


## 21 October · exact-prompt send admission and draft preservation

The actual v2 send path previously treated **any nonempty composer content**
as a successful fill. A responsive input could retain stale text, a user's
unfinished draft or only part of a worker prompt; after high-effort and
queue checks the browser would still click Send. This was a product
misdelivery risk in [issue #698](https://github.com/Zennay/zCloud/issues/698).

The v2 engine and embedded userscript now:

- Refuse to overwrite a nonempty *different* composer draft, including a
  second refusal after ensureHigh has interacted with the browser.
- Verify the complete normalized prompt after DOM insertion rather than only
  checking for nonempty text. Failure emits a bounded operator blocker and
  never advances promptAt or awaiting.
- Reconfirm the visible composer still contains the intended exact prompt
  immediately before the send click, after the fresh queue lease read.
- Choose a **visible** send control among responsive duplicates; ignore hidden
  stop controls when determining whether generation is active.

These changes do not increase polling, authorize edits to an operator's
draft, create a conversation, take a new claim, retry an unconfirmed send,
expand an API permission or bypass Thinking High, fresh queue identity,
protected GitHub main, approvals or release gates. Mismatched input is
preserved for human inspection rather than silently overwritten.

Both standalone v2 engine and its embedded, byte-identical userscript copy
were updated in source, without changing the userscript version/distribution
contract. No browser worker was restarted or QA/regression suite created or
run; deployment and real-device recovery remain **NOT VERIFIED**.


## 21 October · Activity operator handoff

The blocked reasons `composer-draft-preserved`,
`composer-text-not-confirmed`, and `composer-changed-before-send`
are now recognized by the fixed-safe-hint renderer in existing zCloud Activity
[PR #1550](https://github.com/Zennay/zCloud/pull/1550). The browser emits
these codes only on blocked sends and never sends or replaces unknown drafts.
This is source integration in **two separately open PRs**, not a claim that
either source has reached main or an operator's installed browser.


## 21 October · Assignment-identity fence, reload continuity and no duplicate terminal recovery

The actual single-tab v2 engine now closes three independent duplicate-dispatch boundaries. The identical product source is maintained in both `public/zcloud-worker-v2-engine.js` and the shipped `public/zcloud-worker.user.js`, without changing their existing `2.0.3` installation/version contract.

1. **Exactly current authorization before each click.** A pending send remembers the original queue ID, slot and prompt; after the asynchronous Thinking and input UI steps, it reads the current `/runner-targets` response and requires the current project, queue ID, normalized prompt and slot to match **both** its original snapshot and the still-selected browser target. An old prompt is never authorized just because a recycled or edited assignment happens to have a superficially matching queue ID. The existing `assignment-changed-before-send` status is the safe stop.
2. **Fail-closed on an ambiguous old conversation error.** If a `/c/...` page failed to load, that page may represent a prompt the browser already sent, including before this script loaded. The engine now reports `conversation-load-failed-manual-review` rather than automatically pressing New chat and risking a second execution of the same claimed queue task. Only a known unsent empty `/` route may use the existing one-click New chat recovery. The worker preserves its lease/assignment and makes no queue/server mutations.
3. **Same-tab send continuity after a full browser reload.** Before pressing Send, the engine stores only the worker ID and queue ID as a *may-have-dispatched* marker in per-tab `sessionStorage` (no prompt text, answer or credentials). After an actual document reload it stops a new initial send for that identical queue with `previous-queue-send-after-reload-manual-review`, because the previous response/acknowledgement is no longer proven. Normal follow-up sends inside the **same still-running document** remain possible. A genuinely different queue ID can proceed under fresh normal admission. Malformed saved evidence or unavailable session storage cannot authorize a click; `dispatch-ledger-unavailable` makes the failure visible to the owner.

**Five-minute unknown execution:** The previous v2 `generation-never-started-five-minutes` branch automatically clicked New chat even if the original Send might have been accepted remotely. The worker now keeps the same in-memory awaiting state, emits `generation-unconfirmed-manual-review-no-retry`, and permits a late observed reply to resolve normally; elapsed time alone can never authorize a second queue prompt. The same persisted queue marker also prevents the earlier missing-composer repair from clicking New chat immediately after a full reload of a potentially dispatched queue.

**Operator action:** Read the original GitHub/Notion/SQLite queue claim and browser conversation evidence with existing permissions. Reconcile the original task outcome or issue an explicitly authorized new queue assignment; do not clear the session marker as an automatic retry and do not assume that either browser status proves execution or cancellation. Restarting a tab or issuing an identical prompt without adjudicating the prior dispatch is unsafe.

This is source-only product work inside existing [PR #1552](https://github.com/Zennay/zCloud/pull/1552). It did **not** run a QA/regression suite, write a live queue/lease, restart Firefox, change userscript version, dispatch CI or deploy to production. Protected-main CI on the eventual exact HEAD, independent review and the original release owner remain required. `PR_open` is not `merged_on_main`; neither proves a live browser update.
