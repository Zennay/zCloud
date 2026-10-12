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

1. In the VPS browser, install office-relay.user.js into the existing userscript extension.
2. Open a **dedicated, existing** ChatGPT conversation for one employee. Do not attach the relay to an already-assigned zCloud work tab.
3. Choose “Verbind deze chat met Office” in the extension menu (or click its Office Relay badge).
4. Select http://127.0.0.1:3422, the employee id (maya/noah/lina), and the existing Office access code. Credentials are held in closure memory only.
5. In the Office settings, unlock the workspace. A live relay heartbeat makes “Gesprek” available.

The relay checks that the composer is empty and no reply is running. It submits one prompt, captures only its new assistant turn, waits for a finished reply control and sends the visible text back. It never extracts ChatGPT cookies, navigates around account restrictions, or automatically resends an uncertain prompt. Navigation, expired authentication, changed UI, failed delivery or a response beyond three minutes require checking the ChatGPT tab. A restart makes unresolved claims attention-required rather than replaying them.

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
