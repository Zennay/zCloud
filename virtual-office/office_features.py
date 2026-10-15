"""Private Office workstation, decks and explicit ChatGPT UI relay."""
import json
import re
import subprocess
import time
import uuid
PROFILE_IDS = {"maya", "noah", "lina"}
THREAD = re.compile(r"^https://chatgpt[.]com/c/[0-9a-fA-F-]{16,80}$")
COMMANDS = {"uptime": ["/usr/bin/uptime"], "memory": ["/usr/bin/free", "-m"],
            "disk": ["/usr/bin/df", "-h", "/"],
            "services": ["/usr/bin/systemctl", "is-active", "virtual-office.service", "zennay-cloud.service"]}
HELP = """VPS-opdrachten:
  uptime    Live uptime en belasting
  memory    Live geheugengebruik (MB)
  disk      Opslag van de VPS
  services  Status van office en zCloud (in die volgorde)
  queue     Jouw opgeslagen Office-opdrachten
  help      Deze lijst
  clear     Scherm leegmaken
Dit is een beheerde VPS-console. Vrije shellcommando's worden niet uitgevoerd."""

def init(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS office_decks (
      id TEXT PRIMARY KEY, title TEXT NOT NULL, slides_json TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS office_relay (
      employee TEXT PRIMARY KEY, client_id TEXT NOT NULL, conversation TEXT NOT NULL, heartbeat REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS office_chat (
      id TEXT PRIMARY KEY, employee TEXT NOT NULL, instruction TEXT NOT NULL,
      status TEXT NOT NULL, client_id TEXT NOT NULL DEFAULT '', claim_token TEXT NOT NULL DEFAULT '',
      reply TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    """)
    # Never resend a prompt that might already have been sent before a restart.
    db.execute("UPDATE office_chat SET status='attention' WHERE status='claimed'")

def bounded_text(value, name, limit, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= limit or "\x00" in value:
        raise ValueError(name + " heeft een ongeldige lengte of inhoud")
    return value.strip()

ACCENTS = {"gold", "teal", "rose", "violet", "slate"}
LAYOUTS = {"title", "bullets", "stat", "quote", "split"}
VISUALS = {"none", "bars", "steps"}

def _short(value, limit):
    return value.strip()[:limit] if isinstance(value, str) and "\x00" not in value else ""

def clean_slide(slide):
    """Title/body stay mandatory and backwards compatible; visual fields are optional and bounded."""
    out = {"title": bounded_text(slide.get("title"), "Slidetitel", 120),
           "body": bounded_text(slide.get("body", ""), "Slidetekst", 2000, 0)}
    layout, accent, visual = slide.get("layout"), slide.get("accent"), slide.get("visual")
    if layout in LAYOUTS:
        out["layout"] = layout
    if accent in ACCENTS:
        out["accent"] = accent
    if visual in VISUALS and visual != "none":
        out["visual"] = visual
    stat = slide.get("stat")
    if isinstance(stat, dict) and _short(stat.get("value"), 24):
        out["stat"] = {"value": _short(stat.get("value"), 24), "label": _short(stat.get("label"), 80)}
    data = slide.get("data")
    if isinstance(data, list):
        points = []
        for item in data[:8]:
            if isinstance(item, dict) and isinstance(item.get("value"), (int, float)) \
                    and not isinstance(item.get("value"), bool) and abs(item["value"]) < 1e12:
                label = _short(item.get("label"), 40)
                if label:
                    points.append({"label": label, "value": item["value"]})
        if points:
            out["data"] = points
    for key, limit in (("speaker", 700), ("quote_by", 80)):
        text = _short(slide.get(key), limit)
        if text:
            out[key] = text
    return out

def save_deck(core, body):
    title = bounded_text(body.get("title"), "Titel", 120)
    slides = body.get("slides")
    if not isinstance(slides, list) or not 1 <= len(slides) <= 20:
        raise ValueError("Een presentatie bevat 1–20 slides")
    clean = []
    for slide in slides:
        if not isinstance(slide, dict):
            raise ValueError("Elke slide moet een object zijn")
        clean.append(clean_slide(slide))
    ident = body.get("id") or uuid.uuid4().hex
    if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{32}", ident):
        raise ValueError("Ongeldig presentatie-id")
    with core.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM office_decks WHERE id=?", (ident,)).fetchone():
            if db.execute("SELECT COUNT(*) FROM office_decks").fetchone()[0] >= 100:
                raise ValueError("Maximaal 100 presentaties")
        db.execute("INSERT INTO office_decks VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,slides_json=excluded.slides_json,updated_at=excluded.updated_at",
                   (ident, title, json.dumps(clean, ensure_ascii=False), core.timestamp()))
        db.execute("COMMIT")
    return {"id": ident, "title": title, "slides": clean}

def deck_list(core):
    with core.connect() as db:
        return {"decks": [dict(r) for r in db.execute("SELECT id,title,updated_at FROM office_decks ORDER BY updated_at DESC LIMIT 100")]}

def get_deck(core, ident):
    with core.connect() as db:
        row = db.execute("SELECT * FROM office_decks WHERE id=?", (ident,)).fetchone()
    return {"id": row["id"], "title": row["title"], "slides": json.loads(row["slides_json"])} if row else None

def workstation(core, command):
    command = bounded_text(command, "Commando", 80)
    if command == "help":
        return {"output": HELP, "source": "office-help"}
    if command == "queue":
        lines = [r["id"][:8] + "  " + r["employee"] + "  " + r["status"] for r in core.list_jobs()]
        return {"output": "\n".join(lines) or "Geen Office-opdrachten.", "source": "vps-sqlite"}
    if command not in COMMANDS:
        raise ValueError("Onbekend commando. Typ help voor de beschikbare VPS-opdrachten.")
    try:
        result = subprocess.run(COMMANDS[command], shell=False, capture_output=True, text=True,
                                timeout=4, env={"PATH": "/usr/bin:/bin", "LANG": "C"}, cwd="/")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("VPS-opdracht niet beschikbaar of time-out") from exc
    return {"output": result.stdout[:6000] or "Geen uitvoer.", "exit_code": result.returncode,
            "source": "vps", "checked_at": core.timestamp()}

# Browsers may throttle setInterval in long-hidden tabs to once per minute.
# Allow two delayed beats plus network jitter; all relay ownership and enqueue
# checks must use the same expiry. No replay occurs on reconnect.
RELAY_HEARTBEAT_TIMEOUT = 130

def relay_status(core):
    """Expose only authenticated, actionable relay health; never expose claim tokens."""
    with core.connect() as db:
        rows = {r["employee"]: dict(r) for r in db.execute("SELECT employee,conversation,heartbeat FROM office_relay")}
    now = time.time()
    employees = {}
    for employee in PROFILE_IDS:
        row = rows.get(employee)
        age = max(0, int(now - row["heartbeat"])) if row else None
        online = row is not None and age < RELAY_HEARTBEAT_TIMEOUT
        employees[employee] = {
            "online": online,
            "last_seen_seconds": age,
            "conversation": row["conversation"] if online else None,
        }
    return {"employees": employees}

def chat_enqueue(core, body):
    employee = body.get("employee")
    if employee not in PROFILE_IDS:
        raise ValueError("Onbekende medewerker")
    instruction = bounded_text(body.get("instruction"), "Bericht", 2000)
    ident, now = uuid.uuid4().hex, core.timestamp()
    with core.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        relay = db.execute("SELECT heartbeat FROM office_relay WHERE employee=?", (employee,)).fetchone()
        if not relay or time.time() - relay["heartbeat"] >= RELAY_HEARTBEAT_TIMEOUT:
            raise ValueError("Geen ChatGPT Relay verbonden voor deze medewerker. Open ChatGPT ↗ of verbind de Office Relay.")
        if db.execute("SELECT COUNT(*) FROM office_chat WHERE employee=? AND status IN ('queued','claimed')", (employee,)).fetchone()[0] >= 3:
            raise ValueError("Deze medewerker heeft al drie open gespreksberichten")
        db.execute("INSERT INTO office_chat(id,employee,instruction,status,created_at,updated_at) VALUES(?,?,?,'queued',?,?)", (ident, employee, instruction, now, now))
        ack = "Bericht ontvangen. Je gekoppelde ChatGPT-tab antwoordt zodra hij beschikbaar is."
        for role, text in [("user", instruction), ("system", ack)]:
            db.execute("INSERT INTO messages(employee,role,content,created_at) VALUES(?,?,?,?)", (employee, role, text, now))
        db.execute("COMMIT")
    return {"id": ident, "status": "queued", "acknowledgement": ack}

def relay_poll(core, body):
    employee = body.get("employee")
    client = bounded_text(body.get("client_id"), "Relay-id", 80)
    conversation = body.get("conversation", "")
    if employee not in PROFILE_IDS or not isinstance(conversation, str) or not THREAD.fullmatch(conversation):
        raise ValueError("Verbind een bestaande ChatGPT-conversatie met deze medewerker")
    with core.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute("SELECT * FROM office_relay WHERE employee=?", (employee,)).fetchone()
        if prior and prior["client_id"] != client and time.time() - prior["heartbeat"] < RELAY_HEARTBEAT_TIMEOUT:
            raise ValueError("Deze medewerker is al aan een actieve tab gekoppeld")
        db.execute("INSERT INTO office_relay VALUES(?,?,?,?) ON CONFLICT(employee) DO UPDATE SET client_id=excluded.client_id,conversation=excluded.conversation,heartbeat=excluded.heartbeat", (employee, client, conversation, time.time()))
        job = None
        if body.get("ready") is True:
            active = db.execute("SELECT 1 FROM office_chat WHERE employee=? AND status='claimed'", (employee,)).fetchone()
            if not active:
                row = db.execute("SELECT * FROM office_chat WHERE employee=? AND status='queued' ORDER BY created_at,id LIMIT 1", (employee,)).fetchone()
                if row:
                    claim = uuid.uuid4().hex
                    db.execute("UPDATE office_chat SET status='claimed',client_id=?,claim_token=?,updated_at=? WHERE id=?", (client, claim, core.timestamp(), row["id"]))
                    job = {"id": row["id"], "instruction": row["instruction"], "claim_token": claim,
                           "persona": str((core.PERSONAS.get(employee) or {}).get("persona_prompt") or "")[:1800]}
        db.execute("COMMIT")
    return {"ok": True, "job": job}

def relay_reply(core, body):
    ident = bounded_text(body.get("id"), "Bericht-id", 80)
    client = bounded_text(body.get("client_id"), "Relay-id", 80)
    claim = bounded_text(body.get("claim_token"), "Claim", 80)
    state = body.get("status", "done")
    if state not in ("done", "attention"):
        raise ValueError("Ongeldige antwoordstatus")
    reply = bounded_text(body.get("reply"), "Antwoord", 18000)
    with core.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT * FROM office_chat WHERE id=? AND client_id=? AND claim_token=?", (ident, client, claim)).fetchone()
        if not job:
            raise ValueError("Antwoord hoort niet bij deze relay-opdracht")
        if job["status"] in ("done", "attention"):
            db.execute("COMMIT")
            return {"ok": True, "duplicate": True}
        if job["status"] != "claimed":
            raise ValueError("Gespreksbericht is niet geclaimd")
        db.execute("UPDATE office_chat SET status=?,reply=?,updated_at=? WHERE id=?", (state, reply, core.timestamp(), ident))
        kind = job["kind"] if "kind" in job.keys() else "chat"
        if kind == "chat":
            db.execute("INSERT INTO messages(employee,role,content,created_at) VALUES(?,?,?,?)", (job["employee"], "assistant" if state == "done" else "system", reply, core.timestamp()))
        db.execute("COMMIT")
        chat = dict(job)
    if kind != "chat":
        import office_game  # lazy: office_game imports this module
        office_game.after_reply(core, chat, state, reply)
    return {"ok": True}
