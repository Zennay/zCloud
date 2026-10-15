#!/usr/bin/env python3
"""Virtual Office: browser-facing interface for existing zCloud ChatGPT chat workers.

No Codex, OpenAI API, token-billed LLM, or ChatGPT browser-session scraping.
A persistent outbox forwards *actionable project tasks* to zCloud's real
portfolio queue. zCloud's existing authenticated Firefox ChatGPT chats consume
that queue independently of the office browser. This is NOT a free-form chat
relay: zCloud does not expose arbitrary ChatGPT reply text.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import uuid
import sys
import office_features as features
import office_game as game
import office_staff as staff
import office_control as control
import office_experts as experts

WAKE = threading.Event()
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DB = Path(os.getenv("OFFICE_DB_PATH", str(ROOT / "office.db"))).expanduser().resolve()
BIND = os.getenv("OFFICE_BIND", "127.0.0.1")
PORT = int(os.getenv("OFFICE_PORT", "8799"))
TOKEN = os.getenv("OFFICE_ACCESS_TOKEN", "")
ZCLOUD_URL = os.getenv("OFFICE_ZCLOUD_URL", "http://127.0.0.1:8765").rstrip("/")
# The zCloud management bridge must stay on localhost: never proxy it to a
# user-provided URL or use this frontend as a general SSRF/network client.
_parts = urlsplit(ZCLOUD_URL)
if (_parts.scheme != "http" or _parts.hostname not in ("127.0.0.1", "localhost", "::1")
        or _parts.username or _parts.password or _parts.path or _parts.query or _parts.fragment):
    raise SystemExit("OFFICE_ZCLOUD_URL must be a localhost-only HTTP origin")

# Browser modules the page may load, and the vendored three.js tree (exact-name allowlist, no directory listing).
MODULES = ("scene.js", "slides.js", "game.js", "city.js", "post.js", "people.js", "materials.js", "interior.js", "walk.js", "layout.js", "panel.js")
VENDOR_PATH = re.compile(r"/vendor/(three\.module\.min\.js|LICENSE-three\.txt|addons/[a-z]+/[A-Za-z]+\.js)")

PERSONAS = {
    "maya": {"id": "maya", "name": "Maya", "title": "Projectmanager · zCloud",
             "role": "Organiseert en begeleidt zCloud-werk", "project_id": "cloud",
             "personality": "Warm en gestructureerd; ziet eerst de open issues.", "color": "#f0a878", "x": -4.1, "z": -2.9},
    "noah": {"id": "noah", "name": "Noah", "title": "Developer · Supa",
             "role": "Ontwikkelt aan Supa", "project_id": "supa",
             "personality": "Pragmatisch, technisch en direct.", "color": "#78b8d6", "x": 0.15, "z": -2.9},
    "lina": {"id": "lina", "name": "Lina", "title": "QA · LightUp",
             "role": "Valideert LightUp en helpt bugs oplossen", "project_id": "lightup",
             "personality": "Kritisch, precies en security-minded.", "color": "#b6a0e5", "x": 4.1, "z": -2.9},
}
STATUSES = {"queued", "dispatching", "dispatched", "claimed", "running", "verifying",
            "done", "failed", "dropped", "attention", "waiting_zcloud"}
MAX_ACTIVE = 12


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class OfficeConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB), timeout=8, isolation_level=None, factory=OfficeConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=8000")
    db.execute("PRAGMA journal_mode=WAL")
    return db


def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, employee TEXT NOT NULL, kind TEXT NOT NULL,
            instruction TEXT NOT NULL, status TEXT NOT NULL,
            output TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            remote_queue_id TEXT NOT NULL DEFAULT '',
            remote_status TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status,created_at);
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee TEXT NOT NULL, role TEXT NOT NULL,
            content TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """)
        columns = {r["name"] for r in db.execute("PRAGMA table_info(jobs)")}
        for col in ("remote_queue_id", "remote_status"):
            if col not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN " + col + " TEXT NOT NULL DEFAULT ''")
        db.execute("UPDATE jobs SET status='queued' WHERE status='dispatching' AND remote_queue_id=''")
        # Do NOT replay old Codex jobs: they may already have mutated code.
        db.execute("UPDATE jobs SET status='interrupted',error='Oud Codex-jobtype vereist handmatige verificatie.' WHERE status='running' AND remote_queue_id=''")
        db.execute("UPDATE jobs SET status='queued' WHERE status='waiting_zcloud' AND remote_queue_id=''")
        # Dispatched jobs are idempotent by remote_queue_id; reconciliation is read-only.
        features.init(db)
        game.init(db)
        staff.init(db)
        control.init(db)
        experts.init(db)
    staff.load(sys.modules[__name__])


def zcloud(path: str, payload: dict | None = None, timeout: float = 3.5, actor: str = "") -> dict:
    url = ZCLOUD_URL + path
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if actor:  # shows up in zCloud's config audit trail, next to dashboard changes
        headers["X-ZCloud-Actor"] = actor
    request = Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            doc = json.load(response)
    except HTTPError as exc:
        message = "zCloud weigerde de opdracht"
        try:
            err = json.loads(exc.read(4096))
            message = str(err.get("error") or message)
        except (ValueError, UnicodeError):
            pass
        raise RuntimeError(message[:260]) from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise ConnectionError("zCloud op localhost tijdelijk niet bereikbaar") from exc
    if not isinstance(doc, dict):
        raise RuntimeError("Ongeldig antwoord van zCloud")
    return doc


_HEALTH_LOCK = threading.Lock()
_HEALTH_CACHE = {"expires": 0.0, "value": {}}


def zcloud_health() -> dict:
    # /api/runner-live can take >3 seconds on a busy VPS. Cache its
    # authenticated loopback read so public health polling cannot hammer zCloud.
    with _HEALTH_LOCK:
        now = time.monotonic()
        if now < _HEALTH_CACHE["expires"]:
            return dict(_HEALTH_CACHE["value"])
        try:
            status = zcloud("/api/runner-live", timeout=8)
            workers = status.get("chatgpt_runners") or {}
            result = {"zcloud_online": True, "chatgpt_worker_data": bool(workers),
                      "firefox": (status.get("chatgpt_firefox") or {}).get("active", False)}
        except (RuntimeError, ConnectionError, ValueError):
            result = {"zcloud_online": False, "chatgpt_worker_data": False, "firefox": False}
        _HEALTH_CACHE["value"] = result
        _HEALTH_CACHE["expires"] = time.monotonic() + 20
        return dict(result)


def enqueue(employee: str, kind: str, instruction: str) -> dict:
    if employee not in features.PROFILE_IDS:
        raise ValueError("Onbekende medewerker")
    if kind not in ("task", "chat"):
        raise ValueError("Onbekend opdrachttype")
    instruction = str(instruction or "").strip()
    if not 2 <= len(instruction) <= 2000:
        raise ValueError("Schrijf een opdracht van 2–2000 tekens")
    if kind != "task":
        raise ValueError("Vrije gesprekken worden niet door zCloud gerelayd. Gebruik 'Open ChatGPT-chat' voor gesprekken of 'Geef werkopdracht' voor werk.")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        active = db.execute("SELECT COUNT(*) FROM jobs WHERE status NOT IN ('done','failed','dropped','interrupted')").fetchone()[0]
        if active >= MAX_ACTIVE:
            raise ValueError("Er staan al 12 opdrachten open. Rond er eerst een aantal af.")
        ident, now = uuid.uuid4().hex, timestamp()
        db.execute("INSERT INTO jobs(id,employee,kind,instruction,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                   (ident, employee, kind, instruction, "queued", now, now))
        db.execute("INSERT INTO messages(employee,role,content,created_at) VALUES (?,?,?,?)",
                   (employee, "user", instruction, now))
        acknowledgement = "Opdracht ontvangen en opgeslagen. De zCloud-worker pakt deze op zodra er capaciteit is; je hoeft dit venster niet open te houden."
        db.execute("INSERT INTO messages(employee,role,content,created_at) VALUES (?,?,?,?)", (employee, "system", acknowledgement, now))
        db.execute("COMMIT")
    WAKE.set()
    return {**get_job(ident), "acknowledgement": acknowledgement}


def get_job(ident: str) -> dict | None:
    with connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (ident,)).fetchone()
        return dict(row) if row else None


def list_jobs(limit: int = 30) -> list:
    with connect() as db:
        return [dict(x) for x in db.execute("SELECT * FROM jobs ORDER BY created_at DESC,id DESC LIMIT ?", (max(1,min(limit,50)),))]


def retry(ident: str) -> dict | None:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute("UPDATE jobs SET status='queued',error='',updated_at=? WHERE id=? AND status='failed' AND remote_queue_id=''",
                            (timestamp(),ident))
        db.execute("COMMIT")
        return get_job(ident) if cursor.rowcount else None


def office_queue_id(job: dict) -> str:
    return "office-" + PERSONAS[job["employee"]]["project_id"] + "-" + job["id"]


def build_portfolio_payload(job: dict) -> dict:
    persona = PERSONAS[job["employee"]]
    instruction = job["instruction"]
    # This is a real zCloud write/build queue, not a general chat interface.
    # Fail closed on obviously conversation-only messages.
    first = instruction.lower().strip().split(" ", 1)[0].strip(".,!?;:")
    if first in ("hey", "hoi", "hallo", "hoe", "wat", "waar", "waarom", "wie", "wanneer"):
        raise ValueError("Dit klinkt als een gesprek. Open de bestaande ChatGPT-chat; de werkqueue is alleen voor uitvoerbare taken.")
    title = "Implement office-opdracht: " + instruction.replace("\n", " ")[:220]
    criteria = (
        "Office opdracht van de eigenaar voor " + persona["name"] + " (" + persona["title"] + "). "
        "Bestaande open issues/PR's gaan voor nieuw werk; probeer een veilige concrete stap. "
        "Controleer eigen projectcontext in Notion en GitHub en voer materieel uitvoerbaar werk uit. "
         "Vereist: een werkelijk resultaat passend bij de opdracht: code, tests, slides of ander gevraagd bestand, met controleerbaar bewijs. Geen verzonnen afronding. "
        "Geen productie-, secret- of security-scope wijzigingen zonder afzonderlijke toestemming. "
        "Gebruikersinstructie:\n" + instruction
    )
    return {"action":"enqueue","project_id":persona["project_id"],"priority":"P3",
            "title":title,"completion_criteria":criteria,"queue_id":office_queue_id(job)}


def dispatch_once() -> bool:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM jobs WHERE status='queued' AND remote_queue_id='' ORDER BY created_at,id LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE jobs SET status='dispatching',attempts=attempts+1,updated_at=? WHERE id=?",
                       (timestamp(),row["id"]))
        db.execute("COMMIT")
    if not row: return False
    job = dict(row)
    try:
        remote = zcloud("/api/portfolio-queue", build_portfolio_payload(job))
        item = remote.get("item") or remote.get("result") or {}
        remote_id = str(item.get("queue_id") or office_queue_id(job))
        status = str(item.get("status") or "queued")
        with connect() as db:
            db.execute("UPDATE jobs SET status='dispatched',remote_queue_id=?,remote_status=?,updated_at=?,error='' WHERE id=? AND status='dispatching'",
                       (remote_id,status,timestamp(),job["id"]))
    except ValueError as exc:
        with connect() as db:
            db.execute("UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=? AND status='dispatching'",
                       (str(exc)[:450],timestamp(),job["id"]))
            game.on_local_failure(db, job, exc)
    except (RuntimeError,ConnectionError) as exc:
        with connect() as db:
            db.execute("UPDATE jobs SET status='queued',error=?,updated_at=? WHERE id=? AND status='dispatching'",
                       (str(exc)[:450],timestamp(),job["id"]))
        return False
    return True


def sync_remote() -> None:
    with connect() as db:
        count = db.execute("SELECT COUNT(*) FROM jobs WHERE remote_queue_id<>'' AND status NOT IN ('done','failed','dropped')").fetchone()[0]
    if not count: return
    items = zcloud("/api/portfolio-queue?all=1",timeout=7).get("items",[])
    lookup = {str(x.get("queue_id")): x for x in items if isinstance(x,dict)}
    with connect() as db:
        for job in db.execute("SELECT id,employee,remote_queue_id,status FROM jobs WHERE remote_queue_id<>'' AND status NOT IN ('done','failed','dropped')").fetchall():
            item = lookup.get(job["remote_queue_id"])
            if item is None: continue
            remote_status = str(item.get("status") or "queued")
            mapped = remote_status if remote_status in ("claimed","running","verifying","done","dropped") else "dispatched"
            # zCloud queue.done is strong evidence only where zCloud's existing finish-gate
            # validated it. Do not call this an avatar conversation transcript.
            msg = ("zCloud markeerde de projectopdracht als afgerond." if mapped == "done" else
                   "zCloud heeft deze opdracht laten vervallen." if mapped == "dropped" else "")
            db.execute("UPDATE jobs SET status=?,remote_status=?,output=?,updated_at=? WHERE id=?",
                       (mapped,remote_status,msg,timestamp(),job["id"]))
            game.on_remote_item(db, job, item, mapped)
            if mapped in ("done","dropped") and job["status"] not in ("done","dropped"):
                db.execute("INSERT INTO messages(employee,role,content,created_at) VALUES (?,?,?,?)",
                           (job["employee"],"system",msg,timestamp()))


def work_forever(stop: threading.Event | None = None) -> None:
    while stop is None or not stop.is_set():
        try:
            sync_remote()
            # Avoid starvation of retries when zCloud is down.
            dispatch_once()
            game.queue_summaries(sys.modules[__name__])
        except Exception:
            # Keep service available and retry after backoff; never fabricate DONE.
            pass
        for step in (control.sync_changes, experts.auto_review):
            try:
                step(sys.modules[__name__])
            except Exception:
                pass
        if stop:
            stop.wait(8)
        else:
            WAKE.wait(2)
            WAKE.clear()


def employee_chat_links() -> dict:
    """Return account-owned existing ChatGPT thread URLs from local zCloud config."""
    data = zcloud("/api/runner-targets",timeout=4)
    projects = data.get("projects") or {}
    links={}
    for employee, profile in PERSONAS.items():
        pid=profile["project_id"]
        matching=[v for v in projects.values() if isinstance(v,dict) and v.get("base_project_id")==pid and v.get("provider")=="chatgpt"]
        conversation = next((str(v.get("conversation_id")) for v in matching if v.get("conversation_id")), "")
        # IDs originate only from local zCloud, not arbitrary client URLs.
        if conversation and all(x in "0123456789abcdef-" for x in conversation.lower()) and len(conversation)<80:
            links[employee]="https://chatgpt.com/c/"+conversation
        else:
            links[employee]="https://chatgpt.com/"
    return links



_PROJECT_LOCK = threading.Lock()
_PROJECT_CACHE = {"expires": 0.0, "value": {}}
def office_projects():
    """Only expose the fields needed for visual monitoring, never secrets or raw runner config."""
    with _PROJECT_LOCK:
        now = time.monotonic()
        if now < _PROJECT_CACHE["expires"]:
            return dict(_PROJECT_CACHE["value"])
        try:
            data = zcloud("/api/status", timeout=8)
            rows = data.get("projects", [])
            projects = []
            if not isinstance(rows, list):
                raise ValueError("Invalid project snapshot")
            for item in rows[:30]:
                if not isinstance(item, dict):
                    continue
                projects.append({key: item.get(key) for key in ("id", "name", "status", "next", "progress", "source_status")})
            value = {"online": True, "projects": projects, "sample_time": data.get("time"), "checked_at": timestamp()}
        except (ConnectionError, RuntimeError, ValueError):
            value = {"online": False, "projects": [], "checked_at": timestamp()}
        _PROJECT_CACHE.update(expires=time.monotonic()+20, value=value)
        return dict(value)

class Handler(BaseHTTPRequestHandler):
    server_version = "VirtualOffice/0.3"

    def json_reply(self,code:int,obj:object)->None:
        payload=json.dumps(obj,ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(payload)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Referrer-Policy","no-referrer")
        self.end_headers()
        self.wfile.write(payload)

    def authorized(self)->bool:
        if not TOKEN:
            return True
        supplied = self.headers.get("X-Office-Token", "")
        # HTTP headers are decoded as Latin-1 by BaseHTTPRequestHandler.
        # str compare_digest() raises TypeError for non-ASCII values, which
        # previously closed the connection instead of sending JSON 401.
        # Compare encoded bytes and bound untrusted header size.
        if len(supplied) > 512:
            return False
        return hmac.compare_digest(TOKEN.encode("utf-8"), supplied.encode("utf-8"))

    def local_only(self)->bool:
        if self.client_address[0] in ("127.0.0.1","::1"):
            return True
        self.json_reply(403,{"error":"Workers aanpassen kan alleen via de SSH-tunnel (de game) of op de VPS zelf."})
        return False

    def require_auth(self)->bool:
        if not self.authorized():
            self.json_reply(401,{"error":"Voer de Office-toegangscode in. Werkopdrachten zijn niet publiek."})
            return False
        return True

    def do_GET(self)->None:
        url=urlsplit(self.path)
        static = {"/office-relay.user.js": ("office-relay.user.js", "text/javascript; charset=utf-8")}
        for module in MODULES:
            static["/" + module] = (module, "text/javascript; charset=utf-8")
        cache = "no-cache"
        vendor = VENDOR_PATH.fullmatch(url.path)
        if vendor:
            # three.js is served from this server (not a CDN): offline-capable, pinned and testable.
            target = (ROOT / url.path.lstrip("/")).resolve()
            if not target.is_file() or ROOT / "vendor" not in target.parents:
                return self.json_reply(404, {"error": "Niet gevonden"})
            static[url.path] = (str(target.relative_to(ROOT)), "text/javascript; charset=utf-8" if url.path.endswith(".js") else "text/plain; charset=utf-8")
            cache = "public, max-age=86400"
        if url.path in static:
            filename, mime = static[url.path]
            if not (ROOT / filename).is_file():
                return self.json_reply(404, {"error": "Niet gevonden"})
            body = (ROOT / filename).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path in ("/","/index.html"):
            body=(ROOT/"index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(body)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Security-Policy","default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers();self.wfile.write(body);return
        if url.path=="/api/health":
            self.json_reply(200,{"online":True,"auth_required":bool(TOKEN),
                 "mode":"zcloud_chatgpt_chats","version":"0.3-penthouse","release":(ROOT/"RELEASE").read_text().strip() if (ROOT/"RELEASE").is_file() else "development","tasks_enabled":True, **zcloud_health()})
            return
        if not self.require_auth():return
        if url.path=="/api/employees":
            try:links=employee_chat_links()
            except (ConnectionError,RuntimeError,ValueError):links={k:"https://chatgpt.com/" for k in PERSONAS}
            self.json_reply(200,{"employees":[v for v in PERSONAS.values() if v.get("active",True)],"chat_links":links})
        elif url.path=="/api/staff":
            self.json_reply(200,{"staff":staff.list_staff(sys.modules[__name__]),"max":staff.MAX_STAFF})
        elif url.path in ("/api/control/state","/api/control/log"):
            if not self.local_only():return
            core=sys.modules[__name__]
            if url.path=="/api/control/log":self.json_reply(200,{"log":control.recent_log(core)})
            else:
                fresh=parse_qs(url.query).get("fresh",["0"])[0]=="1"
                self.json_reply(200,control.overview(core,fresh=fresh))
        elif url.path=="/api/proposals":
            status=parse_qs(url.query).get("status",["pending"])[0]
            if status not in ("pending","applied","rejected","failed"):return self.json_reply(400,{"error":"Ongeldige status"})
            self.json_reply(200,experts.list_proposals(sys.modules[__name__],status))
        elif url.path=="/api/projects":
            self.json_reply(200, office_projects())
        elif url.path=="/api/events":
            query=parse_qs(url.query)
            try:self.json_reply(200, game.list_events(sys.modules[__name__], query.get("since",["0"])[0], query.get("employee",[""])[0]))
            except ValueError as exc:self.json_reply(400,{"error":str(exc)})
        elif url.path.startswith("/api/jobs/") and url.path.endswith("/summary"):
            result=game.summary_for(sys.modules[__name__], url.path.split("/")[-2])
            self.json_reply(200 if result else 404, result or {"error":"Niet gevonden"})
        elif url.path=="/api/decks":
            self.json_reply(200, features.deck_list(sys.modules[__name__]))
        elif url.path.startswith("/api/decks/"):
            deck = features.get_deck(sys.modules[__name__], url.path.rsplit("/", 1)[-1])
            self.json_reply(200 if deck else 404, deck or {"error":"Niet gevonden"})
        elif url.path=="/api/relay/status":
            self.json_reply(200, features.relay_status(sys.modules[__name__]))
        elif url.path=="/api/jobs":
            self.json_reply(200,{"jobs":list_jobs()})
        elif url.path.startswith("/api/jobs/"):
            job=get_job(url.path.rsplit("/",1)[-1])
            self.json_reply(200 if job else 404,job or {"error":"Niet gevonden"})
        elif url.path=="/api/messages":
            employee=parse_qs(url.query).get("employee",[""])[0]
            if employee not in PERSONAS:return self.json_reply(400,{"error":"Onbekende medewerker"})
            with connect() as db:
                rows=[dict(r) for r in db.execute("SELECT id,role,content,created_at FROM messages WHERE employee=? ORDER BY id DESC LIMIT 30",(employee,))]
            self.json_reply(200,{"messages":list(reversed(rows))})
        else:self.json_reply(404,{"error":"Niet gevonden"})

    def do_POST(self)->None:
        url=urlsplit(self.path)
        if not self.require_auth():return
        length=self.headers.get("Content-Length","")
        if self.headers.get("Content-Type","").split(";")[0].strip()!="application/json":
            return self.json_reply(415,{"error":"Alleen JSON"})
        if not length.isdigit() or int(length)>100000:
            return self.json_reply(413,{"error":"Ongeldige aanvraaglengte"})
        try:
            body=json.loads(self.rfile.read(int(length)))
            if not isinstance(body,dict):raise ValueError("JSON object vereist")
            if url.path=="/api/jobs":
                self.json_reply(201,enqueue(body.get("employee",""),body.get("kind","task"),body.get("instruction","")))
            elif url.path=="/api/staff":
                self.json_reply(201, staff.upsert(sys.modules[__name__], body))
            elif url.path in ("/api/control/prepare","/api/control/confirm") or (url.path.startswith("/api/proposals/") and url.path.rsplit("/",1)[-1] in ("approve","reject")):
                # Changing production workers is only possible from this machine / through the SSH tunnel.
                if not self.local_only():return
                core=sys.modules[__name__]
                if url.path=="/api/control/prepare":self.json_reply(200,control.prepare(core,body))
                elif url.path=="/api/control/confirm":self.json_reply(200,control.confirm(core,body.get("pending_id")))
                else:self.json_reply(200,experts.decide(core,url.path.split("/")[-2],url.path.endswith("/approve")))
            elif url.path=="/api/experts/review":
                self.json_reply(201, experts.request_review(sys.modules[__name__], body))
            elif url.path=="/api/decks/request":
                self.json_reply(201, game.request_deck(sys.modules[__name__], body))
            elif url.path=="/api/decks":
                self.json_reply(201, features.save_deck(sys.modules[__name__], body))
            elif url.path=="/api/workstation":
                self.json_reply(200, features.workstation(sys.modules[__name__], body.get("command")))
            elif url.path=="/api/chat":
                self.json_reply(201, features.chat_enqueue(sys.modules[__name__], body))
            elif url.path=="/api/relay/poll":
                self.json_reply(200, features.relay_poll(sys.modules[__name__], body))
            elif url.path=="/api/relay/reply":
                self.json_reply(200, features.relay_reply(sys.modules[__name__], body))
            elif url.path.startswith("/api/jobs/") and url.path.endswith("/retry"):
                ident=url.path.split("/")[-2]
                job=retry(ident)
                self.json_reply(200 if job else 409,job or {"error":"Opdracht kan niet opnieuw worden gestart"})
            else:self.json_reply(404,{"error":"Niet gevonden"})
        except (ValueError,TypeError,KeyError) as exc:self.json_reply(400,{"error":str(exc)})

    def log_message(self,fmt:str,*args:object)->None:
        pass


def main()->None:
    p=argparse.ArgumentParser()
    p.add_argument("mode",nargs="?",default="all",choices=["all","server","worker"])
    args=p.parse_args()
    if BIND not in ("127.0.0.1","localhost","::1") and len(TOKEN)<24:
        p.error("Remote bind vereist OFFICE_ACCESS_TOKEN van minimaal 24 tekens")
    init_db()
    if args.mode=="worker":return work_forever()
    if args.mode=="all":
        threading.Thread(target=work_forever,name="office-zcloud-dispatcher",daemon=True).start()
    with ThreadingHTTPServer((BIND,PORT),Handler) as server:
        print("Virtual Office on http://"+BIND+":"+str(PORT),flush=True)
        server.serve_forever()


if __name__=="__main__":
    main()
