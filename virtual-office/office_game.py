"""Game-facing Office features: completion events, grounded spoken summaries, employee-made decks.

Design rules (same honesty contract as the rest of the Office):
* An event is only created from something zCloud or the relay really reported.
* A spoken summary is built from the worker's own completion *evidence* stored in
  zCloud's queue. If the ChatGPT relay is online it only rephrases that evidence
  into two short sentences; if not, a deterministic extract of the same evidence is
  used. Nothing is invented and no result is claimed without evidence.
* Decks are requested from an employee through the explicit ChatGPT relay and are
  accepted only if the reply is valid, bounded JSON.
"""
import calendar
import json
import re
import time
import uuid

import office_features as features

SUMMARY_STALE_SECONDS = 600
MAX_OPEN_GAME_REQUESTS = 2

SUMMARY_PROMPT = (
    "Vat het afgeronde werk hieronder samen voor een gesproken update aan je baas.\n"
    "Regels: maximaal 2 korte zinnen, spreektaal in het Nederlands, geen opsomming, geen markdown, "
    "geen emoji, noem alleen wat in het bewijs staat en verzin niets.\n\n"
    "Opdracht: {instruction}\n\nBewijs van de worker:\n{evidence}"
)

DECK_PROMPT = (
    "Maak een presentatie over het onderwerp hieronder, om zelf te presenteren aan je baas.\n"
    "Antwoord UITSLUITEND met één JSON-object, zonder uitleg en zonder markdown-codeblok, in dit formaat:\n"
    '{{"title": "...", "slides": [{{"title": "...", "body": "punt 1\\npunt 2", '
    '"layout": "title|bullets|stat|quote|split", "accent": "gold|teal|rose|violet|slate", '
    '"stat": {{"value": "42%", "label": "..."}}, "visual": "none|bars|steps", '
    '"data": [{{"label": "...", "value": 3}}], "speaker": "wat je hardop zegt bij deze slide"}}]}}\n'
    "Regels: 5 tot 8 slides; korte titels; maximaal 5 punten per slide; stat, data en quote alleen "
    "invullen als de waarde in het onderwerp of bewijs staat, verzin geen cijfers; 'speaker' is "
    "1 tot 3 natuurlijke zinnen in het Nederlands.\n\nOnderwerp: {topic}"
)

ACCENTS = {"gold", "teal", "rose", "violet", "slate"}
LAYOUTS = {"title", "bullets", "stat", "quote", "split"}
VISUALS = {"none", "bars", "steps"}


def init(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS office_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, employee TEXT NOT NULL, kind TEXT NOT NULL,
      ref TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_office_events_once ON office_events(kind, ref);
    """)
    for table, columns in {
        "jobs": {"evidence": "TEXT NOT NULL DEFAULT ''", "blocker": "TEXT NOT NULL DEFAULT ''",
                 "summary": "TEXT NOT NULL DEFAULT ''", "summary_state": "TEXT NOT NULL DEFAULT ''",
                 "summary_at": "TEXT NOT NULL DEFAULT ''"},
        "office_chat": {"kind": "TEXT NOT NULL DEFAULT 'chat'", "ref": "TEXT NOT NULL DEFAULT ''"},
    }.items():
        present = {r[1] for r in db.execute("PRAGMA table_info(" + table + ")")}
        for name, ddl in columns.items():
            if name not in present:
                db.execute("ALTER TABLE " + table + " ADD COLUMN " + name + " " + ddl)


# ---------------------------------------------------------------- events
def add_event(db, employee, kind, ref, payload=None):
    db.execute("INSERT OR IGNORE INTO office_events(employee,kind,ref,payload,created_at) VALUES(?,?,?,?,?)",
               (employee, kind, str(ref), json.dumps(payload or {}, ensure_ascii=False),
                time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())))


def list_events(core, since=0, employee=""):
    try:
        since = max(0, int(since))
    except (TypeError, ValueError):
        raise ValueError("Ongeldige 'since'")
    with core.connect() as db:
        if employee:
            if employee not in features.PROFILE_IDS:
                raise ValueError("Onbekende medewerker")
            rows = db.execute("SELECT * FROM office_events WHERE id>? AND employee=? ORDER BY id LIMIT 100",
                              (since, employee)).fetchall()
        else:
            rows = db.execute("SELECT * FROM office_events WHERE id>? ORDER BY id LIMIT 100", (since,)).fetchall()
        last = db.execute("SELECT COALESCE(MAX(id),0) FROM office_events").fetchone()[0]
    events = [{"id": r["id"], "employee": r["employee"], "kind": r["kind"], "ref": r["ref"],
               "payload": json.loads(r["payload"] or "{}"), "created_at": r["created_at"]} for r in rows]
    return {"events": events, "last_id": last}


# ----------------------------------------------------- completion hooks
def on_remote_item(db, job, item, mapped):
    """Called by sync_remote after a job row was updated from zCloud's real queue item."""
    evidence = str(item.get("evidence") or "")[:4000]
    blocker = str(item.get("blocker") or "")[:600]
    db.execute("UPDATE jobs SET evidence=?,blocker=? WHERE id=?", (evidence, blocker, job["id"]))
    if mapped == "done":
        add_event(db, job["employee"], "job_done", job["id"], {"has_evidence": bool(evidence)})
    elif mapped == "dropped":
        add_event(db, job["employee"], "job_failed", job["id"], {"reason": "dropped"})


def on_local_failure(db, job, reason):
    add_event(db, job["employee"], "job_failed", job["id"], {"reason": str(reason)[:200]})


# ------------------------------------------------------------- summaries
def _sentences(text, limit=2, cap=260):
    text = re.sub(r"\s+", " ", re.sub(r"[`*_#>]+", "", text or "")).strip()
    parts = re.split(r"(?<=[.!?])\s+", text)
    out = " ".join(parts[:limit]).strip()
    return out if len(out) <= cap else out[:cap - 1].rstrip() + "…"


def fallback_summary(job):
    status, evidence, blocker = job["status"], job["evidence"], job["blocker"]
    if status in ("failed", "dropped", "interrupted"):
        reason = job["error"] or blocker
        return "Er is aandacht nodig bij deze opdracht." + (" " + _sentences(reason, 1, 180) if reason else "")
    if evidence:
        return _sentences(evidence)
    return "zCloud markeerde deze opdracht als afgerond, maar leverde geen toelichting mee."


def summary_for(core, job_id):
    job = core.get_job(job_id)
    if not job:
        return None
    ready = bool(job.get("summary")) and job.get("summary_state") == "ready"
    state = job.get("summary_state") or ""
    stale = state == "requested" and (time.time() - _epoch(job.get("summary_at"))) > SUMMARY_STALE_SECONDS
    if ready:
        text, source = job["summary"], "chatgpt"
    else:
        text, source = fallback_summary(job), "evidence"
    return {"job_id": job_id, "employee": job["employee"], "status": job["status"], "text": text,
            "source": source, "pending": state == "requested" and not stale and not ready}


def _epoch(stamp):
    try:
        return float(calendar.timegm(time.strptime(str(stamp)[:19], "%Y-%m-%dT%H:%M:%S")))
    except (ValueError, TypeError):
        return 0.0


def _relay_online(db, employee):
    row = db.execute("SELECT heartbeat FROM office_relay WHERE employee=?", (employee,)).fetchone()
    return bool(row) and time.time() - row["heartbeat"] < features.RELAY_HEARTBEAT_TIMEOUT


def queue_summaries(core):
    """Ask the relay to phrase finished work as two spoken sentences, based on stored evidence only."""
    with core.connect() as db:
        rows = db.execute("SELECT * FROM jobs WHERE status='done' AND summary_state='' AND evidence<>'' "
                          "ORDER BY updated_at LIMIT 3").fetchall()
        for job in rows:
            if not _relay_online(db, job["employee"]):
                continue
            busy = db.execute("SELECT COUNT(*) FROM office_chat WHERE employee=? AND status IN ('queued','claimed')",
                              (job["employee"],)).fetchone()[0]
            if busy >= MAX_OPEN_GAME_REQUESTS:
                continue
            now = core.timestamp()
            prompt = SUMMARY_PROMPT.format(instruction=job["instruction"][:400].replace("\n", " "),
                                           evidence=job["evidence"][:1200])
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO office_chat(id,employee,instruction,status,created_at,updated_at,kind,ref) "
                       "VALUES(?,?,?,?,?,?,?,?)",
                       (uuid.uuid4().hex, job["employee"], prompt, "queued", now, now, "summary", job["id"]))
            db.execute("UPDATE jobs SET summary_state='requested',summary_at=? WHERE id=? AND summary_state=''",
                       (now, job["id"]))
            db.execute("COMMIT")


def clean_summary(reply):
    text = re.sub(r"```.*?```", " ", reply or "", flags=re.S)
    text = re.sub(r"[`*_#>\-•]+", " ", text)
    return _sentences(text, limit=2, cap=320)


# ------------------------------------------------------------------ decks
def request_deck(core, body):
    employee = body.get("employee")
    if employee not in features.PROFILE_IDS:
        raise ValueError("Onbekende medewerker")
    topic = features.bounded_text(body.get("topic"), "Onderwerp", 1200, 3)
    ident, now = uuid.uuid4().hex, core.timestamp()
    with core.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            if not _relay_online(db, employee):
                raise ValueError("Geen ChatGPT Relay verbonden voor deze medewerker, dus die kan nu geen presentatie maken.")
            busy = db.execute("SELECT COUNT(*) FROM office_chat WHERE employee=? AND kind='deck' "
                              "AND status IN ('queued','claimed')", (employee,)).fetchone()[0]
            if busy:
                raise ValueError("Deze medewerker is al een presentatie aan het maken.")
            db.execute("INSERT INTO office_chat(id,employee,instruction,status,created_at,updated_at,kind,ref) "
                       "VALUES(?,?,?,?,?,?,?,?)",
                       (ident, employee, DECK_PROMPT.format(topic=topic), "queued", now, now, "deck", ""))
            db.execute("INSERT INTO messages(employee,role,content,created_at) VALUES(?,?,?,?)",
                       (employee, "user", "Maak een presentatie: " + topic, now))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return {"id": ident, "status": "queued", "acknowledgement": "Prima, ik ga aan de presentatie werken."}


def parse_deck_reply(reply):
    text = (reply or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Geen JSON-presentatie in het antwoord")
    try:
        doc = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("Presentatie-JSON is ongeldig: " + exc.msg) from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("slides"), list):
        raise ValueError("Presentatie mist een slides-lijst")
    slides = doc["slides"][:20]
    if not slides:
        raise ValueError("Presentatie bevat geen slides")
    return {"title": str(doc.get("title") or slides[0].get("title") or "Presentatie")[:120], "slides": slides}


def after_reply(core, chat, state, reply):
    """Runs after the relay_reply transaction has committed. `chat` is the office_chat row as dict."""
    kind = chat.get("kind") or "chat"
    if kind == "summary":
        text = clean_summary(reply) if state == "done" else ""
        with core.connect() as db:
            if text:
                db.execute("UPDATE jobs SET summary=?,summary_state='ready',summary_at=? WHERE id=?",
                           (text, core.timestamp(), chat["ref"]))
                add_event(db, chat["employee"], "summary_ready", chat["ref"])
            else:
                db.execute("UPDATE jobs SET summary_state='fallback' WHERE id=? AND summary_state='requested'",
                           (chat["ref"],))
    elif kind == "review":
        import office_experts  # lazy: office_experts imports office_features
        office_experts.after_reply(core, chat, state, reply)
    elif kind == "deck":
        with core.connect() as db:
            try:
                if state != "done":
                    raise ValueError("De relay meldde dat er aandacht nodig is")
                parsed = parse_deck_reply(reply)
                saved = features.save_deck(core, parsed)
                db.execute("INSERT INTO messages(employee,role,content,created_at) VALUES(?,?,?,?)",
                           (chat["employee"], "system", "Presentatie klaar: " + saved["title"] +
                            " (" + str(len(saved["slides"])) + " slides).", core.timestamp()))
                add_event(db, chat["employee"], "deck_ready", saved["id"], {"title": saved["title"], "slides": len(saved["slides"])})
            except ValueError as exc:
                db.execute("INSERT INTO messages(employee,role,content,created_at) VALUES(?,?,?,?)",
                           (chat["employee"], "system", "De presentatie kon niet worden gebruikt: " + str(exc), core.timestamp()))
                add_event(db, chat["employee"], "deck_failed", chat["id"], {"reason": str(exc)[:200]})
