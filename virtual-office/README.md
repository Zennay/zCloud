# Virtual Office 0.3 — The Penthouse

Live browser entry: http://198.244.191.182:3422/

A desktop-first, 38 × 30 metre penthouse above a procedural Manhattan-inspired skyline. This is an original Three.js/WebGL scene, not a GTA port, and does not require a headset. All assets in the scene are procedural/original; the only external runtime dependency is the pinned Three.js module.

## Implemented
- Full-screen environment with curtain-wall windows, walnut, stone, brass, a skyline, warm lighting, shadows, quality settings and four navigable zones.
- Seated staff at correctly aligned workstations. WASD/arrow keys, drag-to-look, optional pointer lock, touch movement, collision bounds and room shortcuts.
- Immediate **system** acknowledgement after a durable work submission; server dispatch wakes immediately and forwards to the existing zCloud queue. Existing queue status remains the evidence of progress, not an invented AI answer.
- Authenticated real VPS console: uptime, memory, disk, fixed service status and the Office queue. This is a managed command palette, not unrestricted SSH.
- Presentation studio: up to 20 slides, SQLite persistence, edit/reopen/import, standalone HTML export (print to PDF), presentation mode, live texture on the 3D boardroom screen. Requesting Maya's slide content creates a normal project task; import her delivered JSON. Automatic artifact ingestion and PPTX export are not implemented.
- Separate opt-in ChatGPT conversation relay. The server persists messages and accepts an answer only for the claiming relay. No API keys, Codex CLI or model billing service.

## Private workspace
The scene is public; jobs, messages, decks, relay and workstation endpoints require the existing X-Office-Token. The browser stores the entered code only in the open page's memory. The service's token is held in /etc/virtual-office/office.env and is never published in CI output.

The current external URL uses HTTP. Do not enter the code over untrusted public HTTP. Use a trusted localhost tunnel/VPS browser or an existing trusted HTTPS reverse proxy for private operations. Microphone input requires a supported browser and a secure context. This release does not claim a new TLS deployment.

## Connect actual ChatGPT replies
The original zCloud worker queue has no arbitrary-chat-response API. This release adds an explicit UI bridge, not a substitute model.

## Preferred: ChatGPT conversations run on the VPS

The VPS has an existing, **RDP-visible** Firefox desktop on `DISPLAY=:10`. Office now provides a **separate Firefox profile and user service** for real ChatGPT conversations, without touching zCloud workers, their web-ext profile, or their assigned tabs.

1. Log into the VPS RDP desktop as `ubuntu` and start a terminal there. Confirm that the RDP desktop on `:10` is active.
2. The Office release installs `/opt/virtual-office/office-relay-vps.sh` and the `office-relay-vps.service` **user unit**. The deployment tries to start this dedicated service automatically only when the existing RDP display and user bus are available; otherwise it leaves it installed for manual activation. To check or start the browser from the VPS desktop terminal:
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now office-relay-vps.service
   systemctl --user status office-relay-vps.service --no-pager
   ```
   Or for an initial interactive diagnostic, without enabling the service: `bash /opt/virtual-office/office-relay-vps.sh`. Do not run both at once. If the service fails because `:10` is absent, log into the RDP desktop first and retry. Logs: `journalctl --user -u office-relay-vps.service -n 30 --no-pager`.
3. In the **Firefox window on the VPS**, install the Tampermonkey extension and then [Office Relay userscript](https://raw.githubusercontent.com/Zennay/zCloud/feature/virtual-ai-office-mvp-20261012/virtual-office/office-relay.user.js). Sign into your ChatGPT account **in this VPS Firefox profile**; the local Mac browser's login is unrelated.
4. In this dedicated profile, open an **existing, saved** ChatGPT conversation (a `https://chatgpt.com/c/...` URL) for each employee. Do not use an existing zCloud worker tab. In that ChatGPT tab, click `Office Relay · uit`, choose `http://127.0.0.1:3422`, the employee `maya`, `noah`, or `lina`, and enter the Office access code from the VPS. The origin refers to **VPS loopback**, so no SSH tunnel is necessary for the relay itself. Keep one relay tab per employee.
5. Leave this VPS desktop and service running; disconnecting the RDP viewer normally preserves the Xorg desktop session. Use the Office UI from another machine through a trusted HTTPS reverse proxy or a **separate** SSH tunnel for its private access-code endpoints. The UI/browser location is independent of the relay.

**Important:** After a Firefox crash or accidental browser-window close, the user service restarts the **separate expert Firefox**. It cannot authenticate ChatGPT for you, but Firefox may restore signed-in sessions and tabs. Office Relay v0.3.7 supports **explicit opt-in** to storing the Office access code in Tampermonkey's local browser-profile storage (NOT encrypted). On an exact saved ChatGPT conversation URL, the script verifies the local Office health endpoint, reconnects the same employee and resumes heartbeat without re-prompting or replaying uncertain messages. Choosing **Nee** at the pairing prompt leaves the code only in memory and requires manual pairing after restart. Use **Ja** only in the dedicated, trusted VPS Firefox profile; avoid shared browsers. A new conversation is never adopted silently: use Tampermonkey menu **Koppel nieuwe chat aan opgeslagen medewerker** and confirm the named employee. This removes the previous stored conversation binding on success. A stale server lease from an old browser client may delay recovery by up to 130 seconds, and an unavailable ChatGPT login or changed UI still requires human attention. Remote authentication, actual heartbeat and message round-trip are **not** established by backend tests or automatic deployment. Never paste an access code into the public unencrypted `http://198.244.191.182:3422` page. The private VPS browser profile uses ~additional Firefox process memory; monitor RAM if many workers already run.

## Optional: ChatGPT conversations run on your own PC

Alternatively, Tampermonkey can run on **your own PC** (Mac, Windows or Linux) inside a Firefox/Chrome browser at `https://chatgpt.com/c/...`, forwarding to the VPS through SSH. Tampermonkey cannot run inside the native ChatGPT desktop application.

1. On **your own PC**, open an SSH tunnel to the VPS with your existing SSH account and verified host key. Keep this terminal running:
   ```bash
   ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=25 -o ServerAliveCountMax=3 -L 127.0.0.1:3422:127.0.0.1:3422 YOUR_SSH_USER@198.244.191.182
   ```
   Replace `YOUR_SSH_USER` with the account you normally use to SSH into the VPS. For the same command with messages and preconfigured safety options, use [Mac/Linux launcher](./connect-from-pc.sh) (`bash connect-from-pc.sh YOUR_SSH_USER@198.244.191.182`) or [Windows PowerShell launcher](./connect-from-pc.ps1) (`.\\connect-from-pc.ps1 -SshTarget 'YOUR_SSH_USER@198.244.191.182'`). You need working SSH access; neither launcher requests nor logs an Office code.
2. In your **PC browser** open `http://127.0.0.1:3422/api/health`; it should return JSON with `"mode":"zcloud_chatgpt_chats"`. If it fails, fix the tunnel before entering any token. Open `http://127.0.0.1:3422/` in your PC browser to use Office privately.
3. Install Tampermonkey in that **same PC browser** and install the userscript from the **HTTPS GitHub source**: [Install Office Relay userscript](https://raw.githubusercontent.com/Zennay/zCloud/feature/virtual-ai-office-mvp-20261012/virtual-office/office-relay.user.js). Never install through the public HTTP Office URL, which may return `Invalid Userscript`.
4. Open a **dedicated, existing** ChatGPT conversation for one employee. Do not attach the relay to an already-assigned zCloud work tab.
5. Choose “Verbind deze chat met Office” in the PC browser's extension menu (or click its Office Relay badge). Enter `http://127.0.0.1:3422`, the **same employee** as in Office (`maya`/`noah`/`lina`), and your existing Office access code. The userscript first checks the Office server's unauthenticated health to catch accidental port mix-ups before asking for the access code; this is a diagnostic check, not cryptographic authentication of a local process. Use a trusted PC and a verified SSH host key. Credentials are held only in tab memory.
6. In Office settings, unlock with the same access code. The relay must show `Office Relay · maya verbonden` (or your selected employee), and Office's authenticated connection status should change after a valid heartbeat.

**Important:** `127.0.0.1` here means the **PC's loopback endpoint forwarded over SSH to the VPS**; it does not require a VPS browser. A raw public `http://198.244.191.182:3422` relay URL is deliberately refused to avoid exposing the Office token. Use a trusted HTTPS reverse proxy as an alternative only if one has actually been configured. If the browser tab reloads, an ordinary memory-only relay needs manual pairing again; a trusted browser with the new explicitly saved pairing reconnects to the exact previously saved conversation. The SSH tunnel can remain open. If local port 3422 is already in use, stop that process or coordinate a different port rather than sending the token to an unknown service.

Every Office prompt includes a short, named specialist role: Maya (zCloud portfolio), Noah (Supa engineering), or Lina (LightUp QA/security with explicit authorization constraints). This role preface is resent only for an actual new Office job, never as an unclaimed startup message. The relay checks that the composer is empty and no reply is running. It submits one prompt, captures only its new assistant turn, waits for a finished reply control and sends the visible text back. It never extracts ChatGPT cookies, navigates around account restrictions, or automatically resends an uncertain prompt. Navigation, expired authentication, changed UI, failed delivery or a response beyond three minutes require checking the ChatGPT tab. A restart makes unresolved claims attention-required rather than replaying them.

Installation/connection of this relay is **not** proven by backend tests. Until a real tab heartbeat and round trip are observed, the UI correctly shows it as disconnected and offers the real ChatGPT link.

## Runtime and tests
The existing virtual-office.service continues to run independently of the visitor's browser. Background project execution also requires the existing zCloud scheduler and authenticated Firefox workers to remain operational.

The owner-branch CI uses [self-hosted,zcloud,vps] on vps-bb300bba. Tests use temporary databases and an unused zCloud port, never the production queue. Deployment preserves the existing Office SQLite and token. It verifies the real console through authenticated loopback requests without printing secrets or creating work.

Run: python3 -m unittest discover -s virtual-office/tests -v

## Graphics decision
WebAssembly compiles an existing native engine; it does not manufacture high-quality models, animation, textures or lighting. Emscripten maps supported GL calls to browser WebGL:
https://emscripten.org/docs/porting/multimedia_and_graphics/OpenGL-support.html

Unreal Pixel Streaming renders an Unreal application on another machine and streams it to a browser:
https://dev.epicgames.com/documentation/en-us/unreal-engine/pixel-streaming-in-unreal-engine

This bounded office remains native browser WebGL to work with the current VPS architecture without requiring a GPU rendering service. It is an upgraded architectural prototype; it is not represented as GTA-level production art or verified AAA performance.

## Office rework (12 October)
The city now uses a licensed high-altitude Manhattan photograph rather than close procedural building boxes. The backdrop is a flat image, not a 360-degree map. Side-on staff camera positions avoid looking through monitors. Glass partitions have marked open portals, brass safety rails and matching collision. Branding and introductory overlays are hidden during exploration. Maya has a dedicated assistant shortcut. The creative desk offers remembered plush, garden and minimal styles in this browser. The 3D wall screen reflects real Office jobs; the monitor also shows filtered live project milestones from zCloud, with explicit offline state.

28 existing API tests passed for the visual rework. Browser visual validation remains unavailable in the cloud browser because it upgrades the HTTP origin to HTTPS and the server does not speak TLS there. Separate project-monitor tests verify privacy filtering, caching, and offline behavior. Shared human presence, invitations/accounts, expandable staff, native desktop packaging, isolated Ubuntu desktop, arbitrary image-to-3D conversion and TLS remain incomplete. This release does not label them connected or working.
