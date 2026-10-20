# Blocker-first alerts: dashboard + Wear OS (19 Oct 2026)

## Product behavior
- **Dashboard:** `Needs attention` becomes **Wat zit vast?**. The five top actionable incidents show project + problem + exact next action immediately, before the CPU/resource panels. The existing full incident cards (cause, impact, rollback, evidence) are preserved under a native accessible disclosure.
- **Wear OS:** current critical/high incidents from the existing Incident Center are listed **above** progress, CPU, RAM and projects, with a short cause and recommended action.
- **Watch notification:** at most three newly observed high/critical incidents per poll on a dedicated user-controllable importance-high notification channel. Notified IDs are persisted only after posting. If there are more, the remainder remain eligible for later polls; normal milestones do not vibrate. First sync uses unique one-time work; recurring best-effort WorkManager polling is every 15 minutes (Wear OS can delay for battery/network).
- **API contract:** `/api/v1/watch` retains the previous `alerts` history for older clients and adds `active_blockers` with `id, project, severity, title, detail, ts`. These are drawn from **live Incident Center** filtering of resolved/expired incidents (not the raw historical alert stream), and deliberately omit breakthrough/milestone events. The existing watch client prefers this new field and falls back to historical alert rows only on older servers; it now correctly reads `detail` / `ts`.
- **No user interaction required:** an alert is not a grant for sudo, production deploy, bypass of CI, access to retailers, security scanning, trading, or cancellation of an active claim. Workers may continue with independent safe, unclaimed work while the dependent item waits.

## Operational limits
- A notification is **not** real-time push: a fifteen-minute WorkManager request is a lower-bound schedule, not a guaranteed deadline. The watch needs connectivity, notification permission, and an installed build.
- The UI reads the current backend incident feed; **these changes are not deployed just because a branch/PR exists**. Both server and signed/installed watch app must be on compatible versions to benefit from the live blocker feed. Review existing open dashboard/runtime source-owner PRs before main integration.
- The watch app requires no additional account capability or dangerous system privilege. API remains read-only. Historical `alerts` are still available for backwards compatibility but do not establish current intervention needed.
- The hourly ChatGPT portfolio audit uses the matching 20-second blocker-first overview **followed by** full 1/8/24-hour technical and product evidence; that separate scheduled automation was updated in place, preserving the hourly cadence.

## Acceptance gates
1. Independent source-owner review of `server.py` and dashboard integration against current `main` and open PR #1549/#1550; avoid clobbering their owned modifications.
2. Existing exact-head CI/build and resulting-main protections. No QA/regression suite authored or run as part of this product slice.
3. Review `/api/v1/watch` post-deploy with a valid watch token for presence of actual actionable, not resolved, incidents. Do not publish the token or private incident data.
4. Build/install the Wear OS app and inspect the watch card and notification permission/channel in actual operation before claiming device delivery.

## 20 oktober · stale/offline bronwaarheid en opnieuw geopende incidenten

De Wear OS-productbron houdt cache bij een mislukte verversing beschikbaar, maar toont dan **OFFLINE** in plaats van LIVE, plus een expliciete foutkaart op overzicht en projectdetails. Oude incidenten worden bij verbindingsverlies of server-`stale` niet meer als **actuele urgente** kaarten getoond. Lege of dubbele incident-ID's worden voor de Compose-kaarten uitgefilterd, zodat de lazy list stabiele unieke sleutels behoudt.

`WatchSnapshot.currentBlockerFeedAvailable` onderscheidt de werkelijke `active_blockers`-bron van de oudere `alerts`-historie. Een oudere server blijft leesbaar, maar toont een zichtbare waarschuwing **ALLEEN HISTORISCHE MELDINGEN**; de achtergrondworker verstuurt daaruit **geen** urgente meldingen. Hierdoor wordt een historische alert niet opnieuw voorgesteld als een open, actueel incident.

Voor actuele incidenten worden notificatieontvangstbewijzen op combinatie van ID en detectietijd onthouden. Als een incident uit de huidige bron verdwijnt, vervalt zijn oude suppressiebewijs, zodat een latere terugkeer opnieuw kan melden. Notificaties gebruiken een stabiele stringtag per incident in plaats van alleen de mogelijk botsende numerieke `hashCode`. Maximaal drie nieuwe urgente meldingen per best-effort poll, bestaande gebruikerspermissie en notificatiekanaal blijven gelden.

**Levergrens:** GitHub-broncommits op de bestaande PR, geen gesigneerde APK, toestelinstallatie of live-deviceacceptatie. Vereiste onafhankelijke review, Android-build, protected-main en fysieke Wear OS-acceptatie blijven noodzakelijk; geen QA-suites of handmatige regressierondes toegevoegd of gedraaid.
