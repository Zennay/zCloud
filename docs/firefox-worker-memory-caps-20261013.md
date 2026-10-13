# Firefox Web Content memory caps for the dedicated zCloud worker profile

Source branch: `fix/firefox-worker-memory-caps-20261013`. This is a
**startup preference change**, not a live browser restart or a measured
production RAM reduction.

## What changes

On a managed `chatgpt-firefox.service` start, the existing prestart hook
uses `scripts/zcloud_firefox_memory_caps.py` to maintain a small block in
`/home/ubuntu/snap/firefox/common/chatgpt-webext-profile/user.js`.

- `image.mem.surfacecache.max_size_kb=131072`: 128 MiB maximum cached
  decoded image surfaces for this profile, versus Mozilla's large platform
  default. This does **not** cap total Firefox or ChatGPT DOM/JS memory.
- `browser.cache.memory.capacity=16384`: cap Firefox HTTP memory cache at
  16 MiB (Mozilla's automatic memory-cache sizing maxes out at 32 MiB).
- Disable next-link prefetch and network prediction to avoid speculative work.

Mozilla sources:
- https://searchfox.org/firefox-main/source/modules/libpref/init/StaticPrefList.yaml
- https://searchfox.org/firefox-main/source/netwerk/cache2/CacheObserver.cpp
- https://firefox-source-docs.mozilla.org/dom/ipc/process_model.html

**Intentionally unchanged:** Firefox Fission/site isolation, ChatGPT Web
Content process count, session restoration, worker assignment/tab mapping,
active/background tabs, auto-discard, Firefox display `:10`, extensions
(including Violentmonkey), and restart/recovery policy. Lowering isolated
content process counts can make simultaneous worker pages unresponsive.

## Safe rollout / validation

1. Validate source: `python3 -m unittest discover -s tests -p
   test_firefox_memory_caps.py -v` and
   `bash -n deploy/chatgpt-firefox-prestart.sh`.
2. Verify the deployed tuner script exists at
   `/home/ubuntu/zennay-cloud/scripts/zcloud_firefox_memory_caps.py`
   and the live *user* service really references the updated prestart hook.
   The checked-in service definitions are references, not auto-installed.
3. **Do not restart a live/generating Firefox** solely to activate prefs.
   The prestart hook writes them only on a subsequent browser launch. Do
   not use `killall firefox`, `pkill`, or tab discard: the machine hosts
   other Firefox sessions, and worker generation must be preserved.
4. On a genuine idle maintenance/recovery window, sample before/after
   `free -m`, `ps -eo pid,ppid,rss,cmd`, the Firefox service cgroup and
   `about:processes`, together with worker-tab count and generation progress.
   Keep equivalent worker count/load and time interval for comparisons.
   A configured cache cap is **not** evidence of lower observed RSS.
5. Verify exact browser-to-worker tab bindings after any naturally occurring
   restart. Do not promote without owner review and the existing deployment
   safety gates (including worker/queue/source parity).

On the stopped dedicated profile, check expected changes without writing:

```bash
python3 /home/ubuntu/zennay-cloud/scripts/zcloud_firefox_memory_caps.py \
  --profile /home/ubuntu/snap/firefox/common/chatgpt-webext-profile --check
```

To remove only the block added by this feature on a **stopped** profile:

```bash
python3 /home/ubuntu/zennay-cloud/scripts/zcloud_firefox_memory_caps.py \
  --profile /home/ubuntu/snap/firefox/common/chatgpt-webext-profile --remove
```

Set `ZCLOUD_FIREFOX_MEMORY_CAPS=0` in the service environment to disable
future installation. Firefox persists initialized preferences in `prefs.js`;
after removal, reset these four prefs to their original values in
`about:config` (or via a reviewed offline profile change) before a new
browser launch. Existing unrelated `user.js` lines are preserved, custom
duplicate preferences take precedence, and symlink destinations are rejected.

## Limitation

Memory-heavy ChatGPT conversations may still consume several GiB in JS/DOM.
These caps avoid unnecessary *cache* pressure but neither force large active
workers to shrink nor guarantee a particular GiB saving. Reducing concurrent
generations and checking actual RAM headroom requires the separately owned
worker allocation/watchdog logic; do not change that logic as part of this
patch.
