# The Penthouse — downloadbare game (Windows)

Een eigen app rond het 3D-kantoor. De medewerkers (standaard Maya, Noah, Lina; je kunt er tot 8 bijmaken), de zCloud-wachtrij en de ChatGPT-relays blijven **op de VPS** draaien. De app is alleen het scherm, de microfoon en de verbinding.

## Hoe het werkt
- De app bouwt een **SSH-tunnel** naar je VPS (`127.0.0.1:<lokale poort>` → VPS `127.0.0.1:3422`) en laadt het kantoor daar doorheen. De toegangscode gaat dus nooit over de open HTTP-poort.
- Het kantoor zelf (3D-scène, game-logica) komt van de VPS; een update van de VPS is dus meteen een update in de game. De installer bevat alleen de schil.
- Spraak: **houd V ingedrukt** (of de microfoonknop) en praat. De opname wordt op je pc omgezet naar tekst met Whisper (lokaal; het model wordt de eerste keer eenmalig gedownload, ca. 80 MB). Er gaat geen audio naar de VPS of een clouddienst.
- Als een medewerker klaar is, loopt hij of zij naar je toe en wacht. **Pas na jouw "ok, zeg maar"** geeft de medewerker een korte samenvatting (max. 2 zinnen). Zeg "later" of "niet nu" om de medewerker terug naar het bureau te sturen; na 5 minuten komt hij of zij terug.
- Samenvattingen zijn gebaseerd op het bewijs (`evidence`) uit de zCloud-wachtrij. ChatGPT mag dat alleen herformuleren; zonder bewijs staat er letterlijk dat er geen toelichting is. Er wordt dus niets verzonnen.
- Presentaties: zeg bijvoorbeeld "Lina, maak een presentatie over de securitystatus". De medewerker laat via de ChatGPT-relay een deck schrijven, loopt naar het bordroomscherm en presenteert daar slide voor slide (lay-outs, cijfers, staafgrafieken, stappen, citaten). Dit werkt alleen als de relay van die medewerker online is.

## Eerste keer instellen
1. Installeer `The Penthouse Setup x.y.z.exe` (zie hieronder waar je die krijgt) en start de app.
2. Vul in het instelscherm in: VPS-adres, SSH-gebruiker, poort (22), je SSH-private key (bestand kiezen; een wachtwoordzin wordt niet bewaard) en de kantoor-toegangscode.
3. Bij de eerste verbinding toont de app de **vingerafdruk van de VPS-hostkey**. Controleer die (bijv. met `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` op de VPS) en bevestig. Daarna wordt de sleutel vastgezet; bij een afwijking weigert de app te verbinden.
4. De toegangscode wordt versleuteld opgeslagen met Windows-beveiliging (Electron `safeStorage`) en vult de ontgrendeling in het kantoor automatisch in.

Menu **Kantoor**: instellingen (vingerafdruk wissen), opnieuw verbinden (F5), volledig scherm (F11), **VPS-bureaublad openen (F9)**.

## VPS-bureaublad (een kale "VM")
Wil je vanuit het kantoor even op een rustige, bijna lege werkplek? Druk op **F9** of kies de tegel *VPS-bureaublad* in het kantoor. De app opent een RDP-venster (Windows: `mstsc.exe`; macOS `open`; Linux `xfreerdp`) naar het bureaublad van de **VPS**, via dezelfde SSH-tunnel. Er komt dus geen extra poort open op internet.
- Vereist een RDP-server op de VPS (bijv. `xrdp` met een lichte desktop zoals XFCE); de app installeert die niet. De poort op de VPS staat standaard op **3389** en is instelbaar in het instelscherm, samen met de RDP-gebruikersnaam.
- Er wordt **geen wachtwoord opgeslagen**: Windows vraagt er zelf om in het RDP-venster.
- Dit is de VPS, niet je eigen pc. Wil je je eigen pc op afstand bereiken, dan is daar een aparte opzet voor nodig; die zit hier niet in.
- Niet bewezen: de RDP-start op een echte Windows-pc en of xrdp op jouw VPS is ingesteld (zSSH kan dat niet controleren).

## De installer krijgen
De workflow `office-desktop-build.yml` bouwt de installer op een door GitHub gehoste Windows-runner. Download hem bij de run onder *Artifacts* → `penthouse-windows-installer`. De installer is niet code-signed, dus Windows SmartScreen waarschuwt ("Meer informatie" → "Toch uitvoeren").

Zelf bouwen op Windows: `cd virtual-office/desktop && npm install && npm run dist` (resultaat in `dist/`). Tests zonder Electron: `npm test`.

## Beveiliging
- Venster met `sandbox` en `contextIsolation`; navigatie is vergrendeld op de tunnel-oorsprong; microfoon alleen voor dat adres.
- De tunnel luistert alleen op `127.0.0.1`.
- De toegangscode staat nooit in de installer, de repository of logs.

## Wat nog niet bewezen is
Dit is in een omgeving zonder Windows, Electron, VPS-toegang en browser geschreven. Gecontroleerd zijn de logica (Python-API, game-besturing, slide-tekenen, loopgedrag met een nagebootste 3D-omgeving, tunnel met nagebootste SSH-client). **Niet** gecontroleerd: de echte Electron-app, een echte SSH-tunnel, Whisper op een echte microfoon, hoe vlot het op jouw pc draait, de RDP-start, het Beheer-paneel tegen de echte zCloud, en een echte ChatGPT-relay-rondgang. Verwacht dus een ronde fixes na de eerste echte start.

## Relay op de VPS
Het inloggen bij ChatGPT en het koppelen van elke medewerker aan een opgeslagen chat blijft handmatig (eenmalig). Daarna kan `office-relay-vps.sh` de chats na een herstart zelf openen: zet de drie `https://chatgpt.com/c/...`-links (een per regel) in `~/.config/virtual-office/relay-tabs.txt` op de VPS.
