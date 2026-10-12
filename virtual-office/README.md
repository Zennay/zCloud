# Virtual Office 0.2 — 3D ChatGPT-kantoor

GEEN CODEX. GEEN OPENAI API-KEY. GEEN APARTE MODEL-TOKENS.

Dit 3D/WebXR kantoor is een extra interface bovenop zCloud, dat al met ingelogde Firefox ChatGPT-chatworkers werkt. Er zijn drie medewerkers: Maya (zCloud), Noah (Supa), Lina (LightUp).

## Wat werkt
- 3D-kantoor: WASD lopen, slepen om te kijken, klik op een medewerker om te selecteren.
- Via Open ChatGPT-chat ga je naar een bestaande ChatGPT-workerconversatie, als zCloud die heeft gekoppeld, anders opent de officiële ChatGPT-startpagina. Je bestaande account werkt via die site.
- Browsermicrofoon (Chrome/Edge, HTTPS/localhost) dicteert een werkopdracht; speechSynthesis kan Office-status terugspreken.
- De kantoorserver bewaart opdrachten duurzaam in een aparte SQLite-file en geeft ze idempotent door aan de bestaande zCloud /api/portfolio-queue. zCloud verwerkt zulke taken via zijn normale ChatGPT-browserchats, ook nadat de browser gesloten is, mits de scheduler en ingelogde Firefox-workers actief blijven.
- De UI toont echte zCloud-queue-status (queued/claimed/running/verifying/done/dropped), geen verzonnen gespreksantwoorden.

**Belangrijk:** De bestaande zCloud workerqueue accepteert uitvoerbare projecttaken en biedt geen vrije ChatGPT-conversatie API aan. Voor echte gesprekken open je de ChatGPT-chat. Office toont alleen echte opdrachtstatus en eventuele systeemmeldingen — niet het ChatGPT-antwoord. Voor volledige handsfree avatar-gesprekken is een toekomstige veilige ChatGPT UI-bridge nodig.

## Lokaal starten
- Python 3.10+; geen pip packages.
- Vanuit de root van het project: cd virtual-office && python3 server.py all
- Open http://127.0.0.1:8799
- De zCloud-backend hoort bereikbaar te zijn op http://127.0.0.1:8765, of met OFFICE_ZCLOUD_URL ingesteld op een andere localhost HTTP-poort.
- Het kantoor gebruikt geen Codex CLI, OpenAI API-key, ChatGPT cookie harvesting of token-billed modelservice.

## Always-on VPS
Gebruik een eigen systemd-service (Restart=always), een aparte SQLite onder /var/lib/virtual-office en alleen een lokale verbinding naar zCloud. Bind op 0.0.0.0 ALLEEN met een unieke OFFICE_ACCESS_TOKEN van minimaal 24 tekens. Het token moet in een afgeschermde VPS-config staan, niet in GitHub/Actions-logs. Zorg voor TLS/HTTPS voor microfoon- en WebXR-permissies en bescherming van de token-overdracht. Publieke URL mag de 3D-scene tonen; opdrachten blijven met een token beveiligd.

Offline betekent: de Office-server en de zCloud-scheduler blijven draaien, niet het browservenster. De Office outbox probeert zCloud later opnieuw als zCloud tijdelijk onbereikbaar is. Na acceptatie door zCloud komt status alleen uit de echte queue.

## API
- GET /api/health: beperkte status, publiek
- GET /api/employees: medewerkers en ChatGPT-conversatielinks, auth
- GET /api/jobs en GET /api/jobs/{id}: werkopdrachten, auth
- GET /api/messages?employee=noah: opdrachtlog, geen ChatGPT transcript, auth
- POST /api/jobs: JSON { "employee": "noah", "kind": "task", "instruction": "Bouw een test" }, auth
- POST /api/jobs/{id}/retry: veilig opnieuw proberen van nog niet doorgestuurde fouten, auth
- Client authenticatie: X-Office-Token; alleen in geheugen van open tab.

## Tests
python3 -m unittest discover -s virtual-office/tests -v

Hosted CI is onafhankelijk van VPS-credentials en modelaccounts.
