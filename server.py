"""zCloud: read-only project monitoring; stdlib only."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone, timedelta
import json, os, sqlite3, subprocess, shutil, threading, time, mimetypes, logging, hmac, secrets, re, hashlib
from contextlib import contextmanager, closing
import enhancements

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'history.db'
LOCK = threading.Lock()
CACHE = None
CPU_PREV = None
RUNNERS = {'haxlab': 'actions.runner.Zennay-Haxlab.vps-bb300bba-haxlab.service', 'ftmo': 'actions.runner.Zennay-Ftmo.vps-bb300bba-ftmo.service'}
SUPA_SYNC = 'zennay-supa-sync.timer'
FIREFOX_RUNNER_SERVICE = 'chatgpt-firefox.service'
USER_RUNTIME_SERVICES = ['raise-gateway.service', 'zssh.service']
SERVICES = ['haxlab-analyzer.service', 'haxlab-ingest.service', 'haxlab-worker.service', 'haxlab-autonomy.timer', 'haxlab-autonomy.service', 'ftmo-autonomous.service', 'ftmo-autonomous.timer', SUPA_SYNC, *RUNNERS.values()]
WATCH_TOKEN_FILE = ROOT / '.watch-token'
WATCH_TOKEN = WATCH_TOKEN_FILE.read_text().strip() if WATCH_TOKEN_FILE.exists() else ''
ACTION_ALLOW_FILE = ROOT / '.action-allowed-ips'
AUTONOMY_POLICY_FILE = ROOT / 'autonomy-policy.json'
AUTONOMY_TICK_SECONDS = 2
AUTONOMY_SIGNAL_EVENTS = ('autonomy-continue','autonomy-wait-vps','autonomy-wait-human','autonomy-complete')
GLOBAL_CHATGPT_WORKER_LIMIT = 2
MAX_CHATGPT_WORKERS = 2
AI_SLOT_DIVERSITY_PENALTY = 500
PORTFOLIO_QUEUE_URL = 'https://app.notion.com/p/4162fac179f44fcbbe4072a183d2b440'
PORTFOLIO_QUEUE_DATA_SOURCE = 'collection://86e406fd-2c99-4ef5-8058-363c1004b3eb'
PORTFOLIO_AI_COOLDOWN_SECONDS = 120
WORKER_PREFLIGHT_TTL_SECONDS = 600
TASK_CLAIM_METADATA_MAX_BYTES = 4000
TASK_CLAIM_ALTERNATIVE_MAX = 12
FEATURE_FLAG_DEFINITIONS = {
    'high_blast_radius_promotion': {
        'default': False,
        'max_ttl_seconds': 3600,
        'description': 'Tijdelijke toestemming voor brede/cross-plane zCloud-promoties',
    },
}
WORKER_LANES = (
    'kritieke pad / eerstvolgende veilige projectgate',
    'tests, validatie, determinisme en race-condition checks',
    'data, provider-validatie, preprocessing en provenance',
    'VPS-infra, performance, concurrency en resourceveiligheid',
    'stress-tests, transactiekosten, diagnostics en challenge-simulaties',
    'voorbereidend werk voor de volgende generatie zonder verborgen OOS-resultaten',
    'telemetrie, documentatie en reproduceerbaarheid',
    'onafhankelijke QA van open werk zonder bestaand werk te dupliceren',
)
PROJECT_INDEX = {p['id']: p for p in json.loads((ROOT/'projects.json').read_text())}
KNOWN_RUNNER_CONVERSATIONS = {
    'haxlab': '6ab6e0af-b1e8-83eb-b355-6398eb60dce4',
    'ftmo': '6ab611aa-d5c4-83eb-940c-498aa3dbe0e1',
}

def action_request_allowed(handler):
    addr = handler.client_address[0]
    if addr in ('127.0.0.1', '::1'):
        return True
    try:
        allowed = {line.strip() for line in ACTION_ALLOW_FILE.read_text().splitlines() if line.strip()}
    except Exception:
        allowed = set()
    if addr in allowed:
        return True
    host = (handler.headers.get('Host') or '').strip()
    origin = (handler.headers.get('Origin') or '').strip()
    referer = (handler.headers.get('Referer') or '').strip()
    fetch_site = (handler.headers.get('Sec-Fetch-Site') or '').strip().lower()
    same_origin = bool(host) and (
        origin in ('http://' + host, 'https://' + host) or
        referer.startswith('http://' + host + '/') or
        referer.startswith('https://' + host + '/')
    )
    return same_origin and fetch_site in ('same-origin', 'same-site')

def project_runner_prompt(project_id, name):
    return (
        f'Je bent één van maximaal {GLOBAL_CHATGPT_WORKER_LIMIT} dynamische zCloud portfolio-workers. '
        f'Het technische runnerlabel "{name}" / "{project_id}" is géén vaste projecttoewijzing en mag je keuze niet sturen. '
        f'De centrale bron voor het volgende AI-werk is de Notion database Portfolio Work Queue: {PORTFOLIO_QUEUE_URL} '
        f'(data source {PORTFOLIO_QUEUE_DATA_SOURCE}). Begin iedere nieuwe cyclus altijd met een verse queue-check. '
        'Kies niet automatisch hetzelfde project als in de vorige cyclus. Vergelijk opnieuw alle eligible queue-items en '
        'pak het hoogste actuele portfolio-prioriteitswerk. De projectrelation op het gekozen item bepaalt daarna welke '
        'Project HQ, Handoff, GitHub-repo en live/VPS-context je moet openen. Oude chatcontext is nooit een vervanging '
        'voor die actuele broncheck. '
    )

def project_worker_prompt(project_id, name, base_prompt, slot, total):
    return base_prompt + (
        f' Jij bent portfolio-worker {slot}/{total}. Er mogen portfolio-breed nooit meer dan {GLOBAL_CHATGPT_WORKER_LIMIT} '
        'AI-workers tegelijk actief zijn. Voer per cyclus dit protocol uit: '
        '1) lees de Portfolio Work Queue opnieuw; '
        '2) herbeoordeel de top van de queue op basis van actuele blockers, deadlines, projectposture, incidenten, '
        'dependencies, nieuwe evidence en menselijke gates; werk Priority, Eligible, Why now en Last Priority Review bij '
        'wanneer de waarheid veranderd is; '
        '3) kies het hoogste eligible item met Status=Queued dat niet al door de andere worker is geclaimd; '
        '4) claim het item vóór inhoudelijk werk met Worker, Status=Claimed, Claimed At en Claim Expires, haal het item '
        'direct opnieuw op en ga alleen door als de claim nog van jou is; bij conflict pak je het volgende item; '
        '5) zet Status=Running en open via de Project-relatie de actuele canonieke Project HQ/Handoff/repository; '
        '6) gebruik zCloud preflight/task-claims vóór repo-writes waar die gelden en controleer open PRs/branches om '
        'dubbelwerk te voorkomen; '
        '7) voer echte voortgang uit. Deterministisch werk hoort zoveel mogelijk op VPS/services/timers/queues/self-hosted '
        'GitHub Actions. Gebruik AI voor onderzoek, ontwerp, code/review, diagnose en beslissingen die redenering nodig hebben; '
        '8) als nieuw noodzakelijk werk ontstaat, maak daarvoor een apart queue-item met Project, Priority, Execution, '
        'Eligible en concrete Completion Criteria. Maak geen dubbele taken; merge/drop verouderde duplicaten; '
        '9) markeer een item NOOIT Done na alleen analyse, planning, checklist, statusrecap of gedeeltelijke implementatie. '
        'Done is alleen toegestaan als ALLE Completion Criteria aantoonbaar gehaald zijn EN Evidence concrete, verifieerbare '
        'proof bevat (bijv. commit/PR, groene tests, live canary, artifact of gemeten resultaat). Gebruik Verifying zolang '
        'bewijs nog gecontroleerd wordt; '
        '10) bij een echte blocker: zet Status=Blocked, leg Blocker vast, zet Eligible=false en Recheck After als er een '
        'zinvol hercheckmoment bestaat; laat daarna de worker vrij voor ander queuewerk. Als deterministisch vervolgwerk al '
        'loopt, leg dat vast en ga niet in ChatGPT zitten pollen; '
        '11) na Done of Blocked begin je de volgende AI-cyclus opnieuw bij de globale queue. Er bestaat geen vaste '
        'projectrotatie en geen vooraf bepaald aantal AI-cycli per project. '
        'Prioriteitsvolgorde is P0 Critical > P1 High > P2 Normal > P3 Low, maar de actuele bronwaarheid mag een item '
        'promoveren/deprioriteren. Herbeoordeel dit elke cyclus. '
        'Eindig iedere cyclus met exact één marker: ZCLOUD_AUTONOMY: CONTINUE als er nog direct eligible queuewerk is; '
        'ZCLOUD_AUTONOMY: WAIT_VPS als alleen reeds gestart deterministisch werk de relevante voortgang bepaalt; '
        'ZCLOUD_AUTONOMY: WAIT_HUMAN bij een echte menselijke/externe gate; of ZCLOUD_AUTONOMY: COMPLETE alleen als de '
        'globale queue aantoonbaar geen eligible werk meer bevat. '
        'Gebruik daarnaast ZCLOUD_PRIORITY: HIGH/NORMAL/LOW/BACKGROUND uitsluitend als technisch signaal; de Notion queue '
        'blijft de inhoudelijke bron van waarheid voor wat de workers daadwerkelijk kiezen. '
        'PROJECT-SPECIFIEKE SAFETY blijft gelden ongeacht welk technisch runnerlabel deze portfolio-worker heeft. '
        'Als het gekozen queue-item FTMO betreft: houd preregistration, chronologische splits, development, walk-forward en '
        'final holdout strikt gescheiden; gebruik verborgen validation/holdout-resultaten nooit voor ontwerpkeuzes en red of '
        'retune afgewezen generaties niet. '
        'Als het gekozen queue-item zCloud betreft: respecteer het persistent finish-protocol. Controleer vóór een zCloud '
        'write-iteratie de live improvement-state en iteration_count. Alleen na een werkelijk afgeronde implementatie-iteratie '
        'mag ZCLOUD_ITERATION_COMPLETE worden gemeld. Bij expliciete finish-review gebruik exact ZCLOUD_FINISH_REVIEW: '
        'GREEN_NO_P0P1 of ZCLOUD_FINISH_REVIEW: OPEN_P0P1. Bij de harde eind-audit gebruik exact ZCLOUD_FINAL_AUDIT: GREEN '
        'of ZCLOUD_FINAL_AUDIT: FAIL. Gebruik deze markers nooit voor read-only voorbereiding.'
    )
RUNNER_DEFAULTS = {
    pid: {
        'name': project['name'],
        'conversation_id': KNOWN_RUNNER_CONVERSATIONS.get(pid, ''),
        'prompt': project_runner_prompt(pid, project['name'])
    }
    for pid, project in PROJECT_INDEX.items()
}
LAYOUT_FILE = ROOT / 'project-layout.json'

def load_project_layout(projects=None):
    ids=[p['id'] for p in projects] if projects else [p['id'] for p in json.loads((ROOT/'projects.json').read_text())]
    default={'order':ids,'archived':[]}
    if not LAYOUT_FILE.exists():
        return default
    try:
        raw=json.loads(LAYOUT_FILE.read_text())
        order=[x for x in raw.get('order',[]) if x in ids]
        order += [x for x in ids if x not in order]
        archived=[x for x in raw.get('archived',[]) if x in ids]
        return {'order':order,'archived':archived}
    except Exception:
        logging.exception('Invalid project layout')
        return default

def save_project_layout(layout, projects):
    ids=[p['id'] for p in projects]
    order=[x for x in layout.get('order',[]) if x in ids]
    order += [x for x in ids if x not in order]
    archived=[x for x in layout.get('archived',[]) if x in ids]
    payload={'order':order,'archived':archived}
    tmp=LAYOUT_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    tmp.replace(LAYOUT_FILE)
    return payload

def project_views(data):
    layout=load_project_layout(data['projects'])
    rank={pid:i for i,pid in enumerate(layout['order'])}
    all_projects=sorted(data['projects'], key=lambda p: rank.get(p['id'],9999))
    visible=[p for p in all_projects if p['id'] not in layout['archived']]
    archived=[p for p in all_projects if p['id'] in layout['archived']]
    return visible, archived, layout

def public_status(data):
    visible, archived, layout=project_views(data)
    return {**data,'projects':visible,'archived_projects':archived,'project_layout':layout,'alerts':enhancements.list_alerts(DB,8,False)}

def now(): return datetime.now(timezone.utc).isoformat()
def cmd(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=4).strip()
def user_systemctl(*args):
    env=os.environ.copy()
    env.update({'XDG_RUNTIME_DIR':'/run/user/1000','DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/1000/bus'})
    return subprocess.check_output(['systemctl','--user',*args], text=True, stderr=subprocess.STDOUT, timeout=12, env=env).strip()
def firefox_runner_status():
    try:
        try: state=user_systemctl('is-active',FIREFOX_RUNNER_SERVICE)
        except subprocess.CalledProcessError as e: state=(e.output or '').strip() or 'inactive'
        raw=user_systemctl('show',FIREFOX_RUNNER_SERVICE,'-p','MainPID','-p','ActiveEnterTimestamp','-p','NRestarts','-p','Restart')
        fields=dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
        return {'state':state,'active':state=='active','main_pid':int(fields.get('MainPID') or 0),
                'active_since':fields.get('ActiveEnterTimestamp') or None,'restarts':int(fields.get('NRestarts') or 0),
                'auto_restart':fields.get('Restart') or 'unknown'}
    except Exception as e:
        return {'state':'unknown','active':False,'main_pid':0,'active_since':None,'restarts':0,'auto_restart':'unknown','error':str(e)[:200]}
def git(path, *args): return cmd(['git', '-c', 'safe.directory='+path, '-C', path, *args])
@contextmanager
def connect():
    c = sqlite3.connect(DB, timeout=4)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def request_actor(handler):
    addr=str((handler.client_address or ["unknown"])[0] or "unknown")
    hinted=str(handler.headers.get("X-ZCloud-Actor") or "").strip()
    if hinted:
        hinted=re.sub(r"[^a-zA-Z0-9._:@/-]+","_",hinted)[:80]
        return (hinted+"@"+addr)[:128]
    origin=str(handler.headers.get("Origin") or "").strip()
    return (("dashboard@" if origin else "api@")+addr)[:128]

def _audit_json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))

def record_config_audit(config_key,target,actor,old_value,new_value,result,detail="",connection=None):
    row=(
        now(),str(actor or "unknown")[:128],str(config_key or "")[:80],
        str(target or "")[:160],_audit_json(old_value),_audit_json(new_value),
        str(result or "")[:32],str(detail or "")[:500]
    )
    sql=("INSERT INTO config_audit(ts,actor,config_key,target,old_value_json,new_value_json,result,detail) "
         "VALUES(?,?,?,?,?,?,?,?)")
    if connection is not None:
        connection.execute(sql,row)
        return
    with connect() as c:
        c.execute(sql,row)

def config_audit(limit=80,config_key=None,target=None):
    limit=max(1,min(int(limit or 80),500))
    query="SELECT id,ts,actor,config_key,target,old_value_json,new_value_json,result,detail FROM config_audit"
    where=[];args=[]
    if config_key:
        where.append("config_key=?");args.append(str(config_key))
    if target:
        where.append("target=?");args.append(str(target))
    if where:
        query+=" WHERE "+" AND ".join(where)
    query+=" ORDER BY id DESC LIMIT ?";args.append(limit)
    with connect() as c:
        rows=c.execute(query,args).fetchall()
    out=[]
    for row in rows:
        item=dict(row)
        for field in ("old_value_json","new_value_json"):
            key="old_value" if field.startswith("old_") else "new_value"
            try:item[key]=json.loads(item.pop(field))
            except Exception:item[key]=item.pop(field)
        out.append(item)
    return out

def _feature_flag_row(name, connection=None):
    sql="SELECT name,enabled,expires_at,updated_at,actor FROM feature_flags WHERE name=?"
    if connection is not None:
        return connection.execute(sql,(name,)).fetchone()
    with connect() as c:
        return c.execute(sql,(name,)).fetchone()

def feature_flag_state(name, at=None):
    definition=FEATURE_FLAG_DEFINITIONS.get(name)
    if not definition:
        raise ValueError("Onbekende feature flag")
    row=_feature_flag_row(name)
    enabled=bool(row["enabled"]) if row else bool(definition["default"])
    expires_at=(row["expires_at"] if row else None)
    expired=False
    if enabled and expires_at:
        try:
            expired=datetime.fromisoformat(expires_at).astimezone(timezone.utc) <= (at or datetime.now(timezone.utc))
        except Exception:
            expired=True
    effective=enabled and not expired
    return {
        "name":name,
        "enabled":enabled,
        "effective":effective,
        "expires_at":expires_at,
        "updated_at":row["updated_at"] if row else None,
        "actor":row["actor"] if row else None,
        "description":definition["description"],
        "max_ttl_seconds":definition["max_ttl_seconds"],
    }

def feature_flags():
    return [feature_flag_state(name) for name in sorted(FEATURE_FLAG_DEFINITIONS)]

def set_feature_flag(name,enabled,actor,ttl_seconds=None):
    definition=FEATURE_FLAG_DEFINITIONS.get(name)
    if not definition:
        raise ValueError("Onbekende feature flag")
    if not isinstance(enabled,bool):
        raise ValueError("enabled moet true of false zijn")
    now_dt=datetime.now(timezone.utc)
    old=feature_flag_state(name,now_dt)
    expires_at=None
    ttl=None
    if enabled:
        try: ttl=int(ttl_seconds if ttl_seconds is not None else 900)
        except Exception: raise ValueError("ttl_seconds moet een geheel getal zijn")
        max_ttl=int(definition["max_ttl_seconds"])
        if ttl<60 or ttl>max_ttl:
            raise ValueError(f"ttl_seconds moet tussen 60 en {max_ttl} liggen")
        expires_at=(now_dt+timedelta(seconds=ttl)).isoformat()
    with connect() as c:
        c.execute(
            "INSERT INTO feature_flags(name,enabled,expires_at,updated_at,actor) VALUES(?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET enabled=excluded.enabled,expires_at=excluded.expires_at,"
            "updated_at=excluded.updated_at,actor=excluded.actor",
            (name,1 if enabled else 0,expires_at,now_dt.isoformat(),str(actor or "unknown")[:128]),
        )
        new_value={"enabled":enabled,"expires_at":expires_at,"ttl_seconds":ttl}
        old_value={"enabled":old["effective"],"expires_at":old["expires_at"]}
        record_config_audit(
            "feature.flag",name,actor,old_value,new_value,
            "no_change" if old["effective"]==enabled and (not enabled or old["expires_at"]==expires_at) else "succeeded",
            connection=c,
        )
    return feature_flag_state(name)

def init_db():
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE IF NOT EXISTS project_samples(ts TEXT, project TEXT, progress REAL, commits INTEGER, hash TEXT, message TEXT, PRIMARY KEY(ts, project))')
        c.execute('CREATE TABLE IF NOT EXISTS host_samples(ts TEXT PRIMARY KEY, cpu REAL, memory REAL, disk REAL, load REAL)')
        c.execute('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, ts TEXT, project TEXT, kind TEXT, title TEXT, detail TEXT)')
        c.execute('CREATE INDEX IF NOT EXISTS events_ts ON events(ts)')
        c.execute('CREATE TABLE IF NOT EXISTS runner_events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, event TEXT, target TEXT, title TEXT, generating INTEGER, sending INTEGER, reason TEXT, tab_id INTEGER, error TEXT)')
        columns={r['name'] for r in c.execute('PRAGMA table_info(runner_events)').fetchall()}
        if 'project_id' not in columns: c.execute('ALTER TABLE runner_events ADD COLUMN project_id TEXT')
        if 'progress_at' not in columns: c.execute('ALTER TABLE runner_events ADD COLUMN progress_at TEXT')
        if 'assistant_chars' not in columns: c.execute('ALTER TABLE runner_events ADD COLUMN assistant_chars INTEGER')
        if 'worker_slot' not in columns: c.execute('ALTER TABLE runner_events ADD COLUMN worker_slot INTEGER NOT NULL DEFAULT 1')
        c.execute('CREATE INDEX IF NOT EXISTS runner_events_project_ts ON runner_events(project_id,ts)')
        c.execute('CREATE TABLE IF NOT EXISTS runner_targets(project_id TEXT PRIMARY KEY, name TEXT NOT NULL, conversation_id TEXT NOT NULL, prompt TEXT NOT NULL)')
        target_columns={r['name'] for r in c.execute('PRAGMA table_info(runner_targets)').fetchall()}
        migrated_active='active' not in target_columns
        if migrated_active:
            c.execute('ALTER TABLE runner_targets ADD COLUMN active INTEGER NOT NULL DEFAULT 0')
            c.execute("UPDATE runner_targets SET active=1 WHERE project_id IN ('haxlab','ftmo')")
        if 'worker_count' not in target_columns:
            c.execute('ALTER TABLE runner_targets ADD COLUMN worker_count INTEGER NOT NULL DEFAULT 1')
            c.execute("UPDATE runner_targets SET worker_count=2 WHERE project_id='ftmo'")
        c.execute("CREATE TABLE IF NOT EXISTS runner_workers(project_id TEXT NOT NULL, worker_slot INTEGER NOT NULL, conversation_id TEXT NOT NULL DEFAULT '', PRIMARY KEY(project_id,worker_slot))")
        worker_columns={r['name'] for r in c.execute('PRAGMA table_info(runner_workers)').fetchall()}
        if 'desired_state' not in worker_columns:
            c.execute("ALTER TABLE runner_workers ADD COLUMN desired_state TEXT NOT NULL DEFAULT 'running'")
        c.execute("CREATE TABLE IF NOT EXISTS ai_global_slots(slot INTEGER PRIMARY KEY, project_id TEXT NOT NULL, worker_slot INTEGER NOT NULL, assigned_at TEXT NOT NULL)")
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS ai_global_slots_worker ON ai_global_slots(project_id,worker_slot)')
        c.execute('CREATE TABLE IF NOT EXISTS runner_commands(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, result TEXT)')
        c.execute('CREATE INDEX IF NOT EXISTS runner_commands_status ON runner_commands(status,id)')
        c.execute("CREATE TABLE IF NOT EXISTS task_claims(project_id TEXT NOT NULL, claim_key TEXT NOT NULL, owner_id TEXT NOT NULL, worker_id TEXT NOT NULL DEFAULT '', acquired_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, lease_until TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(project_id,claim_key))")
        c.execute('CREATE INDEX IF NOT EXISTS task_claims_lease_until ON task_claims(lease_until)')
        c.execute("""CREATE TABLE IF NOT EXISTS worker_preflights(
            project_id TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            notion_json TEXT NOT NULL,
            github_json TEXT NOT NULL,
            claims_fingerprint TEXT NOT NULL,
            claims_json TEXT NOT NULL,
            vps_json TEXT NOT NULL,
            PRIMARY KEY(project_id,worker_id,owner_id)
        )""")
        c.execute('CREATE INDEX IF NOT EXISTS worker_preflights_expires ON worker_preflights(expires_at)')
        c.execute("CREATE TABLE IF NOT EXISTS improvement_loops(project_id TEXT PRIMARY KEY, state TEXT NOT NULL, iteration_count INTEGER NOT NULL DEFAULT 0, clean_reviews INTEGER NOT NULL DEFAULT 0, stop_reason TEXT, last_green_commit TEXT, audit_result TEXT, updated_at TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS config_audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, config_key TEXT NOT NULL, target TEXT NOT NULL, old_value_json TEXT NOT NULL, new_value_json TEXT NOT NULL, result TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')")
        c.execute("CREATE INDEX IF NOT EXISTS config_audit_ts ON config_audit(ts,id)")
        c.execute("CREATE INDEX IF NOT EXISTS config_audit_key_target ON config_audit(config_key,target,id)")
        c.execute("CREATE TABLE IF NOT EXISTS feature_flags(name TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, expires_at TEXT, updated_at TEXT NOT NULL, actor TEXT NOT NULL DEFAULT 'system')")
        c.execute("CREATE TABLE IF NOT EXISTS autonomy_runtime(project_id TEXT PRIMARY KEY, initialized_at TEXT NOT NULL, manual_pause INTEGER NOT NULL DEFAULT 0, last_dispatch_at TEXT, last_reason TEXT NOT NULL DEFAULT '')")
        for flag_name,definition in FEATURE_FLAG_DEFINITIONS.items():
            c.execute("INSERT OR IGNORE INTO feature_flags(name,enabled,expires_at,updated_at,actor) VALUES(?,?,?,?,?)",
                      (flag_name,1 if definition['default'] else 0,None,now(),'system-default'))
        c.execute("INSERT OR IGNORE INTO improvement_loops(project_id,state,iteration_count,clean_reviews,stop_reason,last_green_commit,audit_result,updated_at) VALUES('cloud','running',0,0,NULL,NULL,NULL,?)",(datetime.now(timezone.utc).isoformat(),))
        for project_id,target in RUNNER_DEFAULTS.items():
            c.execute('INSERT INTO runner_targets(project_id,name,conversation_id,prompt,active) VALUES(?,?,?,?,0) '
                      'ON CONFLICT(project_id) DO UPDATE SET name=excluded.name,prompt=excluded.prompt',
                      (project_id,target['name'],target['conversation_id'],target['prompt']))
            row=c.execute('SELECT conversation_id,worker_count FROM runner_targets WHERE project_id=?',(project_id,)).fetchone()
            c.execute('INSERT OR IGNORE INTO runner_workers(project_id,worker_slot,conversation_id) VALUES(?,?,?)',
                      (project_id,1,row['conversation_id'] or ''))
            for slot in range(2,max(1,int(row['worker_count'] or 1))+1):
                c.execute('INSERT OR IGNORE INTO runner_workers(project_id,worker_slot,conversation_id) VALUES(?,?,?)',
                          (project_id,slot,''))
        c.execute('CREATE INDEX IF NOT EXISTS runner_events_ts ON runner_events(ts)')
        enhancements.init_db(c)
        # Preserve original snapshots, whose timestamps were recorded in VPS UTC.
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='snapshots'").fetchone():
            for r in c.execute('SELECT * FROM snapshots').fetchall():
                ts = datetime.fromisoformat(r['ts']).replace(tzinfo=timezone.utc).isoformat()
                c.execute('INSERT OR IGNORE INTO project_samples VALUES(?,?,?,?,?,?)', (ts,r['project'],r['progress'],r['commits'],r['hash'],r['message']))

def host_metrics():
    global CPU_PREV
    fields = [int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
    total, idle = sum(fields), fields[3]+fields[4]
    cpu = None
    if CPU_PREV and total > CPU_PREV[0]: cpu = round(100*(1-(idle-CPU_PREV[1])/(total-CPU_PREV[0])),1)
    CPU_PREV = (total,idle)
    m = {a:int(b.split()[0])*1024 for a,b in (line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())}
    d = shutil.disk_usage('/')
    return {'cpu':cpu, 'cores':os.cpu_count(), 'load':round(os.getloadavg()[0],2), 'uptime':int(float(Path('/proc/uptime').read_text().split()[0])), 'memory':{'total':m['MemTotal'],'used':m['MemTotal']-m['MemAvailable']}, 'disk':{'total':d.total,'used':d.used,'free':d.free}}

def service_states():
    out = cmd(['systemctl','show',*SERVICES,'--property=Id,ActiveState,SubState,Result,ExecMainExitTimestamp,LastTriggerUSec'])
    return {b['Id']:b for b in [dict(l.split('=',1) for l in block.splitlines() if '=' in l) for block in out.split('\n\n')] if 'Id' in b}

def user_service_states():
    states={}
    for name in USER_RUNTIME_SERVICES:
        try:
            raw=user_systemctl('show',name,'--property=Id,ActiveState,SubState,Result,ExecMainExitTimestamp,LastTriggerUSec')
            row=dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
            if row.get('Id'): states[row['Id']]=row
        except Exception:
            states[name]={'Id':name,'ActiveState':'unknown','SubState':'unknown','Result':'unknown'}
    return states

def replay_metrics():
    # Read-only connection, bounded query, no trainer commands and no state changes.
    with closing(sqlite3.connect('file:/var/lib/haxlab/state/haxlab.sqlite3?mode=ro',uri=True,timeout=1)) as c:
        deadline=time.monotonic()+1
        c.set_progress_handler(lambda: int(time.monotonic()>deadline),1000)
        counts=dict(c.execute('SELECT status,COUNT(*) FROM replay_analysis GROUP BY status'))
        r=c.execute("SELECT SUM(duration_seconds) FROM replay_processing WHERE status='ok'").fetchone()
    return {'analyzed':counts.get('ok',0),'failed':counts.get('failed',0),'pending':counts.get('pending',0),'hours':round((r[0] or 0)/3600,1),'available':True}

def collect():
    projects = json.loads((ROOT/'projects.json').read_text())
    errors=[]
    try: states=service_states()
    except Exception: states={}; errors.append('Servicestatus tijdelijk niet beschikbaar')
    try: user_states=user_service_states()
    except Exception: user_states={}; errors.append('User-servicestatus tijdelijk niet beschikbaar')
    events=[]
    resources=enhancements.resource_snapshot()
    for p in projects:
        values=[float(m.get('progress',100 if m.get('done') else 0)) for m in p['milestones']]
        for m,value in zip(p['milestones'],values):
            m['progress']=round(max(0,min(100,value)),1);m['done']=m['progress']>=100
        computed_progress=sum(m['progress'] for m in p['milestones'])/len(p['milestones'])
        p['progress']=round(float(p.pop('progress_override',computed_progress)))
        p['completed']=sum(m['done'] for m in p['milestones'])
        p['next']=next((m['title'] for m in p['milestones'] if not m['done']), 'Alle milestones afgerond')
        p['current_milestone_progress']=next((m['progress'] for m in p['milestones'] if not m['done']),100)
        p['resource']=resources.get(p['id'],{})
        p['source_status']='ok'; p['commits']=None; p['commit']=''; p['updated']=None; p['message']='Nog geen Git-bron'; p['shallow']=False
        path=p.pop('repo',None)
        repo_ref=p.pop('repo_ref','HEAD')
        if path:
            try:
                git(path,'rev-parse','--git-dir')
                active_ref=None
                valid_refs=[]
                for candidate in dict.fromkeys((repo_ref,'HEAD')):
                    try:
                        git(path,'rev-parse','--verify',candidate+'^{commit}')
                        ts=git(path,'show','-s','--format=%ct',candidate)
                        valid_refs.append((int(ts),candidate))
                    except Exception:
                        pass
                if valid_refs:
                    active_ref=max(valid_refs)[1]
                if active_ref is None:
                    p['source_status']='empty'; p['commits']=0; p['message']='Repo gekoppeld · nog geen commits'
                else:
                    logs=git(path,'log','-30',active_ref,'--format=%H%x1f%cI%x1f%s').splitlines()
                    for line in logs:
                        sha,ts,title=line.split('\x1f',2)
                        events.append((p['id']+':git:'+sha, datetime.fromisoformat(ts).astimezone(timezone.utc).isoformat(),p['id'],'commit',title,sha[:7]))
                    p['commit'],p['updated'],p['message']=logs[0].split('\x1f',2)
                    p['commit']=p['commit'][:7]
                    p['commits']=int(git(path,'rev-list','--count',active_ref))
                    p['shallow']=git(path,'rev-parse','--is-shallow-repository')=='true'
            except Exception:
                p['source_status']='unavailable'; p['message']='Git-bron tijdelijk niet beschikbaar'
        p['services']=[]
        if p['id']=='haxlab':
            names=['haxlab-analyzer.service','haxlab-ingest.service','haxlab-worker.service','haxlab-autonomy.timer','haxlab-autonomy.service',RUNNERS['haxlab']]
            try:p['metrics']=replay_metrics()
            except Exception:p['metrics']={'available':False}
            try:p['quality']=enhancements.quality_for('haxlab')
            except Exception:p['quality']={'available':False,'items':[]}
        elif p['id']=='ftmo':
            names=['ftmo-autonomous.timer','ftmo-autonomous.service',RUNNERS['ftmo']]
            try:p['quality']=enhancements.quality_for('ftmo')
            except Exception:p['quality']={'available':False,'items':[]}
        elif p['id']=='supa': names=[SUPA_SYNC]
        elif p['id']=='raiseai': names=['raise-gateway.service']
        elif p['id']=='zssh': names=['zssh.service']
        else: names=[]
        for name in names:
            s=(user_states if name in USER_RUNTIME_SERVICES else states).get(name,{})
            state=s.get('ActiveState','unknown')
            label=name.removesuffix('.service').removesuffix('.timer').replace('haxlab-','').replace('ftmo-autonomous','Research').split('.')[-1]
            if name in RUNNERS.values():label='GitHub runner'
            if name.endswith('.timer'):label='Research timer'
            if name==SUPA_SYNC:label='Repo sync'
            if name=='haxlab-autonomy.timer':label='Autonomy timer'
            if name=='haxlab-autonomy.service':label='Autonomy tick'
            if name=='raise-gateway.service':label='AI gateway'
            if name=='zssh.service':label='Remote gateway'
            if name=='ftmo-autonomous.service' and state=='inactive' and s.get('Result')=='success':state='waiting'
            p['services'].append({'name':label,'state':state,'result':s.get('Result'), 'last_run':s.get('ExecMainExitTimestamp') or None})
        p['health']='healthy' if not names or all(s['state'] in ('active','activating','waiting') for s in p['services']) else 'attention'
        p['last_chatgpt_run']=watch_last_run(p['id'])
    with connect() as c:
        c.executemany('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)',events)
    data={'version':3,'time':now(),'host':host_metrics(),'projects':projects,'errors':errors,'sampling':{'host_seconds':15,'projects_seconds':60,'history_seconds':300},'timezone':'Europe/Amsterdam'}
    try: enhancements.evaluate_alerts(data,runner_status(),DB)
    except Exception: logging.exception('Alert evaluation failed')
    return data

def persist(data):
    with connect() as c:
        for p in data['projects']:
            c.execute('INSERT OR IGNORE INTO project_samples VALUES(?,?,?,?,?,?)',(data['time'],p['id'],p['progress'],p['commits'],p['commit'],p['message']))
            eid=f"milestone:{p['id']}:{p['milestone_revision']}"
            c.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)',(eid,data['time'],p['id'],'milestone',f"Milestoneplan: {p['completed']} van {len(p['milestones'])} afgerond",'Planninginschatting · geen kwaliteitsscore'))
        h=data['host']
        c.execute('INSERT OR IGNORE INTO host_samples VALUES(?,?,?,?,?)',(data['time'],h['cpu'],h['memory']['used']/h['memory']['total']*100,h['disk']['used']/h['disk']['total']*100,h['load']))
        # About 106k small records per year. Retain one year of host samples.
        cutoff=datetime.fromtimestamp(time.time()-366*86400,timezone.utc).isoformat()
        c.execute('DELETE FROM host_samples WHERE ts < ?', (cutoff,))

def sampler():
    global CACHE
    tick=0
    while True:
        try:
            if tick%4==0 or CACHE is None: data=collect()
            else:
                with LOCK:data={**CACHE}
                data['time']=now();data['host']=host_metrics()
            data['stale']=False
            if tick%20==0:persist(data)
            with LOCK:CACHE=data
        except Exception:
            logging.exception('Monitor sampling failed')
            with LOCK:
                if CACHE:CACHE={**CACHE,'stale':True}
        tick+=1
        time.sleep(15)

def history(pid, days):
    cutoff=datetime.fromtimestamp(time.time()-days*86400,timezone.utc).isoformat() if days else ''
    with connect() as c:
        rows=c.execute('SELECT ts AS date,progress,commits,hash,message FROM project_samples WHERE project=? AND ts>=? ORDER BY ts',(pid,cutoff)).fetchall()
    # Preserve endpoints while keeping responses small on mobile.
    if len(rows)>1200:rows=rows[::max(1,len(rows)//1000)]+[rows[-1]]
    return [dict(r) for r in rows]

def watch_activity(pid, limit=8):
    with connect() as c:
        rows=c.execute('SELECT ts,kind,title,detail FROM events WHERE project=? ORDER BY ts DESC LIMIT ?',(pid,limit)).fetchall()
    return [dict(r) for r in rows]

def compact_history(pid, limit=24):
    with connect() as c:
        rows=c.execute('SELECT ts AS date,progress FROM project_samples WHERE project=? ORDER BY ts',(pid,)).fetchall()
    if not rows:
        return []
    step=max(1,len(rows)//limit)
    out=[dict(r) for r in rows[::step]]
    if out[-1]['date'] != rows[-1]['date']:
        out.append(dict(rows[-1]))
    return out[-limit:]

def _claim_lease_seconds(raw):
    try:
        seconds=int(raw)
    except Exception:
        seconds=300
    return max(15,min(3600,seconds))

def _claim_timestamp(seconds=0):
    return datetime.fromtimestamp(time.time()+seconds,timezone.utc).isoformat()

def _claim_payload(row):
    if not row:
        return None
    item=dict(row)
    try:
        item['metadata']=json.loads(item.pop('metadata_json') or '{}')
    except Exception:
        item['metadata']={}
        item.pop('metadata_json',None)
    return item

IMPROVEMENT_PROJECT_ID = 'cloud'
IMPROVEMENT_RUNNING = 'running'

def canonical_main_sha():
    # The production checkout is intentionally divergent, so its local refs may
    # lag canonical GitHub main. Prefer a read-only remote lookup and only fall
    # back to local refs when the remote is temporarily unavailable.
    try:
        remote=(cmd(['git','-C',str(ROOT),'ls-remote','--exit-code','origin','refs/heads/main']) or '').strip()
        value=(remote.split()[0] if remote else '').strip()
        if re.fullmatch(r'[0-9a-f]{40}',value,re.I):
            return value
    except Exception:
        pass
    for ref in ('origin/main', 'main', 'HEAD'):
        try:
            value=(cmd(['git','-C',str(ROOT),'rev-parse',ref]) or '').strip()
        except Exception:
            continue
        if re.fullmatch(r'[0-9a-f]{40}',value,re.I):
            return value
    return None

def improvement_loop_state(project_id=IMPROVEMENT_PROJECT_ID):
    with connect() as c:
        row=c.execute('SELECT * FROM improvement_loops WHERE project_id=?',(project_id,)).fetchone()
    if not row:
        return {'project_id':project_id,'state':IMPROVEMENT_RUNNING,'iteration_count':0,'clean_reviews':0,
                'stop_reason':None,'last_green_commit':None,'audit_result':None,'updated_at':None,'auto_continue':True}
    item=dict(row)
    item['auto_continue']=item['state']==IMPROVEMENT_RUNNING
    return item

def improvement_loop_record(project_id, signal, *, finish_gate_green=None, p0p1_open=None, audit_green=None):
    if project_id != IMPROVEMENT_PROJECT_ID:
        return improvement_loop_state(project_id)
    ts=now()
    with connect() as c:
        row=c.execute('SELECT * FROM improvement_loops WHERE project_id=?',(project_id,)).fetchone()
        if not row:
            c.execute("INSERT INTO improvement_loops(project_id,state,iteration_count,clean_reviews,updated_at) VALUES(?,?,?,?,?)",
                      (project_id,IMPROVEMENT_RUNNING,0,0,ts))
            row=c.execute('SELECT * FROM improvement_loops WHERE project_id=?',(project_id,)).fetchone()
        state=row['state']; iterations=int(row['iteration_count'] or 0); clean=int(row['clean_reviews'] or 0)
        stop_reason=row['stop_reason']; audit=row['audit_result']; green_commit=row['last_green_commit']
        if signal=='iteration' and state==IMPROVEMENT_RUNNING:
            iterations += 1
            if iterations >= 10:
                state='audit_required'; stop_reason='hard_iteration_limit_waiting_final_audit'
        elif signal=='review':
            review_green=bool(finish_gate_green) and not bool(p0p1_open)
            if state==IMPROVEMENT_RUNNING and review_green:
                clean += 1; green_commit=canonical_main_sha() or green_commit
                if clean >= 2:
                    state='finished'; stop_reason='two_consecutive_clean_senior_reviews'
            elif state==IMPROVEMENT_RUNNING:
                clean=0
            elif (
                state=='finished'
                and stop_reason=='two_consecutive_clean_senior_reviews'
                and not review_green
            ):
                # A later evidence-backed P0/P1 invalidates a clean-review finish.
                # Preserve iteration/provenance history; only reopen the finish gate.
                state=IMPROVEMENT_RUNNING
                clean=0
                stop_reason=None
        elif signal=='audit' and state=='audit_required':
            audit='green' if bool(audit_green) else 'failed'
            if bool(audit_green):
                state='finished'; stop_reason='hard_iteration_limit_final_audit_green'
                green_commit=canonical_main_sha() or green_commit
            else:
                state='audit_failed'; stop_reason='hard_iteration_limit_final_audit_failed'
        c.execute(
            'UPDATE improvement_loops SET state=?,iteration_count=?,clean_reviews=?,stop_reason=?,last_green_commit=?,audit_result=?,updated_at=? WHERE project_id=?',
            (state,iterations,clean,stop_reason,green_commit,audit,ts,project_id))
    return improvement_loop_state(project_id)

def improvement_loop_resume(project_id=IMPROVEMENT_PROJECT_ID):
    ts=now()
    with connect() as c:
        c.execute(
            'INSERT INTO improvement_loops(project_id,state,iteration_count,clean_reviews,stop_reason,last_green_commit,audit_result,updated_at) '
            'VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET state=excluded.state,iteration_count=0,clean_reviews=0,stop_reason=NULL,audit_result=NULL,updated_at=excluded.updated_at',
            (project_id,IMPROVEMENT_RUNNING,0,0,None,None,None,ts))
    return improvement_loop_state(project_id)

def _coordination_claim_rows_locked(connection,project_id,exclude_owner=None,ts=None):
    ts=ts or now()
    connection.execute('DELETE FROM task_claims WHERE lease_until<=?',(ts,))
    rows=connection.execute(
        'SELECT project_id,claim_key,owner_id,worker_id FROM task_claims WHERE project_id=? ORDER BY claim_key,owner_id,worker_id',
        (project_id,),
    ).fetchall()
    return [
        {'project_id':r['project_id'],'claim_key':r['claim_key'],'owner_id':r['owner_id'],'worker_id':r['worker_id']}
        for r in rows if not exclude_owner or r['owner_id']!=exclude_owner
    ]

def _coordination_claims_fingerprint_locked(connection,project_id,exclude_owner=None,ts=None):
    rows=_coordination_claim_rows_locked(connection,project_id,exclude_owner,ts)
    raw=json.dumps(rows,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(raw).hexdigest(),rows

def _coordination_claim_rows(project_id, exclude_owner=None):
    with connect() as c:
        return _coordination_claim_rows_locked(c,project_id,exclude_owner)

def _coordination_claims_fingerprint(project_id, exclude_owner=None):
    with connect() as c:
        return _coordination_claims_fingerprint_locked(c,project_id,exclude_owner)

def coordination_vps_health():
    checks={}
    try:
        checks['zcloud_service']=cmd(['systemctl','is-active','zennay-cloud.service'])=='active'
    except Exception:
        checks['zcloud_service']=False
    firefox=firefox_runner_status()
    checks['firefox_automation']=bool(firefox.get('active'))
    try:
        with connect() as c:
            quick=c.execute('PRAGMA quick_check').fetchone()
        checks['state_store']=bool(quick and str(quick[0]).lower()=='ok')
    except Exception:
        checks['state_store']=False
    return {'ok':all(checks.values()),'checks':checks}

def _preflight_external_evidence(payload,label):
    evidence=payload if isinstance(payload,dict) else {}
    if not evidence.get('checked'):
        raise ValueError(f'{label} moet als gecontroleerd zijn gemarkeerd')
    if label=='Notion':
        project_ref=str(evidence.get('project_ref') or '').strip()
        handoff_ref=str(evidence.get('handoff_ref') or '').strip()
        if not project_ref or not handoff_ref:
            raise ValueError('Notion project_ref en handoff_ref zijn verplicht')
        return {'checked':True,'project_ref':project_ref[:500],'handoff_ref':handoff_ref[:500]}
    repo=str(evidence.get('repo') or '').strip()
    main_sha=str(evidence.get('main_sha') or '').strip()
    open_prs=evidence.get('open_prs')
    branches=evidence.get('branches')
    if not repo or not re.fullmatch(r'[0-9a-f]{7,40}',main_sha,re.I):
        raise ValueError('GitHub repo en geldige main_sha zijn verplicht')
    if not isinstance(open_prs,list) or not isinstance(branches,list):
        raise ValueError('GitHub open_prs en branches moeten lijsten zijn')
    return {
        'checked':True,
        'repo':repo[:240],
        'main_sha':main_sha.lower(),
        'open_prs':[str(x)[:120] for x in open_prs[:100]],
        'branches':[str(x)[:200] for x in branches[:200]],
    }

def _canonical_github_repo(project_id):
    project=PROJECT_INDEX.get(project_id,{})
    repo_url=str(project.get('repo_url') or '').strip()
    if repo_url:
        match=re.search(r'github\.com[/:]([^/]+/[^/#]+)',repo_url,re.I)
        if match:return match.group(1).removesuffix('.git')
    path=str(project.get('repo') or (ROOT if project_id=='cloud' else '')).strip()
    if path:
        try:
            origin=git(path,'config','--get','remote.origin.url')
            match=re.search(r'github\.com[/:]([^/]+/[^/#]+)',origin,re.I)
            if match:return match.group(1).removesuffix('.git')
        except Exception:
            pass
    return None

def _verify_preflight_sources(project_id,notion_evidence,github_evidence):
    project=PROJECT_INDEX.get(project_id,{})
    expected_project=str(project.get('notion_url') or '').strip()
    expected_handoff=str(project.get('handoff_url') or '').strip()
    if expected_project and notion_evidence.get('project_ref')!=expected_project:
        raise ValueError('Notion project_ref wijkt af van de canonieke projectbron')
    if expected_handoff and notion_evidence.get('handoff_ref')!=expected_handoff:
        raise ValueError('Notion handoff_ref wijkt af van de canonieke handoff')
    expected_repo=_canonical_github_repo(project_id)
    if expected_repo and str(github_evidence.get('repo') or '').lower().removesuffix('.git')!=expected_repo.lower():
        raise ValueError('GitHub repo wijkt af van de canonieke projectrepo')

def worker_preflight_record(project_id,worker_id,owner_id,notion,github):
    project_id=str(project_id or '').strip()[:80]
    worker_id=str(worker_id or '').strip()[:160]
    owner_id=str(owner_id or '').strip()[:160]
    if not project_id or not worker_id or not owner_id:
        raise ValueError('project_id, worker_id en owner_id zijn verplicht')
    target=runner_worker_targets().get(worker_id)
    if not target or target.get('base_project_id')!=project_id:
        raise ValueError('worker_id hoort niet bij dit project')
    notion_evidence=_preflight_external_evidence(notion,'Notion')
    github_evidence=_preflight_external_evidence(github,'GitHub')
    _verify_preflight_sources(project_id,notion_evidence,github_evidence)
    vps=coordination_vps_health()
    if not vps.get('ok'):
        return {'ok':False,'blocked':'vps_unhealthy','vps':vps}
    fingerprint,claims=_coordination_claims_fingerprint(project_id,owner_id)
    checked=datetime.now(timezone.utc)
    expires=checked+timedelta(seconds=WORKER_PREFLIGHT_TTL_SECONDS)
    with connect() as c:
        c.execute(
            """INSERT INTO worker_preflights(
                 project_id,worker_id,owner_id,checked_at,expires_at,notion_json,github_json,
                 claims_fingerprint,claims_json,vps_json
               ) VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(project_id,worker_id,owner_id) DO UPDATE SET
                 checked_at=excluded.checked_at,expires_at=excluded.expires_at,
                 notion_json=excluded.notion_json,github_json=excluded.github_json,
                 claims_fingerprint=excluded.claims_fingerprint,claims_json=excluded.claims_json,
                 vps_json=excluded.vps_json""",
            (
                project_id,worker_id,owner_id,checked.isoformat(),expires.isoformat(),
                json.dumps(notion_evidence,ensure_ascii=False,separators=(',',':')),
                json.dumps(github_evidence,ensure_ascii=False,separators=(',',':')),
                fingerprint,json.dumps(claims,ensure_ascii=False,separators=(',',':')),
                json.dumps(vps,ensure_ascii=False,separators=(',',':')),
            ),
        )
    return worker_preflight_state(project_id,worker_id,owner_id)

def _worker_preflight_state_locked(connection,project_id,worker_id,owner_id,ts=None):
    row=connection.execute(
        'SELECT * FROM worker_preflights WHERE project_id=? AND worker_id=? AND owner_id=?',
        (project_id,worker_id,owner_id),
    ).fetchone()
    if not row:
        return {'ok':False,'blocked':'preflight_required','project_id':project_id,'worker_id':worker_id,'owner_id':owner_id}
    try:
        reference=datetime.fromisoformat(ts).astimezone(timezone.utc) if ts else datetime.now(timezone.utc)
    except Exception:
        reference=datetime.now(timezone.utc)
    try:
        expired=datetime.fromisoformat(row['expires_at']).astimezone(timezone.utc)<=reference
    except Exception:
        expired=True
    fingerprint,current_claims=_coordination_claims_fingerprint_locked(
        connection,project_id,owner_id,ts or reference.isoformat()
    )
    claims_changed=fingerprint!=row['claims_fingerprint']
    try:notion=json.loads(row['notion_json'])
    except Exception:notion={}
    try:github=json.loads(row['github_json'])
    except Exception:github={}
    try:vps=json.loads(row['vps_json'])
    except Exception:vps={'ok':False,'checks':{}}
    return {
        'ok':not expired and not claims_changed,
        'blocked':'preflight_expired' if expired else 'claim_landscape_changed' if claims_changed else None,
        'project_id':project_id,
        'worker_id':worker_id,
        'owner_id':owner_id,
        'checked_at':row['checked_at'],
        'expires_at':row['expires_at'],
        'notion':notion,
        'github':github,
        'claims':current_claims,
        'vps':vps,
    }

def worker_preflight_state(project_id,worker_id,owner_id):
    with connect() as c:
        return _worker_preflight_state_locked(c,project_id,worker_id,owner_id)

def _normalize_conflict_scope(metadata):
    metadata=metadata if isinstance(metadata,dict) else {}
    raw=metadata.get('conflict_scope')
    if raw is None:
        return None
    if not isinstance(raw,dict):
        raise ValueError('metadata.conflict_scope moet een object zijn')
    capabilities=[]
    for value in raw.get('capabilities') or []:
        item=re.sub(r'[^a-z0-9._:/-]+','-',str(value or '').strip().lower()).strip('-')
        if item and item not in capabilities:
            capabilities.append(item[:160])
    files=[]
    for value in raw.get('files') or []:
        item=str(value or '').strip().replace('\\','/')
        while item.startswith('./'):
            item=item[2:]
        item=item.strip('/')
        if not item:
            continue
        parts=[part for part in item.split('/') if part not in ('','.')]
        if not parts or any(part=='..' for part in parts):
            raise ValueError('conflict_scope files moeten veilige repo-relatieve paden zijn')
        item='/'.join(parts)
        if item not in files:
            files.append(item[:300])
    return {'capabilities':capabilities[:100],'files':files[:200]}

def _conflict_scope_overlap(left,right):
    left=left or {'capabilities':[],'files':[]}
    right=right or {'capabilities':[],'files':[]}
    shared_caps=sorted(set(left.get('capabilities') or []) & set(right.get('capabilities') or []))
    shared_files=[]
    for a in left.get('files') or []:
        for b in right.get('files') or []:
            if a==b or a.startswith(b.rstrip('/')+'/') or b.startswith(a.rstrip('/')+'/'):
                pair=a if len(a)<=len(b) else b
                if pair not in shared_files:
                    shared_files.append(pair)
    return {'capabilities':shared_caps,'files':sorted(shared_files)}

def _claim_scope_conflict(connection,project_id,owner_id,new_scope,ts):
    if not new_scope or (not new_scope.get('capabilities') and not new_scope.get('files')):
        return None
    rows=connection.execute(
        'SELECT * FROM task_claims WHERE project_id=? AND owner_id<>? AND lease_until>? ORDER BY acquired_at,claim_key',
        (project_id,owner_id,ts),
    ).fetchall()
    for row in rows:
        try:
            metadata=json.loads(row['metadata_json'] or '{}')
            existing_scope=_normalize_conflict_scope(metadata)
        except Exception:
            return {
                'claim':_claim_payload(row),
                'claim_key':row['claim_key'],
                'owner_id':row['owner_id'],
                'worker_id':row['worker_id'],
                'overlap':{'capabilities':['unreadable-claim-metadata'],'files':[]},
                'reason':'unreadable_claim_metadata',
            }
        if not existing_scope:
            continue
        overlap=_conflict_scope_overlap(new_scope,existing_scope)
        if overlap['capabilities'] or overlap['files']:
            return {
                'claim':_claim_payload(row),
                'claim_key':row['claim_key'],
                'owner_id':row['owner_id'],
                'worker_id':row['worker_id'],
                'overlap':overlap,
            }
    return None

def _claim_metadata_json(metadata):
    raw=json.dumps(metadata,ensure_ascii=False,separators=(',',':'))
    if len(raw.encode('utf-8')) > TASK_CLAIM_METADATA_MAX_BYTES:
        raise ValueError(f'claimmetadata is te groot; maximum is {TASK_CLAIM_METADATA_MAX_BYTES} bytes')
    return raw

def task_claims(project_id=None):
    ts=now()
    with connect() as c:
        c.execute('DELETE FROM task_claims WHERE lease_until<=?',(ts,))
        if project_id:
            rows=c.execute('SELECT * FROM task_claims WHERE project_id=? ORDER BY claim_key',(project_id,)).fetchall()
        else:
            rows=c.execute('SELECT * FROM task_claims ORDER BY project_id,claim_key').fetchall()
    return [_claim_payload(row) for row in rows]

def _claim_candidate(project_id,claim_key,metadata):
    claim_key=str(claim_key or '').strip()[:240]
    if not claim_key:
        raise ValueError('claim_key is verplicht')
    metadata=metadata if isinstance(metadata,dict) else {}
    scope=_normalize_conflict_scope(metadata)
    if scope is not None:
        metadata={**metadata,'conflict_scope':scope}
    if project_id==IMPROVEMENT_PROJECT_ID and metadata.get('loop')=='self_improvement':
        if not scope or (not scope.get('capabilities') and not scope.get('files')):
            raise ValueError('metadata.conflict_scope is verplicht voor autonome zCloud-writes')
    return {
        'claim_key':claim_key,
        'metadata':metadata,
        'scope':scope,
        'metadata_json':_claim_metadata_json(metadata),
    }

def _claim_candidates(project_id,claim_key,metadata,alternatives=None):
    primary=_claim_candidate(project_id,claim_key,metadata)
    items=[primary]
    raw=alternatives or []
    if not isinstance(raw,list):
        raise ValueError('alternatives moet een lijst zijn')
    if len(raw)>TASK_CLAIM_ALTERNATIVE_MAX:
        raise ValueError(f'alternatives mag maximaal {TASK_CLAIM_ALTERNATIVE_MAX} kandidaten bevatten')
    primary_self_improvement=(
        project_id==IMPROVEMENT_PROJECT_ID and
        primary['metadata'].get('loop')=='self_improvement'
    )
    seen={primary['claim_key']}
    for index,entry in enumerate(raw):
        if not isinstance(entry,dict):
            raise ValueError(f'alternatives[{index}] moet een object zijn')
        candidate=_claim_candidate(project_id,entry.get('claim_key'),entry.get('metadata'))
        if candidate['claim_key'] in seen:
            raise ValueError('alternatieve claim_keys moeten uniek zijn')
        if primary_self_improvement:
            if candidate['metadata'].get('loop')!='self_improvement':
                raise ValueError('alternatieve zCloud-taken moeten loop=self_improvement behouden')
            if not candidate['scope'] or (
                not candidate['scope'].get('capabilities') and
                not candidate['scope'].get('files')
            ):
                raise ValueError('alternatieve zCloud-taken vereisen een eigen conflict_scope')
            if not str(candidate['metadata'].get('task') or '').strip():
                raise ValueError('alternatieve zCloud-taken vereisen metadata.task')
        seen.add(candidate['claim_key'])
        items.append(candidate)
    return items

def _attempt_claim_candidate(connection,project_id,candidate,owner_id,worker_id,ts,until):
    conflict=_claim_scope_conflict(connection,project_id,owner_id,candidate['scope'],ts)
    if conflict:
        return {
            'acquired':False,
            'blocked':'scope_conflict',
            'claim':conflict['claim'],
            'conflict':conflict,
        }
    cur=connection.execute(
        """INSERT INTO task_claims(project_id,claim_key,owner_id,worker_id,acquired_at,heartbeat_at,lease_until,metadata_json)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(project_id,claim_key) DO UPDATE SET
             owner_id=excluded.owner_id,
             worker_id=excluded.worker_id,
             acquired_at=CASE WHEN task_claims.owner_id=excluded.owner_id THEN task_claims.acquired_at ELSE excluded.acquired_at END,
             heartbeat_at=excluded.heartbeat_at,
             lease_until=excluded.lease_until,
             metadata_json=excluded.metadata_json
           WHERE task_claims.owner_id=excluded.owner_id OR task_claims.lease_until<=excluded.acquired_at""",
        (project_id,candidate['claim_key'],owner_id,worker_id,ts,ts,until,candidate['metadata_json'])
    )
    row=connection.execute(
        'SELECT * FROM task_claims WHERE project_id=? AND claim_key=?',
        (project_id,candidate['claim_key'])
    ).fetchone()
    acquired=bool(cur.rowcount>0 and row and row['owner_id']==owner_id)
    if acquired:
        return {'acquired':True,'claim':_claim_payload(row)}
    return {
        'acquired':False,
        'blocked':'task_conflict',
        'claim':_claim_payload(row),
        'conflict':{
            'claim':_claim_payload(row),
            'claim_key':candidate['claim_key'],
            'owner_id':row['owner_id'] if row else None,
            'worker_id':row['worker_id'] if row else None,
            'overlap':{'capabilities':[],'files':[]},
        } if row else None,
    }

def task_claim_acquire(project_id,claim_key,owner_id,worker_id='',lease_seconds=300,metadata=None,alternatives=None,require_preflight=False):
    project_id=str(project_id or '').strip()[:80]
    owner_id=str(owner_id or '').strip()[:160]
    worker_id=str(worker_id or '').strip()[:160]
    if not project_id or not owner_id:
        raise ValueError('project_id en owner_id zijn verplicht')
    metadata=metadata if isinstance(metadata,dict) else {}
    if project_id==IMPROVEMENT_PROJECT_ID and metadata.get('loop')=='self_improvement' and not improvement_loop_state(project_id)['auto_continue']:
        return {'acquired':False,'blocked':'improvement_loop_finished','claim':None}
    candidates=_claim_candidates(project_id,claim_key,metadata,alternatives)
    lease_seconds=_claim_lease_seconds(lease_seconds)
    ts=now()
    until=_claim_timestamp(lease_seconds)
    attempted=[]
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM task_claims WHERE lease_until<=?',(ts,))
        if require_preflight:
            if not worker_id:
                return {'acquired':False,'blocked':'worker_identity_required','claim':None}
            atomic_preflight=_worker_preflight_state_locked(c,project_id,worker_id,owner_id,ts)
            if not atomic_preflight.get('ok'):
                return {
                    'acquired':False,
                    'blocked':atomic_preflight.get('blocked') or 'preflight_required',
                    'claim':None,
                    'preflight':atomic_preflight,
                }
        for index,candidate in enumerate(candidates):
            result=_attempt_claim_candidate(c,project_id,candidate,owner_id,worker_id,ts,until)
            if result['acquired']:
                return {
                    **result,
                    'selected_claim_key':candidate['claim_key'],
                    'selected_from':'primary' if index==0 else 'alternative',
                    'selected_index':index-1 if index else None,
                    'attempted':attempted,
                }
            attempted.append({
                'claim_key':candidate['claim_key'],
                'blocked':result.get('blocked'),
                'conflict':result.get('conflict'),
            })
    if len(candidates)==1:
        result=attempted[0]
        return {
            'acquired':False,
            'blocked':result.get('blocked'),
            'claim':(result.get('conflict') or {}).get('claim'),
            'conflict':result.get('conflict'),
        }
    return {
        'acquired':False,
        'blocked':'no_safe_alternative',
        'claim':(attempted[0].get('conflict') or {}).get('claim') if attempted else None,
        'conflict':attempted[0].get('conflict') if attempted else None,
        'attempted':attempted,
    }

def task_claim_heartbeat(project_id,claim_key,owner_id,lease_seconds=300):
    lease_seconds=_claim_lease_seconds(lease_seconds)
    ts=now()
    until=_claim_timestamp(lease_seconds)
    with connect() as c:
        cur=c.execute(
            'UPDATE task_claims SET heartbeat_at=?,lease_until=? WHERE project_id=? AND claim_key=? AND owner_id=? AND lease_until>?',
            (ts,until,project_id,claim_key,owner_id,ts)
        )
        row=c.execute('SELECT * FROM task_claims WHERE project_id=? AND claim_key=?',(project_id,claim_key)).fetchone()
    return {'renewed':cur.rowcount==1,'claim':_claim_payload(row)}

def task_claim_release(project_id,claim_key,owner_id):
    with connect() as c:
        cur=c.execute('DELETE FROM task_claims WHERE project_id=? AND claim_key=? AND owner_id=?',(project_id,claim_key,owner_id))
    return {'released':cur.rowcount==1}

def load_autonomy_policy():
    default={
        'schema_version':1,
        'default':{
            'mode':'ai_worker','auto_start':True,'dispatch_mode':'vps','continue_delay_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,
            'min_ai_interval_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,
            'wait_vps_seconds':900,'wait_human_seconds':21600,'complete_recheck_seconds':86400,
        },
        'projects':{
            'cloud':{
                'mode':'zcloud_stopgate','auto_start':True,'dispatch_mode':'vps',
                'continue_delay_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,'min_ai_interval_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,
            },
            'haxlab':{
                'mode':'haxlab_status','auto_start':True,'dispatch_mode':'vps',
                'status_file':'/var/lib/haxlab/state/autonomy-status.json',
                'ai_states':['NEEDS_AI'],'continue_delay_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,'min_ai_interval_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,
            },
            'ftmo':{
                'mode':'ftmo_status','auto_start':True,'dispatch_mode':'vps',
                'status_file':'/opt/ftmo-autonomous/.scratch/autonomy/status.json',
                'ai_stages':[
                    'provider_foundation','freeze_data_split','await_preregistration','development',
                    'development_review','close_development_reject','walk_forward',
                    'close_walk_forward_reject','final_holdout','close_validated','next_generation_design',
                ],
                'continue_delay_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,'min_ai_interval_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,
            },
            'ulab':{
                'mode':'external_gate','auto_start':False,'dispatch_mode':'vps',
                'continue_delay_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,'min_ai_interval_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,
            },
            'supa':{
                'mode':'ai_worker','auto_start':True,'dispatch_mode':'vps',
                'continue_delay_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,'min_ai_interval_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,
            },
            'raiseai':{
                'mode':'ai_worker','auto_start':True,'dispatch_mode':'vps',
                'continue_delay_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,'min_ai_interval_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,
            },
            'zssh':{
                'mode':'ai_worker','auto_start':True,'dispatch_mode':'vps',
                'continue_delay_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,'min_ai_interval_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,
            },
        },
    }
    if not AUTONOMY_POLICY_FILE.exists():
        return default
    try:
        raw=json.loads(AUTONOMY_POLICY_FILE.read_text(encoding='utf-8'))
        if not isinstance(raw,dict) or raw.get('schema_version')!=1:
            raise ValueError('schema_version must be 1')
        base={**default['default'],**(raw.get('default') if isinstance(raw.get('default'),dict) else {})}
        file_projects=raw.get('projects') if isinstance(raw.get('projects'),dict) else {}
        projects={**default['projects'],**file_projects}
        return {'schema_version':1,'default':base,'projects':projects}
    except Exception:
        logging.exception('Invalid autonomy policy; fail closed')
        return {'schema_version':1,'default':{**default['default'],'mode':'manual','auto_start':False},'projects':{}}

def _autonomy_config(project_id):
    policy=load_autonomy_policy()
    cfg={**policy['default']}
    override=policy.get('projects',{}).get(project_id)
    if isinstance(override,dict):
        cfg.update(override)
    try: cfg['continue_delay_seconds']=max(0,min(3600,int(cfg.get('continue_delay_seconds') if cfg.get('continue_delay_seconds') is not None else PORTFOLIO_AI_COOLDOWN_SECONDS)))
    except Exception: cfg['continue_delay_seconds']=PORTFOLIO_AI_COOLDOWN_SECONDS
    try: cfg['min_ai_interval_seconds']=max(0,min(24*3600,int(cfg.get('min_ai_interval_seconds') if cfg.get('min_ai_interval_seconds') is not None else cfg['continue_delay_seconds'])))
    except Exception: cfg['min_ai_interval_seconds']=cfg['continue_delay_seconds']
    for key,fallback in (('wait_vps_seconds',900),('wait_human_seconds',21600),('complete_recheck_seconds',86400)):
        try: cfg[key]=max(60,min(7*86400,int(cfg.get(key) or fallback)))
        except Exception: cfg[key]=fallback
    cfg['auto_start']=bool(cfg.get('auto_start'))
    cfg['dispatch_mode']='vps' if str(cfg.get('dispatch_mode') or 'vps').lower()=='vps' else 'browser'
    return cfg

def _autonomy_status_json(path):
    try:
        target=Path(str(path or ''))
        if not target.is_absolute() or target.is_symlink() or not target.is_file():
            return None
        raw=json.loads(target.read_text(encoding='utf-8'))
        return raw if isinstance(raw,dict) else None
    except Exception:
        return None

def _latest_autonomy_signal(project_id):
    try:
        with connect() as c:
            placeholders=','.join('?' for _ in AUTONOMY_SIGNAL_EVENTS)
            row=c.execute(
                f'SELECT ts,event,reason FROM runner_events WHERE project_id=? AND event IN ({placeholders}) ORDER BY id DESC LIMIT 1',
                (project_id,*AUTONOMY_SIGNAL_EVENTS),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None

def _autonomy_signal_hold(project_id,cfg):
    signal=_latest_autonomy_signal(project_id)
    if not signal:
        return None
    event=str(signal.get('event') or '')
    if event=='autonomy-continue':
        return None
    seconds={
        'autonomy-wait-vps':cfg['wait_vps_seconds'],
        'autonomy-wait-human':cfg['wait_human_seconds'],
        'autonomy-complete':cfg['complete_recheck_seconds'],
    }.get(event)
    if not seconds:
        return None
    try:
        age=max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(signal['ts']).astimezone(timezone.utc)).total_seconds())
    except Exception:
        age=0
    if age>=seconds:
        return None
    return {'event':event,'age_seconds':round(age),'hold_seconds':seconds,'until_seconds':round(seconds-age)}

def project_autonomy_state(project_id):
    cfg=_autonomy_config(project_id)
    mode=str(cfg.get('mode') or 'manual')
    allow=False
    reason='manual_or_unknown_mode'
    detail={}
    if mode=='ai_worker':
        allow=True;reason='bounded_ai_worker'
    elif mode=='zcloud_stopgate':
        improvement=improvement_loop_state(project_id)
        allow=bool(improvement.get('auto_continue'));reason='zcloud_improvement_running' if allow else 'zcloud_finished_maintain'
        detail={'improvement':improvement}
    elif mode=='haxlab_status':
        status=_autonomy_status_json(cfg.get('status_file'))
        state=str((status or {}).get('state') or 'MISSING')
        allowed={str(x) for x in (cfg.get('ai_states') or ['NEEDS_AI'])}
        allow=status is None or state in allowed
        reason='haxlab_status_missing' if status is None else ('haxlab_needs_ai' if allow else 'haxlab_vps_'+state.lower())
        detail={'vps_status':status,'state':state}
    elif mode=='ftmo_status':
        status=_autonomy_status_json(cfg.get('status_file'))
        research=(status or {}).get('research') if isinstance((status or {}).get('research'),dict) else {}
        stage=str(research.get('next_stage') or 'missing')
        paper=(status or {}).get('paper_forward_shadow') if isinstance((status or {}).get('paper_forward_shadow'),dict) else {}
        allowed={str(x) for x in (cfg.get('ai_stages') or [])}
        runtime_ok=bool((status or {}).get('ok'))
        paper_busy=paper.get('action')=='running'
        allow=status is None or (runtime_ok and stage in allowed and not paper_busy)
        reason='ftmo_status_missing' if status is None else (('ftmo_stage_'+stage) if allow else ('ftmo_paper_running' if paper_busy else 'ftmo_vps_'+('not_ok' if not runtime_ok else stage)))
        detail={'vps_status':status,'next_stage':stage,'paper_action':paper.get('action')}
    elif mode=='external_gate':
        allow=False;reason='external_or_human_gate'
    elif mode=='manual':
        allow=False;reason='manual_mode'

    hold=_autonomy_signal_hold(project_id,cfg)
    if allow and hold:
        allow=False;reason=hold['event']
    return {
        'project_id':project_id,'mode':mode,'allow_ai':bool(allow),'auto_start':cfg['auto_start'],
        'dispatch_mode':cfg['dispatch_mode'],'continue_delay_seconds':cfg['continue_delay_seconds'],
        'min_ai_interval_seconds':cfg['min_ai_interval_seconds'],'reason':reason,'hold':hold,'detail':detail,
    }

def autonomy_states():
    return {project_id:project_autonomy_state(project_id) for project_id in RUNNER_DEFAULTS}

def _autonomy_runtime(project_id):
    with connect() as c:
        row=c.execute('SELECT * FROM autonomy_runtime WHERE project_id=?',(project_id,)).fetchone()
    return dict(row) if row else None

def _autonomy_initialize_project(project_id):
    ts=now()
    with connect() as c:
        c.execute('INSERT OR IGNORE INTO autonomy_runtime(project_id,initialized_at,manual_pause,last_reason) VALUES(?,?,0,?)',
                  (project_id,ts,'autonomy_initialized'))
        row=c.execute('SELECT * FROM autonomy_runtime WHERE project_id=?',(project_id,)).fetchone()
    return dict(row) if row else None

def _autonomy_enqueue_start(project_id,reason):
    ts=now()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        runtime=c.execute('SELECT * FROM autonomy_runtime WHERE project_id=?',(project_id,)).fetchone()
        if runtime and bool(runtime['manual_pause']):
            return False
        target=c.execute('SELECT active FROM runner_targets WHERE project_id=?',(project_id,)).fetchone()
        if not target or bool(target['active']):
            return False
        pending=c.execute("SELECT id FROM runner_commands WHERE project_id=? AND action IN ('start','push') AND status='pending' LIMIT 1",(project_id,)).fetchone()
        if pending:
            return False
        c.execute('UPDATE runner_targets SET active=1 WHERE project_id=?',(project_id,))
        c.execute('INSERT INTO runner_commands(project_id,action,status,created_at,updated_at,result) VALUES(?,?,?,?,?,?)',
                  (project_id,'start','pending',ts,ts,None))
        c.execute('UPDATE autonomy_runtime SET last_reason=? WHERE project_id=?',(str(reason)[:250],project_id))
    return True

def _autonomy_dispatch_due(runtime,min_interval_seconds):
    try:
        min_interval_seconds=max(0,int(min_interval_seconds or 0))
    except Exception:
        min_interval_seconds=0
    if min_interval_seconds<=0 or not runtime or not runtime.get('last_dispatch_at'):
        return True
    try:
        last=datetime.fromisoformat(runtime['last_dispatch_at']).astimezone(timezone.utc)
        return (datetime.now(timezone.utc)-last).total_seconds() >= min_interval_seconds
    except Exception:
        return True

def _autonomy_enqueue_push(project_id,reason,min_interval_seconds):
    ts=now()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        runtime=c.execute('SELECT * FROM autonomy_runtime WHERE project_id=?',(project_id,)).fetchone()
        if runtime and bool(runtime['manual_pause']):
            return False
        if not _autonomy_dispatch_due(dict(runtime) if runtime else None,min_interval_seconds):
            return False
        target=c.execute('SELECT active FROM runner_targets WHERE project_id=?',(project_id,)).fetchone()
        if not target or not bool(target['active']):
            return False
        pending=c.execute("SELECT id FROM runner_commands WHERE project_id=? AND action IN ('start','push','new_chat') AND status='pending' LIMIT 1",(project_id,)).fetchone()
        if pending:
            return False
        latest=c.execute('SELECT id,generating,sending,event FROM runner_events WHERE project_id=? ORDER BY id DESC LIMIT 1',(project_id,)).fetchone()
        if latest and (bool(latest['generating']) or bool(latest['sending'])):
            return False
        last_prompt=c.execute("SELECT MAX(id) AS id FROM runner_events WHERE project_id=? AND event='prompt-sent'",(project_id,)).fetchone()
        last_ready=c.execute("SELECT MAX(id) AS id FROM runner_events WHERE project_id=? AND event IN ('awaiting-vps-dispatch','generation-not-started')",(project_id,)).fetchone()
        prompt_id=int((last_prompt or {'id':0})['id'] or 0)
        ready_id=int((last_ready or {'id':0})['id'] or 0)
        if prompt_id and ready_id < prompt_id:
            return False
        c.execute('INSERT INTO runner_commands(project_id,action,status,created_at,updated_at,result) VALUES(?,?,?,?,?,?)',
                  (project_id,'push','pending',ts,ts,None))
        c.execute('UPDATE autonomy_runtime SET last_dispatch_at=?,last_reason=? WHERE project_id=?',
                  (ts,str(reason)[:250],project_id))
    return True


def _latest_ai_priority_hint(project_id):
    try:
        with connect() as c:
            row=c.execute(
                "SELECT ts,reason FROM runner_events WHERE project_id=? AND event='autonomy-priority' ORDER BY id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        if not row:
            return 0
        try:
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(row['ts']).astimezone(timezone.utc)).total_seconds()
            if age>6*3600:
                return 0
        except Exception:
            pass
        return {'high':900,'normal':200,'low':-250,'background':-700}.get(str(row['reason'] or '').lower(),0)
    except Exception:
        return 0

def _busy_ai_worker_keys():
    busy=set()
    cutoff=datetime.fromtimestamp(time.time()-30*60,timezone.utc).isoformat()
    try:
        with connect() as c:
            rows=c.execute("""
                SELECT e.project_id,e.worker_slot,e.generating,e.sending,e.ts
                FROM runner_events e
                JOIN (
                    SELECT project_id,worker_slot,MAX(id) AS id
                    FROM runner_events
                    WHERE project_id IS NOT NULL
                    GROUP BY project_id,worker_slot
                ) latest ON latest.id=e.id
                WHERE e.ts>=?
            """,(cutoff,)).fetchall()
        for row in rows:
            if bool(row['generating']) or bool(row['sending']):
                busy.add(f"{row['project_id']}::w{max(1,int(row['worker_slot'] or 1))}")
    except Exception:
        logging.exception('Could not read busy AI workers for allocator')
    return busy

def _worker_desired_states():
    try:
        with connect() as c:
            rows=c.execute('SELECT project_id,worker_slot,desired_state FROM runner_workers').fetchall()
        return {
            f"{row['project_id']}::w{max(1,int(row['worker_slot'] or 1))}":str(row['desired_state'] or 'running')
            for row in rows
        }
    except Exception:
        return {}

def _project_ai_priority_score(project_id,state,target,resource_policy):
    resource_name=str((resource_policy.get(project_id) or {}).get('priority') or 'normal')
    score={'background':0,'normal':400,'high':800,'turbo':1200}.get(resource_name,400)
    project_priority=str((PROJECT_INDEX.get(project_id) or {}).get('priority') or 'normal')
    score += {'low':0,'normal':200,'high':500,'system':700}.get(project_priority,200)
    reason=str(state.get('reason') or '')
    if reason=='haxlab_needs_ai':
        score += 1000
    elif reason.startswith('ftmo_stage_'):
        score += 700
    elif reason.endswith('_status_missing') or reason in ('haxlab_status_missing','ftmo_status_missing'):
        score += 850
    elif reason=='zcloud_improvement_running':
        score += 600
    elif reason=='bounded_ai_worker':
        score += 300
    score += _latest_ai_priority_hint(project_id)
    if bool(target.get('active')):
        score += 120
    runtime=_autonomy_runtime(project_id)
    if runtime and runtime.get('last_dispatch_at'):
        try:
            age=max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(runtime['last_dispatch_at']).astimezone(timezone.utc)).total_seconds())
            score += min(300,int(age/12))
        except Exception:
            pass
    else:
        score += 300
    return int(score)

def global_worker_allocation(states=None,targets=None):
    states=states or autonomy_states()
    targets=targets or runner_targets()
    resource_policy=enhancements.load_resource_policy()
    busy=_busy_ai_worker_keys()
    desired=_worker_desired_states()
    currently_allocated=_current_global_slot_keys()
    candidates=[]
    for project_id,target in targets.items():
        state=states.get(project_id) or project_autonomy_state(project_id)
        if not state.get('auto_start') or not state.get('allow_ai'):
            continue
        runtime=_autonomy_runtime(project_id)
        if runtime and bool(runtime.get('manual_pause')):
            continue
        base_score=_project_ai_priority_score(project_id,state,target,resource_policy)
        project_cap=max(1,min(GLOBAL_CHATGPT_WORKER_LIMIT,int(target.get('worker_count') or 1)))
        for slot in range(1,project_cap+1):
            worker_key=f'{project_id}::w{slot}'
            worker_state=desired.get(worker_key,'running')
            if worker_state=='paused':
                continue
            if worker_state=='draining' and worker_key not in currently_allocated:
                continue
            score=base_score-(slot-1)*AI_SLOT_DIVERSITY_PENALTY
            if worker_key in busy or worker_state=='draining':
                score += 100000
            candidates.append({
                'worker_key':worker_key,'project_id':project_id,'worker_slot':slot,
                'score':score,'reason':state.get('reason') or 'eligible',
                'desired_state':worker_state,
            })
    candidates.sort(key=lambda item:(-item['score'],item['project_id'],item['worker_slot']))
    selected=candidates[:GLOBAL_CHATGPT_WORKER_LIMIT]
    return {
        'limit':GLOBAL_CHATGPT_WORKER_LIMIT,
        'workers':selected,
        'keys':[item['worker_key'] for item in selected],
        'projects':sorted({item['project_id'] for item in selected}),
        'candidates':candidates,
        'queue_url':PORTFOLIO_QUEUE_URL,
        'queue_data_source':PORTFOLIO_QUEUE_DATA_SOURCE,
        'dispatch_cooldown_seconds':PORTFOLIO_AI_COOLDOWN_SECONDS,
    }

def _persist_global_worker_allocation(allocation):
    selected=list(allocation.get('workers') or [])[:GLOBAL_CHATGPT_WORKER_LIMIT]
    ts=now()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM ai_global_slots')
        for global_slot,item in enumerate(selected,1):
            c.execute(
                'INSERT INTO ai_global_slots(slot,project_id,worker_slot,assigned_at) VALUES(?,?,?,?)',
                (global_slot,str(item['project_id']),int(item['worker_slot']),ts),
            )

def _current_global_slot_keys():
    try:
        with connect() as c:
            rows=c.execute('SELECT project_id,worker_slot FROM ai_global_slots ORDER BY slot LIMIT ?',
                           (GLOBAL_CHATGPT_WORKER_LIMIT,)).fetchall()
            if rows:
                return {
                    f"{row['project_id']}::w{max(1,int(row['worker_slot'] or 1))}"
                    for row in rows
                }
            # Safe bootstrap before the scheduler's first tick: preserve at most two
            # already-active workers, never the old per-project total.
            targets=c.execute(
                'SELECT project_id,worker_count FROM runner_targets WHERE active=1 ORDER BY project_id'
            ).fetchall()
            selected=[]
            for target in targets:
                project_id=str(target['project_id'])
                count=max(1,min(GLOBAL_CHATGPT_WORKER_LIMIT,int(target['worker_count'] or 1)))
                for slot in range(1,count+1):
                    row=c.execute(
                        'SELECT desired_state FROM runner_workers WHERE project_id=? AND worker_slot=?',
                        (project_id,slot),
                    ).fetchone()
                    if row and str(row['desired_state'] or 'running')=='paused':
                        continue
                    selected.append(f'{project_id}::w{slot}')
                    if len(selected)>=GLOBAL_CHATGPT_WORKER_LIMIT:
                        return set(selected)
            return set(selected)
    except Exception:
        logging.exception('Could not read global AI slot state')
        return set()

def _autonomy_deactivate_project(project_id,reason):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        target=c.execute('SELECT active FROM runner_targets WHERE project_id=?',(project_id,)).fetchone()
        if not target or not bool(target['active']):
            return False
        # Do not touch runner_workers.desired_state here. That field belongs to
        # explicit/manual worker pause/drain controls, not scheduler allocation.
        c.execute('UPDATE runner_targets SET active=0 WHERE project_id=?',(project_id,))
        c.execute('UPDATE autonomy_runtime SET last_reason=? WHERE project_id=?',(str(reason)[:250],project_id))
    return True

def autonomy_scheduler_tick():
    states=autonomy_states()
    targets=runner_targets()
    for project_id,state in states.items():
        if state.get('auto_start'):
            _autonomy_initialize_project(project_id)

    allocation=global_worker_allocation(states,targets)
    _persist_global_worker_allocation(allocation)
    selected_projects=set(allocation['projects'])
    selected_running_projects={
        item['project_id'] for item in allocation['workers']
        if item.get('desired_state','running')=='running'
    }

    started=[]
    paused=[]
    pushed=[]
    for project_id,state in states.items():
        target=targets.get(project_id) or {}
        if project_id in selected_projects:
            if not target.get('active'):
                if _autonomy_enqueue_start(project_id,'global-slot:'+str(state.get('reason') or 'eligible')):
                    started.append(project_id)
            elif project_id in selected_running_projects and state.get('dispatch_mode')=='vps' and _autonomy_enqueue_push(
                project_id,
                'global-slot:'+str(state.get('reason') or 'eligible'),
                state.get('min_ai_interval_seconds') or 0,
            ):
                pushed.append(project_id)
        elif target.get('active'):
            if _autonomy_deactivate_project(project_id,'global-slot-reallocated'):
                paused.append(project_id)
    return {
        'started':started,'paused':paused,'pushed':pushed,'allocation':allocation,
        'states':states,'time':now(),
    }

def autonomy_scheduler():
    time.sleep(8)
    while True:
        try:
            result=autonomy_scheduler_tick()
            if result['started']:
                logging.info('Autonomy started: %s', ','.join(result['started']))
            if result['pushed']:
                logging.info('VPS requested AI cycle: %s', ','.join(result['pushed']))
            if result.get('paused'):
                logging.info('Global AI slots reallocated away from: %s', ','.join(result['paused']))
        except Exception:
            logging.exception('Autonomy scheduler failed')
        time.sleep(AUTONOMY_TICK_SECONDS)

def runner_targets():
    with connect() as c:
        rows=c.execute('SELECT project_id,name,conversation_id,prompt,active,worker_count FROM runner_targets ORDER BY project_id').fetchall()
    out={}
    for r in rows:
        improvement=improvement_loop_state(r['project_id']) if r['project_id']==IMPROVEMENT_PROJECT_ID else None
        autonomy=project_autonomy_state(r['project_id'])
        out[r['project_id']]={'project_id':r['project_id'],'name':r['name'],'conversation_id':r['conversation_id'],
                              'url':('https://chatgpt.com/c/'+r['conversation_id']) if r['conversation_id'] else 'https://chatgpt.com/',
                              'prompt':r['prompt'],'active':bool(r['active']),'worker_count':max(1,int(r['worker_count'] or 1)),
                              'auto_continue':bool(autonomy['allow_ai']),
                              'auto_continue_delay_seconds':autonomy['continue_delay_seconds'],
                              'vps_dispatch_only':autonomy.get('dispatch_mode')=='vps',
                              'ai_dispatch_interval_seconds':autonomy.get('min_ai_interval_seconds',600),
                              'autonomy':autonomy,'improvement':improvement}
    return out

def runner_worker_targets():
    base=runner_targets()
    selected=_current_global_slot_keys()
    out={}
    with connect() as c:
        for project_id,cfg in base.items():
            count=max(1,min(GLOBAL_CHATGPT_WORKER_LIMIT,int(cfg.get('worker_count') or 1)))
            for slot in range(1,count+1):
                c.execute('INSERT OR IGNORE INTO runner_workers(project_id,worker_slot,conversation_id) VALUES(?,?,?)',
                          (project_id,slot,cfg['conversation_id'] if slot==1 else ''))
                row=c.execute('SELECT conversation_id,desired_state FROM runner_workers WHERE project_id=? AND worker_slot=?',(project_id,slot)).fetchone()
                conversation_id=(row['conversation_id'] if row else '') or ''
                desired_state=(row['desired_state'] if row else 'running') or 'running'
                worker_key=f'{project_id}::w{slot}'
                allocated=worker_key in selected
                active=allocated and desired_state!='paused'
                out[worker_key]={
                    'project_id':worker_key,'base_project_id':project_id,'worker_slot':slot,'worker_count':count,
                    'name':f"{cfg['name']} · worker {slot}/{count}",'conversation_id':conversation_id,
                    'url':('https://chatgpt.com/c/'+conversation_id) if conversation_id else 'https://chatgpt.com/',
                    'prompt':project_worker_prompt(project_id,cfg['name'],cfg['prompt'],slot,count),
                    'desired_state':desired_state,'active':active,
                    'auto_continue':active and bool(cfg.get('auto_continue',True)),
                    'auto_continue_delay_seconds':int(cfg.get('auto_continue_delay_seconds') or 0),
                    'vps_dispatch_only':bool(cfg.get('vps_dispatch_only')),
                    'ai_dispatch_interval_seconds':int(cfg.get('ai_dispatch_interval_seconds') or 0),
                    'autonomy':cfg.get('autonomy'),'improvement':cfg.get('improvement')
                }
    return out

def runner_record(payload):
    event=str(payload.get('event') or 'unknown')[:64]
    raw_ts=str(payload.get('at') or now())
    try: ts=datetime.fromisoformat(raw_ts.replace('Z','+00:00')).astimezone(timezone.utc).isoformat()
    except Exception: ts=now()
    target=str(payload.get('target') or payload.get('targetConversation') or '')[:500]
    title=str(payload.get('title') or '')[:250]
    reason=str(payload.get('reason') or '')[:250]
    error=str(payload.get('error') or '')[:500]
    progress_at=str(payload.get('progressAt') or '')
    try: progress_at=datetime.fromisoformat(progress_at.replace('Z','+00:00')).astimezone(timezone.utc).isoformat() if progress_at else None
    except Exception: progress_at=None
    try: assistant_chars=max(0,min(2000000,int(payload.get('assistantCharacters')))) if payload.get('assistantCharacters') is not None else None
    except Exception: assistant_chars=None
    tab_id=payload.get('tabId')
    raw_project_id=str(payload.get('projectId') or '')[:80]
    project_id=str(payload.get('baseProjectId') or raw_project_id.split('::w',1)[0])[:40]
    try: worker_slot=max(1,min(MAX_CHATGPT_WORKERS,int(payload.get('workerSlot') or (raw_project_id.split('::w',1)[1] if '::w' in raw_project_id else 1))))
    except Exception: worker_slot=1
    match=re.search(r'/c/([0-9a-f-]{20,})',target,re.I)
    with connect() as c:
        if not project_id and target:
            for pid,t in runner_targets().items():
                if t['conversation_id'] and t['conversation_id'] in target: project_id=pid; break
        c.execute('INSERT INTO runner_events(ts,event,target,title,generating,sending,reason,tab_id,error,project_id,progress_at,assistant_chars,worker_slot) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (ts,event,target,title,int(bool(payload.get('generating'))),int(bool(payload.get('sending'))),reason,tab_id,error,project_id or None,progress_at,assistant_chars,worker_slot))
        if event == 'conversation-adopted' and project_id in runner_targets() and match:
            c.execute('INSERT INTO runner_workers(project_id,worker_slot,conversation_id) VALUES(?,?,?) ON CONFLICT(project_id,worker_slot) DO UPDATE SET conversation_id=excluded.conversation_id',
                      (project_id,worker_slot,match.group(1)))
            if worker_slot==1:
                c.execute('UPDATE runner_targets SET conversation_id=? WHERE project_id=?',(match.group(1),project_id))
        if event in ('runner-drained','runner-paused') and project_id in runner_targets():
            c.execute("UPDATE runner_workers SET desired_state='paused' WHERE project_id=? AND worker_slot=?",(project_id,worker_slot))
        cutoff=datetime.fromtimestamp(time.time()-14*86400,timezone.utc).isoformat()
        c.execute('DELETE FROM runner_events WHERE ts < ?', (cutoff,))
    if project_id==IMPROVEMENT_PROJECT_ID:
        if event=='improvement-iteration-complete':
            improvement_loop_record(project_id,'iteration')
        elif event=='improvement-review-green':
            improvement_loop_record(project_id,'review',finish_gate_green=True,p0p1_open=False)
        elif event=='improvement-review-open':
            improvement_loop_record(project_id,'review',finish_gate_green=False,p0p1_open=True)
        elif event=='improvement-audit-green':
            improvement_loop_record(project_id,'audit',audit_green=True)
        elif event=='improvement-audit-failed':
            improvement_loop_record(project_id,'audit',audit_green=False)

def runner_status(project_id=None):
    target_cfg=runner_targets()
    with connect() as c:
        if project_id in target_cfg:
            cfg=target_cfg[project_id]
            if cfg['conversation_id']:
                where='(project_id=? OR (project_id IS NULL AND target LIKE ?))'
                args=(project_id,'%'+cfg['conversation_id']+'%')
            else:
                where='project_id=?'; args=(project_id,)
        else:
            where='1=1'; args=()
        latest=c.execute('SELECT * FROM runner_events WHERE '+where+' ORDER BY id DESC LIMIT 1',args).fetchone()
        heartbeat=c.execute('SELECT * FROM runner_events WHERE '+where+" AND event='heartbeat' ORDER BY id DESC LIMIT 1",args).fetchone()
        def event_row(event):
            return c.execute('SELECT * FROM runner_events WHERE '+where+' AND event=? ORDER BY id DESC LIMIT 1',args+(event,)).fetchone()
        prompt=event_row('prompt-sent'); started=event_row('generation-started'); finished=event_row('generation-finished')
        command=c.execute('SELECT id,status,action,created_at,updated_at,result FROM runner_commands WHERE project_id=? ORDER BY id DESC LIMIT 1',(project_id,)).fetchone() if project_id in target_cfg else None
    def info(row):
        if not row:return None
        return {'time':row['ts'],'event':row['event'],'reason':row['reason'] or None,'error':row['error'] or None}
    cfg=target_cfg.get(project_id,{})
    active=bool(cfg.get('active'))
    if not latest:
        return {'project_id':project_id,'name':cfg.get('name'),'state':'offline' if active else 'paused','active':active,'age_seconds':None,
                'generating':False,'sending':False,'last_event':None,'last_heartbeat':None,
                'last_prompt_sent':None,'last_generation_started':None,'last_generation_finished':None,
                'worker_count':max(1,int(cfg.get('worker_count') or 1)),'command':dict(command) if command else None,'auto_continue':bool(cfg.get('auto_continue',True)),'improvement':cfg.get('improvement')}
    try: age=max(0,int((datetime.now(timezone.utc)-datetime.fromisoformat(latest['ts'])).total_seconds()))
    except Exception: age=999999
    progress_time=latest['progress_at'] or (heartbeat['progress_at'] if heartbeat else None)
    try: progress_age=max(0,int((datetime.now(timezone.utc)-datetime.fromisoformat(progress_time)).total_seconds())) if progress_time else None
    except Exception: progress_age=None
    generating=bool(latest['generating'])
    stalled=bool(generating and progress_age is not None and progress_age>=20*60)
    state=('paused' if not active else 'stalled' if stalled else 'live' if age<=90 else 'stale' if age<=300 else 'offline')
    return {'project_id':project_id,'name':cfg.get('name') or latest['title'],'state':state,'active':active,'age_seconds':age,
            'generating':generating if active else False,'sending':bool(latest['sending']) if active else False,'stalled':stalled if active else False,
            'progress_age_seconds':progress_age,'assistant_characters':latest['assistant_chars'],
            'title':latest['title'] or cfg.get('name'),'target':latest['target'] or cfg.get('url'),
            'tab_id':latest['tab_id'],'error':latest['error'] or None,'event':latest['event'],
            'last_event':info(latest),'last_heartbeat':info(heartbeat),'last_prompt_sent':info(prompt),
            'last_generation_started':info(started),'last_generation_finished':info(finished),
            'worker_count':max(1,int(cfg.get('worker_count') or 1)),'command':dict(command) if command else None,'auto_continue':bool(cfg.get('auto_continue',True)),'improvement':cfg.get('improvement')}

def runner_worker_statuses(project_id):
    base=runner_targets().get(project_id)
    if not base:
        return []
    targets=runner_worker_targets()
    claims=task_claims(project_id)
    by_worker={}
    for claim in claims:
        worker_id=str(claim.get('worker_id') or '')
        if worker_id:
            by_worker.setdefault(worker_id,[]).append(claim)
    out=[]
    with connect() as c:
        for worker_key,cfg in sorted(targets.items(),key=lambda item:item[1].get('worker_slot',1)):
            if cfg.get('base_project_id')!=project_id:
                continue
            slot=int(cfg.get('worker_slot') or 1)
            args=(project_id,slot)
            latest=c.execute('SELECT * FROM runner_events WHERE project_id=? AND worker_slot=? ORDER BY id DESC LIMIT 1',args).fetchone()
            heartbeat=c.execute("SELECT * FROM runner_events WHERE project_id=? AND worker_slot=? AND event='heartbeat' ORDER BY id DESC LIMIT 1",args).fetchone()
            command=c.execute('SELECT id,status,action,created_at,updated_at,result FROM runner_commands WHERE project_id=? ORDER BY id DESC LIMIT 1',(worker_key,)).fetchone()
            desired=cfg.get('desired_state') or 'running'
            active=bool(cfg.get('active')) and desired!='paused'
            try:
                age=max(0,int((datetime.now(timezone.utc)-datetime.fromisoformat(latest['ts'])).total_seconds())) if latest else None
            except Exception:
                age=999999
            progress_time=(latest['progress_at'] if latest else None) or (heartbeat['progress_at'] if heartbeat else None)
            try:
                progress_age=max(0,int((datetime.now(timezone.utc)-datetime.fromisoformat(progress_time)).total_seconds())) if progress_time else None
            except Exception:
                progress_age=None
            generating=bool(latest['generating']) if latest else False
            stalled=bool(generating and progress_age is not None and progress_age>=20*60)
            if not active or desired=='paused':
                state='paused'
            elif desired=='draining':
                state='draining'
            elif not latest:
                state='starting'
            elif stalled:
                state='stalled'
            elif age is not None and age<=90:
                state='live'
            elif age is not None and age<=300:
                state='stale'
            else:
                state='offline'
            worker_claims=by_worker.get(worker_key,[])
            claim=worker_claims[0] if worker_claims else None
            metadata=(claim or {}).get('metadata') or {}
            out.append({
                'worker_id':worker_key,'worker_slot':slot,'worker_count':cfg.get('worker_count') or 1,
                'work_area':WORKER_LANES[(slot-1)%len(WORKER_LANES)],
                'desired_state':desired,'active':active,'state':state,'generating':generating if active else False,
                'sending':bool(latest['sending']) if latest and active else False,'stalled':stalled if active else False,
                'age_seconds':age,'progress_age_seconds':progress_age,
                'last_event':({'time':latest['ts'],'event':latest['event'],'reason':latest['reason'] or None,'error':latest['error'] or None} if latest else None),
                'last_heartbeat':({'time':heartbeat['ts'],'event':heartbeat['event']} if heartbeat else None),
                'current_task':({'claim_key':claim.get('claim_key'),'title':metadata.get('task') or claim.get('claim_key'),
                                 'lease_until':claim.get('lease_until'),'branch':metadata.get('branch'),'pr':metadata.get('pr') or metadata.get('pr_url')} if claim else None),
                'conversation_id':cfg.get('conversation_id') or None,
                'command':dict(command) if command else None,
                'error':(latest['error'] if latest else None) or None,
            })
    return out

def runner_statuses():
    result={}
    for pid in runner_targets():
        status=runner_status(pid)
        workers=runner_worker_statuses(pid)
        status['workers']=workers
        status['desired_worker_count']=len(workers)
        status['active_worker_count']=sum(1 for w in workers if w['state'] not in ('paused','offline'))
        status['attention_worker_count']=sum(1 for w in workers if w['state'] in ('offline','stalled') or w.get('error'))
        result[pid]=status
    return result

def watch_last_run(pid):
    aliases={'haxlab':['haxlab'],'ftmo':['ftmo'],'cloud':['zennay cloud','zennay-cloud'],'supa':['supa']}.get(pid,[pid])
    with connect() as c:
        row=None
        for alias in aliases:
            row=c.execute("SELECT ts,title,event FROM runner_events WHERE event IN ('generation-started','prompt-sent') AND lower(title) LIKE ? ORDER BY id DESC LIMIT 1",('%'+alias.lower()+'%',)).fetchone()
            if row: break
        if row:
            return {'time':row['ts'],'label':row['title'] or 'ChatGPT run','source':'runner_telemetry','estimated':False}
        if pid=='supa':
            return None
        row=c.execute('SELECT ts,title FROM events WHERE project=? ORDER BY ts DESC LIMIT 1',(pid,)).fetchone()
    if not row:
        return None
    return {'time':row['ts'],'label':row['title'],'source':'project_activity','estimated':True}

def watch_warnings(data):
    warnings=[]
    h=data['host']
    mem=round(h['memory']['used']/h['memory']['total']*100)
    if data.get('stale'): warnings.append({'level':'warning','message':'Monitoringdata is stale'})
    if h.get('cpu') is not None and h['cpu'] >= 85: warnings.append({'level':'warning','message':f"CPU {h['cpu']}%"})
    if mem >= 85: warnings.append({'level':'warning','message':f'RAM {mem}%'})
    visible, _, _ = project_views(data)
    for p in visible:
        if p.get('health') != 'healthy':
            warnings.append({'level':'warning','project':p['id'],'message':f"{p['name']} needs attention"})
    return warnings

def watch_summary(data):
    projects=[]
    runner=runner_status()
    warnings=watch_warnings(data)
    if runner.get('state') != 'live': warnings.append({'level':'warning','message':'ChatGPT runner '+runner.get('state','offline')})
    visible, _, _ = project_views(data)
    for p in visible:
        activity=watch_activity(p['id'],1)
        projects.append({
            'id':p['id'],'name':p['name'],'progress':p['progress'],
            'status':p.get('status') or ('active' if p['progress'] < 100 else 'complete'),
            'health':p['health'],'phase':p.get('phase') or p['next'],
            'milestone':p.get('phase') or p['next'],'next_step':p.get('next_step') or p['next'],
            'last_activity':activity[0] if activity else None,
            'last_chatgpt_run':p.get('last_chatgpt_run')
        })
    return {
        'version':2,'time':data['time'],'stale':data.get('stale',False),
        'overall_progress':round(sum(p['progress'] for p in visible)/max(1,len(visible))),
        'active_projects':sum(1 for p in visible if p['progress'] < 100),
        'host':{
            'cpu':data['host']['cpu'],
            'memory_percent':round(data['host']['memory']['used']/data['host']['memory']['total']*100),
            'load':data['host']['load']
        },
        'warnings':warnings,
        'chatgpt_runner':runner,
        'alerts':enhancements.list_alerts(DB,5,True),
        'projects':projects
    }

def watch_project(data,pid):
    p=next((x for x in data['projects'] if x['id']==pid),None)
    if not p:return None
    return {
        'version':2,'time':data['time'],
        'project':{
            'id':p['id'],'name':p['name'],'progress':p['progress'],'health':p['health'],
            'status':p.get('status') or ('active' if p['progress'] < 100 else 'complete'),
            'phase':p.get('phase') or p['next'],
            'current_milestone':p.get('phase') or p['next'],'next_step':p.get('next_step') or p['next'],
            'last_chatgpt_run':p.get('last_chatgpt_run'),
            'recent_activity':watch_activity(pid,8),
            'progress_history':compact_history(pid,24),
            'services':p.get('services',[]),
            'quality':p.get('quality'),
            'resource':p.get('resource'),
            'milestones':p.get('milestones',[])
        }
    }


class Handler(BaseHTTPRequestHandler):
    def reply(self,body,status=200,kind='application/json; charset=utf-8'):
        raw=json.dumps(body,ensure_ascii=False).encode() if not isinstance(body,bytes) else body
        self.send_response(status)
        for k,v in {'Content-Type':kind,'Content-Length':str(len(raw)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}.items():self.send_header(k,v)
        origin=self.headers.get('Origin','')
        if origin.startswith('moz-extension://'):
            self.send_header('Access-Control-Allow-Origin',origin)
            self.send_header('Vary','Origin')
        self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        try:self.route()
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception:
            logging.exception('Request failed');self.reply({'error':'Tijdelijk niet beschikbaar'},503)
    def do_POST(self):
        try:
            u=urlparse(self.path)
            size=min(int(self.headers.get('Content-Length','0') or 0),16384)
            if size<=0:return self.reply({'error':'Lege payload'},400)
            payload=json.loads(self.rfile.read(size))
            if not isinstance(payload,dict):return self.reply({'error':'Ongeldige payload'},400)
            if u.path=='/api/runner-status':
                if self.client_address[0] not in ('127.0.0.1','::1'):return self.reply({'error':'Alleen lokaal'},403)
                runner_record(payload)
                return self.reply({'ok':True,'time':now()})
            if u.path=='/api/runner-command-result':
                if self.client_address[0] not in ('127.0.0.1','::1'):return self.reply({'error':'Alleen lokaal'},403)
                command_id=int(payload.get('command_id') or 0)
                status=str(payload.get('status') or '')
                if status not in ('completed','failed'):return self.reply({'error':'Ongeldige status'},400)
                with connect() as c:
                    c.execute('UPDATE runner_commands SET status=?,updated_at=?,result=? WHERE id=?',
                              (status,now(),str(payload.get('result') or '')[:300],command_id))
                return self.reply({'ok':True})
            if u.path=='/api/runner-control':
                if not action_request_allowed(self):return self.reply({'error':'Acties zijn alleen toegestaan vanaf een vertrouwd beheer-IP'},403)
                project_id=str(payload.get('project_id') or '')
                action=str(payload.get('action') or '')
                if action=='restart_firefox':
                    try:
                        user_systemctl('restart',FIREFOX_RUNNER_SERVICE)
                        status=firefox_runner_status()
                        return self.reply({'ok':bool(status.get('active')),'status':status},200 if status.get('active') else 503)
                    except Exception as e:
                        logging.exception('Firefox runner restart failed')
                        return self.reply({'error':'Firefox-initiator kon niet worden herstart','detail':str(e)[:200]},500)
                configs=runner_targets()
                worker_configs=runner_worker_targets()
                worker_cfg=worker_configs.get(project_id)
                is_worker=worker_cfg is not None
                base_project_id=worker_cfg.get('base_project_id') if is_worker else project_id
                allowed=('start','pause','new_chat','push','drain') if is_worker else ('start','pause','new_chat','push')
                if base_project_id not in configs or action not in allowed:return self.reply({'error':'Ongeldige runneractie'},400)
                if base_project_id==IMPROVEMENT_PROJECT_ID and action=='push' and not configs[base_project_id].get('auto_continue',True):
                    return self.reply({'error':'zCloud improvements staan op Finished / Maintain. Gebruik Resume improvements om bewust verder te gaan.','improvement':configs[base_project_id].get('improvement')},409)
                desired_state=('draining' if action=='drain' else 'paused' if action=='pause' else 'running')
                command_id=None;deduplicated=False;rate_limited=False
                with connect() as c:
                    # Serialize same-project action admission so concurrent retries cannot
                    # both observe an empty queue and enqueue duplicate browser work.
                    c.execute('BEGIN IMMEDIATE')
                    inflight=c.execute(
                        "SELECT id,action FROM runner_commands WHERE project_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
                        (project_id,)
                    ).fetchone()
                    if inflight and inflight['action']==action:
                        command_id=int(inflight['id']);deduplicated=True
                    else:
                        recent=c.execute("SELECT created_at FROM runner_commands WHERE project_id=? AND action=? AND status='completed' ORDER BY id DESC LIMIT 1",(project_id,action)).fetchone()
                        if recent:
                            try:
                                seconds=(datetime.now(timezone.utc)-datetime.fromisoformat(recent['created_at'])).total_seconds()
                                cooldown=5 if action in ('push','start','pause','drain') else 30
                                rate_limited=seconds<cooldown
                            except Exception: pass
                        if not rate_limited:
                            if is_worker:
                                if not configs[base_project_id].get('active') and action!='pause':
                                    return self.reply({'error':'Start eerst het project voordat je deze worker bedient'},409)
                                slot=int(worker_cfg.get('worker_slot') or 1)
                                current=worker_cfg.get('desired_state') or 'running'
                                if action=='push' and current!='running':
                                    return self.reply({'error':'Deze worker is niet beschikbaar voor een nieuwe push'},409)
                                if action in ('start','new_chat'):
                                    c.execute("UPDATE runner_workers SET desired_state='running' WHERE project_id=? AND worker_slot=?",(base_project_id,slot))
                                elif action=='pause':
                                    c.execute("UPDATE runner_workers SET desired_state='paused' WHERE project_id=? AND worker_slot=?",(base_project_id,slot))
                                elif action=='drain':
                                    c.execute("UPDATE runner_workers SET desired_state='draining' WHERE project_id=? AND worker_slot=?",(base_project_id,slot))
                            else:
                                c.execute('INSERT OR IGNORE INTO autonomy_runtime(project_id,initialized_at,manual_pause,last_reason) VALUES(?,?,0,?)',(project_id,now(),'dashboard_control'))
                                if action in ('start','new_chat'):
                                    c.execute("UPDATE autonomy_runtime SET manual_pause=0,last_reason='manual_resume' WHERE project_id=?",(project_id,))
                                elif action=='pause':
                                    c.execute("UPDATE autonomy_runtime SET manual_pause=1,last_reason='manual_pause' WHERE project_id=?",(project_id,))
                                if action=='push' and not configs[project_id].get('active'):
                                    return self.reply({'error':'Start dit project eerst voordat je pusht'},409)
                                if action=='push' and not any(w.get('active') for w in worker_configs.values() if w.get('base_project_id')==project_id):
                                    return self.reply({'error':'Geen actieve workers om te pushen'},409)
                                if action in ('start','new_chat'):
                                    c.execute('UPDATE runner_targets SET active=1 WHERE project_id=?',(project_id,))
                                    c.execute("UPDATE runner_workers SET desired_state='running' WHERE project_id=?",(project_id,))
                                elif action=='pause':
                                    c.execute('UPDATE runner_targets SET active=0 WHERE project_id=?',(project_id,))
                                    c.execute("UPDATE runner_workers SET desired_state='paused' WHERE project_id=?",(project_id,))
                            ts=now()
                            cur=c.execute('INSERT INTO runner_commands(project_id,action,status,created_at,updated_at) VALUES(?,?,?,?,?)',(project_id,action,'pending',ts,ts))
                            command_id=cur.lastrowid
                if rate_limited:
                    return self.reply({'error':'Er is net al een actie voor deze worker of dit project gestart'},429)
                return self.reply({'ok':True,'command_id':command_id,'status':'pending','active':action!='pause','desired_state':desired_state,'deduplicated':deduplicated})
            if u.path=='/api/improvement-loop':
                if not action_request_allowed(self):return self.reply({'error':'Acties zijn alleen toegestaan vanaf een vertrouwd beheer-IP'},403)
                project_id=str(payload.get('project_id') or IMPROVEMENT_PROJECT_ID).strip()
                if project_id!=IMPROVEMENT_PROJECT_ID:return self.reply({'error':'Alleen de zCloud self-improvement loop wordt door deze gate beheerd'},400)
                action=str(payload.get('action') or '').strip()
                if action=='resume': return self.reply({'ok':True,'improvement':improvement_loop_resume(project_id)})
                if action=='review':
                    result=improvement_loop_record(project_id,'review',finish_gate_green=payload.get('finish_gate_green'),p0p1_open=payload.get('p0p1_open'))
                    return self.reply({'ok':True,'improvement':result})
                if action=='iteration': return self.reply({'ok':True,'improvement':improvement_loop_record(project_id,'iteration')})
                if action=='audit': return self.reply({'ok':True,'improvement':improvement_loop_record(project_id,'audit',audit_green=payload.get('green'))})
                return self.reply({'error':'Ongeldige improvement-loop actie'},400)
            if u.path=='/api/worker-preflight':
                if not action_request_allowed(self):return self.reply({'error':'Acties zijn alleen toegestaan vanaf een vertrouwd beheer-IP'},403)
                try:
                    result=worker_preflight_record(
                        payload.get('project_id'),payload.get('worker_id'),payload.get('owner_id'),
                        payload.get('notion'),payload.get('github'),
                    )
                    return self.reply(result,200 if result.get('ok') else 409)
                except ValueError as e:
                    return self.reply({'error':str(e)},400)
            if u.path=='/api/task-claims':
                if not action_request_allowed(self):return self.reply({'error':'Acties zijn alleen toegestaan vanaf een vertrouwd beheer-IP'},403)
                action=str(payload.get('action') or 'acquire')
                project_id=str(payload.get('project_id') or '').strip()
                claim_key=str(payload.get('claim_key') or '').strip()
                owner_id=str(payload.get('owner_id') or '').strip()
                try:
                    if action=='acquire':
                        worker_id=str(payload.get('worker_id') or '').strip()
                        metadata=payload.get('metadata') if isinstance(payload.get('metadata'),dict) else {}
                        if worker_id:
                            preflight=worker_preflight_state(project_id,worker_id,owner_id)
                            if not preflight.get('ok'):
                                return self.reply({'error':'Verse worker-preflight vereist vóór een write-taakclaim','blocked':preflight.get('blocked'),'preflight':preflight},428)
                            if project_id==IMPROVEMENT_PROJECT_ID and metadata.get('loop')=='self_improvement':
                                try:
                                    scope=_normalize_conflict_scope(metadata)
                                except ValueError as e:
                                    return self.reply({'error':str(e),'blocked':'invalid_conflict_scope'},400)
                                if not scope or (not scope['capabilities'] and not scope['files']):
                                    return self.reply({'error':'metadata.conflict_scope is verplicht voor autonome zCloud-writes','blocked':'conflict_scope_required'},428)
                        else:
                            if payload.get('manual_override') is not True:
                                return self.reply({'error':'worker_id is verplicht voor autonome taakclaims; gebruik alleen bewust een handmatige override','blocked':'worker_identity_required'},428)
                            reason=str(payload.get('override_reason') or '').strip()
                            if not reason:
                                return self.reply({'error':'override_reason is verplicht voor een handmatige taakclaim'},400)
                            metadata={**metadata,'manual_override':True,'manual_override_reason':reason[:300],
                                      'manual_override_actor':request_actor(self)}
                        alternatives=payload.get('alternatives')
                        if alternatives is not None and not isinstance(alternatives,list):
                            return self.reply({'error':'alternatives moet een lijst zijn','blocked':'invalid_alternatives'},400)
                        result=task_claim_acquire(
                            project_id,claim_key,owner_id,worker_id,
                            payload.get('lease_seconds') or 300,metadata,alternatives,
                            require_preflight=bool(worker_id)
                        )
                        if not result['acquired'] and result.get('claim'):
                            holder=result['claim'].get('owner_id') or 'andere worker'
                            blocker_key=(result.get('conflict') or {}).get('claim_key') or claim_key
                            emit=getattr(enhancements,'emit_incident',None)
                            if emit:
                                emit(DB, project_id, 'claim_conflict', 'warning', 'Taakclaim botst',
                                     f'{blocker_key} · huidige eigenaar {holder} · nieuwe poging {owner_id}',
                                     f'claim-conflict:{project_id}:{blocker_key}:{holder}', 15*60)
                        status=200 if result['acquired'] else (
                            428 if result.get('blocked') in ('preflight_required','preflight_expired','claim_landscape_changed','worker_identity_required')
                            else 409
                        )
                        return self.reply(result,status)
                    if action=='heartbeat':
                        result=task_claim_heartbeat(project_id,claim_key,owner_id,payload.get('lease_seconds') or 300)
                        return self.reply(result,200 if result['renewed'] else 409)
                    if action=='release':
                        result=task_claim_release(project_id,claim_key,owner_id)
                        return self.reply(result,200 if result['released'] else 409)
                    return self.reply({'error':'Ongeldige claimactie'},400)
                except ValueError as e:
                    return self.reply({'error':str(e)},400)
            if u.path=='/api/feature-flags':
                if not action_request_allowed(self):return self.reply({'error':'Acties zijn alleen toegestaan vanaf een vertrouwd beheer-IP'},403)
                actor=request_actor(self)
                name=str(payload.get('name') or '').strip()
                enabled=payload.get('enabled')
                try:
                    result=set_feature_flag(name,enabled,actor,payload.get('ttl_seconds'))
                except ValueError as e:
                    return self.reply({'error':str(e)},400)
                return self.reply({'ok':True,'feature_flag':result,'time':now()})
            if u.path=='/api/runner-workers':
                if not action_request_allowed(self):return self.reply({'error':'Acties zijn alleen toegestaan vanaf een vertrouwd beheer-IP'},403)
                actor=request_actor(self)
                project_id=str(payload.get('project_id') or '')
                try: worker_count=int(payload.get('worker_count'))
                except Exception: return self.reply({'error':'Aantal ChatGPT-tabs moet een geheel getal zijn'},400)
                if project_id not in runner_targets():return self.reply({'error':'Onbekend project'},404)
                if worker_count<1 or worker_count>MAX_CHATGPT_WORKERS:return self.reply({'error':f'Kies 1 t/m {MAX_CHATGPT_WORKERS} ChatGPT-tabs'},400)
                with connect() as c:
                    before=c.execute('SELECT worker_count FROM runner_targets WHERE project_id=?',(project_id,)).fetchone()
                    old_count=max(1,int((before or {'worker_count':1})['worker_count'] or 1))
                    c.execute('UPDATE runner_targets SET worker_count=? WHERE project_id=?',(worker_count,project_id))
                    primary=c.execute('SELECT conversation_id FROM runner_targets WHERE project_id=?',(project_id,)).fetchone()
                    for slot in range(1,worker_count+1):
                        c.execute('INSERT OR IGNORE INTO runner_workers(project_id,worker_slot,conversation_id) VALUES(?,?,?)',
                                  (project_id,slot,(primary['conversation_id'] if slot==1 and primary else '') or ''))
                    record_config_audit(
                        'runner.worker_count',project_id,actor,old_count,worker_count,
                        'no_change' if old_count==worker_count else 'succeeded',
                        connection=c,
                    )
                return self.reply({'ok':True,'project_id':project_id,'worker_count':worker_count,'max_workers':MAX_CHATGPT_WORKERS})
            if u.path=='/api/resource-priority':
                if not action_request_allowed(self):return self.reply({'error':'Acties zijn alleen toegestaan vanaf een vertrouwd beheer-IP'},403)
                actor=request_actor(self)
                project=str(payload.get('project') or '')
                priority=str(payload.get('priority') or '')
                old_priority=(enhancements.load_resource_policy().get(project) or {}).get('priority')
                try:
                    result=enhancements.set_priority(project,priority)
                except ValueError as e:
                    record_config_audit('resource.priority',project,actor,old_priority,priority,'rejected',str(e))
                    return self.reply({'error':str(e)},400)
                except Exception as e:
                    record_config_audit('resource.priority',project,actor,old_priority,priority,'failed',str(e)[:300])
                    raise
                record_config_audit(
                    'resource.priority',project,actor,old_priority,result.get('priority'),
                    'no_change' if old_priority==result.get('priority') else 'succeeded',
                    None if result.get('applied', True) else ('saved; live apply warning: '+str(result.get('apply_error') or 'unknown'))[:300]
                )
                # Keep /api/status coherent immediately. The normal sampler only
                # rebuilds project telemetry every 60 seconds, which otherwise
                # makes a freshly saved priority appear to jump back.
                with LOCK:
                    if CACHE:
                        for item in CACHE.get('projects', []):
                            if item.get('id') != project:
                                continue
                            resource=dict(item.get('resource') or {})
                            resource.update({
                                'priority': result.get('priority'),
                                'weight': result.get('weight'),
                            })
                            if 'applied' in result:
                                resource['applied']=bool(result.get('applied'))
                            item['resource']=resource
                            break
                return self.reply({'ok':True,'resource':result,'time':now()})
            if u.path=='/api/project-layout':
                if not action_request_allowed(self):return self.reply({'error':'Acties zijn alleen toegestaan vanaf een vertrouwd beheer-IP'},403)
                if 'application/json' not in self.headers.get('Content-Type',''):return self.reply({'error':'JSON vereist'},415)
                actor=request_actor(self)
                with LOCK:data=CACHE
                if data is None:return self.reply({'error':'Monitor start op'},503)
                old_layout=load_project_layout(data['projects'])
                try:
                    layout=save_project_layout(payload,data['projects'])
                except Exception as e:
                    record_config_audit('project.layout','portfolio',actor,old_layout,payload,'failed',str(e)[:300])
                    raise
                record_config_audit(
                    'project.layout','portfolio',actor,old_layout,layout,
                    'no_change' if old_layout==layout else 'succeeded'
                )
                return self.reply({'ok':True,'layout':layout,'time':now()})
            return self.reply({'error':'Niet gevonden'},404)
        except Exception:
            logging.exception('POST failed');self.reply({'error':'Opslaan mislukt'},500)
    def route(self):
        u=urlparse(self.path);q=parse_qs(u.query)
        if u.path=='/api/runner-targets':
            if self.client_address[0] not in ('127.0.0.1','::1'):return self.reply({'error':'Alleen lokaal'},403)
            allocation=global_worker_allocation()
            return self.reply({'projects':runner_worker_targets(),'max_workers':GLOBAL_CHATGPT_WORKER_LIMIT,'global_allocation':allocation})
        if u.path=='/api/autonomy':
            if self.client_address[0] not in ('127.0.0.1','::1') and not action_request_allowed(self):return self.reply({'error':'Niet toegestaan'},403)
            states=autonomy_states()
            for project_id,state in states.items():
                state['runtime']=_autonomy_runtime(project_id)
            return self.reply({'projects':states,'time':now()})
        if u.path=='/api/worker-preflight':
            if not action_request_allowed(self):return self.reply({'error':'Alleen vertrouwde beheerclients'},403)
            project_id=str(q.get('project',[''])[0] or '').strip()
            worker_id=str(q.get('worker',[''])[0] or '').strip()
            owner_id=str(q.get('owner',[''])[0] or '').strip()
            if not project_id or not worker_id or not owner_id:return self.reply({'error':'project, worker en owner zijn verplicht'},400)
            return self.reply(worker_preflight_state(project_id,worker_id,owner_id))
        if u.path=='/api/config-audit':
            if not action_request_allowed(self):return self.reply({'error':'Alleen vertrouwde beheerclients'},403)
            try: limit=int(q.get('limit',['80'])[0])
            except Exception: limit=80
            return self.reply({'items':config_audit(limit,q.get('key',[''])[0] or None,q.get('target',[''])[0] or None),'time':now()})
        if u.path=='/api/feature-flags':
            if not action_request_allowed(self):return self.reply({'error':'Alleen vertrouwde beheerclients'},403)
            return self.reply({'items':feature_flags(),'time':now()})
        if u.path=='/api/improvement-loop':
            if self.client_address[0] not in ('127.0.0.1','::1') and not action_request_allowed(self):return self.reply({'error':'Niet toegestaan'},403)
            return self.reply({'improvement':improvement_loop_state(q.get('project',[IMPROVEMENT_PROJECT_ID])[0] or IMPROVEMENT_PROJECT_ID),'time':now()})
        if u.path=='/api/task-claims':
            if not action_request_allowed(self):return self.reply({'error':'Alleen vertrouwde beheerclients'},403)
            return self.reply({'claims':task_claims(q.get('project',[''])[0] or None),'time':now()})
        if u.path=='/api/runner-commands':
            if self.client_address[0] not in ('127.0.0.1','::1'):return self.reply({'error':'Alleen lokaal'},403)
            with connect() as c:
                rows=c.execute("SELECT id,project_id,action,created_at FROM runner_commands WHERE status='pending' ORDER BY id LIMIT 10").fetchall()
            return self.reply({'commands':[dict(r) for r in rows]})
        with LOCK:data=CACHE
        if u.path.startswith('/api/'):
            if data is None:return self.reply({'error':'Monitor start op'},503)
            pid=q.get('project',[''])[0]
            if u.path.startswith('/api/v1/watch'):
                auth=self.headers.get('Authorization','')
                if WATCH_TOKEN and auth != 'Bearer '+WATCH_TOKEN:return self.reply({'error':'Unauthorized'},401)
            if u.path in ('/api/status','/api/v1/status'):
                runners=runner_statuses()
                incidents=enhancements.incident_center(DB,runners,data=data)
                return self.reply({**public_status(data),'chatgpt_runner':runner_status(),'chatgpt_runners':runners,'chatgpt_firefox':firefox_runner_status(),'incidents':incidents})
            if u.path=='/api/v1/watch':return self.reply(watch_summary(data))
            if u.path=='/api/v1/alerts':return self.reply({'time':data['time'],'alerts':enhancements.list_alerts(DB,20,True)})
            if u.path=='/api/alerts':return self.reply(enhancements.list_alerts(DB,20,False))
            if u.path=='/api/resources':return self.reply(enhancements.resource_snapshot())
            if u.path=='/api/v1/watch/project':
                detail=watch_project(data,pid)
                return self.reply(detail if detail else {'error':'Onbekend project'},200 if detail else 404)
            if u.path=='/api/snapshot':
                if self.client_address[0] not in ('127.0.0.1','::1'):return self.reply({'error':'Alleen lokaal'},403)
                persist(data);return self.reply({'saved':True,'time':data['time']})
            if u.path=='/api/history':
                if pid not in [p['id'] for p in data['projects']]:return self.reply({'error':'Onbekend project'},404)
                days={'24h':1,'7d':7,'30d':30,'all':0}.get(q.get('range',['7d'])[0],7)
                return self.reply(history(pid,days))
            if u.path=='/api/activity':
                with connect() as c:
                    rows=c.execute('SELECT * FROM events WHERE (?="" OR project=?) ORDER BY ts DESC LIMIT 80',(pid,pid)).fetchall()
                return self.reply([dict(r) for r in rows])
            if u.path=='/api/host-history':
                with connect() as c:rows=c.execute('SELECT * FROM host_samples ORDER BY ts DESC LIMIT 288').fetchall()
                return self.reply([dict(r) for r in reversed(rows)])
            return self.reply({'error':'Niet gevonden'},404)
        files={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/enhancements.js':'enhancements.js','/style.css':'style.css','/enhancements.css':'enhancements.css','/ftmo-readiness.css':'ftmo-readiness.css','/favicon.svg':'favicon.svg','/manifest.webmanifest':'manifest.webmanifest'}
        if u.path not in files:return self.reply({'error':'Niet gevonden'},404)
        path=ROOT/'public'/files[u.path]
        return self.reply(path.read_bytes(),kind=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
    def log_message(self,*args):pass

if __name__=='__main__':
    init_db()
    threading.Thread(target=sampler,daemon=True,name='cloud-monitor').start()
    threading.Thread(target=autonomy_scheduler,daemon=True,name='zcloud-autonomy').start()
    ThreadingHTTPServer((os.getenv('ZENNAY_BIND','0.0.0.0'),int(os.getenv('ZENNAY_PORT','8765'))),Handler).serve_forever()
