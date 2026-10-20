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

## 20 oktober · interactieve blocker-details en directe notificatiedoorgang

- Een actuele urgente incidentkaart is op de Galaxy Watch uitklapbaar. In de compacte modus blijft de lijst scanbaar; uitklappen toont de volledige oorzaak en volgende actie in aparte secties, de waargenomen leeftijd en optioneel **Open project** wanneer de project-ID echt in de actuele projectlijst voorkomt. Dit voert zelf geen herstel, claim, merge of serviceactie uit.
- Elke Android-blokkadenotificatie gebruikt nu een unieke expliciete activity-intent-identiteit (intern `zcloud-watch://incident/` + encoded ID), zodat meerdere notificaties niet meer via één gedeelde `PendingIntent` naar het verkeerde incident springen. Er wordt uitsluitend een begrensde incident-ID doorgegeven, nooit een externe URL of herstelautorisatie.
- De app behandelt zowel cold start als `onNewIntent` bij een al geopende Watch-activiteit. Voor het uitklappen haalt de client **eerst nieuw** `/api/v1/watch`-bewijs op. Alleen een bij die nieuwe observatie nog actuele high/critical blokkade met overeenkomstige ID komt in beeld; historische fallback, offline, stale of inmiddels verdwenen incidenten worden niet als open gemarkeerd. De aanvraag is éénmalig, geen achtergrondloop.
- Dit zijn broncommits binnen de bestaande [PR #1561](https://github.com/Zennay/zCloud/pull/1561). Geen QA-tests geschreven of gestart, geen nieuwe PR, signering, APK-publicatie, device-installatie, runtime-/broker-/securityactie of deploy. Android-build, onafhankelijke review en fysieke horlogeacceptatie zijn voor productie verplicht.

## 20 oktober · uitsluitend brongebonden menselijke escalaties

De **actuele** Watch-API bevat op elk `active_blockers`-item een expliciete booleaanse `human_action_required`. Deze vlag is conservatief alleen waar voor herkende `recovery_failed` en `deploy_failed`-incidenttypen: een mislukte herstel-/releaseactie waarvoor menselijke bron-/releasebeoordeling nodig is voordat een onomkeerbare vervolgstap wordt toegestaan. `high` of `critical` severity alleen is niet voldoende. Claimconflicten, overige servicestatussen en oudere servermeldingen blijven in het informatieve incidentoverzicht zichtbaar, maar lokken niet zelfstandig een interruptieve horlogemelding uit. Andere gevallen kunnen pas aan deze allowlist worden toegevoegd na expliciete brongebonden classificatie door de bevoegde owner; een onbekend of ontbrekend veld is altijd `false` in de Kotlin-client.

De notificatieworker eist nu tegelijk een **niet-stale actuele `active_blockers`-bron**, een geldige incident-ID, een urgente severity en `humanActionRequired=true`. De compacte Watch-kaart toont duidelijk het verschil tussen **Menselijke actie nodig** en **Actuele status**. Historische feeds, ontbrekende bronvlag of algemene hoge severity worden niet tot menselijke interventie gepromoveerd. Dit is uitsluitend weergave en best-effort notificatieroutering, geen herstel- of releaseautorisatie.

Bronupdate in de bestaande open PR, **nog niet op main of op Watch 7 geïnstalleerd**. Compatibele server en app moeten samen na bestaande verplichte CI/review en release-eigenaarsacceptatie worden uitgerold; anders staan nieuwe notificaties bewust uit. Geen QA/regressiesuite, handmatige CI-rerun, actuele SQLite-/Notionclaimwijziging of service-restart.

## 20 oktober · verdwenen notificatie + severity error-bronwaarheid

Bij het openen van een melding doet de Watch-app een nieuwe bronopvraag. Als de exacte incident-ID in de actuele `active_blockers`-bron ontbreekt, opent de app geen oude details en toont hij **MELDING NIET MEER ACTIEF** met een afsluitbare toelichting. Bij offline of stale bron blijft de bestaande offline/stale waarschuwing van kracht: afwezigheid kan dan niet als opgelost worden bewezen.

De actuele server selecteert `critical`, `high` **en `error` als ernstige incidentniveaus. De notificatie- en UI-clients accepteren nu dezelfde drie niveaus; `error` wordt als kritisch weergegeven en alleen als `human_action_required=true` daadwerkelijk notificatiewaardig. Oude feed/algemene status blijft zonder human attestation stil. Dit voorkomt dat een echte brongebonden `recovery_failed` of `deploy_failed` met severity `error` wel door de server maar niet door de Watch zou worden getoond.

Geen nieuwe test-/QA-suite of handmatige CI-rerun. Deze bronverbeteringen zijn nog niet naar `main` gemerged of op een fysiek toestel geïnstalleerd; bestaande Android-CI, onafhankelijke review, signing en device-acceptatie blijven nodig.

## 20 oktober · gestructureerd incidentdossier in plaats van afgebroken samenvatting

De backend retourneert nu bij `active_blockers` aparte maximaal 240 tekens lange `cause`, `impact` en `action` velden. Het oude `detail` blijft backwards compatible, maar de tekstbudgetten voor oorzaak en actie zijn afzonderlijk begrensd (elk max. 180 tekens); één extreem lange oorzaak kan daarom de vervolgstap niet meer volledig wegdrukken.

Op de Watch toont een uitgeklapte actieve incidentkaart alle drie de secties, inclusief de bedrijfsimpact. Bij een oudere server zonder de drie velden blijft de bestaande gecombineerde tekst als fallback. Bij een brongebonden menselijke escalatie toont de eigenlijke Android-notificatie eerst de expliciete `action`, zodat het scherm niet alleen een algemeen probleem noemt. Het notificatiebeleid blijft fail-closed voor oude of onbekende menselijke-autorisatievelden.

Alles staat alleen als source op [bestaande PR #1561](https://github.com/Zennay/zCloud/pull/1561); er zijn geen QA-tests geschreven/gedraaid, geen CI-gates uitgeschakeld, geen APK, Watch-installatie of live-deploy geleverd. Vereiste exacte-head CI, onafhankelijke review en release-eigenaar blijven verplicht.

## 20 oktober · lokaal één uur dempen van een specifiek incident

De actuele menselijke escalatiekaart op Wear OS heeft een expliciete gebruikersactie **Notificaties 1 uur dempen** en **Meldingen hervatten**. De bestaande urgente statuskaart blijft volledig zichtbaar en uitklapbaar. Dit is **geen** erkenning of oplossing van een GitHub/Notion/zCloud-blokkade; geen lease, incidentstatus, workflow, queue of server wordt aangepast.

- De lokale `IncidentSnoozeStore.kt` bewaart uitsluitend SHA-256-afgeleide sleutels en een uur-expiratietijdstip voor maximaal 32 incidentele episodes, zonder inhoud of ruwe incidenttitels. De huidige incident-ID + detectietijd vormen de episode; een werkelijk nieuwe episode wordt niet door een oude demping verborgen.
- De achtergrondworker leest uitsluitend actuele, niet-stale `active_blockers` met de bestaande human-only authorisatievlag, slaat tijdelijk gedempte episodes over **vóór** de maximaal drie notificaties per normale poll, en laat ze na afloop weer toe. Een snooze haalt een eventueel al getoonde Android-notificatie van het horloge weg, zonder een serveractie te doen.
- Handmatig hervatten wist alleen de lokale demping en de lokale afgiftebeperking. Een eventuele nieuwe melding mag pas via een **volgende normale, bestaande** WorkManager-poll komen; er wordt geen nieuwe polllus gestart.
- De resterende tijd is maximaal één uur; extreme klokterugsprongen mogen niet zorgen voor langere stille perioden. Bij offline/historische bronnen blijft dezelfde reeds bestaande fail-closed notificatiegrens gelden.
- De UI is alleen voor incidenten met `humanActionRequired=true` bereikbaar en toont expliciet dat het incident actief blijft. Lokale SharedPreferences zijn appprivé, zonder online synchronisatie.

**Scope/levering:** broncode in de bestaande open [zCloud PR #1561](https://github.com/Zennay/zCloud/pull/1561), geen nieuwe PR of QA-/regressiesuite, geen handmatige CI-rerun, APK-installatie of productie-deploy. Verplichte exacte-head CI, onafhankelijke review, protected-main merge en hardwareacceptatie blijven van kracht.

## 20 oktober · herhaalde statusverversingen veroorzaken geen nieuwe incidentepisode

Eerdere Watch-ontvangstbewijzen koppelden een episode aan `id + ts`. Het bestaande Incident Center vult bij `service_health` soms `detected_at` met de **huidige statusverversingstijd**. Hetzelfde onopgeloste probleem kon daardoor na elke statusverversing als een nieuwe episode worden behandeld. Het product heeft nu een brongebonden, vaste `episode_id`:

- De geauthenticeerde Watch-API gebruikt een SHA-256 identiteit van gecontroleerd incidenttype, bron-ID en severity. Alleen bij `service_health` wordt de onderliggende oorzaak/status opgenomen in plaats van de veranderlijke waarnemingstijd; andere eventtypes gebruiken hun oorspronkelijke detectietijd als episodegrens. De 64-tekens hash onthult geen ruwe oorzaak of vrije technische details. `ts` blijft uitsluitend voor presentatie van waarneming/leeftijd.
- `ZennayCloudRepository` accepteert uitsluitend een volledig 64-tekens lowercase-hex `episode_id`. De cliënt blijft achterwaarts compatibel met oudere servers: zonder geldige bron-ID wordt het oude `id + ts` gebruikt.
- `IncidentSnoozeStore` gebruikt dezelfde episode voor één uur lokaal dempen én afgiftebeperking, met migratie van bestaande nog geldige tijdstempel-gebaseerde snoozes. `AlertWorker` migreert oude al-afgeleverde ontvangstbewijzen naar de nieuwe episode voor de actuele lijst. Daardoor wordt een bestaand onopgelost incident tijdens een compatibele upgrade niet onnodig opnieuw gepusht; verdwijnt het werkelijk uit de bron en keert het later terug, dan kan het via de reeds bestaande actuele-incidentroute opnieuw notificeren.
- De bronvelden zijn **alleen observatie**, geven geen eigenaar-, claim-, merge-, herstel- of deploybevoegdheid. Historische alerts blijven uit notificatie-routing; alleen actuele `human_action_required` incidenten kunnen trillen. Noch de API, noch deze app schrijft naar Notion, queue/SQLite, CI of runners.

**Opleverstatus:** broncode binnen bestaande [PR #1561](https://github.com/Zennay/zCloud/pull/1561), nog niet gemerged of fysiek geaccepteerd op Galaxy Watch 7. Geen nieuwe QA-tests/-suite, workflowdispatch of productie-actie. Exact-head CI, onafhankelijke review, Android-build en bevoegde release blijven vereist.

### 20 oktober · actuele lokale demptijd zonder nieuwe polling

Tijdens een uitgeklapte actuele menselijke incidentkaart ververst alleen de Compose-weergave van de resterende demptijd ongeveer elke 30 seconden. De lifecycle stopt deze tijdelijke UI-coroutine bij het dichtklappen; er komt géén extra HTTP-request, WorkManager-poll of zCloud-taak bij. De kaart leest uitsluitend de bestaande privévoorkeur en houdt de eerder afgedwongen maximaal-één-uur grens aan.

De notificatieworker sorteert bron-`error` en `critical` beide vóór `high` binnen de bestaande begrensde batch. Geen nieuwe urgentiemeldingen worden toegestaan buiten de brongebonden `human_action_required` classificatie.
