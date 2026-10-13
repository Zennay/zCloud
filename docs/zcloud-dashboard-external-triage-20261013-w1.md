# zCloud externe dashboardtriage — operatorgids

Kandidaat bij issue #1166 en PR #1351 (https://github.com/Zennay/zCloud/pull/1351). Alleen observatie. Een groen API-antwoord bewijst geen gezonde productie-deploy, SQLite-write, afgeronde worker-drain of echte AI-generatie.

## Gebruik — pas NA bevoegde integratie op main

1. Open GitHub Actions, workflow “zCloud external dashboard triage (manual, hosted, read-only)”.
2. Start handmatig op main, door repo-eigenaar Zennay. Er is geen push-, PR-, cron- of healthcheck-trigger.
3. Lees de Nederlandse samenvatting in het Actions-joboverzicht. Maximaal zes GET-pogingen van 12 seconden, met vijf seconden ertussen. Vast IP en statuspad, zonder omgevingsproxy of redirect, nooit POST, SSH, sudo, SQLite, Firefox of servicebesturing.
4. Bij fouten: geef bewijs aan de verantwoordelijke #1135 / #580-PWQ-41 / #1089-eigenaar. Geen herstart, repair, SQLite-permissiewijziging, queue-retry of deploy vanuit deze diagnose.

## Uitslagen en betekenis

| Bericht | Interpretatie |
| --- | --- |
| API bereikbaar | Alleen een geldig, vers extern API-antwoord; niet complete productiegezondheid. |
| Hersteld na hercontrole | Tijdelijk transportprobleem gevolgd door een goed antwoord. |
| Verbinding geweigerd/verbroken | TCP-transportfout; geen bewijs dat de runner kapot is. |
| DNS, TLS of time-out | Externe verbinding kon niet binnen budget worden geverifieerd. |
| HTTP-status niet 200 | Serverstatus/redirect geweigerd; nooit responsebody of geheimen loggen. |
| Onverwacht antwoordtype of te groot | Ongeldige API-representatie; fail closed. |
| Ongeldige JSON of schema | Body onbruikbaar, of vereiste velden ontbreken. |
| Verouderd of toekomstige tijd | >180 seconden oud of >30 seconden in de toekomst: mogelijk cache, replay of klokafwijking. |
| MAIN_CHANGED / MAIN_UNVERIFIABLE | Exacte actuele main-head vóór/na observatie niet bewezen; geen groene samenvatting publiceren. |

De manual job verifieert current-main tweemaal via een afzonderlijk, tot 12 seconden begrensd git ls-remote. Pas als beide resultaten exact gelijk zijn aan de GitHub event-SHA, wordt de menselijke samenvatting gepubliceerd. Dit voorkomt een deel van de branch-races maar is geen atomaire branch- of live-worker-proof.

## Verificatie zonder production mutation

- PR-CI checkt de onveranderlijke head-commit rechtstreeks, niet de synthetische PR-merge. Een andere repository als PR-bron wordt geweigerd.
- Negatieve tests gebruiken tevens een echte tijdelijke 127.0.0.1 HTTP-server, inclusief redirects, 503, foutieve body, oversize, stale snapshot en proxy-injectie. Geen productie-GET in de PR-tests.
- De vaste API GET heeft maximaal 128 KiB body-budget, beperkte nested JSON, duplicate-key/Unicode/nonfinite denial, strenge MIME- en UTC-freshnesscontrole.
- De originele dashboard recovery workflow blijft ongewijzigd; de privilegieerde recover-job blijft gedeactiveerd.
- Succesvolle CI is geen herstelsleutel. Productiedeploy en zCloud-workers behoren aan andere geserialiseerde eigenaren.

## Review- en releasegrens

PR blijft draft totdat exact-head gerichte en volledige suite plus standaard zCloud smoke groen zijn, actuele PR en PR-loze ownership opnieuw gecontroleerd is, en onafhankelijke review plaatsvindt. Dit document autoriseert geen merge, dispatch, runner/job-annulering of productie-VPS-mutatie.
