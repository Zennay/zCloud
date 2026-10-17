"""Experts that review the queue and the workers and *propose* better steering.

An expert (a ChatGPT conversation reached through the explicit relay) receives a factual digest of zCloud's
workers, project priorities and open queue. It answers with strict JSON proposals. Nothing is executed by the
expert: every proposal is validated against live zCloud state, stored as pending and only applied after the
owner approves it (voice "ja, doe maar" or the panel). Notion is read by the expert itself through its own
ChatGPT connection; the Office server holds no Notion credentials.
"""
import json
import os
import re
import time
import uuid

import office_control as control
import office_features as features

REVIEW_MINUTES = int(os.getenv("OFFICE_REVIEW_MINUTES", "180"))   # 0 disables automatic reviews
MAX_PROPOSALS = 5

REVIEW_PROMPT = (
    "Beoordeel als expert hoe de zCloud-workers en de queue beter aangestuurd kunnen worden, vanuit jouw "
    "specialisme ({focus}). Hieronder staat de actuele toestand zoals zCloud die meldt.\n"
    "Controleer waar mogelijk ook Notion (projectfase, open punten). Zeg eerlijk in 'notion' wat je wel of niet "
    "hebt kunnen raadplegen. Verzin geen status.\n\n{digest}\n\n{extra}"
    "Antwoord UITSLUITEND met één JSON-object, zonder uitleg en zonder markdown-codeblok:\n"
    '{{"summary": "max 2 gesproken zinnen: wat valt je op en wat stel je voor", "notion": "wat je in Notion zag of niet kon zien", '
    '"proposals": [ ... max 5 ... ]}}\n'
    "Een voorstel is een van:\n"
    '{{"action":"queue_add","project":"<project-id>","title":"<concrete uitvoerbare taak>","queue_priority":"P1|P2|P3","criteria":"<wanneer is het klaar>","why":"<reden>"}}\n'
    '{{"action":"queue_drop","queue_id":"<id uit de queue>","why":"<reden>"}}\n'
    '{{"action":"worker","worker":"<worker-id>","op":"start|pause|push|new_chat|drain","why":"<reden>"}}\n'
    '{{"action":"priority","project":"<project-id>","priority":"background|normal|high","why":"<reden>"}}\n'
    "Alleen voorstellen die de voortgang echt verbeteren; een lege lijst is prima. Gebruik alleen id's uit de toestand hierboven."
)


def init(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS office_proposals (
      id TEXT PRIMARY KEY, employee TEXT NOT NULL, review_id TEXT NOT NULL, action TEXT NOT NULL,
      summary TEXT NOT NULL, why TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
      detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, decided_at TEXT NOT NULL DEFAULT '');
    CREATE INDEX IF NOT EXISTS idx_office_proposals_status ON office_proposals(status, created_at);
    """)


def _online(db, employee):
    row = db.execute("SELECT heartbeat FROM office_relay WHERE employee=?", (employee,)).fetchone()
    return bool(row) and time.time() - row["heartbeat"] < features.RELAY_HEARTBEAT_TIMEOUT


def request_review(core, body, automatic=False):
    employee = body.get("employee")
    if employee not in features.PROFILE_IDS:
        raise ValueError("Onbekende medewerker")
    extra = str(body.get("question") or "").strip()[:600]
    state = control.overview(core)
    if not state["online"]:
        raise ValueError("zCloud is nu niet bereikbaar, dus er is niets om te beoordelen.")
    persona = core.PERSONAS[employee]
    prompt = REVIEW_PROMPT.format(focus=persona.get("focus") or persona.get("role") or "algemeen",
                                  digest=control.digest(state), extra=("Vraag van de eigenaar: " + extra + "\n\n") if extra else "")
    ident, now = uuid.uuid4().hex, core.timestamp()
    with core.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            if not _online(db, employee):
                raise ValueError("Geen ChatGPT Relay verbonden voor deze medewerker, dus die kan nu niet meekijken.")
            if db.execute("SELECT COUNT(*) FROM office_chat WHERE employee=? AND kind='review' AND status IN ('queued','claimed')", (employee,)).fetchone()[0]:
                raise ValueError("Deze medewerker kijkt al mee.")
            db.execute("INSERT INTO office_chat(id,employee,instruction,status,created_at,updated_at,kind,ref) VALUES(?,?,?,?,?,?,?,?)",
                       (ident, employee, prompt, "queued", now, now, "review", "auto" if automatic else "owner"))
            if not automatic:
                db.execute("INSERT INTO messages(employee,role,content,created_at) VALUES(?,?,?,?)",
                           (employee, "user", "Kijk mee met de queue en de workers." + ((" " + extra) if extra else ""), now))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return {"id": ident, "status": "queued", "acknowledgement": "Ik kijk even naar de queue en de workers."}


def auto_review(core):
    """Background: let one idle expert at a time look at the queue every REVIEW_MINUTES."""
    if REVIEW_MINUTES <= 0:
        return None
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - REVIEW_MINUTES * 60))
    with core.connect() as db:
        if db.execute("SELECT COUNT(*) FROM office_chat WHERE kind='review' AND status IN ('queued','claimed')").fetchone()[0]:
            return None
        if db.execute("SELECT COUNT(*) FROM office_proposals WHERE status='pending'").fetchone()[0]:
            return None
        candidates = []
        for employee in sorted(features.PROFILE_IDS):
            if not _online(db, employee):
                continue
            last = db.execute("SELECT MAX(created_at) FROM office_chat WHERE employee=? AND kind='review'", (employee,)).fetchone()[0]
            if not last or last < cutoff:
                candidates.append((last or "", employee))
    if not candidates:
        return None
    employee = sorted(candidates)[0][1]
    try:
        return request_review(core, {"employee": employee}, automatic=True)
    except ValueError:
        return None


# ------------------------------------------------------------- reply parsing
def parse_review(reply):
    text = (reply or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Geen JSON in het antwoord")
    try:
        doc = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("JSON is ongeldig: " + exc.msg) from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("proposals", []), list):
        raise ValueError("Voorstellen ontbreken of zijn ongeldig")
    spoken = re.sub(r"\s+", " ", re.sub(r"[`*_#>]+", "", str(doc.get("summary") or ""))).strip()[:400]
    return {"summary": spoken, "notion": str(doc.get("notion") or "")[:300], "proposals": doc.get("proposals", [])[:MAX_PROPOSALS]}


def after_reply(core, chat, state, reply):
    import office_game
    employee = chat["employee"]
    with core.connect() as db:
        try:
            if state != "done":
                raise ValueError("De relay meldde dat er aandacht nodig is")
            parsed = parse_review(reply)
        except ValueError as exc:
            db.execute("INSERT INTO messages(employee,role,content,created_at) VALUES(?,?,?,?)",
                       (employee, "system", "De beoordeling kon niet worden gebruikt: " + str(exc), core.timestamp()))
            office_game.add_event(db, employee, "review_failed", chat["id"], {"reason": str(exc)[:200]})
            return
    live = control.overview(core, fresh=True)
    stored, skipped = [], 0
    with core.connect() as db:
        existing = {json.loads(r["action"]).get("signature") for r in db.execute("SELECT action FROM office_proposals WHERE status='pending'")}
        for raw in parsed["proposals"]:
            if not isinstance(raw, dict):
                skipped += 1
                continue
            try:
                action, summary, risk = control.validate(core, raw, live)
            except ValueError:
                skipped += 1
                continue
            signature = json.dumps(action, sort_keys=True)
            if signature in existing:
                continue
            existing.add(signature)
            ident = uuid.uuid4().hex[:12]
            db.execute("INSERT INTO office_proposals(id,employee,review_id,action,summary,why,created_at) VALUES(?,?,?,?,?,?,?)",
                       (ident, employee, chat["id"], json.dumps({**action, "signature": signature}), summary,
                        re.sub(r"\s+", " ", str(raw.get("why") or ""))[:240], core.timestamp()))
            stored.append(ident)
        text = parsed["summary"] or ("Ik heb %d voorstel(len)." % len(stored))
        db.execute("INSERT INTO messages(employee,role,content,created_at) VALUES(?,?,?,?)",
                   (employee, "assistant", text + (" (%d voorstel(len) wachten op jouw akkoord.)" % len(stored) if stored else ""), core.timestamp()))
        office_game.add_event(db, employee, "review_ready", chat["id"],
                              {"proposals": len(stored), "skipped": skipped, "spoken": text, "notion": parsed["notion"]})


# --------------------------------------------------------------- decisions
def list_proposals(core, status="pending"):
    with core.connect() as db:
        rows = db.execute("SELECT id,employee,summary,why,status,detail,created_at FROM office_proposals WHERE status=? ORDER BY created_at LIMIT 30",
                          (status,)).fetchall()
    return {"proposals": [dict(r) for r in rows]}


def decide(core, ident, approve):
    with core.connect() as db:
        row = db.execute("SELECT * FROM office_proposals WHERE id=?", (str(ident or ""),)).fetchone()
        if not row:
            raise ValueError("Onbekend voorstel")
        if row["status"] != "pending":
            raise ValueError("Dit voorstel is al behandeld")
        if not approve:
            db.execute("UPDATE office_proposals SET status='rejected',decided_at=? WHERE id=?", (core.timestamp(), row["id"]))
            return {"id": row["id"], "status": "rejected"}
        action = json.loads(row["action"])
        action.pop("signature", None)
    try:
        result = control.execute(core, action, row["summary"], source="expert:" + row["employee"])
        status, detail = "applied", result.get("detail", "")
    except ValueError as exc:
        status, detail = "failed", str(exc)[:240]
    with core.connect() as db:
        import office_game
        db.execute("UPDATE office_proposals SET status=?,detail=?,decided_at=? WHERE id=?", (status, detail, core.timestamp(), row["id"]))
        office_game.add_event(db, row["employee"], "proposal_" + status, row["id"], {"summary": row["summary"], "detail": detail})
    return {"id": row["id"], "status": status, "detail": detail, "summary": row["summary"]}
