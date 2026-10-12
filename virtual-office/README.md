# Virtual Office 0.1

Een speelbaar 3D AI-kantoor bovenop de zCloud-portefeuille, in een **zelfstandige submap**. Er zijn drie collega's met verschillende persoonlijkheden (Maya, Noah, Lina), een 3D-scene, browser-spraakherkenning, voorgelezen antwoorden, een duurzame SQLite-takenwachtrij en een achtergrondworker op basis van **Codex CLI met de eigen ChatGPT-login**. Geen OpenAI API-key, aparte tokenrekening of scraping van ChatGPT-sessiecookies.

## Wat werkt in deze versie?

- Rondlopen met WASD, slepen om te kijken, op een medewerker klikken en direct een tekstgesprek of werkopdracht starten.
- Browser-voice: microfoon (Chrome/Edge met Web Speech op localhost of HTTPS) en Nederlandse speechSynthesis. Bij andere browsers blijft typen werken.
- Experimental WebXR via Three.js VRButton op een geschikte headset; de webchat is nog **geen VR-native 3D-paneel**.
- De backend bewaart opdrachten en berichten in SQLite. Een aparte server/worker of één gecombineerd proces kan blijven draaien zonder open tabblad.
- Wanneer Codex CLI op de server met een ChatGPT-account is ingelogd, haalt een worker opdrachten op. Voor echte codewijzigingen moet OFFICE_ALLOW_WRITES=1 expliciet zijn ingesteld.
- Zonder Codex-login: opdrachten blijven zichtbaar in de wachtrij en worden NIET als afgerond getoond. Geen gesimuleerde AI-uitslagen.
- Na een onverwachte worker-crash wordt een lopende opdracht **interrupted**, niet blind opnieuw uitgevoerd (kan immers al bestanden hebben veranderd). Handmatig opnieuw starten kan na inspectie.

## Start (lokaal)

Vereist Python 3.10+ en voor AI-opdrachten [Codex CLI](https://developers.openai.com/codex/cli/) als beschikbaar via je ChatGPT-abonnement.

```bash
npm install -g @openai/codex
codex login                  # interactieve login met je eigen ChatGPT-account
codex login status
cd virtual-office
python3 server.py            # UI én worker; http://127.0.0.1:8799
```

Met een geïsoleerde sandbox waarin Codex code mag wijzigen:

```bash
mkdir -p /srv/virtual-office-workspace
export OFFICE_WORKSPACE_ROOT=/srv/virtual-office-workspace
export OFFICE_ALLOW_WRITES=1
python3 server.py
```

**Gebruik geen live productiecheckout of een directory met secrets als werkmap.** De default map `virtual-office/workspace` staat in gitignore. Codex mag er alleen met een sandbox schrijven; push/deploy is niet toegestaan vanuit de agentprompt. Dit is geen vervanging voor OS/containerisolatie.

## Altijd aan (VPS)

Gebruik een dedicated service-user met Codex-login, een afgeschermde werkmap en een reverse proxy met HTTPS en authenticatie. Installeer **niet** over de bestaande zCloud-dashboardservice of zCloud SQLite heen. De service moet blijven draaien als de VR-headset/browser uitstaat.

Voorbeeld: kopieer deze map naar `/opt/virtual-office` onder eigen servicegebruiker (niet de zCloud checkout), installeer Codex en meld die gebruiker zelf aan via `codex login`; maak vervolgens een systemd unit aan:

```ini
[Unit]
Description=Virtual AI Office
After=network-online.target

[Service]
Type=simple
User=office
WorkingDirectory=/opt/virtual-office
Environment=OFFICE_BIND=127.0.0.1
Environment=OFFICE_PORT=8799
Environment=OFFICE_WORKSPACE_ROOT=/var/lib/virtual-office/workspace
Environment=OFFICE_DB_PATH=/var/lib/virtual-office/office.db
Environment=OFFICE_ALLOW_WRITES=0
ExecStart=/usr/bin/python3 /opt/virtual-office/server.py all
Restart=always
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

Bij een remote bind (`OFFICE_BIND=0.0.0.0`) weigert de app te starten als `OFFICE_ACCESS_TOKEN` ontbreekt. Gebruik **ook** een toegangscode achter een HTTPS-reverse proxy op loopback, en laat de poort niet open staan naar internet. Zet tokens nooit in Git; gebruik systemd `EnvironmentFile` met restrictive permissions.

Op de server voert Codex opdrachten uit met de beschikbare accountlimieten, geen onbeperkte gebruiksgarantie. De eigenaar kan in ChatGPT instellingen de gebruikte allowance controleren.

## API

- `GET /api/health`: readiness, auth-required, write-enabled
- `GET /api/employees`: medewerkerprofielen
- `GET /api/jobs`, `GET /api/jobs/<id>`: persistente opdrachten
- `GET /api/messages?employee=maya`: duurzaam gespreksgeheugen
- `POST /api/jobs` met JSON `{"employee":"noah","kind":"chat"|"task","instruction":"..."}`
- `POST /api/jobs/<id>/retry`: alleen fout/onderbroken/wacht-op-login

Voor afgeschermde endpoints gebruik je `X-Office-Token`; de UI vraagt dit lokaal in het geopende tabblad, maar bewaart het niet permanent. De server accepteert uitsluitend JSON voor mutaties, met een verzoeklimiet van 10 KB.

## Grenzen in 0.1

- Het kantoor ondersteunt nog geen Google/Notion/GitHub-agenttools binnen de app. Deze MVP voert Codex uit binnen een **specifieke lokale sandbox**; voor echte LightUp/Supa-projectwijzigingen moet een veilige checkout aan de worker worden gekoppeld.
- Codex is een code-agent en geen officiële onbeperkte ChatGPT-gespreks-API. Conversaties gaan hier via read-only Codex-oproepen (als ingelogd).
- Browser-microfoon is afhankelijk van browser/HTTPS; WebXR is experimenteel en mist 3D-gesprekspanelen en controller-teleport.
- De worker verwerkt op dit moment **één job tegelijk**. Voor meer medewerkers is aparte capaciteit/admission control nodig.
- Geen automatische production-deploy, zCloud SQLite-mutatie, GitHub push of merge door agents.

## Tests

```bash
python3 -m unittest discover -s virtual-office/tests -v
```

De tests zijn los van Codex, API-keys, zCloud-proddata en de VPS en toetsen persistentie, concurrency, auth, inputvalidatie en crash-veiligheid.
