"""Worker control room: read and change zCloud workers, project priority and the work queue from the game.

There is exactly one source of truth: zCloud itself. Every read goes to zCloud's own loopback API and every
change goes through the same endpoints the dashboard uses (``X-ZCloud-Actor: virtual-office`` marks it in
zCloud's config audit). So a change made in the dashboard shows up here on the next poll, and a change made here
shows up in the dashboard. Nothing is cached as truth.

Safety rules:
* Every change is a two-step *prepare -> confirm* (or an explicit owner approval of an expert proposal).
* Only allow-listed operations exist; ids are validated against what zCloud currently reports.
* 'turbo' priority, project layout and feature flags stay dashboard-only.
"""
import hashlib
import json
import re
import threading
import time
import uuid

ACTOR = "virtual-office"
WORKER_OPS = {"start": "starten", "pause": "pauzeren", "new_chat": "nieuwe chat openen",
              "push": "een duwtje geven", "drain": "laten uitlopen (drain)"}
PROJECT_PRIORITIES = {"background": "achtergrond", "normal": "normaal", "high": "hoog"}   # no 'turbo'
QUEUE_PRIORITIES = ("P0", "P1", "P2", "P3")
ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
PENDING_TTL = 90
_PENDING = {}
_LOCK = threading.Lock()
_CACHE = {"expires": 0.0, "value": None}


def init(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS office_control_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, source TEXT NOT NULL,
      action TEXT NOT NULL, summary TEXT NOT NULL, ok INTEGER NOT NULL, detail TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS office_kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)


# ------------------------------------------------------------------ reading
def _safe(core, path, timeout=6):
    try:
        return core.zcloud(path, timeout=timeout), ""
    except (ConnectionError, RuntimeError, ValueError) as exc:
        return {}, str(exc)[:160]


def overview(core, fresh=False):
    with _LOCK:
        if not fresh and _CACHE["value"] is not None and time.monotonic() < _CACHE["expires"]:
            return json.loads(json.dumps(_CACHE["value"]))
    targets, e1 = _safe(core, "/api/runner-targets")
    status, e2 = _safe(core, "/api/status", 8)
    queue, e3 = _safe(core, "/api/portfolio-queue")
    names, priorities = {}, {}
    for p in status.get("projects") or []:
        if isinstance(p, dict) and p.get("id"):
            names[str(p["id"])] = str(p.get("name") or p["id"])[:60]
            res = p.get("resource") if isinstance(p.get("resource"), dict) else {}
            priorities[str(p["id"])] = str(res.get("priority") or "")
    workers = []
    for key, cfg in (targets.get("projects") or {}).items():
        if not isinstance(cfg, dict) or not ID_RE.match(str(key)):
            continue
        base = str(cfg.get("base_project_id") or key)
        item = cfg.get("queue_item") if isinstance(cfg.get("queue_item"), dict) else {}
        workers.append({"worker": str(key), "project": base, "name": names.get(base, base),
                        "slot": cfg.get("worker_slot", cfg.get("slot")), "provider": str(cfg.get("provider") or ""),
                        "active": bool(cfg.get("active")), "state": str(cfg.get("desired_state") or "running"),
                        "queue_id": str(item.get("queue_id") or ""), "queue_priority": str(item.get("priority") or "")})
    workers.sort(key=lambda w: (w["project"], str(w["slot"] or ""), w["worker"]))
    items = []
    for it in (queue.get("items") or [])[:80]:
        if isinstance(it, dict) and it.get("queue_id"):
            items.append({"queue_id": str(it["queue_id"])[:160], "project": str(it.get("project_id") or ""),
                          "title": str(it.get("title") or "")[:200], "priority": str(it.get("priority") or ""),
                          "status": str(it.get("status") or "")})
    projects = [{"id": pid, "name": names[pid], "priority": priorities.get(pid, "")} for pid in sorted(names)]
    errors = [e for e in (e1, e2, e3) if e]
    value = {"online": not (e1 and e2 and e3), "errors": errors, "workers": workers, "projects": projects,
             "queue": items, "max_workers": targets.get("max_workers"), "checked_at": core.timestamp()}
    with _LOCK:
        _CACHE.update(expires=time.monotonic() + 4, value=value)
    return json.loads(json.dumps(value))


def digest(state, limit=2400):
    """Compact, factual text for an expert to review. Only values zCloud really reported."""
    lines = ["Workers (project · staat · taak):"]
    for w in state["workers"][:30]:
        lines.append("- %s [%s] %s%s" % (w["worker"], w["name"], w["state"] + ("" if w["active"] else ", niet actief"),
                                         (" · " + w["queue_id"] + " " + w["queue_priority"]) if w["queue_id"] else ""))
    lines.append("Projectprioriteit: " + ", ".join("%s=%s" % (p["id"], p["priority"] or "?") for p in state["projects"][:30]))
    lines.append("Open queue (%d):" % len(state["queue"]))
    for q in state["queue"][:25]:
        lines.append("- %s %s %s %s" % (q["queue_id"], q["priority"], q["status"], q["title"][:90]))
    return "\n".join(lines)[:limit]


def signature(state):
    keyed = {"w": [[w["worker"], w["state"], w["active"], w["queue_id"]] for w in state["workers"]],
             "p": [[p["id"], p["priority"]] for p in state["projects"]],
             "q": sorted(q["queue_id"] + ":" + q["status"] for q in state["queue"])}
    return hashlib.sha256(json.dumps(keyed, sort_keys=True).encode()).hexdigest()[:16], keyed


def diff_summary(old, new):
    """Human-readable changes between two keyed snapshots (what the dashboard or someone else did)."""
    out = []
    ow = {w[0]: w for w in old.get("w", [])}
    for w in new.get("w", []):
        before = ow.get(w[0])
        if before and before[1] != w[1]:
            out.append("Worker %s: %s → %s" % (w[0], before[1], w[1]))
    op = {p[0]: p[1] for p in old.get("p", [])}
    for p in new.get("p", []):
        if p[0] in op and op[p[0]] != p[1]:
            out.append("Prioriteit %s: %s → %s" % (p[0], op[p[0]] or "?", p[1] or "?"))
    gained = set(new.get("q", [])) - set(old.get("q", []))
    lost = set(old.get("q", [])) - set(new.get("q", []))
    if gained:
        out.append("%d taak/taken erbij in de queue" % len(gained))
    if lost:
        out.append("%d taak/taken weg of afgerond" % len(lost))
    return out[:6]


def sync_changes(core):
    """Called from the background loop: notice changes made elsewhere (dashboard) and turn them into events."""
    import office_game  # lazy: avoids an import cycle
    state = overview(core, fresh=True)
    if not state["online"]:
        return None
    sig, keyed = signature(state)
    with core.connect() as db:
        row = db.execute("SELECT value FROM office_kv WHERE key='control_snapshot'").fetchone()
        db.execute("INSERT INTO office_kv(key,value) VALUES('control_snapshot',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (json.dumps({"sig": sig, "keyed": keyed}),))
        if not row:
            return []
        old = json.loads(row["value"])
        if old.get("sig") == sig:
            return []
        recent = db.execute("SELECT COUNT(*) FROM office_control_log WHERE ok=1 AND created_at>=?",
                            (time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 30)),)).fetchone()[0]
        changes = diff_summary(old.get("keyed", {}), keyed)
        if changes:
            office_game.add_event(db, "", "control_changed", sig, {"changes": changes, "source": "office" if recent else "dashboard"})
        return changes


# ----------------------------------------------------------------- changing
def _known_worker(state, ident):
    return any(w["worker"] == ident for w in state["workers"]) or any(p["id"] == ident for p in state["projects"])


def validate(core, body, state=None):
    """Return a normalized action dict plus a Dutch summary and risk level, or raise ValueError."""
    state = state or overview(core)
    kind = str(body.get("action") or "")
    if kind == "worker":
        ident, op = str(body.get("worker") or ""), str(body.get("op") or "")
        if op not in WORKER_OPS:
            raise ValueError("Onbekende worker-actie")
        if not ID_RE.match(ident) or not _known_worker(state, ident):
            raise ValueError("Onbekende worker of project")
        return {"action": "worker", "worker": ident, "op": op}, "Worker %s: %s" % (ident, WORKER_OPS[op]), ("low" if op in ("start", "push", "new_chat") else "high")
    if kind == "priority":
        project, level = str(body.get("project") or ""), str(body.get("priority") or "")
        if level == "turbo":
            raise ValueError("Turbo zet je alleen in het dashboard aan")
        if level not in PROJECT_PRIORITIES:
            raise ValueError("Prioriteit moet achtergrond, normaal of hoog zijn")
        if not any(p["id"] == project for p in state["projects"]):
            raise ValueError("Onbekend project")
        return {"action": "priority", "project": project, "priority": level}, "Prioriteit van %s op %s zetten" % (project, PROJECT_PRIORITIES[level]), "high"
    if kind == "worker_count":
        project = str(body.get("project") or "")
        try:
            count = int(body.get("count"))
        except (TypeError, ValueError):
            raise ValueError("Aantal moet een geheel getal zijn")
        limit = int(state.get("max_workers") or 4)
        if not any(p["id"] == project for p in state["projects"]) or not 1 <= count <= min(limit, 8):
            raise ValueError("Onbekend project of aantal buiten de grenzen")
        return {"action": "worker_count", "project": project, "count": count}, "%s op %d ChatGPT-tabs zetten" % (project, count), "high"
    if kind == "force_push":
        return {"action": "force_push"}, "Alle actieve workers een duwtje geven", "high"
    if kind == "queue_add":
        project = str(body.get("project") or "")
        title = re.sub(r"\s+", " ", str(body.get("title") or "")).strip()
        level = str(body.get("queue_priority") or "P2").upper()
        criteria = re.sub(r"\s+", " ", str(body.get("criteria") or "")).strip()
        if not any(p["id"] == project for p in state["projects"]):
            raise ValueError("Onbekend project")
        if not 8 <= len(title) <= 220 or level not in QUEUE_PRIORITIES or len(criteria) > 1500:
            raise ValueError("Titel (8-220 tekens), prioriteit P0-P3 en criteria (max 1500) zijn vereist")
        if level == "P0":
            level = "P1"   # P0 is reserved for the dashboard owner
        return {"action": "queue_add", "project": project, "title": title, "queue_priority": level, "criteria": criteria}, "Taak toevoegen aan %s (%s): %s" % (project, level, title[:80]), "low"
    if kind == "queue_drop":
        ident = str(body.get("queue_id") or "")
        if not ID_RE.match(ident) or not any(q["queue_id"] == ident for q in state["queue"]):
            raise ValueError("Onbekende queue-taak")
        return {"action": "queue_drop", "queue_id": ident, "reason": str(body.get("reason") or "")[:300]}, "Taak laten vervallen: " + ident, "high"
    raise ValueError("Onbekende besturingsactie")


def prepare(core, body):
    action, summary, risk = validate(core, body)
    ident = uuid.uuid4().hex[:12]
    now = time.monotonic()
    with _LOCK:
        for key in [k for k, v in _PENDING.items() if v["expires"] < now]:
            del _PENDING[key]
        if len(_PENDING) >= 20:
            raise ValueError("Er wachten al te veel onbevestigde acties")
        _PENDING[ident] = {"action": action, "summary": summary, "expires": now + PENDING_TTL}
    return {"pending_id": ident, "summary": summary, "risk": risk, "expires_in": PENDING_TTL}


def confirm(core, pending_id, source="game"):
    with _LOCK:
        entry = _PENDING.pop(str(pending_id or ""), None)
    if not entry or entry["expires"] < time.monotonic():
        raise ValueError("Deze actie is verlopen of onbekend. Vraag het nog een keer.")
    return execute(core, entry["action"], entry["summary"], source)


def execute(core, action, summary, source="game"):
    """Run an already validated action against zCloud. Re-validates against live state first."""
    action, summary, _risk = validate(core, action)
    ok, detail = True, ""
    try:
        kind = action["action"]
        if kind == "worker":
            result = core.zcloud("/api/runner-control", {"project_id": action["worker"], "action": action["op"]}, timeout=10, actor=ACTOR)
            detail = "command %s" % result.get("command_id", "")
        elif kind == "priority":
            core.zcloud("/api/resource-priority", {"project": action["project"], "priority": action["priority"]}, timeout=15, actor=ACTOR)
        elif kind == "worker_count":
            core.zcloud("/api/runner-workers", {"project_id": action["project"], "worker_count": action["count"]}, timeout=10, actor=ACTOR)
        elif kind == "force_push":
            core.zcloud("/api/dynamic-workers/force-push", {}, timeout=15, actor=ACTOR)
        elif kind == "queue_add":
            queue_id = "office-%s-%s" % (action["project"], uuid.uuid4().hex[:12])
            criteria = action["criteria"] or ("Voorstel van een Office-expert, goedgekeurd door de eigenaar. Alleen uitvoerbaar werk met controleerbaar bewijs. "
                                              "Geen productie-, secret- of security-scope wijzigingen zonder afzonderlijke toestemming.")
            core.zcloud("/api/portfolio-queue", {"action": "enqueue", "project_id": action["project"], "title": action["title"],
                                                 "priority": action["queue_priority"], "completion_criteria": criteria, "queue_id": queue_id},
                        timeout=10, actor=ACTOR)
            detail = queue_id
        elif kind == "queue_drop":
            core.zcloud("/api/portfolio-queue", {"action": "drop", "queue_id": action["queue_id"],
                                                 "evidence": ("Door de eigenaar laten vervallen via Office. " + action.get("reason", ""))[:400]}, timeout=10, actor=ACTOR)
    except (ConnectionError, RuntimeError, ValueError) as exc:
        ok, detail = False, str(exc)[:240]
    with core.connect() as db:
        db.execute("INSERT INTO office_control_log(created_at,source,action,summary,ok,detail) VALUES(?,?,?,?,?,?)",
                   (core.timestamp(), source, action["action"], summary, 1 if ok else 0, detail))
    if not ok:
        raise ValueError("zCloud kon dit niet uitvoeren: " + detail)
    with _LOCK:
        _CACHE["expires"] = 0.0
    return {"ok": True, "summary": summary, "detail": detail}


def recent_log(core, limit=20):
    with core.connect() as db:
        return [dict(r) for r in db.execute("SELECT created_at,source,action,summary,ok,detail FROM office_control_log ORDER BY id DESC LIMIT ?", (max(1, min(limit, 50)),))]


def reset_cache():
    with _LOCK:
        _CACHE.update(expires=0.0, value=None)
        _PENDING.clear()
