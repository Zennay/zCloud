"""Expandable team: employees live in SQLite instead of being hard-coded.

The three original experts (Maya, Noah, Lina) are seeded once. More experts can be added
through the authenticated API; every other module keeps using ``core.PERSONAS`` and
``office_features.PROFILE_IDS``, which are refreshed in place by :func:`load`.
"""
import json
import re

import office_features as features

MAX_STAFF = 8          # four desks per row, two rows on the work floor
ID_RE = re.compile(r"^[a-z][a-z0-9]{1,15}$")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

EXPERT_RULES = (
    " Je bent een echte expert: geef een onderbouwd oordeel, benoem onzekerheid en verzin geen status, "
    "resultaten of bronnen. Je mag voorstellen doen om de zCloud-queue en de workers beter aan te sturen "
    "(taken toevoegen, laten vervallen, prioriteiten of werkers aanpassen), maar je voert zelf niets uit: "
    "de eigenaar moet eerst akkoord geven. Controleer Notion en andere beschikbare projectbronnen voordat "
    "je feiten over voortgang claimt en zeg het eerlijk als je er niet bij kunt. Antwoord in het Nederlands."
)

DEFAULT_STAFF = [
    {"id": "maya", "name": "Maya", "title": "Projectmanager · zCloud", "role": "Organiseert en begeleidt zCloud-werk",
     "project_id": "cloud", "focus": "portfolio, prioriteiten, planning en Notion",
     "personality": "Warm en gestructureerd; ziet eerst de open issues.", "color": "#f0a878",
     "look": {"skin": "#c99679", "suit": "#414e50", "hair": "#302b28", "style": "long"},
     "voice": {"pitch": 1.1, "rate": 1.0, "gender": "f"},
     "persona_prompt": "Je bent Maya, projectleider en portfolio-assistent van zCloud. Je helpt prioriteiten, projectvoortgang, risico’s en afhankelijkheden overzichtelijk te houden."},
    {"id": "noah", "name": "Noah", "title": "Developer · Supa", "role": "Ontwikkelt aan Supa",
     "project_id": "supa", "focus": "implementatie, UX, regressietests en bestaande code",
     "personality": "Pragmatisch, technisch en direct.", "color": "#78b8d6",
     "look": {"skin": "#ae805f", "suit": "#344452", "hair": "#302b28", "style": "short"},
     "voice": {"pitch": 0.85, "rate": 1.0, "gender": "m"},
     "persona_prompt": "Je bent Noah, senior product-engineer met Supa als specialisme. Denk vanuit functionaliteit, UX, betrouwbare implementatie, regressietests en bestaande code."},
    {"id": "lina", "name": "Lina", "title": "QA · LightUp", "role": "Valideert LightUp en helpt bugs oplossen",
     "project_id": "lightup", "focus": "kwaliteit, security en reproduceerbare testgevallen",
     "personality": "Kritisch, precies en security-minded.", "color": "#b6a0e5",
     "look": {"skin": "#d6b497", "suit": "#796c61", "hair": "#aa8650", "style": "long"},
     "voice": {"pitch": 1.0, "rate": 1.05, "gender": "f"},
     "persona_prompt": "Je bent Lina, kwaliteits- en securityexpert met LightUp als specialisme. Geef reproduceerbare testgevallen, veilige validatie, duidelijke risico-inschattingen en herstelstappen. Actieve securitytests alleen op expliciet geautoriseerde scope; neem geen toestemming aan."},
]
PALETTE = ["#e0a458", "#7fb7a4", "#d98aa0", "#8ea7e0", "#c8b26a", "#a3c27a"]
SKINS = ["#c99679", "#ae805f", "#d6b497", "#8d5f43", "#e0bfa3"]
SUITS = ["#414e50", "#344452", "#796c61", "#4b3f5a", "#3f5742", "#5a4638"]
HAIRS = ["#302b28", "#aa8650", "#5a3b27", "#1d1b1a", "#9a9288"]


def init(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS office_staff (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', role TEXT NOT NULL DEFAULT '',
      project_id TEXT NOT NULL DEFAULT 'cloud', focus TEXT NOT NULL DEFAULT '', personality TEXT NOT NULL DEFAULT '',
      persona_prompt TEXT NOT NULL DEFAULT '', color TEXT NOT NULL DEFAULT '#e0a458',
      look TEXT NOT NULL DEFAULT '{}', voice TEXT NOT NULL DEFAULT '{}',
      slot INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    """)
    if db.execute("SELECT COUNT(*) FROM office_staff").fetchone()[0] == 0:
        stamp = "1970-01-01T00:00:00+00:00"
        for slot, s in enumerate(DEFAULT_STAFF):
            db.execute("INSERT INTO office_staff(id,name,title,role,project_id,focus,personality,persona_prompt,color,look,voice,slot,active,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                       (s["id"], s["name"], s["title"], s["role"], s["project_id"], s["focus"], s["personality"],
                        s["persona_prompt"] + EXPERT_RULES, s["color"], json.dumps(s["look"]), json.dumps(s["voice"]),
                        slot, stamp, stamp))


def desk_for_slot(slot):
    """Desk centre for a work-floor slot: four desks per row, two rows (the back row is further from the lounge)."""
    return {"x": [-13.5, -4.5, 4.5, 13.5][slot % 4], "z": [-8.0, -12.4][min(1, slot // 4)]}


def _row(r):
    look, voice = json.loads(r["look"] or "{}"), json.loads(r["voice"] or "{}")
    desk = desk_for_slot(r["slot"])
    return {"id": r["id"], "name": r["name"], "title": r["title"], "role": r["role"], "project_id": r["project_id"],
            "focus": r["focus"], "personality": r["personality"], "persona_prompt": r["persona_prompt"],
            "color": r["color"], "look": look, "voice": voice, "slot": r["slot"], "active": bool(r["active"]),
            "x": -4.1 + 4.1 * (r["slot"] % 3), "z": -2.9, "desk": desk}


def list_staff(core, include_inactive=False):
    with core.connect() as db:
        rows = db.execute("SELECT * FROM office_staff " + ("" if include_inactive else "WHERE active=1 ") + "ORDER BY slot").fetchall()
    return [_row(r) for r in rows]


def load(core):
    """Refresh the live dicts used by the rest of the server. Called after init and every change."""
    staff = list_staff(core, include_inactive=True)
    core.PERSONAS.clear()
    for s in staff:
        core.PERSONAS[s["id"]] = s
    features.PROFILE_IDS.clear()
    features.PROFILE_IDS.update(s["id"] for s in staff if s["active"])
    return staff


def _text(value, label, limit, required=False):
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    if required and not value:
        raise ValueError(label + " is verplicht")
    if len(value) > limit:
        raise ValueError(label + " is te lang (max " + str(limit) + " tekens)")
    return value


def _color(value, default):
    if value in (None, ""):
        return default
    if not isinstance(value, str) or not HEX_RE.match(value):
        raise ValueError("Kleur moet de vorm #rrggbb hebben")
    return value.lower()


def _num(value, lo, hi, default):
    if value in (None, ""):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("Ongeldig getal in stem-instelling")
    return max(lo, min(hi, number))


def build_prompt(name, title, role, focus, personality):
    parts = ["Je bent " + name + ", " + (title or "expert") + "."]
    if role:
        parts.append("Je taak: " + role + ".")
    if focus:
        parts.append("Je specialisme: " + focus + ".")
    if personality:
        parts.append("Karakter: " + personality)
    return " ".join(parts) + EXPERT_RULES


def upsert(core, body):
    ident = str(body.get("id") or "").strip().lower()
    if not ID_RE.match(ident):
        raise ValueError("Id: 2-16 tekens, kleine letters en cijfers, beginnend met een letter")
    with core.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            prior = db.execute("SELECT * FROM office_staff WHERE id=?", (ident,)).fetchone()
            if "active" in body and prior and body.get("active") in (False, 0, "false") and len(body) <= 2:
                db.execute("UPDATE office_staff SET active=0,updated_at=? WHERE id=?", (core.timestamp(), ident))
                db.execute("COMMIT")
                load(core)
                return {"staff": list_staff(core)}
            active_count = db.execute("SELECT COUNT(*) FROM office_staff WHERE active=1").fetchone()[0]
            if (not prior or not prior["active"]) and active_count >= MAX_STAFF:
                raise ValueError("Er passen " + str(MAX_STAFF) + " medewerkers op de werkvloer")
            name = _text(body.get("name", prior["name"] if prior else ""), "Naam", 24, True)
            title = _text(body.get("title", prior["title"] if prior else ""), "Functie", 60)
            role = _text(body.get("role", prior["role"] if prior else ""), "Taak", 200)
            focus = _text(body.get("focus", prior["focus"] if prior else ""), "Specialisme", 200)
            personality = _text(body.get("personality", prior["personality"] if prior else ""), "Karakter", 200)
            project = str(body.get("project_id", prior["project_id"] if prior else "cloud") or "cloud").strip().lower()
            if not PROJECT_RE.match(project):
                raise ValueError("Ongeldig project-id")
            n = db.execute("SELECT COUNT(*) FROM office_staff").fetchone()[0]
            look_in = body.get("look") if isinstance(body.get("look"), dict) else json.loads(prior["look"]) if prior else {}
            look = {"skin": _color(look_in.get("skin"), SKINS[n % len(SKINS)]),
                    "suit": _color(look_in.get("suit"), SUITS[n % len(SUITS)]),
                    "hair": _color(look_in.get("hair"), HAIRS[n % len(HAIRS)]),
                    "style": look_in.get("style") if look_in.get("style") in ("short", "long", "bun") else "short"}
            voice_in = body.get("voice") if isinstance(body.get("voice"), dict) else json.loads(prior["voice"]) if prior else {}
            voice = {"pitch": _num(voice_in.get("pitch"), .6, 1.5, 1.0), "rate": _num(voice_in.get("rate"), .8, 1.3, 1.0),
                     "gender": voice_in.get("gender") if voice_in.get("gender") in ("f", "m") else "f"}
            color = _color(body.get("color", prior["color"] if prior else None), PALETTE[n % len(PALETTE)])
            prompt = _text(body.get("persona_prompt", ""), "Persona", 1500) if body.get("persona_prompt") else None
            prompt = (prompt + EXPERT_RULES) if prompt and EXPERT_RULES.strip()[:30] not in prompt else prompt
            prompt = prompt or build_prompt(name, title, role, focus, personality)
            now = core.timestamp()
            used = {r[0] for r in db.execute("SELECT slot FROM office_staff WHERE active=1 AND id<>?", (ident,))}
            slot = prior["slot"] if prior and prior["active"] else next(s for s in range(MAX_STAFF) if s not in used)
            if prior:
                db.execute("UPDATE office_staff SET name=?,title=?,role=?,project_id=?,focus=?,personality=?,persona_prompt=?,color=?,look=?,voice=?,slot=?,active=1,updated_at=? WHERE id=?",
                           (name, title, role, project, focus, personality, prompt, color, json.dumps(look), json.dumps(voice), slot, now, ident))
            else:
                db.execute("INSERT INTO office_staff(id,name,title,role,project_id,focus,personality,persona_prompt,color,look,voice,slot,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                           (ident, name, title, role, project, focus, personality, prompt, color, json.dumps(look), json.dumps(voice), slot, now, now))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    load(core)
    return {"staff": list_staff(core)}
