"""zCloud: read-only project monitoring; stdlib only."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
from datetime import datetime, timezone, timedelta
import json, os, sqlite3, subprocess, shutil, threading, time, mimetypes, logging, hmac, secrets, re, hashlib, signal
from contextlib import contextmanager, closing
import enhancements
import project_runtime
from lane_generator import classify_backlog_item, generate_execution_lanes, scopes_overlap

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
AUTONOMY_TICK_SECONDS = 5
AUTONOMY_SIGNAL_EVENTS = ('autonomy-continue','autonomy-wait-vps','autonomy-wait-human','autonomy-complete')
# Compatibility: this remains the TOTAL number of dynamic browser workers.
# Provider-specific counts live in DYNAMIC_CHATGPT_WORKERS / DYNAMIC_CLAUDE_WORKERS.
GLOBAL_CHATGPT_WORKER_LIMIT = 3
MAX_CHATGPT_WORKERS = 8
MAX_DYNAMIC_WORKERS_PER_PROVIDER = 8
DYNAMIC_CHATGPT_WORKERS = 2
DYNAMIC_CLAUDE_WORKERS = 1
DYNAMIC_CHATGPT_COOLDOWN_SECONDS = 120
DYNAMIC_CLAUDE_COOLDOWN_SECONDS = 120
DYNAMIC_WORKER_CHECK_INTERVAL_MS = 5000
DYNAMIC_WORKER_TICK_INTERVAL_MS = 1500
DYNAMIC_WORKER_HEARTBEAT_INTERVAL_MS = 30000
DYNAMIC_WORKER_GENERATION_TIMEOUT_MS = 120000
RUNNER_COMMAND_STALE_SECONDS = max(
    30,
    int(os.environ.get('ZCLOUD_RUNNER_COMMAND_STALE_SECONDS', '300')),
)
# Browser workers are memory-heavy (Firefox content processes can exceed multiple GiB).
# Keep real RAM headroom before claiming *new* worker slots; existing work is left intact.
WORKER_MEMORY_HEADROOM_MB = max(1536, int(os.environ.get('ZCLOUD_WORKER_MEMORY_HEADROOM_MB', '2048')))
WORKER_MEMORY_PER_NEW_SLOT_MB = max(512, int(os.environ.get('ZCLOUD_WORKER_MEMORY_PER_NEW_SLOT_MB', '1536')))
WORKER_MEMORY_WARN_MB = max(1024, int(os.environ.get('ZCLOUD_WORKER_MEMORY_WARN_MB', '2048')))
WORKER_MEMORY_CRITICAL_MB = max(512, int(os.environ.get('ZCLOUD_WORKER_MEMORY_CRITICAL_MB', '1024')))
WORKER_SWAP_MIN_TOTAL_MB = max(0, int(os.environ.get('ZCLOUD_WORKER_SWAP_MIN_TOTAL_MB', '1024')))
WORKER_SWAP_MIN_FREE_MB = max(0, int(os.environ.get('ZCLOUD_WORKER_SWAP_MIN_FREE_MB', '512')))
WORKER_OOM_RECOVERY_HOLD_SECONDS = max(60, int(os.environ.get('ZCLOUD_WORKER_OOM_RECOVERY_HOLD_SECONDS', '180')))
WORKER_OOM_RECOVERY_HOLD_KEY = 'worker_oom_recovery_hold_until'
DYNAMIC_WORKER_SETTING_KEY = 'dynamic_worker_limit'
DYNAMIC_WORKER_SETTING_KEYS = {
    'chatgpt_count':'dynamic_worker_chatgpt_count',
    'claude_count':'dynamic_worker_claude_count',
    'chatgpt_cooldown_seconds':'dynamic_worker_chatgpt_cooldown_seconds',
    'claude_cooldown_seconds':'dynamic_worker_claude_cooldown_seconds',
    'check_interval_ms':'dynamic_worker_check_interval_ms',
    'tick_interval_ms':'dynamic_worker_tick_interval_ms',
    'heartbeat_interval_ms':'dynamic_worker_heartbeat_interval_ms',
    'generation_start_timeout_ms':'dynamic_worker_generation_start_timeout_ms',
    'scheduler_interval_seconds':'dynamic_worker_scheduler_interval_seconds',
}
AI_SLOT_DIVERSITY_PENALTY = 500
NOTION_PORTFOLIO_QUEUE_URL = 'https://app.notion.com/p/4162fac179f44fcbbe4072a183d2b440'
NOTION_PORTFOLIO_QUEUE_DATA_SOURCE = 'collection://86e406fd-2c99-4ef5-8058-363c1004b3eb'
PORTFOLIO_QUEUE_SEED_FILE = ROOT / 'portfolio_queue.seed.json'
PORTFOLIO_ATTENTION_NOTION_URL = 'https://app.notion.com/p/3ec9e19ac9558140a2d8d05d5ebbf103'
PORTFOLIO_QUEUE_LEASE_SECONDS = 3600
PORTFOLIO_QUEUE_MIN_READY_PER_WORKER = 3
PORTFOLIO_AI_COOLDOWN_SECONDS = 120
MANUAL_START_PRIORITY_SECONDS = 180
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
def _load_project_registry():
    """Load presentation metadata and hydrate runtime fields from the canonical contract."""
    projects=json.loads((ROOT/'projects.json').read_text())
    for project in projects:
        project_id=str(project.get('id') or '').strip().lower()
        try:
            contract=project_runtime.project_contract(project_id)
        except Exception:
            logging.exception('Missing/invalid runtime contract for registry project %s',project_id)
            # Keep the dashboard available, but fail closed for autonomous scheduling.
            project['queue_mode']='human-gated'
            project['lane_profile']='product'
            project['runtime_contract_error']='missing_or_invalid'
            continue
        project['queue_mode']=str(contract.get('queue_mode') or '')
        project['lane_profile']=str(contract.get('lane_profile') or '')
    return projects

PROJECT_INDEX = {p['id']: p for p in _load_project_registry()}

VPS_EXECUTION_POLICY_FILE = ROOT / 'vps-execution-policy.json'

def _load_vps_execution_policy():
    try:
        value = json.loads(VPS_EXECUTION_POLICY_FILE.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or not value.get('enabled'):
            raise ValueError('vps-execution-policy.json must be an enabled object')
        directive = str(value.get('prompt_directive') or '').strip()
        if not directive:
            raise ValueError('vps-execution-policy.json requires prompt_directive')
        return value
    except Exception as exc:
        raise RuntimeError(
            'VPS execution policy is required; refusing to start with an implicit fallback'
        ) from exc

VPS_EXECUTION_POLICY = _load_vps_execution_policy()
VPS_EXECUTION_DIRECTIVE = str(VPS_EXECUTION_POLICY['prompt_directive']).strip() + ' '
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
        f'Werk verder aan {name}. '
        'Kijk in Notion in welke fase het project zit, bepaal wat er nog gedaan moet worden en werk dat concreet uit.'
    )


def project_worker_prompt(project_id, name, base_prompt, slot, total, queue_item=None):
    # Queue items, execution lanes and claims are internal coordination only.
    # The worker itself gets one simple project-first instruction.
    return project_runner_prompt(project_id, name)


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

def live_receipt_status(data):
    """Overlay the latest durable receipt state without waiting for the 60s sampler."""
    status_data={**data,'projects':[dict(project) for project in data.get('projects',[])]}
    try:
        with connect() as c:
            receipts=project_runtime.latest_receipts(c)
            coverage=project_runtime.receipt_coverage(
                c,
                [
                    project.get('id')
                    for project in status_data['projects']
                    if str(project.get('status') or 'active')!='archived'
                ],
            )
        for project in status_data['projects']:
            project_id=str(project.get('id') or '')
            if project_id in receipts:
                project_runtime.apply_receipt(project,receipts.get(project_id))
        status_data['state_receipt_coverage']=coverage
    except Exception:
        logging.exception('Could not refresh live project state receipts')
    return status_data

def public_status(data):
    live_data=live_receipt_status(data)
    visible, archived, layout=project_views(live_data)
    return {**live_data,'projects':visible,'archived_projects':archived,'project_layout':layout,'alerts':enhancements.list_alerts(DB,8,False)}

def now(): return datetime.now(timezone.utc).isoformat()
def cmd(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=4).strip()
def user_systemctl(*args):
    env=os.environ.copy()
    env.update({'XDG_RUNTIME_DIR':'/run/user/1000','DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/1000/bus'})
    return subprocess.check_output(['systemctl','--user',*args], text=True, stderr=subprocess.STDOUT, timeout=12, env=env).strip()
FIREFOX_LEGACY_DISABLE_FILE = Path.home() / '.config/systemd/user/chatgpt-firefox.service.d/10-legacy-disabled.conf'

def _violentmonkey_only_mode():
    try:
        text=FIREFOX_LEGACY_DISABLE_FILE.read_text(encoding='utf-8').lower()
        return 'violentmonkey only' in text and 'execcondition=/bin/false' in text
    except OSError:
        return False

def _standalone_firefox_pids():
    try:
        proc=subprocess.run(
            ['pgrep','-u',str(os.getuid()),'-f',r'/snap/firefox/.*/usr/lib/firefox/firefox'],
            text=True,capture_output=True,check=False,timeout=5,
        )
        return [int(value) for value in proc.stdout.split() if value.isdigit()]
    except Exception:
        return []

def _firefox_session_env(primary_pid=None):
    allowed={
        'DISPLAY','XAUTHORITY','WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS',
        'XDG_RUNTIME_DIR','HOME','USER','LOGNAME',
    }
    values={}
    if primary_pid:
        try:
            for item in Path(f'/proc/{int(primary_pid)}/environ').read_bytes().split(b'\0'):
                if b'=' not in item:
                    continue
                key,value=item.split(b'=',1)
                name=key.decode('utf-8','replace')
                if name in allowed:
                    values[name]=value.decode('utf-8','replace')
        except OSError:
            pass
    if not values:
        try:
            raw=user_systemctl('show-environment')
            for line in raw.splitlines():
                if '=' not in line:
                    continue
                key,value=line.split('=',1)
                if key in allowed:
                    values[key]=value
        except Exception:
            pass
    env=os.environ.copy()
    env.pop('RUNNER_TRACKING_ID',None)
    env.update(values)
    if not env.get('DISPLAY'):
        sockets=sorted(
            (Path('/tmp/.X11-unix').glob('X*') if Path('/tmp/.X11-unix').exists() else []),
            key=lambda p:(p.name!='X10',p.name),
        )
        env['DISPLAY']=(':'+sockets[0].name[1:]+'.0') if sockets else ':10.0'
    env.setdefault('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}')
    env.setdefault('DBUS_SESSION_BUS_ADDRESS','unix:path='+env['XDG_RUNTIME_DIR']+'/bus')
    return env

def _firefox_bootstrap_urls():
    urls=[]
    try:
        with connect() as c:
            rows=c.execute(
                """SELECT s.project_id,s.worker_slot,COALESCE(w.provider,'chatgpt') provider
                   FROM ai_global_slots s
                   LEFT JOIN runner_workers w
                     ON w.project_id=s.project_id AND w.worker_slot=s.worker_slot
                   ORDER BY s.slot"""
            ).fetchall()
        for row in rows:
            worker=f"{row['project_id']}::w{int(row['worker_slot'] or 1)}"
            provider='claude' if str(row['provider'] or '').lower()=='claude' else 'chatgpt'
            base='https://claude.ai/new' if provider=='claude' else 'https://chatgpt.com/'
            urls.append(base+('&' if '?' in base else '?')+'zcloud_worker='+quote(worker,safe=''))
    except Exception:
        logging.exception('Could not build standalone Firefox bootstrap URLs')
    return urls

def firefox_runner_status():
    standalone=_standalone_firefox_pids()
    try:
        try: service_state=user_systemctl('is-active',FIREFOX_RUNNER_SERVICE)
        except subprocess.CalledProcessError as e: service_state=(e.output or '').strip() or 'inactive'
        raw=user_systemctl('show',FIREFOX_RUNNER_SERVICE,'-p','MainPID','-p','ActiveEnterTimestamp','-p','NRestarts','-p','Restart')
        fields=dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
    except Exception as e:
        service_state='unknown'
        fields={}
        service_error=str(e)[:200]
    else:
        service_error=None
    service_active=service_state=='active'
    active=service_active or bool(standalone)
    runtime_mode='systemd' if service_active else ('standalone' if standalone else ('violentmonkey-only' if _violentmonkey_only_mode() else 'none'))
    result={
        'state':'active' if active else service_state,
        'service_state':service_state,
        'active':active,
        'runtime_mode':runtime_mode,
        'main_pid':(int(fields.get('MainPID') or 0) if service_active else (standalone[0] if standalone else 0)),
        'standalone_pids':standalone[:20],
        'active_since':fields.get('ActiveEnterTimestamp') or None,
        'restarts':int(fields.get('NRestarts') or 0),
        'auto_restart':('watchdog' if runtime_mode=='standalone' else (fields.get('Restart') or 'unknown')),
    }
    if service_error and not active:
        result['error']=service_error
    return result

def restart_firefox_runtime():
    """Recover either the managed systemd Firefox or the standalone VM runtime."""
    existing=_standalone_firefox_pids()
    current=firefox_runner_status()
    browser_was_dead=not bool(current.get('active'))
    recovery_guard=None
    released=[]
    if browser_was_dead:
        recovery_guard=worker_memory_status()
        safe_capacity=max(0,int(recovery_guard.get('new_worker_capacity') or 0))
        if safe_capacity <= 0:
            return {
                **current,
                'recovery_deferred':'memory-pressure',
                'memory_guard':recovery_guard,
                'released_slots':[],
            }
        released=_trim_dead_browser_allocations_for_recovery(safe_capacity)
        _set_worker_recovery_hold()
        _persist_global_worker_allocation(portfolio_queue_allocation())

    if _violentmonkey_only_mode():
        old_pids=existing
        env=_firefox_session_env(old_pids[0] if old_pids else None)
        for pid in old_pids:
            try: os.kill(pid,signal.SIGTERM)
            except ProcessLookupError: pass
        deadline=time.time()+10
        alive=list(old_pids)
        while alive and time.time()<deadline:
            time.sleep(0.5)
            next_alive=[]
            for pid in alive:
                try: os.kill(pid,0);next_alive.append(pid)
                except ProcessLookupError: pass
            alive=next_alive
        for pid in alive:
            try: os.kill(pid,signal.SIGKILL)
            except ProcessLookupError: pass
        log_path=Path('/tmp/zcloud-firefox-recovery.log')
        with log_path.open('ab',buffering=0) as log:
            subprocess.Popen(
                ['/usr/bin/firefox',*_firefox_bootstrap_urls()],
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                env=env,start_new_session=True,close_fds=True,
            )
        deadline=time.time()+20
        while time.time()<deadline:
            current=_standalone_firefox_pids()
            fresh=[pid for pid in current if pid not in old_pids]
            if fresh or (not old_pids and current):
                status=firefox_runner_status()
                status['memory_guard']=recovery_guard or worker_memory_status()
                status['released_slots']=released
                return status
            time.sleep(1)
        status=firefox_runner_status()
        status['recovery_error']='standalone Firefox did not become active before timeout'
        status['memory_guard']=recovery_guard or worker_memory_status()
        status['released_slots']=released
        return status

    for dependency in ('chatgpt-display.service','chatgpt-openbox.service'):
        try: user_systemctl('reset-failed',dependency)
        except Exception: pass
        user_systemctl('start',dependency)
    try: user_systemctl('reset-failed',FIREFOX_RUNNER_SERVICE)
    except Exception: pass
    user_systemctl('restart',FIREFOX_RUNNER_SERVICE)
    deadline=time.time()+15
    status=firefox_runner_status()
    while not status.get('active') and time.time()<deadline:
        time.sleep(1)
        status=firefox_runner_status()
    status['memory_guard']=recovery_guard or worker_memory_status()
    status['released_slots']=released
    return status

def worker_memory_status(meminfo_path=Path('/proc/meminfo')):
    """Return host RAM/swap admission state for browser workers.

    MemAvailable already accounts for reclaimable cache and current Firefox usage,
    so new_worker_capacity is intentionally a *new claim* budget for this scheduler
    pass. Existing work is normally preserved; genuinely critical pressure is handled
    separately by the allocator by shedding at most one safe idle browser allocation
    per tick so zCloud/Firefox keep enough headroom to remain controllable.
    """
    values={}
    try:
        for line in Path(meminfo_path).read_text(encoding='utf-8').splitlines():
            if ':' not in line:
                continue
            key,raw=line.split(':',1)
            match=re.search(r'([0-9]+)',raw)
            if match:
                values[key.strip()]=int(match.group(1)) // 1024
    except Exception as exc:
        return {
            'available_mb':None,'total_mb':None,'swap_total_mb':None,'swap_free_mb':None,
            'headroom_mb':WORKER_MEMORY_HEADROOM_MB,'per_new_slot_mb':WORKER_MEMORY_PER_NEW_SLOT_MB,
            'new_worker_capacity':0,'pressure':'unknown','healthy_for_new_worker':False,
            'swap_healthy':False,'error':str(exc)[:200],
        }
    total=max(0,int(values.get('MemTotal') or 0))
    available=max(0,int(values.get('MemAvailable') or values.get('MemFree') or 0))
    swap_total=max(0,int(values.get('SwapTotal') or 0))
    swap_free=max(0,int(values.get('SwapFree') or 0))
    reserve=WORKER_MEMORY_HEADROOM_MB
    # Swap is a last-resort shock absorber, not worker capacity. With no useful
    # swap configured, preserve an extra 512 MiB of real RAM before admitting tabs.
    swap_healthy=(swap_total >= WORKER_SWAP_MIN_TOTAL_MB and swap_free >= min(WORKER_SWAP_MIN_FREE_MB,swap_total))
    effective_reserve=reserve + (0 if swap_healthy else 512)
    capacity=max(0,(available-effective_reserve)//WORKER_MEMORY_PER_NEW_SLOT_MB)
    if available < WORKER_MEMORY_CRITICAL_MB:
        pressure='critical'
    elif available < WORKER_MEMORY_WARN_MB:
        pressure='warning'
    elif capacity <= 0:
        pressure='guarded'
    else:
        pressure='ok'
    return {
        'available_mb':available,'total_mb':total,'swap_total_mb':swap_total,'swap_free_mb':swap_free,
        'headroom_mb':reserve,'effective_headroom_mb':effective_reserve,
        'per_new_slot_mb':WORKER_MEMORY_PER_NEW_SLOT_MB,'new_worker_capacity':int(capacity),
        'pressure':pressure,'healthy_for_new_worker':bool(capacity > 0),'swap_healthy':bool(swap_healthy),
    }

def _worker_recovery_hold_until():
    try:
        with connect() as c:
            row=c.execute("SELECT value FROM runtime_settings WHERE key=?",(WORKER_OOM_RECOVERY_HOLD_KEY,)).fetchone()
        if not row or not row['value']:
            return None
        until=datetime.fromisoformat(str(row['value']).replace('Z','+00:00')).astimezone(timezone.utc)
        return until if until > datetime.now(timezone.utc) else None
    except Exception:
        return None

def _set_worker_recovery_hold(seconds=WORKER_OOM_RECOVERY_HOLD_SECONDS):
    until=datetime.now(timezone.utc)+timedelta(seconds=max(60,int(seconds)))
    try:
        with connect() as c:
            c.execute(
                """INSERT INTO runtime_settings(key,value,updated_at,actor) VALUES(?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at,actor=excluded.actor""",
                (WORKER_OOM_RECOVERY_HOLD_KEY,until.isoformat(),now(),'oom-recovery'),
            )
    except Exception:
        logging.exception('Could not persist worker OOM recovery hold')
    return until

def _trim_dead_browser_allocations_for_recovery(capacity):
    """Release excess queue slots only after the browser host is already gone."""
    cap=max(0,min(int(GLOBAL_CHATGPT_WORKER_LIMIT),int(capacity or 0)))
    ts=now()
    released=[]
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        rows=c.execute(
            """SELECT queue_id,worker_slot,project_id FROM portfolio_queue
               WHERE eligible=1 AND status IN ('claimed','running','verifying')
                 AND worker_slot IS NOT NULL AND worker_slot>?
               ORDER BY worker_slot DESC""",
            (cap,),
        ).fetchall()
        released=[dict(row) for row in rows]
        if released:
            c.execute(
                """UPDATE portfolio_queue
                   SET status='queued',worker_slot=NULL,claimed_at=NULL,claim_expires=NULL,updated_at=?
                   WHERE eligible=1 AND status IN ('claimed','running','verifying')
                     AND worker_slot IS NOT NULL AND worker_slot>?""",
                (ts,cap),
            )
        c.execute('DELETE FROM ai_global_slots WHERE slot>?',(cap,))
    if released:
        logging.warning(
            'OOM recovery released %s browser slot(s) above safe capacity=%s: %s',
            len(released),cap,','.join(str(item.get('queue_id')) for item in released),
        )
    return released

def git(path, *args): return cmd(['git', '-c', 'safe.directory='+path, '-C', path, *args])
DB_BUSY_TIMEOUT_SECONDS = 15
DB_BUSY_TIMEOUT_MS = DB_BUSY_TIMEOUT_SECONDS * 1000

@contextmanager
def connect():
    c = sqlite3.connect(DB, timeout=DB_BUSY_TIMEOUT_SECONDS)
    c.execute(f'PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}')
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

def _dynamic_int(value,name,minimum,maximum):
    try: parsed=int(value)
    except Exception: raise ValueError(f'{name} moet een geheel getal zijn')
    if parsed < minimum or parsed > maximum:
        raise ValueError(f'{name} moet tussen {minimum} en {maximum} liggen')
    return parsed

def _dynamic_provider_counts():
    # Provider counts are independent sources of truth. The legacy global
    # value is derived from these counts, never used to repartition them.
    chat=max(0,min(MAX_DYNAMIC_WORKERS_PER_PROVIDER,int(DYNAMIC_CHATGPT_WORKERS)))
    claude=max(0,min(MAX_DYNAMIC_WORKERS_PER_PROVIDER,int(DYNAMIC_CLAUDE_WORKERS)))
    return {'chatgpt':chat,'claude':claude}

def dynamic_provider_for_global_slot(slot):
    try: slot=max(1,int(slot))
    except Exception: return 'chatgpt'
    counts=_dynamic_provider_counts()
    return 'chatgpt' if slot <= counts['chatgpt'] else 'claude'

def dynamic_provider_count(provider):
    return int(_dynamic_provider_counts().get(str(provider or '').lower(),0))

def dynamic_provider_cooldown(provider):
    return int(DYNAMIC_CLAUDE_COOLDOWN_SECONDS if str(provider or '').lower()=='claude' else DYNAMIC_CHATGPT_COOLDOWN_SECONDS)

def dynamic_provider_url(provider,conversation_id=''):
    provider='claude' if str(provider or '').lower()=='claude' else 'chatgpt'
    cid=str(conversation_id or '').strip()
    if provider=='claude':
        return ('https://claude.ai/chat/'+cid) if cid else 'https://claude.ai/new'
    return ('https://chatgpt.com/c/'+cid) if cid else 'https://chatgpt.com/'

def dynamic_worker_settings():
    counts=_dynamic_provider_counts()
    total=counts['chatgpt']+counts['claude']
    return {
        'count':int(total),
        'enabled':bool(total > 0),
        'max_workers':int(MAX_DYNAMIC_WORKERS_PER_PROVIDER*2),
        'max_workers_per_provider':int(MAX_DYNAMIC_WORKERS_PER_PROVIDER),
        'cooldown_seconds':int(DYNAMIC_CHATGPT_COOLDOWN_SECONDS),
        'chatgpt_count':counts['chatgpt'],
        'claude_count':counts['claude'],
        'chatgpt_cooldown_seconds':int(DYNAMIC_CHATGPT_COOLDOWN_SECONDS),
        'claude_cooldown_seconds':int(DYNAMIC_CLAUDE_COOLDOWN_SECONDS),
        'providers':{
            'chatgpt':{'count':counts['chatgpt'],'cooldown_seconds':int(DYNAMIC_CHATGPT_COOLDOWN_SECONDS)},
            'claude':{'count':counts['claude'],'cooldown_seconds':int(DYNAMIC_CLAUDE_COOLDOWN_SECONDS)},
        },
        'check_interval_ms':int(DYNAMIC_WORKER_CHECK_INTERVAL_MS),
        'tick_interval_ms':int(DYNAMIC_WORKER_TICK_INTERVAL_MS),
        'heartbeat_interval_ms':int(DYNAMIC_WORKER_HEARTBEAT_INTERVAL_MS),
        'generation_start_timeout_ms':int(DYNAMIC_WORKER_GENERATION_TIMEOUT_MS),
        'scheduler_interval_seconds':int(AUTONOMY_TICK_SECONDS),
        'memory_guard':worker_memory_status(),
        'policy':'per_worker_rate_limit_plus_memory_admission',
    }

def set_dynamic_worker_settings(payload,actor='dashboard'):
    global GLOBAL_CHATGPT_WORKER_LIMIT,DYNAMIC_CHATGPT_WORKERS,DYNAMIC_CLAUDE_WORKERS
    global DYNAMIC_CHATGPT_COOLDOWN_SECONDS,DYNAMIC_CLAUDE_COOLDOWN_SECONDS
    global DYNAMIC_WORKER_CHECK_INTERVAL_MS,DYNAMIC_WORKER_TICK_INTERVAL_MS
    global DYNAMIC_WORKER_HEARTBEAT_INTERVAL_MS,DYNAMIC_WORKER_GENERATION_TIMEOUT_MS,AUTONOMY_TICK_SECONDS
    payload=payload if isinstance(payload,dict) else {}
    legacy_only='count' in payload and not any(key in payload for key in DYNAMIC_WORKER_SETTING_KEYS)
    chatgpt_count=_dynamic_int(
        payload.get('count') if legacy_only else payload.get('chatgpt_count',DYNAMIC_CHATGPT_WORKERS),
        'ChatGPT workers',0,MAX_DYNAMIC_WORKERS_PER_PROVIDER
    )
    claude_count=_dynamic_int(
        0 if legacy_only else payload.get('claude_count',DYNAMIC_CLAUDE_WORKERS),
        'Claude workers',0,MAX_DYNAMIC_WORKERS_PER_PROVIDER
    )
    chatgpt_cooldown=_dynamic_int(payload.get('chatgpt_cooldown_seconds',DYNAMIC_CHATGPT_COOLDOWN_SECONDS),'ChatGPT cooldown',5,86400)
    claude_cooldown=_dynamic_int(payload.get('claude_cooldown_seconds',DYNAMIC_CLAUDE_COOLDOWN_SECONDS),'Claude cooldown',5,86400)
    check_ms=_dynamic_int(payload.get('check_interval_ms',DYNAMIC_WORKER_CHECK_INTERVAL_MS),'Check interval',1000,60000)
    tick_ms=_dynamic_int(payload.get('tick_interval_ms',DYNAMIC_WORKER_TICK_INTERVAL_MS),'DOM tick interval',250,10000)
    heartbeat_ms=_dynamic_int(payload.get('heartbeat_interval_ms',DYNAMIC_WORKER_HEARTBEAT_INTERVAL_MS),'Heartbeat interval',5000,300000)
    generation_ms=_dynamic_int(payload.get('generation_start_timeout_ms',DYNAMIC_WORKER_GENERATION_TIMEOUT_MS),'Generation timeout',10000,600000)
    scheduler_seconds=_dynamic_int(payload.get('scheduler_interval_seconds',AUTONOMY_TICK_SECONDS),'Scheduler interval',1,300)
    old=dynamic_worker_settings()
    values={
        DYNAMIC_WORKER_SETTING_KEY:str(chatgpt_count+claude_count),
        DYNAMIC_WORKER_SETTING_KEYS['chatgpt_count']:str(chatgpt_count),
        DYNAMIC_WORKER_SETTING_KEYS['claude_count']:str(claude_count),
        DYNAMIC_WORKER_SETTING_KEYS['chatgpt_cooldown_seconds']:str(chatgpt_cooldown),
        DYNAMIC_WORKER_SETTING_KEYS['claude_cooldown_seconds']:str(claude_cooldown),
        DYNAMIC_WORKER_SETTING_KEYS['check_interval_ms']:str(check_ms),
        DYNAMIC_WORKER_SETTING_KEYS['tick_interval_ms']:str(tick_ms),
        DYNAMIC_WORKER_SETTING_KEYS['heartbeat_interval_ms']:str(heartbeat_ms),
        DYNAMIC_WORKER_SETTING_KEYS['generation_start_timeout_ms']:str(generation_ms),
        DYNAMIC_WORKER_SETTING_KEYS['scheduler_interval_seconds']:str(scheduler_seconds),
    }
    with connect() as c:
        for key,value in values.items():
            c.execute(
                "INSERT INTO runtime_settings(key,value,updated_at,actor) VALUES(?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at,actor=excluded.actor",
                (key,value,now(),str(actor or 'dashboard')[:128]),
            )
        record_config_audit(
            'runner.dynamic_worker_settings','portfolio',actor,old,
            {
                'chatgpt_count':chatgpt_count,'claude_count':claude_count,
                'chatgpt_cooldown_seconds':chatgpt_cooldown,'claude_cooldown_seconds':claude_cooldown,
                'check_interval_ms':check_ms,'tick_interval_ms':tick_ms,'heartbeat_interval_ms':heartbeat_ms,
                'generation_start_timeout_ms':generation_ms,'scheduler_interval_seconds':scheduler_seconds,
            },
            'succeeded',connection=c,
        )
        if chatgpt_count + claude_count == 0:
            c.execute('DELETE FROM ai_global_slots')
    DYNAMIC_CHATGPT_WORKERS=chatgpt_count
    DYNAMIC_CLAUDE_WORKERS=claude_count
    GLOBAL_CHATGPT_WORKER_LIMIT=chatgpt_count+claude_count
    DYNAMIC_CHATGPT_COOLDOWN_SECONDS=chatgpt_cooldown
    DYNAMIC_CLAUDE_COOLDOWN_SECONDS=claude_cooldown
    DYNAMIC_WORKER_CHECK_INTERVAL_MS=check_ms
    DYNAMIC_WORKER_TICK_INTERVAL_MS=tick_ms
    DYNAMIC_WORKER_HEARTBEAT_INTERVAL_MS=heartbeat_ms
    DYNAMIC_WORKER_GENERATION_TIMEOUT_MS=generation_ms
    AUTONOMY_TICK_SECONDS=scheduler_seconds
    allocation=reconcile_dynamic_worker_limit()
    settings=dynamic_worker_settings()
    settings['allocated_workers']=len(allocation.get('workers') or [])
    settings['allocation_keys']=list(allocation.get('keys') or [])
    settings['reconciled']=True
    return settings

def set_dynamic_worker_limit(value,actor='dashboard'):
    return set_dynamic_worker_settings({'count':value},actor)

def init_db():
    global GLOBAL_CHATGPT_WORKER_LIMIT,DYNAMIC_CHATGPT_WORKERS,DYNAMIC_CLAUDE_WORKERS
    global DYNAMIC_CHATGPT_COOLDOWN_SECONDS,DYNAMIC_CLAUDE_COOLDOWN_SECONDS
    global DYNAMIC_WORKER_CHECK_INTERVAL_MS,DYNAMIC_WORKER_TICK_INTERVAL_MS
    global DYNAMIC_WORKER_HEARTBEAT_INTERVAL_MS,DYNAMIC_WORKER_GENERATION_TIMEOUT_MS,AUTONOMY_TICK_SECONDS
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
        # Hot read-model paths repeatedly ask for the newest event/heartbeat
        # by project and worker. Keep those ORDER BY id DESC LIMIT 1 lookups
        # index-backed so /api/runner-live does not scan/sort runner history.
        c.execute('CREATE INDEX IF NOT EXISTS runner_events_project_id ON runner_events(project_id,id)')
        c.execute('CREATE INDEX IF NOT EXISTS runner_events_project_event_id ON runner_events(project_id,event,id)')
        c.execute('CREATE INDEX IF NOT EXISTS runner_events_project_worker_id ON runner_events(project_id,worker_slot,id)')
        c.execute('CREATE INDEX IF NOT EXISTS runner_events_project_worker_event_id ON runner_events(project_id,worker_slot,event,id)')
        c.execute('CREATE TABLE IF NOT EXISTS runner_targets(project_id TEXT PRIMARY KEY, name TEXT NOT NULL, conversation_id TEXT NOT NULL, prompt TEXT NOT NULL)')
        target_columns={r['name'] for r in c.execute('PRAGMA table_info(runner_targets)').fetchall()}
        migrated_active='active' not in target_columns
        if migrated_active:
            c.execute('ALTER TABLE runner_targets ADD COLUMN active INTEGER NOT NULL DEFAULT 0')
            c.execute("UPDATE runner_targets SET active=1 WHERE project_id IN ('haxlab','ftmo')")
        if 'worker_count' not in target_columns:
            c.execute('ALTER TABLE runner_targets ADD COLUMN worker_count INTEGER NOT NULL DEFAULT 1')
            c.execute("UPDATE runner_targets SET worker_count=2 WHERE project_id='ftmo'")
        # Keep FTMO's browser/code-worker fan-out bounded even when an older live DB
        # persisted a larger dashboard value. This does not touch FTMO compute workers.
        c.execute("UPDATE runner_targets SET worker_count=2 WHERE project_id='ftmo' AND worker_count>2")
        c.execute("CREATE TABLE IF NOT EXISTS runner_workers(project_id TEXT NOT NULL, worker_slot INTEGER NOT NULL, conversation_id TEXT NOT NULL DEFAULT '', PRIMARY KEY(project_id,worker_slot))")
        worker_columns={r['name'] for r in c.execute('PRAGMA table_info(runner_workers)').fetchall()}
        if 'desired_state' not in worker_columns:
            c.execute("ALTER TABLE runner_workers ADD COLUMN desired_state TEXT NOT NULL DEFAULT 'running'")
        if 'provider' not in worker_columns:
            c.execute("ALTER TABLE runner_workers ADD COLUMN provider TEXT NOT NULL DEFAULT 'chatgpt'")
        c.execute("CREATE TABLE IF NOT EXISTS ai_global_slots(slot INTEGER PRIMARY KEY, project_id TEXT NOT NULL, worker_slot INTEGER NOT NULL, assigned_at TEXT NOT NULL)")
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS ai_global_slots_worker ON ai_global_slots(project_id,worker_slot)')
        c.execute('CREATE TABLE IF NOT EXISTS runner_commands(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, result TEXT)')
        c.execute('CREATE INDEX IF NOT EXISTS runner_commands_status ON runner_commands(status,id)')
        c.execute("CREATE TABLE IF NOT EXISTS task_claims(project_id TEXT NOT NULL, claim_key TEXT NOT NULL, owner_id TEXT NOT NULL, worker_id TEXT NOT NULL DEFAULT '', acquired_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, lease_until TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(project_id,claim_key))")
        c.execute('CREATE INDEX IF NOT EXISTS task_claims_lease_until ON task_claims(lease_until)')
        c.execute("""CREATE TABLE IF NOT EXISTS portfolio_queue(
            queue_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            title TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'P2',
            status TEXT NOT NULL DEFAULT 'queued',
            eligible INTEGER NOT NULL DEFAULT 1,
            completion_criteria TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '',
            blocker TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            claimed_at TEXT,
            claim_expires TEXT,
            worker_slot INTEGER,
            attempts INTEGER NOT NULL DEFAULT 0,
            parent_queue_id TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )""")
        c.execute('CREATE INDEX IF NOT EXISTS portfolio_queue_sched ON portfolio_queue(eligible,status,priority,created_at)')
        c.execute('CREATE INDEX IF NOT EXISTS portfolio_queue_worker ON portfolio_queue(worker_slot,status)')
        c.execute("""CREATE TABLE IF NOT EXISTS portfolio_attention(
            attention_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            severity TEXT NOT NULL DEFAULT 'attention',
            source_url TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            resolved_at TEXT
        )""")
        c.execute('CREATE INDEX IF NOT EXISTS portfolio_attention_status ON portfolio_attention(status,severity,updated_at)')
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
        c.execute("CREATE TABLE IF NOT EXISTS runtime_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL, actor TEXT NOT NULL DEFAULT 'system')")
        c.execute("INSERT OR IGNORE INTO runtime_settings(key,value,updated_at,actor) VALUES(?,?,?,?)",
                  (DYNAMIC_WORKER_SETTING_KEY,str(GLOBAL_CHATGPT_WORKER_LIMIT),now(),'system-default'))
        legacy_row=c.execute("SELECT value FROM runtime_settings WHERE key=?",(DYNAMIC_WORKER_SETTING_KEY,)).fetchone()
        try: legacy_count=max(0,min(MAX_DYNAMIC_WORKERS_PER_PROVIDER,int(legacy_row['value'])))
        except Exception: legacy_count=1
        defaults={
            DYNAMIC_WORKER_SETTING_KEYS['chatgpt_count']:DYNAMIC_CHATGPT_WORKERS,
            DYNAMIC_WORKER_SETTING_KEYS['claude_count']:DYNAMIC_CLAUDE_WORKERS,
            DYNAMIC_WORKER_SETTING_KEYS['chatgpt_cooldown_seconds']:PORTFOLIO_AI_COOLDOWN_SECONDS,
            DYNAMIC_WORKER_SETTING_KEYS['claude_cooldown_seconds']:PORTFOLIO_AI_COOLDOWN_SECONDS,
            DYNAMIC_WORKER_SETTING_KEYS['check_interval_ms']:5000,
            DYNAMIC_WORKER_SETTING_KEYS['tick_interval_ms']:1500,
            DYNAMIC_WORKER_SETTING_KEYS['heartbeat_interval_ms']:30000,
            DYNAMIC_WORKER_SETTING_KEYS['generation_start_timeout_ms']:120000,
            DYNAMIC_WORKER_SETTING_KEYS['scheduler_interval_seconds']:AUTONOMY_TICK_SECONDS,
        }
        for key,value in defaults.items():
            c.execute("INSERT OR IGNORE INTO runtime_settings(key,value,updated_at,actor) VALUES(?,?,?,?)",
                      (key,str(value),now(),'system-default'))
        rows={r['key']:r['value'] for r in c.execute(
            "SELECT key,value FROM runtime_settings WHERE key IN ("+','.join('?' for _ in DYNAMIC_WORKER_SETTING_KEYS.values())+")",
            tuple(DYNAMIC_WORKER_SETTING_KEYS.values())
        ).fetchall()}
        def setting_int(name,default,minimum,maximum):
            try: return max(minimum,min(maximum,int(rows.get(DYNAMIC_WORKER_SETTING_KEYS[name],default))))
            except Exception: return default
        DYNAMIC_CHATGPT_WORKERS=setting_int('chatgpt_count',DYNAMIC_CHATGPT_WORKERS,0,MAX_DYNAMIC_WORKERS_PER_PROVIDER)
        DYNAMIC_CLAUDE_WORKERS=setting_int('claude_count',DYNAMIC_CLAUDE_WORKERS,0,MAX_DYNAMIC_WORKERS_PER_PROVIDER)
        DYNAMIC_CHATGPT_COOLDOWN_SECONDS=setting_int('chatgpt_cooldown_seconds',PORTFOLIO_AI_COOLDOWN_SECONDS,5,86400)
        DYNAMIC_CLAUDE_COOLDOWN_SECONDS=setting_int('claude_cooldown_seconds',PORTFOLIO_AI_COOLDOWN_SECONDS,5,86400)
        DYNAMIC_WORKER_CHECK_INTERVAL_MS=setting_int('check_interval_ms',5000,1000,60000)
        DYNAMIC_WORKER_TICK_INTERVAL_MS=setting_int('tick_interval_ms',1500,250,10000)
        DYNAMIC_WORKER_HEARTBEAT_INTERVAL_MS=setting_int('heartbeat_interval_ms',30000,5000,300000)
        DYNAMIC_WORKER_GENERATION_TIMEOUT_MS=setting_int('generation_start_timeout_ms',120000,10000,600000)
        AUTONOMY_TICK_SECONDS=setting_int('scheduler_interval_seconds',AUTONOMY_TICK_SECONDS,1,300)
        GLOBAL_CHATGPT_WORKER_LIMIT=DYNAMIC_CHATGPT_WORKERS+DYNAMIC_CLAUDE_WORKERS
        c.execute("CREATE TABLE IF NOT EXISTS autonomy_runtime(project_id TEXT PRIMARY KEY, initialized_at TEXT NOT NULL, manual_pause INTEGER NOT NULL DEFAULT 0, last_dispatch_at TEXT, last_reason TEXT NOT NULL DEFAULT '')")
        project_runtime.init_tables(c)
        if PORTFOLIO_QUEUE_SEED_FILE.exists():
            try:
                seed=json.loads(PORTFOLIO_QUEUE_SEED_FILE.read_text(encoding='utf-8'))
                for item in seed if isinstance(seed,list) else []:
                    ts=now()
                    c.execute("""INSERT OR IGNORE INTO portfolio_queue(
                        queue_id,project_id,title,priority,status,eligible,completion_criteria,source_url,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",(
                        str(item.get('queue_id') or '').strip(),
                        str(item.get('project_id') or '').strip(),
                        str(item.get('title') or '').strip(),
                        str(item.get('priority') or 'P2').upper(),
                        str(item.get('status') or 'queued').lower(),
                        1 if item.get('eligible',True) else 0,
                        str(item.get('completion_criteria') or ''),
                        str(item.get('source_url') or ''),
                        ts,ts
                    ))
            except Exception:
                logging.exception('Could not seed VPS portfolio queue')
        # Human/external gates are intentionally stored outside the execution queue.
        for project in PROJECT_INDEX.values():
            for gate in project.get('human_gates') or []:
                attention_id=str(gate.get('id') or '').strip()
                action=str(gate.get('action') or '').strip()
                if not attention_id or not action:
                    continue
                ts=now()
                c.execute("""INSERT OR IGNORE INTO portfolio_attention(
                    attention_id,project_id,action,detail,severity,source_url,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",(
                    attention_id,str(project.get('id') or ''),action,str(gate.get('detail') or ''),
                    str(gate.get('severity') or 'attention'),str(gate.get('source_url') or project.get('notion_url') or ''),
                    'open',ts,ts
                ))
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
    projects = _load_project_registry()
    errors=[]
    try:
        with connect() as c:
            state_receipts=project_runtime.latest_receipts(c)
            receipt_coverage=project_runtime.receipt_coverage(
                c,
                [p.get('id') for p in projects if str(p.get('status') or 'active')!='archived'],
            )
    except Exception:
        logging.exception('Could not read project state receipts')
        state_receipts={}
        receipt_coverage={'ready':False,'error':'unavailable','missing':[],'invalid':[],'stale':[]}
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
        try:
            contract=project_runtime.project_contract(p['id'])
            p['runtime_contract']={
                'queue_mode':contract.get('queue_mode'),
                'lane_profile':contract.get('lane_profile'),
                'ai_worker_cap':contract.get('ai_worker_cap'),
                'compute':contract.get('compute') or {},
            }
        except Exception:
            p['runtime_contract']={'error':'missing_or_invalid'}
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
        project_runtime.apply_receipt(p,state_receipts.get(p['id']))
        p['last_chatgpt_run']=watch_last_run(p['id'])
    with connect() as c:
        c.executemany('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)',events)
    try:
        with connect() as c:
            governor=project_runtime.resource_status(c)
    except Exception:
        logging.exception('Resource governor status unavailable')
        governor={'time':now(),'pools':{},'leases':[],'error':'unavailable'}
    data={'version':5,'time':now(),'host':host_metrics(),'resource_summary':resources.get('_summary',{}),'resource_governor':governor,'state_receipt_coverage':receipt_coverage,'projects':projects,'errors':errors,'sampling':{'host_seconds':15,'projects_seconds':60,'history_seconds':300},'timezone':'Europe/Amsterdam','attention_needed':portfolio_attention_items(),'portfolio_queue_health':portfolio_queue_health()}
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

def coordination_vps_health(profile='default'):
    profile=str(profile or 'default').strip().lower()
    profiles={
        'default':('zcloud_service','firefox_automation','state_store'),
        'control_plane':('zcloud_service','state_store'),
    }
    if profile not in profiles:
        raise ValueError('Onbekend VPS-healthprofiel')
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
    required=profiles[profile]
    return {
        'ok':all(checks.get(name) is True for name in required),
        'profile':profile,
        'required_checks':list(required),
        'checks':checks,
    }

def _preflight_external_evidence(payload,label):
    evidence=payload if isinstance(payload,dict) else {}
    if label=='Notion':
        if not evidence.get('checked'):
            return {'checked':False,'optional':True}
        project_ref=str(evidence.get('project_ref') or '').strip()
        handoff_ref=str(evidence.get('handoff_ref') or '').strip()
        return {'checked':True,'optional':True,'project_ref':project_ref[:500],'handoff_ref':handoff_ref[:500]}
    if not evidence.get('checked'):
        raise ValueError(f'{label} moet als gecontroleerd zijn gemarkeerd')
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
    if notion_evidence.get('checked'):
        if expected_project and notion_evidence.get('project_ref') and notion_evidence.get('project_ref')!=expected_project:
            raise ValueError('Notion project_ref wijkt af van de canonieke projectbron')
        if expected_handoff and notion_evidence.get('handoff_ref') and notion_evidence.get('handoff_ref')!=expected_handoff:
            raise ValueError('Notion handoff_ref wijkt af van de canonieke handoff')
    expected_repo=_canonical_github_repo(project_id)
    if expected_repo and str(github_evidence.get('repo') or '').lower().removesuffix('.git')!=expected_repo.lower():
        raise ValueError('GitHub repo wijkt af van de canonieke projectrepo')

def worker_preflight_record(project_id,worker_id,owner_id,notion,github,vps_profile='default'):
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
    vps_profile=str(vps_profile or 'default').strip().lower()
    if vps_profile not in ('default','control_plane'):
        raise ValueError('Onbekend VPS-healthprofiel')
    # Preserve the legacy zero-argument call for the default profile so
    # existing callers/test doubles keep the exact historical contract.
    vps=coordination_vps_health() if vps_profile=='default' else coordination_vps_health(vps_profile)
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

def task_claims(project_id=None, *, prune_expired=True):
    ts=now()
    with connect() as c:
        if prune_expired:
            c.execute('DELETE FROM task_claims WHERE lease_until<=?',(ts,))
            if project_id:
                rows=c.execute('SELECT * FROM task_claims WHERE project_id=? ORDER BY claim_key',(project_id,)).fetchall()
            else:
                rows=c.execute('SELECT * FROM task_claims ORDER BY project_id,claim_key').fetchall()
        elif project_id:
            rows=c.execute(
                'SELECT * FROM task_claims WHERE project_id=? AND lease_until>? ORDER BY claim_key',
                (project_id,ts),
            ).fetchall()
        else:
            rows=c.execute(
                'SELECT * FROM task_claims WHERE lease_until>? ORDER BY project_id,claim_key',
                (ts,),
            ).fetchall()
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

def _legacy_autonomy_policy():
    """Compatibility loader without a second hard-coded project truth source."""
    try:
        canonical=project_runtime.autonomy_policy()
    except Exception:
        logging.exception('Canonical project contracts unavailable to legacy autonomy compatibility loader')
        canonical={'schema_version':1,'default':{'mode':'manual','auto_start':False},'projects':{}}
    if not AUTONOMY_POLICY_FILE.exists():
        return canonical
    try:
        raw=json.loads(AUTONOMY_POLICY_FILE.read_text(encoding='utf-8'))
        if not isinstance(raw,dict) or raw.get('schema_version')!=1:
            raise ValueError('schema_version must be 1')
        raw_default=raw.get('default') if isinstance(raw.get('default'),dict) else {}
        base={**(canonical.get('default') or {}),**raw_default}
        file_projects=raw.get('projects') if isinstance(raw.get('projects'),dict) else {}
        projects={}
        for project_id,cfg in file_projects.items():
            if not isinstance(cfg,dict):
                raise ValueError(f'project {project_id!r} autonomy override must be an object')
            projects[str(project_id)]={**cfg}
        return {'schema_version':1,'default':base,'projects':projects}
    except Exception:
        logging.exception('Invalid autonomy compatibility policy; fail closed')
        return {'schema_version':1,'default':{'mode':'manual','auto_start':False},'projects':{}}


def load_autonomy_policy():
    """Use project-contracts.json as production autonomy truth.

    Tests or recovery tools that explicitly redirect AUTONOMY_POLICY_FILE keep the
    legacy isolated policy path so migration/recovery remains testable.
    """
    if AUTONOMY_POLICY_FILE != ROOT / 'autonomy-policy.json':
        return _legacy_autonomy_policy()
    try:
        return project_runtime.autonomy_policy()
    except Exception:
        logging.exception('Invalid canonical project runtime contracts; fail closed')
        return {'schema_version':1,'default':{'mode':'manual','auto_start':False},'projects':{}}

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
        configured_states=cfg.get('ai_states')
        allowed={str(x) for x in configured_states} if isinstance(configured_states,list) else {'NEEDS_AI'}
        local_owner=bool(cfg.get('local_executor_owns_states'))
        missing_fail_closed=bool(cfg.get('status_missing_fail_closed'))
        allow=(not missing_fail_closed) if status is None else (not local_owner and state in allowed)
        reason=('haxlab_status_missing' if status is None else
                ('haxlab_local_executor' if local_owner else
                 ('haxlab_needs_ai' if allow else 'haxlab_vps_'+state.lower())))
        detail={'vps_status':status,'state':state,'local_executor_owns_states':local_owner}
    elif mode=='ftmo_status':
        status=_autonomy_status_json(cfg.get('status_file'))
        research=(status or {}).get('research') if isinstance((status or {}).get('research'),dict) else {}
        stage=str(research.get('next_stage') or 'missing')
        paper=(status or {}).get('paper_forward_shadow') if isinstance((status or {}).get('paper_forward_shadow'),dict) else {}
        configured_stages=cfg.get('ai_stages')
        allowed={str(x) for x in configured_stages} if isinstance(configured_stages,list) else set()
        local_owner=bool(cfg.get('local_executor_owns_stages'))
        missing_fail_closed=bool(cfg.get('status_missing_fail_closed'))
        runtime_ok=bool((status or {}).get('ok'))
        paper_busy=paper.get('action')=='running'
        allow=(not missing_fail_closed) if status is None else (not local_owner and runtime_ok and stage in allowed and not paper_busy)
        reason=('ftmo_status_missing' if status is None else
                ('ftmo_local_executor' if local_owner else
                 ('ftmo_stage_'+stage if allow else
                  ('ftmo_paper_running' if paper_busy else 'ftmo_vps_'+('not_ok' if not runtime_ok else stage)))))
        detail={'vps_status':status,'next_stage':stage,'paper_action':paper.get('action'),'local_executor_owns_stages':local_owner}
    elif mode=='external_gate':
        allow=False;reason='external_or_human_gate'
    elif mode=='manual':
        allow=False;reason='manual_mode'

    # Dynamic workers are non-stopping by policy. WAIT/COMPLETE markers are
    # telemetry only; manual dashboard controls and anti-spam are the stop gates.
    hold=None
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

def _global_dispatch_due(min_interval_seconds):
    """Apply the cooldown across the single global worker, even after project rotation."""
    try:
        min_interval_seconds=max(0,int(min_interval_seconds or 0))
    except Exception:
        min_interval_seconds=0
    if min_interval_seconds<=0:
        return True
    try:
        with connect() as c:
            row=c.execute('SELECT MAX(last_dispatch_at) AS last_dispatch_at FROM autonomy_runtime').fetchone()
        last_value=(row['last_dispatch_at'] if row else None) or ''
        if not last_value:
            return True
        last=datetime.fromisoformat(last_value).astimezone(timezone.utc)
        return (datetime.now(timezone.utc)-last).total_seconds() >= min_interval_seconds
    except Exception:
        logging.exception('Could not evaluate global AI dispatch cooldown')
        return False


def _worker_prompt_interval_due(connection,project_id,worker_slot,min_interval_seconds):
    try:
        min_interval_seconds=max(5,int(min_interval_seconds or PORTFOLIO_AI_COOLDOWN_SECONDS))
    except Exception:
        min_interval_seconds=PORTFOLIO_AI_COOLDOWN_SECONDS
    row=connection.execute(
        "SELECT ts FROM runner_events WHERE project_id=? AND worker_slot=? AND event='prompt-sent' ORDER BY id DESC LIMIT 1",
        (project_id,worker_slot),
    ).fetchone()
    if not row or not row['ts']:
        return True
    try:
        last=datetime.fromisoformat(row['ts']).astimezone(timezone.utc)
        return (datetime.now(timezone.utc)-last).total_seconds() >= min_interval_seconds
    except Exception:
        return False


def _global_dispatch_interval_due(connection,min_interval_seconds):
    """Guard the single global worker across project rotation."""
    try:
        min_interval_seconds=max(5,int(min_interval_seconds or PORTFOLIO_AI_COOLDOWN_SECONDS))
    except Exception:
        min_interval_seconds=PORTFOLIO_AI_COOLDOWN_SECONDS
    row=connection.execute(
        "SELECT MAX(last_dispatch_at) AS ts FROM autonomy_runtime"
    ).fetchone()
    if not row or not row["ts"]:
        return True
    try:
        last=datetime.fromisoformat(row["ts"]).astimezone(timezone.utc)
        return (datetime.now(timezone.utc)-last).total_seconds() >= min_interval_seconds
    except Exception:
        return False


def _autonomy_enqueue_worker_push(project_id,worker_slot,reason,min_interval_seconds):
    try:
        worker_slot=max(1,min(GLOBAL_CHATGPT_WORKER_LIMIT,int(worker_slot)))
    except Exception:
        return False
    worker_key=f'{project_id}::w{worker_slot}'
    ts=now()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        runtime=c.execute('SELECT * FROM autonomy_runtime WHERE project_id=?',(project_id,)).fetchone()
        if runtime and bool(runtime['manual_pause']):
            return False
        target=c.execute('SELECT active FROM runner_targets WHERE project_id=?',(project_id,)).fetchone()
        if not target or not bool(target['active']):
            return False
        desired=c.execute(
            'SELECT desired_state FROM runner_workers WHERE project_id=? AND worker_slot=?',
            (project_id,worker_slot),
        ).fetchone()
        if desired and str(desired['desired_state'] or 'running')!='running':
            return False
        # Anti-spam is per dynamic worker: each worker may dispatch at most once
        # per configured interval, while separate workers can progress independently.
        if not _worker_prompt_interval_due(c,project_id,worker_slot,min_interval_seconds):
            return False
        pending=c.execute(
            "SELECT id FROM runner_commands WHERE project_id=? AND action IN ('start','push','new_chat') AND status='pending' LIMIT 1",
            (worker_key,),
        ).fetchone()
        if pending:
            return False
        latest=c.execute(
            'SELECT id,generating,sending,event FROM runner_events WHERE project_id=? AND worker_slot=? ORDER BY id DESC LIMIT 1',
            (project_id,worker_slot),
        ).fetchone()
        if latest and (bool(latest['generating']) or bool(latest['sending'])):
            return False
        last_prompt=c.execute(
            "SELECT MAX(id) AS id FROM runner_events WHERE project_id=? AND worker_slot=? AND event='prompt-sent'",
            (project_id,worker_slot),
        ).fetchone()
        last_ready=c.execute(
            "SELECT MAX(id) AS id FROM runner_events WHERE project_id=? AND worker_slot=? AND event IN ('awaiting-vps-dispatch','generation-not-started','runner-auto-paused')",
            (project_id,worker_slot),
        ).fetchone()
        prompt_id=int((last_prompt or {'id':0})['id'] or 0)
        ready_id=int((last_ready or {'id':0})['id'] or 0)
        # A prompt from an older browser session must not permanently block the
        # first dispatch after a runner restart/recovery. If a newer, idle
        # runner event exists, the old cycle has been superseded and the
        # normal global 300-second cooldown remains the final guard.
        latest_id=int((latest or {'id':0})['id'] or 0)
        latest_generating=bool(latest['generating']) if latest else False
        latest_sending=bool(latest['sending']) if latest else False
        if prompt_id and ready_id < prompt_id:
            if latest_id <= prompt_id or latest_generating or latest_sending:
                return False
        c.execute('INSERT INTO runner_commands(project_id,action,status,created_at,updated_at,result) VALUES(?,?,?,?,?,?)',
                  (worker_key,'push','pending',ts,ts,None))
        c.execute('UPDATE autonomy_runtime SET last_dispatch_at=?,last_reason=? WHERE project_id=?',
                  (ts,str(reason)[:250],project_id))
    return True


def _autonomy_enqueue_push(project_id,reason,min_interval_seconds):
    """Compatibility helper for tests/manual callers; production scheduler dispatches per worker."""
    return _autonomy_enqueue_worker_push(project_id,1,reason,min_interval_seconds)


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


def _portfolio_priority_rank(priority):
    return {'P0':0,'P1':1,'P2':2,'P3':3}.get(str(priority or 'P3').upper(),3)

def _manual_start_priority_key(project_id):
    return 'manual_start_priority:' + str(project_id or '').strip().lower()

def _manual_force_start_priority_key(project_id):
    return 'manual_force_start_priority:' + str(project_id or '').strip().lower()

def _bounded_manual_intent_active_locked(connection, key, at=None):
    row=connection.execute(
        'SELECT value FROM runtime_settings WHERE key=?',
        (str(key or ''),),
    ).fetchone()
    if not row or not row['value']:
        return False
    try:
        started=datetime.fromisoformat(str(row['value']).replace('Z','+00:00')).astimezone(timezone.utc)
        age=((at or datetime.now(timezone.utc))-started).total_seconds()
        return 0 <= age <= MANUAL_START_PRIORITY_SECONDS
    except Exception:
        return False

def _manual_start_priority_active_locked(connection, project_id, at=None):
    project_id=str(project_id or '').strip().lower()
    if not project_id:
        return False
    return _bounded_manual_intent_active_locked(
        connection,
        _manual_start_priority_key(project_id),
        at,
    )

def _manual_force_start_priority_active_locked(connection, project_id, at=None):
    project_id=str(project_id or '').strip().lower()
    if not project_id:
        return False
    return _bounded_manual_intent_active_locked(
        connection,
        _manual_force_start_priority_key(project_id),
        at,
    )

def _pending_worker_handoff_floor_locked(connection, project_id):
    """Highest local worker ordinal that must remain stable for an in-flight browser handoff.

    Worker keys are project-local ordinals (project::wN) derived from the ordered
    queue claims. Protecting only the exact wN row is insufficient: if any earlier
    project claim is preempted in the same allocation pass, the target row is
    renumbered before Firefox consumes its pending command. Pin the full prefix
    w1..wN while start/new_chat/push is pending so the command keeps the same
    queue-backed identity until it is consumed.
    """
    project_id=str(project_id or '').strip().lower()
    if not project_id:
        return 0
    prefix=project_id+'::w'
    rows=connection.execute(
        """SELECT project_id FROM runner_commands
           WHERE project_id LIKE ?
             AND action IN ('start','new_chat','push')
             AND status='pending'""",
        (prefix+'%',),
    ).fetchall()
    floor=0
    for row in rows:
        worker_key=str(row['project_id'] or '')
        if not worker_key.startswith(prefix):
            continue
        try:
            floor=max(floor,int(worker_key[len(prefix):]))
        except (TypeError,ValueError):
            continue
    return max(0,min(GLOBAL_CHATGPT_WORKER_LIMIT,floor))

def _portfolio_project_soft_cap(project_id=None):
    """Return the preferred browser-worker share for one runnable project.

    This is deliberately a *soft* cap. A project whose canonical autonomy state
    currently says AI should yield gets no preferred slot while another project
    has runnable work, but may still borrow otherwise-idle capacity. That keeps
    FTMO/HaxLab gate waits from monopolising browser workers without turning their
    maintenance backlog into a permanent hard block. Running/verifying work is
    still never preempted for diversity.
    """
    limit=max(1,int(GLOBAL_CHATGPT_WORKER_LIMIT))
    base=1 if limit <= 2 else limit - 1
    pid=str(project_id or '').strip().lower()
    if not pid:
        return base
    mode=str((_autonomy_config(pid) or {}).get('mode') or 'manual').lower()
    if mode in {'ftmo_status','haxlab_status','zcloud_stopgate'}:
        try:
            if not bool(project_autonomy_state(pid).get('allow_ai')):
                return 0
        except Exception:
            logging.exception('Could not evaluate project autonomy soft-cap for %s',pid)
            return 0
    return base


def _portfolio_project_hard_cap(project_id):
    """Return the canonical per-project AI/code-worker cap."""
    limit=max(1,int(GLOBAL_CHATGPT_WORKER_LIMIT))
    try:
        return project_runtime.ai_worker_cap(str(project_id or '').strip().lower(),limit)
    except Exception:
        logging.exception('Missing/invalid project runtime contract for %s',project_id)
        # Fail closed to one worker rather than silently granting the full pool.
        return 1

def _portfolio_queue_metadata(row):
    if not row:
        return {}
    if isinstance(row,dict) and isinstance(row.get('metadata'),dict):
        return dict(row.get('metadata') or {})
    try:
        raw=row.get('metadata_json') if isinstance(row,dict) else row['metadata_json']
    except Exception:
        raw=None
    try:
        value=json.loads(raw or '{}')
    except Exception:
        value={}
    return value if isinstance(value,dict) else {}

def _portfolio_queue_row(row):
    if not row:
        return None
    item=dict(row)
    item['eligible']=bool(item.get('eligible'))
    metadata=_portfolio_queue_metadata(item)
    item['metadata']=metadata
    item.pop('metadata_json',None)
    item['execution_lane']=metadata.get('execution_lane') if isinstance(metadata.get('execution_lane'),dict) else None
    return item

def _portfolio_lane_snapshot_locked(connection,project_ids=None,ts=None):
    ts=ts or now()
    params=[]
    project_clause=''
    ids=sorted({str(value or '').strip().lower() for value in (project_ids or []) if str(value or '').strip()})
    if ids:
        project_clause=' AND project_id IN ('+','.join('?' for _ in ids)+')'
        params.extend(ids)
    backlog=connection.execute(
        """SELECT * FROM portfolio_queue
           WHERE eligible=1 AND status IN ('queued','claimed','running','verifying')""" + project_clause +
        """ ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
                    created_at,queue_id""",
        params,
    ).fetchall()
    claim_params=[ts]
    claim_clause=''
    if ids:
        claim_clause=' AND project_id IN ('+','.join('?' for _ in ids)+')'
        claim_params.extend(ids)
    claim_rows=connection.execute(
        'SELECT * FROM task_claims WHERE lease_until>?' + claim_clause + ' ORDER BY project_id,acquired_at,claim_key',
        claim_params,
    ).fetchall()
    claims_by_project={}
    for row in claim_rows:
        claims_by_project.setdefault(str(row['project_id']),[]).append(_claim_payload(row))
    backlog_by_project={}
    for row in backlog:
        backlog_by_project.setdefault(str(row['project_id']),[]).append(dict(row))
    target_ids=ids or sorted(backlog_by_project)
    lanes=[]
    for project_id in target_ids:
        project=PROJECT_INDEX.get(project_id) or {}
        autonomy_mode=str((_autonomy_config(project_id) or {}).get('mode') or 'manual').lower()
        if (not project
                or str(project.get('queue_mode') or '').lower()=='human-gated'
                or autonomy_mode in {'external_gate','manual'}):
            continue
        lanes.extend(generate_execution_lanes(
            project,
            backlog_by_project.get(project_id,[]),
            claims_by_project.get(project_id,[]),
        ))
    return lanes

def portfolio_execution_lanes(project_id=None):
    ids=[str(project_id).strip().lower()] if project_id else None
    with connect() as c:
        return _portfolio_lane_snapshot_locked(c,ids,now())

def _portfolio_eligible_lane_candidates_locked(connection,ts,preempted=None):
    rows=connection.execute(
        """SELECT * FROM portfolio_queue
           WHERE eligible=1 AND status='queued' AND worker_slot IS NULL
           ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
                    created_at,queue_id LIMIT 80"""
    ).fetchall()
    preempted={str(value) for value in (preempted or set())}
    rows=[row for row in rows if str(row['queue_id']) not in preempted]
    if not rows:
        return []
    project_ids={str(row['project_id']) for row in rows}
    lane_map={
        str(lane.get('queue_id')):lane
        for lane in _portfolio_lane_snapshot_locked(connection,project_ids,ts)
        if lane.get('queue_id') and lane.get('status')=='queued'
    }
    return [(row,lane_map[str(row['queue_id'])]) for row in rows if str(row['queue_id']) in lane_map]

def _portfolio_lane_metadata(row,lane):
    metadata=_portfolio_queue_metadata(dict(row))
    metadata['execution_lane']={
        'project_id':str(lane.get('project_id') or row['project_id']),
        'profile':str(lane.get('profile') or ''),
        'lane_id':str(lane.get('lane_id') or ''),
        'scope':lane.get('scope') if isinstance(lane.get('scope'),dict) else {'capabilities':[],'files':[]},
    }
    return json.dumps(metadata,ensure_ascii=False,separators=(',',':'))

def _portfolio_requeue_conflicting_claimed_lanes_locked(connection,ts):
    """Release only unstarted claimed rows whose derived write lane already conflicts."""
    rows=connection.execute(
        """SELECT * FROM portfolio_queue
           WHERE eligible=1 AND status IN ('claimed','running','verifying') AND worker_slot IS NOT NULL
           ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'verifying' THEN 1 ELSE 2 END,
                    CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
                    claimed_at,created_at,queue_id"""
    ).fetchall()
    accepted=[]
    released=[]
    for row in rows:
        project_id=str(row['project_id'] or '')
        project=PROJECT_INDEX.get(project_id) or {}
        if not project:
            continue
        lane=classify_backlog_item(project,dict(row))
        conflict=None
        for other in accepted:
            if other['project_id']!=project_id:
                continue
            overlap=scopes_overlap(lane['scope'],other['scope'])
            if lane['lane_id']==other['lane_id'] or overlap['capabilities'] or overlap['files']:
                conflict={
                    'queue_id':other['queue_id'],
                    'lane_id':other['lane_id'],
                    'overlap':overlap,
                }
                break
        if conflict and str(row['status'])=='claimed':
            connection.execute(
                """UPDATE portfolio_queue
                   SET status='queued',worker_slot=NULL,claimed_at=NULL,claim_expires=NULL,updated_at=?
                   WHERE queue_id=? AND status='claimed'""",
                (ts,row['queue_id']),
            )
            released.append({
                'queue_id':str(row['queue_id']),
                'project_id':project_id,
                'lane_id':lane['lane_id'],
                'conflict':conflict,
            })
            continue
        accepted.append({
            'queue_id':str(row['queue_id']),
            'project_id':project_id,
            'lane_id':lane['lane_id'],
            'scope':lane['scope'],
        })
    return released

def _portfolio_queue_execution_capable(title,completion_criteria=''):
    title_text=str(title or '').strip().lower()
    criteria=str(completion_criteria or '').strip().lower()
    combined=title_text+'\n'+criteria
    human_only=(
        'human-only','human only','requires human','waiting for human','wait for human',
        'external gate only','physical test only','manual-only','manual only',
    )
    if any(marker in combined for marker in human_only):
        return False
    non_exec_prefixes=('inspect ','audit ','review ','verify ','check ','monitor ','report ','summarize ','status ','superseded ')
    execution_words=(
        'implement','build','fix','change','write','deploy','merge','create','update','refactor',
        'execute','run','train','generate','migrate','configure','persist','experiment',
    )
    has_execution=bool(re.search(r'\\b(?:'+('|'.join(execution_words))+r')\\b',combined)) or any(
        marker in combined for marker in ('material code','material config','material workflow','runtime state change')
    )
    if title_text.startswith(non_exec_prefixes) and not has_execution:
        return False
    if ('read-only evidence is recorded' in combined or 'read-only verification' in combined) and not has_execution:
        return False
    return True

def portfolio_attention_items(include_resolved=False):
    with connect() as c:
        if include_resolved:
            rows=c.execute("""SELECT * FROM portfolio_attention
                              ORDER BY CASE severity WHEN 'urgent' THEN 0 WHEN 'attention' THEN 1 ELSE 2 END,
                                       updated_at DESC, attention_id""").fetchall()
        else:
            rows=c.execute("""SELECT * FROM portfolio_attention WHERE status='open'
                              ORDER BY CASE severity WHEN 'urgent' THEN 0 WHEN 'attention' THEN 1 ELSE 2 END,
                                       updated_at DESC, attention_id""").fetchall()
    return [dict(row) for row in rows]

def portfolio_attention_add(project_id,action,detail='',source_url='',severity='attention',attention_id=None):
    project_id=str(project_id or '').strip().lower()
    action=str(action or '').strip()
    if project_id not in PROJECT_INDEX:
        raise ValueError('Onbekend project voor attention item')
    if not action:
        raise ValueError('action is verplicht')
    if severity not in ('urgent','attention','later'):
        severity='attention'
    if not attention_id:
        digest=hashlib.sha256((project_id+'\n'+action).encode()).hexdigest()[:16]
        attention_id=project_id+'-'+digest
    attention_id=re.sub(r'[^a-zA-Z0-9._:-]+','-',str(attention_id).strip())[:160]
    ts=now()
    with connect() as c:
        c.execute("""INSERT INTO portfolio_attention(
            attention_id,project_id,action,detail,severity,source_url,status,created_at,updated_at,resolved_at
        ) VALUES(?,?,?,?,?,?, 'open',?,?,NULL)
        ON CONFLICT(attention_id) DO UPDATE SET
            project_id=excluded.project_id,action=excluded.action,detail=excluded.detail,
            severity=excluded.severity,source_url=excluded.source_url,status='open',
            updated_at=excluded.updated_at,resolved_at=NULL""",
            (attention_id,project_id,action,str(detail or '')[:4000],severity,str(source_url or ''),ts,ts))
        row=c.execute('SELECT * FROM portfolio_attention WHERE attention_id=?',(attention_id,)).fetchone()
    return dict(row)

def portfolio_attention_resolve(attention_id):
    attention_id=str(attention_id or '').strip()
    if not attention_id:
        raise ValueError('attention_id is verplicht')
    ts=now()
    with connect() as c:
        cur=c.execute("""UPDATE portfolio_attention SET status='resolved',resolved_at=?,updated_at=?
                         WHERE attention_id=? AND status='open'""",(ts,ts,attention_id))
    return {'resolved':bool(cur.rowcount),'attention_id':attention_id}

def _blocker_needs_human(evidence):
    text=str(evidence or '').lower()
    markers=(
        'human','user action','manual approval','physical','watch','participant','maintainer',
        'credential','api key','secret','oauth approval','review submission','external gate',
        'needs_human','wait_human'
    )
    return any(marker in text for marker in markers)

def _attention_action_for_blocker(project_id,title,evidence):
    text=(str(title or '')+' '+str(evidence or '')).lower()
    name=str((PROJECT_INDEX.get(project_id) or {}).get('name') or project_id)
    if 'participant' in text or 'maintainer' in text or 'usability' in text:
        return f'{name}: do or arrange the real user/maintainer test'
    if 'watch' in text or 'physical' in text or 'device' in text:
        return f'{name}: complete the physical device test'
    if 'credential' in text or 'api key' in text or 'secret' in text:
        return f'{name}: add or approve the required account credential'
    if 'review' in text or 'submission' in text:
        return f'{name}: complete the external review/submission step'
    return f'{name}: your input is needed to unblock this step'

def portfolio_queue_items(include_done=False):
    with connect() as c:
        if include_done:
            rows=c.execute("""SELECT * FROM portfolio_queue
                              ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
                                       created_at, queue_id""").fetchall()
        else:
            rows=c.execute("""SELECT * FROM portfolio_queue
                              WHERE status NOT IN ('done','dropped')
                              ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
                                       created_at, queue_id""").fetchall()
    return [_portfolio_queue_row(row) for row in rows]

def portfolio_queue_current_for_slot(global_slot):
    try: slot=int(global_slot)
    except Exception: return None
    ts=now()
    with connect() as c:
        row=c.execute("""SELECT * FROM portfolio_queue
                         WHERE worker_slot=? AND status IN ('claimed','running','verifying')
                           AND (claim_expires IS NULL OR claim_expires>?)
                         ORDER BY updated_at DESC LIMIT 1""",(slot,ts)).fetchone()
    return _portfolio_queue_row(row)

def portfolio_queue_has_project_assignment(project_id):
    ts=now()
    with connect() as c:
        row=c.execute("""SELECT 1 FROM portfolio_queue
                         WHERE project_id=? AND status IN ('claimed','running','verifying')
                           AND (claim_expires IS NULL OR claim_expires>?) LIMIT 1""",(project_id,ts)).fetchone()
    return bool(row)

def portfolio_queue_has_eligible_work(exclude_queue_id=None):
    with connect() as c:
        if exclude_queue_id:
            row=c.execute("""SELECT 1 FROM portfolio_queue
                             WHERE eligible=1 AND status IN ('queued','claimed','running','verifying')
                               AND queue_id<>? LIMIT 1""",(str(exclude_queue_id),)).fetchone()
        else:
            row=c.execute("""SELECT 1 FROM portfolio_queue
                             WHERE eligible=1 AND status IN ('queued','claimed','running','verifying')
                             LIMIT 1""").fetchone()
    return bool(row)

def portfolio_queue_enqueue(project_id,title,priority='P2',completion_criteria='',source_url='',parent_queue_id=None,queue_id=None):
    project_id=str(project_id or '').strip().lower()
    title=str(title or '').strip()
    priority=str(priority or 'P2').strip().upper()
    if project_id not in PROJECT_INDEX:
        raise ValueError('Onbekend project voor portfolio queue')
    if not title:
        raise ValueError('title is verplicht')
    if priority not in ('P0','P1','P2','P3'):
        raise ValueError('priority moet P0, P1, P2 of P3 zijn')
    # Project defaults belong in portfolio_write_continuation(). Explicit HaxLab
    # implementation items may use P1/P2 so real roadmap work can outrank stale
    # generic continuations. HaxLab remains a hobby/background project overall,
    # so it may never claim the portfolio's P0 system/revenue tier.
    if project_id=='haxlab' and priority=='P0':
        priority='P2'
    if str((PROJECT_INDEX.get(project_id) or {}).get('queue_mode') or '').lower()=='human-gated':
        raise ValueError('Project is human-gated; gebruik Attention Needed in plaats van de workerqueue')
    if not _portfolio_queue_execution_capable(title,completion_criteria):
        raise ValueError('Alleen uitvoerbare write/build/test/deploy taken mogen in de workerqueue')
    if not queue_id:
        digest=hashlib.sha256((project_id+'\n'+title+'\n'+str(parent_queue_id or '')).encode()).hexdigest()[:16]
        queue_id=project_id+'-'+digest
    queue_id=re.sub(r'[^a-zA-Z0-9._:-]+','-',str(queue_id).strip())[:160]
    ts=now()
    with connect() as c:
        c.execute("""INSERT INTO portfolio_queue(
            queue_id,project_id,title,priority,status,eligible,completion_criteria,source_url,
            created_at,updated_at,parent_queue_id
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(queue_id) DO UPDATE SET
            project_id=excluded.project_id,title=excluded.title,priority=excluded.priority,
            completion_criteria=excluded.completion_criteria,source_url=excluded.source_url,
            updated_at=excluded.updated_at""",
            (queue_id,project_id,title,priority,'queued',1,str(completion_criteria or ''),
             str(source_url or ''),ts,ts,parent_queue_id))
        row=c.execute('SELECT * FROM portfolio_queue WHERE queue_id=?',(queue_id,)).fetchone()
    return _portfolio_queue_row(row)

def portfolio_write_continuation(project_id,parent_queue_id=None):
    project_id=str(project_id or '').strip().lower()
    project=PROJECT_INDEX.get(project_id) or {}
    if str(project.get('queue_mode') or '').lower()=='human-gated':
        return None
    name=str(project.get('name') or project_id or 'project').strip()
    next_step=str(project.get('next_step') or '').strip()
    priority_by_project={
        'lightup':'P0',
        'zssh':'P1',
        'ftmo':'P1',
        'zguard':'P2',
        'cloud':'P2',
        'raiseai':'P3',
        'supa':'P3',
        'ulab':'P3',
        'haxlab':'P3',
    }
    priority=priority_by_project.get(project_id,'P3')
    if parent_queue_id is None:
        with connect() as c:
            sequence=c.execute('SELECT COUNT(*) FROM portfolio_queue WHERE project_id=?',(project_id,)).fetchone()[0]+1
        parent_queue_id=f'auto-refill-{project_id}-{sequence}'
    criteria=(
        f'Read the current {name} HQ/handoff and execute a substantial multi-step work package, not a single micro-task. '
        'Treat this assignment as a long-running implementation block: identify the highest-value safe unblocked roadmap area, '
        'then complete as many adjacent write-capable steps as can be safely finished in the same lane before yielding. '
        'Completion requires at least three material implementation actions across code/config/workflows/experiments/runtime state '
        'OR one clearly large end-to-end implementation that spans multiple files/components, plus automated test/build/run evidence. '
        'A single tiny edit, one config tweak, one commit, read-only inspection, audit, status, checklist, documentation-only work, '
        'or evidence collection alone cannot complete this item. After every successful sub-step, immediately continue to the next '
        'safe adjacent roadmap step within the same assignment. Only return DONE when the work package is genuinely exhausted, '
        'a meaningful milestone is proven green, or further progress requires a real human/external gate.'
    )
    if next_step:
        criteria += ' Current project next-step hint: ' + next_step
    if project_id=='ftmo':
        criteria += ' FTMO priority rule: keep advancing safe preregistered generations continuously; after one gate is proven, continue to the next safe write-capable generation step instead of yielding the lane to lower-priority projects.'
    if project_id=='zssh':
        criteria += ' zSSH research-first rule: before architectural, security, authentication, permission, public-review, or UX decisions, research current primary sources and record dated source URLs plus the engineering decision in docs/research or an ADR; then implement and test the decision. Research-only is not completion, but coding without research evidence is also not completion. Optimize every continuation toward a ship-ready public plugin: universal HTTPS MCP endpoint, production OAuth, simple connect website, target pairing/revocation, explicit scoped sudo grants, plugin package/review materials, red-team gates, and production submission-readiness evidence.'
    return portfolio_queue_enqueue(
        project_id,
        f'Execute substantial {name} roadmap work package',
        priority,
        criteria,
        project.get('notion_url') or project.get('handoff_url') or '',
        parent_queue_id=parent_queue_id,
    )

def portfolio_queue_audit(refill=True):
    """Keep the execution queue write-only, broad enough, and separate from human gates."""
    removed=[]
    with connect() as c:
        rows=c.execute("""SELECT queue_id,project_id,title,completion_criteria FROM portfolio_queue
                          WHERE eligible=1 AND status IN ('queued','claimed','running','verifying')""").fetchall()
        for row in rows:
            project=PROJECT_INDEX.get(str(row['project_id'])) or {}
            human_gated=str(project.get('queue_mode') or '').lower()=='human-gated'
            if human_gated or not _portfolio_queue_execution_capable(row['title'],row['completion_criteria']):
                c.execute("""UPDATE portfolio_queue SET status='dropped',eligible=0,worker_slot=NULL,
                             claimed_at=NULL,claim_expires=NULL,blocker=?,updated_at=? WHERE queue_id=?""",
                          ('queue-audit: non-executable or human-gated task',now(),row['queue_id']))
                removed.append(str(row['queue_id']))
    if not refill:
        with connect() as c:
            ready=c.execute("""SELECT COUNT(*) FROM portfolio_queue WHERE eligible=1 AND status IN ('queued','claimed','running','verifying')""").fetchone()[0]
            projects=c.execute("""SELECT COUNT(DISTINCT project_id) FROM portfolio_queue WHERE eligible=1 AND status IN ('queued','claimed','running','verifying')""").fetchone()[0]
        return {'ready':int(ready),'projects':int(projects),'removed':removed,'created':[],'time':now()}
    # Keep at least one executable item available for every non-human-gated active project.
    with connect() as c:
        rows=c.execute("""SELECT DISTINCT project_id FROM portfolio_queue
                          WHERE eligible=1 AND status IN ('queued','claimed','running','verifying')""").fetchall()
        represented={str(row['project_id']) for row in rows}
    project_order=sorted(
        PROJECT_INDEX.values(),
        key=lambda p: ({'system':0,'high':1,'normal':2,'low':3,'background':4}.get(str(p.get('priority') or 'normal'),2), str(p.get('id') or ''))
    )
    created=[]
    for project in project_order:
        project_id=str(project.get('id') or '')
        if not project_id or str(project.get('status') or 'active')!='active':
            continue
        if str(project.get('queue_mode') or '').lower()=='human-gated' or project_id in represented:
            continue
        try:
            item=portfolio_write_continuation(project_id)
            if item:
                created.append(item['queue_id'])
                represented.add(project_id)
        except ValueError:
            continue
    with connect() as c:
        ready=c.execute("""SELECT COUNT(*) FROM portfolio_queue
                           WHERE eligible=1 AND status IN ('queued','claimed','running','verifying')""").fetchone()[0]
        projects=c.execute("""SELECT COUNT(DISTINCT project_id) FROM portfolio_queue
                              WHERE eligible=1 AND status IN ('queued','claimed','running','verifying')""").fetchone()[0]
    return {'ready':int(ready),'projects':int(projects),'removed':removed,'created':created,'time':now()}

def portfolio_queue_health():
    with connect() as c:
        ready=c.execute("""SELECT COUNT(*) FROM portfolio_queue
                           WHERE eligible=1 AND status IN ('queued','claimed','running','verifying')""").fetchone()[0]
        queued=c.execute("SELECT COUNT(*) FROM portfolio_queue WHERE eligible=1 AND status='queued'").fetchone()[0]
        projects=c.execute("""SELECT COUNT(DISTINCT project_id) FROM portfolio_queue
                              WHERE eligible=1 AND status IN ('queued','claimed','running','verifying')""").fetchone()[0]
        dropped=c.execute("SELECT COUNT(*) FROM portfolio_queue WHERE status='dropped'").fetchone()[0]
        attention=c.execute("SELECT COUNT(*) FROM portfolio_attention WHERE status='open'").fetchone()[0]
    target=max(6,max(1,int(GLOBAL_CHATGPT_WORKER_LIMIT))*PORTFOLIO_QUEUE_MIN_READY_PER_WORKER)
    return {
        'ready':int(ready),'queued':int(queued),'projects':int(projects),'dropped':int(dropped),
        'attention':int(attention),'target_ready':int(target),
        'healthy':int(ready)>=min(target,max(1,len([p for p in PROJECT_INDEX.values() if str(p.get('queue_mode') or '').lower()!='human-gated']))),
        'notion_attention_url':PORTFOLIO_ATTENTION_NOTION_URL,
    }

def portfolio_queue_allocate():
    ts_dt=datetime.now(timezone.utc)
    ts=ts_dt.isoformat()
    lease_until=(ts_dt+timedelta(seconds=PORTFOLIO_QUEUE_LEASE_SECONDS)).isoformat()
    selected=[]
    memory_guard=worker_memory_status()
    # Capacity applies only to fresh browser-worker claims in this pass. Existing
    # claims already contribute to MemAvailable and are deliberately preserved.
    # After an OOM recovery we briefly hold fresh allocations so Firefox can
    # settle and MemAvailable reflects the relaunched tabs before scaling again.
    recovery_hold_until=_worker_recovery_hold_until()
    new_worker_capacity=0 if recovery_hold_until else max(0,int(memory_guard.get('new_worker_capacity') or 0))
    new_workers_claimed=0
    # Force Start is allowed to override scheduler priority, but never active
    # browser work. Read this outside the allocator transaction to avoid opening
    # a nested SQLite connection while the write lock is held.
    busy_worker_keys=_busy_ai_worker_keys()
    # Queue items preempted during this allocation pass must stay queued until
    # the next scheduler tick. Re-claiming them immediately into another slot
    # races the browser/worker mapping that is still being recycled.
    preempted=set()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute("""UPDATE portfolio_queue
                     SET status='queued',worker_slot=NULL,claimed_at=NULL,claim_expires=NULL,updated_at=?
                     WHERE eligible=1 AND status IN ('claimed','running','verifying')
                       AND claim_expires IS NOT NULL AND claim_expires<=?""",(ts,ts))
        _portfolio_requeue_conflicting_claimed_lanes_locked(c,ts)

        occupied_rows=c.execute(
            """SELECT * FROM portfolio_queue
               WHERE worker_slot IS NOT NULL
                 AND status IN ('claimed','running','verifying')
                 AND eligible=1 AND (claim_expires IS NULL OR claim_expires>?)
               ORDER BY worker_slot""",
            (ts,),
        ).fetchall()

        # A live browser pool can become the thing that starves the control-plane:
        # MemAvailable can fall below the critical floor after workers were already
        # admitted, while new_worker_capacity=0 only blocks *new* slots. In that
        # state release at most one safe idle allocation per scheduler tick. This
        # is deliberately conservative: verifying work, system-priority projects,
        # generating/sending workers and in-flight browser handoffs are pinned.
        # Releasing one slot at a time gives Firefox a chance to reclaim memory
        # before the next decision and avoids turning memory pressure into churn.
        if str(memory_guard.get('pressure') or '').lower()=='critical':
            local_ordinals={}
            project_protection={'system':0,'high':1,'normal':2,'low':3,'background':4}
            relief_candidates=[]
            for occupied in occupied_rows:
                row_project=str(occupied['project_id'] or '')
                local_ordinals[row_project]=local_ordinals.get(row_project,0)+1
                local_slot=local_ordinals[row_project]
                worker_key=f'{row_project}::w{local_slot}'
                status=str(occupied['status'] or '')
                project_priority=str((PROJECT_INDEX.get(row_project) or {}).get('priority') or 'normal').lower()
                if status=='verifying' or project_priority=='system':
                    continue
                if worker_key in busy_worker_keys:
                    continue
                handoff_floor=_pending_worker_handoff_floor_locked(c,row_project)
                if 0 < local_slot <= handoff_floor:
                    continue
                relief_candidates.append((
                    0 if status=='claimed' else 1,
                    -_portfolio_priority_rank(occupied['priority']),
                    -project_protection.get(project_priority,2),
                    str(occupied['updated_at'] or ''),
                    int(occupied['worker_slot'] or 0),
                    str(occupied['queue_id'] or ''),
                ))
            if relief_candidates:
                *_,relief_slot,relief_queue_id=min(relief_candidates)
                relief_update=c.execute(
                    """UPDATE portfolio_queue
                       SET status='queued',worker_slot=NULL,claimed_at=NULL,claim_expires=NULL,updated_at=?
                       WHERE queue_id=? AND worker_slot=?
                         AND status IN ('claimed','running')""",
                    (ts,relief_queue_id,relief_slot),
                )
                if relief_update.rowcount:
                    preempted.add(relief_queue_id)
                    occupied_rows=[
                        row for row in occupied_rows
                        if str(row['queue_id'] or '')!=relief_queue_id
                    ]
                    logging.warning(
                        'Critical memory pressure released idle browser allocation %s from slot %s (available_mb=%s)',
                        relief_queue_id,relief_slot,memory_guard.get('available_mb'),
                    )

        occupied_projects={str(row['project_id'] or '') for row in occupied_rows}

        # A Force Start only needs an allocation override when its project is not
        # already resident. Pick one queued force-intent candidate deterministically.
        force_pairs=_portfolio_eligible_lane_candidates_locked(c,ts,preempted)
        force_pairs=[
            pair for pair in force_pairs
            if str(pair[0]['project_id'] or '') not in occupied_projects
            and _manual_force_start_priority_active_locked(c,pair[0]['project_id'],ts_dt)
        ]
        force_pair=min(
            force_pairs,
            key=lambda pair: (
                _portfolio_priority_rank(pair[0]['priority']),
                str(pair[0]['created_at'] or ''),
                str(pair[0]['queue_id'] or ''),
            ),
        ) if force_pairs else None
        force_project=str(force_pair[0]['project_id'] or '') if force_pair else ''
        force_queue_id=str(force_pair[0]['queue_id'] or '') if force_pair else ''

        # If Force Start targets an already-resident project, the normal command
        # path is enough; consume the transient allocation override immediately.
        for project_id in occupied_projects:
            if _manual_force_start_priority_active_locked(c,project_id,ts_dt):
                c.execute(
                    'DELETE FROM runtime_settings WHERE key=?',
                    (_manual_force_start_priority_key(project_id),),
                )

        force_victim_slot=None
        if force_pair:
            local_ordinals={}
            victims=[]
            project_protection={'system':0,'high':1,'normal':2,'low':3,'background':4}
            for occupied in occupied_rows:
                row_project=str(occupied['project_id'] or '')
                local_ordinals[row_project]=local_ordinals.get(row_project,0)+1
                local_slot=local_ordinals[row_project]
                worker_key=f'{row_project}::w{local_slot}'
                project_priority=str((PROJECT_INDEX.get(row_project) or {}).get('priority') or 'normal').lower()
                if row_project==force_project or project_priority=='system':
                    continue
                if worker_key in busy_worker_keys:
                    continue
                handoff_floor=_pending_worker_handoff_floor_locked(c,row_project)
                if 0 < local_slot <= handoff_floor:
                    continue
                victims.append((
                    0 if _manual_start_priority_active_locked(c,row_project,ts_dt) else 1,
                    -project_protection.get(project_priority,2),
                    -_portfolio_priority_rank(occupied['priority']),
                    str(occupied['updated_at'] or ''),
                    int(occupied['worker_slot'] or 0),
                ))
            if victims:
                force_victim_slot=min(victims)[-1]

        for slot in range(1,GLOBAL_CHATGPT_WORKER_LIMIT+1):
            row=c.execute("""SELECT * FROM portfolio_queue
                             WHERE worker_slot=? AND status IN ('claimed','running','verifying')
                               AND eligible=1 AND (claim_expires IS NULL OR claim_expires>?)
                             ORDER BY updated_at DESC LIMIT 1""",(slot,ts)).fetchone()
            # Memory admission counts net-new browser slots, not queue-owner
            # turnover inside a slot that is already resident. Remember that the
            # slot was occupied before any priority/manual-start preemption below.
            slot_was_occupied = row is not None
            lane_candidates=_portfolio_eligible_lane_candidates_locked(c,ts,preempted)
            used_counts={}
            for item in selected:
                pid=str(item.get('project_id') or '')
                used_counts[pid]=used_counts.get(pid,0)+1

            # Explicit Force Start may recycle exactly one safe idle occupied slot.
            # This is intentionally separate from normal scheduler preemption:
            # running/verifying work is only displaced when it is not generating
            # or sending, has no in-flight browser handoff, and is not a system
            # control-plane project.
            force_replacement=False
            if row and force_victim_slot==slot and force_pair:
                previous_queue_id=str(row['queue_id'] or '')
                c.execute(
                    """UPDATE portfolio_queue
                       SET status='queued',worker_slot=NULL,claimed_at=NULL,claim_expires=NULL,updated_at=?
                       WHERE queue_id=? AND status IN ('claimed','running','verifying')""",
                    (ts,previous_queue_id),
                )
                preempted.add(previous_queue_id)
                row=None
                force_replacement=True
                lane_candidates=[
                    pair for pair in _portfolio_eligible_lane_candidates_locked(c,ts,preempted)
                    if str(pair[0]['queue_id'] or '')==force_queue_id
                ]
            if row and str(row['status']) == 'claimed':
                row_project=str(row['project_id'] or '')
                # Once a browser command is pending, the queue claim is part of an
                # in-flight handoff. Rebalancing it before Firefox consumes that
                # command makes the userscript correctly reject the send because its
                # current VPS assignment vanished. Pin the claim until the command
                # leaves pending, then resume normal priority/diversity preemption.
                local_slot=used_counts.get(row_project,0)+1
                handoff_floor=_pending_worker_handoff_floor_locked(c,row_project)
                if 0 < local_slot <= handoff_floor:
                    # A pending wN browser command depends on the stable project-local
                    # ordinal of every preceding claim. Keep the prefix intact until
                    # Firefox consumes the handoff; otherwise preempting w1 can silently
                    # rename the intended w2 claim to w1 and orphan the accepted command.
                    c.execute(
                        'UPDATE portfolio_queue SET claim_expires=?,updated_at=? WHERE queue_id=?',
                        (lease_until,ts,row['queue_id']),
                    )
                    row=c.execute('SELECT * FROM portfolio_queue WHERE queue_id=?',(row['queue_id'],)).fetchone()
                    selected.append(_portfolio_queue_row(row))
                    continue
                row_mode=str((_autonomy_config(row_project) or {}).get('mode') or 'manual').lower()
                if row_mode in {'external_gate','manual'}:
                    c.execute("""UPDATE portfolio_queue
                                 SET status='queued',worker_slot=NULL,claimed_at=NULL,claim_expires=NULL,updated_at=?
                                 WHERE queue_id=? AND status='claimed'""",(ts,row['queue_id']))
                    preempted.add(str(row['queue_id']))
                    row=None
                    lane_candidates=_portfolio_eligible_lane_candidates_locked(c,ts,preempted)
                else:
                    manual_candidates=[
                        pair for pair in lane_candidates
                        if used_counts.get(str(pair[0]['project_id'] or ''),0)==0
                        and _manual_start_priority_active_locked(c,pair[0]['project_id'],ts_dt)
                    ]
                    higher=min(
                        manual_candidates or lane_candidates,
                        key=lambda pair: (
                            _portfolio_priority_rank(pair[0]['priority']),
                            0 if _manual_start_priority_active_locked(c,pair[0]['project_id'],ts_dt) else 1,
                            str(pair[0]['created_at'] or ''),
                            str(pair[0]['queue_id'] or ''),
                        ),
                    )[0] if (manual_candidates or lane_candidates) else None
                    hard_cap=_portfolio_project_hard_cap(row_project)
                    row_manual=(
                        used_counts.get(row_project,0)==0
                        and _manual_start_priority_active_locked(c,row_project,ts_dt)
                    )
                    alternate_project_available=any(
                        str(pair[0]['project_id'] or '') != row_project
                        and (
                            used_counts.get(str(pair[0]['project_id'] or ''),0)
                                < _portfolio_project_soft_cap(pair[0]['project_id'])
                            or (
                                used_counts.get(str(pair[0]['project_id'] or ''),0)==0
                                and _manual_start_priority_active_locked(c,pair[0]['project_id'],ts_dt)
                            )
                        )
                        for pair in lane_candidates
                    )
                    should_enforce_hard_cap=used_counts.get(row_project,0) >= hard_cap
                    row_soft_cap=max(_portfolio_project_soft_cap(row_project),1 if row_manual else 0)
                    should_diversify=used_counts.get(row_project,0) >= row_soft_cap and alternate_project_available
                    should_preempt_priority=(
                        higher and
                        _portfolio_priority_rank(higher['priority']) < _portfolio_priority_rank(row['priority'])
                    )
                    should_preempt_manual=(
                        higher and
                        _portfolio_priority_rank(higher['priority']) <= _portfolio_priority_rank(row['priority'])
                        and _manual_start_priority_active_locked(c,higher['project_id'],ts_dt)
                        and not row_manual
                    )
                    if should_enforce_hard_cap or should_diversify or should_preempt_priority or should_preempt_manual:
                        c.execute("""UPDATE portfolio_queue
                                     SET status='queued',worker_slot=NULL,claimed_at=NULL,claim_expires=NULL,updated_at=?
                                     WHERE queue_id=? AND status='claimed'""",(ts,row['queue_id']))
                        preempted.add(str(row['queue_id']))
                        row=None
                        lane_candidates=_portfolio_eligible_lane_candidates_locked(c,ts,preempted)
            if row:
                c.execute('UPDATE portfolio_queue SET claim_expires=?,updated_at=? WHERE queue_id=?',
                          (lease_until,ts,row['queue_id']))
                row=c.execute('SELECT * FROM portfolio_queue WHERE queue_id=?',(row['queue_id'],)).fetchone()
                selected.append(_portfolio_queue_row(row))
                continue
            # Replacing a preempted claim in an already-occupied global
            # browser slot is not a new memory admission. The browser runner
            # removes the old inactive assignment before opening the replacement.
            # Truly empty slots still obey the fail-closed new-worker budget.
            if not slot_was_occupied and new_workers_claimed >= new_worker_capacity:
                continue
            if not lane_candidates:
                continue

            if force_replacement:
                lane_candidates=[
                    pair for pair in lane_candidates
                    if str(pair[0]['queue_id'] or '')==force_queue_id
                ]
                if not lane_candidates:
                    continue

            # Hard caps are project-specific and apply even when no alternative
            # project is runnable. This prevents FTMO PR/CI churn from consuming
            # more browser/code-worker slots than the runner can productively serve.
            lane_candidates=[
                pair for pair in lane_candidates
                if used_counts.get(str(pair[0]['project_id'] or ''),0) < _portfolio_project_hard_cap(pair[0]['project_id'])
            ]
            if not lane_candidates:
                continue

            diverse_candidates=[
                pair for pair in lane_candidates
                if (
                    used_counts.get(str(pair[0]['project_id'] or ''),0)
                        < _portfolio_project_soft_cap(pair[0]['project_id'])
                    or (
                        used_counts.get(str(pair[0]['project_id'] or ''),0)==0
                        and _manual_start_priority_active_locked(c,pair[0]['project_id'],ts_dt)
                    )
                )
            ]
            if diverse_candidates:
                lane_candidates=diverse_candidates
            used_projects={str(item.get('project_id') or '') for item in selected}
            row,lane=min(
                lane_candidates,
                key=lambda pair: (
                    _portfolio_priority_rank(pair[0]['priority']),
                    0 if (
                        used_counts.get(str(pair[0]['project_id'] or ''),0)==0
                        and _manual_start_priority_active_locked(c,pair[0]['project_id'],ts_dt)
                    ) else 1,
                    1 if str(pair[0]['project_id']) in used_projects else 0,
                    str(pair[0]['created_at'] or ''),
                    str(pair[0]['queue_id'] or ''),
                )
            )
            lane_metadata=_portfolio_lane_metadata(row,lane)
            c.execute("""UPDATE portfolio_queue
                         SET status='claimed',worker_slot=?,claimed_at=?,claim_expires=?,updated_at=?,
                             attempts=attempts+1,metadata_json=?
                         WHERE queue_id=? AND status='queued' AND eligible=1""",
                      (slot,ts,lease_until,ts,lane_metadata,row['queue_id']))
            row=c.execute('SELECT * FROM portfolio_queue WHERE queue_id=?',(row['queue_id'],)).fetchone()
            selected.append(_portfolio_queue_row(row))
            if force_replacement and str(row['project_id'] or '')==force_project:
                c.execute(
                    'DELETE FROM runtime_settings WHERE key=?',
                    (_manual_force_start_priority_key(force_project),),
                )
                force_pair=None
                force_project=''
                force_queue_id=''
                force_victim_slot=None
            if not slot_was_occupied:
                new_workers_claimed += 1
    return selected

def portfolio_queue_drop(queue_id,evidence=''):
    queue_id=str(queue_id or '').strip()
    if not queue_id:
        raise ValueError('queue_id is verplicht')
    ts=now()
    with connect() as c:
        cur=c.execute("""UPDATE portfolio_queue
                         SET status='dropped',eligible=0,evidence=?,blocker=?,
                             worker_slot=NULL,claimed_at=NULL,claim_expires=NULL,updated_at=?
                         WHERE queue_id=? AND status NOT IN ('done','dropped')""",
                      (str(evidence or '')[:4000],str(evidence or '')[:2000],ts,queue_id))
        dropped=bool(cur.rowcount)
    # A dropped item is intentionally retired, but an active project must not
    # lose its runnable inventory until the next scheduler tick. Refill from
    # the canonical project state immediately without resurrecting this queue_id.
    audit=portfolio_queue_audit(refill=True)
    return {'dropped':dropped,'queue_id':queue_id,'queue_audit':audit}

def portfolio_queue_finish(global_slot,queue_id,result,evidence='',next_task=None):
    result=str(result or '').strip().upper()
    if result not in ('DONE','BLOCKED','CONTINUE'):
        raise ValueError('queue result moet DONE, BLOCKED of CONTINUE zijn')
    queue_id=str(queue_id or '').strip()
    try: slot=int(global_slot)
    except Exception: raise ValueError('geldige global worker slot vereist')
    ts=now()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("""SELECT * FROM portfolio_queue
                         WHERE queue_id=? AND worker_slot=? AND status IN ('claimed','running','verifying')""",
                      (queue_id,slot)).fetchone()
        if not row:
            return {'updated':False,'reason':'assignment-mismatch'}
        if result=='DONE':
            c.execute("""UPDATE portfolio_queue SET status='done',eligible=0,evidence=?,blocker='',
                         worker_slot=NULL,claimed_at=NULL,claim_expires=NULL,updated_at=? WHERE queue_id=?""",
                      (str(evidence or '')[:4000],ts,queue_id))
        elif result=='BLOCKED':
            # BLOCKED is a transition, never runnable queue inventory.
            c.execute("""UPDATE portfolio_queue SET status='dropped',eligible=0,evidence=?,blocker=?,
                         worker_slot=NULL,claimed_at=NULL,claim_expires=NULL,updated_at=? WHERE queue_id=?""",
                      (str(evidence or '')[:4000],str(evidence or '')[:2000],ts,queue_id))
        else:
            c.execute("""UPDATE portfolio_queue SET status='queued',eligible=1,evidence=?,
                         worker_slot=NULL,claimed_at=NULL,claim_expires=NULL,updated_at=? WHERE queue_id=?""",
                      (str(evidence or '')[:4000],ts,queue_id))
    attention=None
    if result=='BLOCKED' and _blocker_needs_human(evidence):
        attention=portfolio_attention_add(
            row['project_id'],
            _attention_action_for_blocker(row['project_id'],row['title'],evidence),
            str(evidence or '')[:4000],
            row['source_url'] or (PROJECT_INDEX.get(row['project_id']) or {}).get('notion_url') or '',
            'urgent' if str(row['priority']).upper()=='P0' else 'attention',
            attention_id='queue:'+queue_id,
        )
    created=None
    if isinstance(next_task,dict) and str(next_task.get('title') or '').strip():
        try:
            child_criteria=str(next_task.get('completion_criteria') or '').strip()
            child_criteria += (
                (' ' if child_criteria else '') +
                'Queue sizing rule: execute this as a substantial multi-step work package, not an isolated micro-task. '
                'After each successful implementation step, continue into the next safe adjacent write-capable step in the same '
                'project area. DONE requires at least three material implementation actions or one clearly large end-to-end change '
                'spanning multiple files/components, plus automated test/build/run evidence, unless a real human/external gate stops progress.'
            )
            created=portfolio_queue_enqueue(
                next_task.get('project_id') or row['project_id'],
                next_task.get('title'),
                next_task.get('priority') or 'P2',
                child_criteria,
                next_task.get('source_url') or '',
                parent_queue_id=queue_id
            )
        except ValueError:
            created=None
    elif result in ('DONE','BLOCKED'):
        try:
            created=portfolio_write_continuation(row['project_id'],queue_id)
        except ValueError:
            created=None
    # Every material queue transition leaves an evidence-backed state receipt.
    # Rich CI/deploy paths may later supersede this with a more specific receipt.
    try:
        next_gate=(
            (created or {}).get('title')
            if isinstance(created,dict)
            else ''
        ) or (str(row['title']) if result=='CONTINUE' else '')
        project=PROJECT_INDEX.get(str(row['project_id'])) or {}
        receipt_status={'DONE':'success','BLOCKED':'failure','CONTINUE':'in_progress'}[result]
        with connect() as c:
            project_runtime.record_receipt(
                c,
                str(row['project_id']),
                phase=str(project.get('phase') or ''),
                action=f"{row['title']} -> {result}",
                ci_status=receipt_status,
                blocker=str(evidence or '') if result=='BLOCKED' else '',
                next_gate=next_gate or str(project.get('next_step') or ''),
                source='portfolio_queue:'+queue_id,
                evidence={
                    'queue_id':queue_id,
                    'result':result,
                    'priority':str(row['priority'] or ''),
                    'evidence':str(evidence or '')[:3500],
                },
            )
    except Exception:
        logging.exception('Could not persist project state receipt for %s',queue_id)
    audit=portfolio_queue_audit(refill=False)
    return {'updated':True,'queue_id':queue_id,'result':result,'next_task':created,'attention':attention,'queue_audit':audit}

def portfolio_queue_allocation():
    active=[]
    for slot in range(1,GLOBAL_CHATGPT_WORKER_LIMIT+1):
        item=portfolio_queue_current_for_slot(slot)
        if item:
            active.append(item)
    used={}
    workers=[]
    for item in sorted(active,key=lambda x:int(x.get('worker_slot') or 99)):
        project_id=item['project_id']
        local_slot=used.get(project_id,0)+1
        used[project_id]=local_slot
        workers.append({
            'worker_key':f'{project_id}::w{local_slot}',
            'project_id':project_id,
            'worker_slot':local_slot,
            'global_worker_slot':int(item['worker_slot']),
            'provider':dynamic_provider_for_global_slot(item['worker_slot']),
            'queue_id':item['queue_id'],
            'priority':item['priority'],
            'reason':'vps_queue:'+item['queue_id'],
            'desired_state':'running',
            'score':10000-_portfolio_priority_rank(item['priority'])*1000,
        })
    queued=portfolio_queue_items(False)
    candidates=[{
        'worker_key':None,'project_id':item['project_id'],'worker_slot':None,
        'score':10000-_portfolio_priority_rank(item['priority'])*1000,
        'reason':'vps_queue:'+item['queue_id'],'desired_state':'queued',
        'queue_id':item['queue_id'],'priority':item['priority']
    } for item in queued if item['status']=='queued' and item['eligible']]
    return {
        'limit':GLOBAL_CHATGPT_WORKER_LIMIT,
        'workers':workers,
        'keys':[item['worker_key'] for item in workers],
        'projects':sorted({item['project_id'] for item in workers}),
        'candidates':workers+candidates,
        'queue_backend':'sqlite',
        'queue_db':str(DB),
        'notion_mirror_url':NOTION_PORTFOLIO_QUEUE_URL,
        'dispatch_cooldown_seconds':min(DYNAMIC_CHATGPT_COOLDOWN_SECONDS,DYNAMIC_CLAUDE_COOLDOWN_SECONDS),
        'provider_cooldowns':{
            'chatgpt':int(DYNAMIC_CHATGPT_COOLDOWN_SECONDS),
            'claude':int(DYNAMIC_CLAUDE_COOLDOWN_SECONDS),
        },
        'provider_counts':_dynamic_provider_counts(),
        'dispatch_rule':'vps_queue_claim_then_execute',
        'memory_guard':{
            **worker_memory_status(),
            'recovery_hold_until':(_worker_recovery_hold_until().isoformat() if _worker_recovery_hold_until() else None),
        },
    }

def reconcile_dynamic_worker_limit():
    """Immediately converge SQLite queue claims and browser slot mapping to the dashboard limit."""
    limit=max(0,min(MAX_CHATGPT_WORKERS,int(GLOBAL_CHATGPT_WORKER_LIMIT)))
    ts=now()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if limit <= 0:
            c.execute("""UPDATE portfolio_queue
                         SET status='queued',worker_slot=NULL,claimed_at=NULL,claim_expires=NULL,updated_at=?
                         WHERE eligible=1 AND status IN ('claimed','running','verifying')
                           AND worker_slot IS NOT NULL""",(ts,))
            c.execute('DELETE FROM ai_global_slots')
        else:
            c.execute("""UPDATE portfolio_queue
                         SET status='queued',worker_slot=NULL,claimed_at=NULL,claim_expires=NULL,updated_at=?
                         WHERE eligible=1 AND status IN ('claimed','running','verifying')
                           AND worker_slot>?""",(ts,limit))
            c.execute('DELETE FROM ai_global_slots WHERE slot>?',(limit,))
    portfolio_queue_allocate()
    allocation=portfolio_queue_allocation()
    _persist_global_worker_allocation(allocation)
    return allocation

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
    return portfolio_queue_allocation()

def _persist_global_worker_allocation(allocation):
    """Persist queue-owned portfolio slot identities exactly as assigned by SQLite."""
    selected=list(allocation.get('workers') or [])[:GLOBAL_CHATGPT_WORKER_LIMIT]
    ts=now()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM ai_global_slots')
        for fallback_slot,item in enumerate(selected,1):
            global_slot=max(1,min(GLOBAL_CHATGPT_WORKER_LIMIT,int(item.get('global_worker_slot') or fallback_slot)))
            c.execute(
                'INSERT INTO ai_global_slots(slot,project_id,worker_slot,assigned_at) VALUES(?,?,?,?)',
                (global_slot,str(item['project_id']),int(item['worker_slot']),ts),
            )

def _current_global_slot_map():
    if GLOBAL_CHATGPT_WORKER_LIMIT <= 0:
        return {}
    try:
        with connect() as c:
            rows=c.execute('SELECT slot,project_id,worker_slot FROM ai_global_slots ORDER BY slot LIMIT ?',
                           (GLOBAL_CHATGPT_WORKER_LIMIT,)).fetchall()
            if rows:
                return {
                    f"{row['project_id']}::w{max(1,int(row['worker_slot'] or 1))}":int(row['slot'])
                    for row in rows
                }
            # Safe bootstrap before the scheduler's first tick: preserve at most two
            # already-active workers and assign deterministic portfolio slot numbers.
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
                        return {key:index for index,key in enumerate(selected,1)}
            return {key:index for index,key in enumerate(selected,1)}
    except Exception:
        logging.exception('Could not read global AI slot state')
        return {}

def _current_global_slot_keys():
    return set(_current_global_slot_map())

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

def _reconcile_stale_runner_commands_locked(
    connection,
    *,
    at=None,
    result='scheduler superseded stale pending command',
):
    pending_rows=connection.execute(
        "SELECT id,created_at FROM runner_commands "
        "WHERE status='pending' ORDER BY id"
    ).fetchall()
    stale_ids=[]
    observed_at=at or datetime.now(timezone.utc)
    for pending in pending_rows:
        try:
            created=datetime.fromisoformat(
                str(pending['created_at']).replace('Z','+00:00')
            ).astimezone(timezone.utc)
            age=(observed_at-created).total_seconds()
        except Exception:
            age=RUNNER_COMMAND_STALE_SECONDS
        if age >= RUNNER_COMMAND_STALE_SECONDS:
            stale_ids.append(int(pending['id']))
    if stale_ids:
        placeholders=','.join('?' for _ in stale_ids)
        connection.execute(
            f"UPDATE runner_commands SET status='failed',updated_at=?,result=? "
            f"WHERE id IN ({placeholders}) AND status='pending'",
            (now(),str(result)[:500],*stale_ids),
        )
    return stale_ids

def reconcile_stale_runner_commands():
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        return _reconcile_stale_runner_commands_locked(c)

def autonomy_scheduler_tick():
    stale_commands=reconcile_stale_runner_commands()
    if stale_commands:
        logging.warning(
            'Autonomy scheduler superseded %s stale runner command(s): %s',
            len(stale_commands),
            ','.join(str(command_id) for command_id in stale_commands),
        )
    portfolio_queue_audit()
    portfolio_queue_allocate()
    states=autonomy_states()
    targets=runner_targets()
    for project_id,state in states.items():
        if state.get('auto_start'):
            _autonomy_initialize_project(project_id)

    allocation=global_worker_allocation(states,targets)
    _persist_global_worker_allocation(allocation)
    selected_projects=set(allocation['projects'])

    started=[]
    paused=[]
    pushed=[]
    for project_id,state in states.items():
        target=targets.get(project_id) or {}
        if project_id in selected_projects:
            if not target.get('active'):
                if _autonomy_enqueue_start(project_id,'global-slot:'+str(state.get('reason') or 'eligible')):
                    started.append(project_id)
            elif state.get('dispatch_mode')=='vps':
                for worker in allocation['workers']:
                    if worker.get('project_id')!=project_id or worker.get('desired_state','running')!='running':
                        continue
                    if _autonomy_enqueue_worker_push(
                        project_id,
                        int(worker.get('worker_slot') or 1),
                        'global-slot:'+str(worker.get('global_worker_slot') or '')+':'+str(state.get('reason') or 'eligible'),
                        dynamic_provider_cooldown(worker.get('provider')),
                    ):
                        pushed.append(worker.get('worker_key') or project_id)
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
        queue_active=portfolio_queue_has_project_assignment(r['project_id'])
        gate_blocks_auto_continue=str(autonomy.get('mode') or '').lower() in {'external_gate','manual'}
        out[r['project_id']]={'project_id':r['project_id'],'name':r['name'],'conversation_id':r['conversation_id'],
                              'url':('https://chatgpt.com/c/'+r['conversation_id']) if r['conversation_id'] else 'https://chatgpt.com/',
                              'prompt':r['prompt'],'active':bool(r['active']),'worker_count':max(1,int(r['worker_count'] or 1)),
                              'auto_continue':(queue_active or bool(autonomy['allow_ai'])) and not gate_blocks_auto_continue,
                              'auto_continue_delay_seconds':autonomy['continue_delay_seconds'],
                              'vps_dispatch_only':autonomy.get('dispatch_mode')=='vps',
                              'ai_dispatch_interval_seconds':autonomy.get('min_ai_interval_seconds',PORTFOLIO_AI_COOLDOWN_SECONDS),
                              'autonomy':autonomy,'improvement':improvement}
    return out

def runner_worker_targets(allocation=None, base=None, *, reconcile=True):
    # Callers that need a coherent live read-model may pass one target snapshot.
    # This avoids rebuilding the full project/autonomy/queue state for every
    # project while still preserving the existing default for control paths.
    base=base if base is not None else runner_targets()
    # /api/runner-targets passes one allocation snapshot so a scheduler
    # preemption cannot cross-wire a project's worker to whichever queue item
    # happens to own the same numeric slot a few milliseconds later. Internal
    # control paths still use the persisted/bootstrap slot map because manual
    # start/pause/drain flows may exist before a queue claim is allocated.
    allocation_workers={
        str(item.get('worker_key') or ''):item
        for item in ((allocation or {}).get('workers') or [])
        if str(item.get('worker_key') or '')
    }
    # An explicitly empty queue allocation is the pre-claim/manual bootstrap
    # state, not proof that manually started workers disappeared.
    snapshot_bound=allocation is not None and bool(allocation_workers)
    global_slots=(
        {
            key:int(item.get('global_worker_slot'))
            for key,item in allocation_workers.items()
            if item.get('global_worker_slot') is not None
        }
        if snapshot_bound else _current_global_slot_map()
    )
    out={}
    with connect() as c:
        for project_id,cfg in base.items():
            allocated_count=sum(1 for key in global_slots if key.startswith(project_id+'::'))
            count=max(1,min(GLOBAL_CHATGPT_WORKER_LIMIT,max(int(cfg.get('worker_count') or 1),allocated_count)))
            for slot in range(1,count+1):
                if reconcile:
                    c.execute('INSERT OR IGNORE INTO runner_workers(project_id,worker_slot,conversation_id) VALUES(?,?,?)',
                              (project_id,slot,cfg['conversation_id'] if slot==1 else ''))
                row=c.execute('SELECT conversation_id,desired_state,provider FROM runner_workers WHERE project_id=? AND worker_slot=?',(project_id,slot)).fetchone()
                desired_state=(row['desired_state'] if row else 'running') or 'running'
                worker_key=f'{project_id}::w{slot}'
                allocation_item=allocation_workers.get(worker_key)
                global_slot=global_slots.get(worker_key)
                allocated=global_slot is not None
                reviewer_mode=project_id=='portfolio-review'
                persisted_provider=str((row['provider'] if row else 'chatgpt') or 'chatgpt')
                provider_name='chatgpt' if reviewer_mode else (dynamic_provider_for_global_slot(global_slot) if allocated else persisted_provider)
                provider_name='claude' if provider_name=='claude' else 'chatgpt'
                conversation_id=(row['conversation_id'] if row else (cfg['conversation_id'] if slot==1 else '')) or ''
                if row and persisted_provider != provider_name:
                    conversation_id=''
                    if reconcile:
                        c.execute('UPDATE runner_workers SET provider=?,conversation_id=? WHERE project_id=? AND worker_slot=?',
                                  (provider_name,'',project_id,slot))
                prompt_slot=int(global_slot or slot)
                prompt_total=GLOBAL_CHATGPT_WORKER_LIMIT if allocated else count
                queue_item=None
                expected_queue_id=''
                if allocated and not reviewer_mode:
                    if snapshot_bound:
                        expected_queue_id=str((allocation_item or {}).get('queue_id') or '').strip()
                        if expected_queue_id:
                            queue_row=c.execute(
                                'SELECT * FROM portfolio_queue WHERE queue_id=?',
                                (expected_queue_id,),
                            ).fetchone()
                            queue_item=_portfolio_queue_row(queue_row)
                    else:
                        queue_item=portfolio_queue_current_for_slot(global_slot)
                        expected_queue_id=str((queue_item or {}).get('queue_id') or '').strip()
                assignment_ready=bool(
                    (reviewer_mode and cfg.get('active')) or
                    (
                        allocated and queue_item and expected_queue_id
                        and str(queue_item.get('queue_id') or '').strip()==expected_queue_id
                        and str(queue_item.get('project_id') or '').strip()==project_id
                        and str(queue_item.get('status') or '').strip() in ('claimed','running','verifying')
                        and int(queue_item.get('worker_slot') or 0)==int(global_slot)
                    )
                )
                active=(bool(cfg.get('active')) if reviewer_mode else allocated) and desired_state!='paused'
                worker_name=(cfg['name'] if reviewer_mode else
                             (f"Portfolio Worker {global_slot}/{GLOBAL_CHATGPT_WORKER_LIMIT} · {cfg['name']}"
                              if allocated else f"{cfg['name']} · worker {slot}/{count}"))
                rendered_prompt=cfg['prompt'] if reviewer_mode else project_worker_prompt(
                    project_id,cfg['name'],cfg['prompt'],prompt_slot,prompt_total,queue_item
                )
                out[worker_key]={
                    'project_id':worker_key,'base_project_id':project_id,'worker_slot':slot,'worker_count':count,
                    'global_worker_slot':global_slot,'global_worker_count':GLOBAL_CHATGPT_WORKER_LIMIT,
                    'provider':provider_name,
                    'provider_worker_slot':(int(global_slot) if provider_name=='chatgpt' else int(global_slot)-dynamic_provider_count('chatgpt')) if allocated else slot,
                    'provider_worker_count':dynamic_provider_count(provider_name) if allocated else count,
                    'name':worker_name,'conversation_id':conversation_id,
                    'url':dynamic_provider_url(provider_name,conversation_id),
                    'prompt':rendered_prompt,
                    'queue_item':queue_item,
                    'reviewer_mode':reviewer_mode,
                    'assignment_ready':assignment_ready,
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
    try: global_worker_slot=max(1,min(GLOBAL_CHATGPT_WORKER_LIMIT,int(payload.get('globalWorkerSlot') or worker_slot)))
    except Exception: global_worker_slot=worker_slot
    if event in ('autonomy-wait-vps','autonomy-wait-human','autonomy-complete'):
        event='autonomy-continue'
        reason='backend-non-stopping-dynamic-worker-policy'
    if event=='runner-auto-paused':
        event='quality-recovery-requested'
        reason='backend-rejected-automatic-worker-pause'
    if event=='runner-paused' and reason!='dashboard-pause':
        event='quality-recovery-requested'
        reason='backend-rejected-non-dashboard-worker-pause'
    event_provider=str(payload.get('provider') or '').strip().lower()
    if event_provider not in ('chatgpt','claude'):
        event_provider='claude' if re.search(r'https?://(?:www\\.)?(?:claude\\.ai|claude\\.com)/',target,re.I) else 'chatgpt'
    match=(re.search(r'/chat/([0-9a-f-]{20,})',target,re.I) if event_provider=='claude'
           else re.search(r'/c/([0-9a-f-]{20,})',target,re.I))
    with connect() as c:
        if not project_id and target:
            for pid,t in runner_targets().items():
                if t['conversation_id'] and t['conversation_id'] in target: project_id=pid; break
        c.execute('INSERT INTO runner_events(ts,event,target,title,generating,sending,reason,tab_id,error,project_id,progress_at,assistant_chars,worker_slot) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (ts,event,target,title,int(bool(payload.get('generating'))),int(bool(payload.get('sending'))),reason,tab_id,error,project_id or None,progress_at,assistant_chars,worker_slot))
        if event == 'conversation-adopted' and project_id in runner_targets() and match:
            c.execute('INSERT INTO runner_workers(project_id,worker_slot,conversation_id,provider) VALUES(?,?,?,?) ON CONFLICT(project_id,worker_slot) DO UPDATE SET conversation_id=excluded.conversation_id,provider=excluded.provider',
                      (project_id,worker_slot,match.group(1),event_provider))
            if worker_slot==1:
                c.execute('UPDATE runner_targets SET conversation_id=? WHERE project_id=?',(match.group(1),project_id))
        if event in ('runner-drained','runner-paused') and project_id in runner_targets():
            c.execute("UPDATE runner_workers SET desired_state='paused' WHERE project_id=? AND worker_slot=?",(project_id,worker_slot))
        cutoff=datetime.fromtimestamp(time.time()-14*86400,timezone.utc).isoformat()
        c.execute('DELETE FROM runner_events WHERE ts < ?', (cutoff,))
    if event=='portfolio-queue-result':
        try:
            portfolio_queue_finish(
                global_worker_slot,
                payload.get('queueItem'),
                payload.get('queueResult'),
                payload.get('queueEvidence') or '',
                payload.get('nextTask') if isinstance(payload.get('nextTask'),dict) else None,
            )
        except Exception:
            logging.exception('Portfolio queue result could not be applied')
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

def runner_status(project_id=None, target_cfg=None):
    target_cfg=target_cfg if target_cfg is not None else runner_targets()
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

def runner_worker_statuses(project_id, *, base=None, targets=None):
    if base is None:
        base=runner_targets().get(project_id)
    if not base:
        return []
    targets=targets if targets is not None else runner_worker_targets()
    # Aggregate status reads must not prune leases with DELETEs. Expired rows
    # are filtered in SQL and the scheduler/control paths retain cleanup ownership.
    claims=task_claims(project_id,prune_expired=False)
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
            global_slot_row=c.execute(
                'SELECT slot FROM ai_global_slots WHERE project_id=? AND worker_slot=?',
                (project_id,slot),
            ).fetchone()
            queue_assignment=None
            if global_slot_row:
                queue_row=c.execute(
                    """SELECT * FROM portfolio_queue
                       WHERE worker_slot=? AND project_id=?
                         AND status IN ('claimed','running','verifying')
                       ORDER BY updated_at DESC LIMIT 1""",
                    (global_slot_row['slot'],project_id),
                ).fetchone()
                queue_assignment=_portfolio_queue_row(queue_row)
            queue_lane=(queue_assignment or {}).get('execution_lane') or {}
            queue_task=(
                {
                    'claim_key':queue_assignment.get('queue_id'),
                    'title':queue_assignment.get('title'),
                    'lease_until':queue_assignment.get('claim_expires'),
                    'branch':None,
                    'pr':None,
                }
                if queue_assignment else None
            )
            out.append({
                'worker_id':worker_key,'worker_slot':slot,'worker_count':cfg.get('worker_count') or 1,
                'work_area':queue_lane.get('lane_id') or WORKER_LANES[(slot-1)%len(WORKER_LANES)],
                'execution_lane':queue_lane or None,
                'desired_state':desired,'active':active,'state':state,'generating':generating if active else False,
                'sending':bool(latest['sending']) if latest and active else False,'stalled':stalled if active else False,
                'age_seconds':age,'progress_age_seconds':progress_age,
                'last_event':({'time':latest['ts'],'event':latest['event'],'reason':latest['reason'] or None,'error':latest['error'] or None} if latest else None),
                'last_heartbeat':({'time':heartbeat['ts'],'event':heartbeat['event']} if heartbeat else None),
                'current_task':({'claim_key':claim.get('claim_key'),'title':metadata.get('task') or claim.get('claim_key'),
                                 'lease_until':claim.get('lease_until'),'branch':metadata.get('branch'),'pr':metadata.get('pr') or metadata.get('pr_url')} if claim else queue_task),
                'conversation_id':cfg.get('conversation_id') or None,
                'command':dict(command) if command else None,
                'error':(latest['error'] if latest else None) or None,
            })
    return out

def runner_statuses(target_cfg=None):
    # /api/runner-live and /api/status must stay read-only. Rebuilding worker
    # targets used to INSERT/UPDATE runner_workers while serving a GET, which can
    # block behind scheduler writes for the full SQLite busy timeout.
    target_cfg=target_cfg if target_cfg is not None else runner_targets()
    worker_targets=runner_worker_targets(base=target_cfg,reconcile=False)
    result={}
    for pid in target_cfg:
        status=runner_status(pid,target_cfg=target_cfg)
        workers=runner_worker_statuses(
            pid,
            base=target_cfg.get(pid),
            targets=worker_targets,
        )
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


# --- Worker contract + diagnostics ---------------------------------------------
# The browser drivers (public/zcloud-worker.user.js and firefox-extension/background.js)
# silently refuse to send unless the server prompt satisfies their validators. These helpers
# make that contract explicit and observable. tests/test_worker_prompt_contract.py runs the
# REAL JS validators against server output, so this mirror cannot drift unnoticed.
WORKER_BLOCK_EVENTS = ('assignment-invalid', 'send-blocked', 'assignment-refresh-failed', 'thinking-effort-unavailable')

def worker_contract_failures(cfg):
    problems = []
    if cfg.get('active') is not True: problems.append('not-active')
    if cfg.get('assignment_ready') is not True: problems.append('server-assignment-not-ready')
    prompt = str(cfg.get('prompt') or '')
    if cfg.get('reviewer_mode') is True:
        base_project = str(cfg.get('base_project_id') or cfg.get('project_id') or '').split('::w', 1)[0]
        if base_project != 'portfolio-review': problems.append('reviewer-wrong-project')
        if "Portfolio Bird's-eye Reviewer" not in prompt: problems.append('reviewer-prompt-missing-role')
        if 'Senior Team OS' not in prompt: problems.append('reviewer-prompt-missing-policy')
        return problems
    queue_id = str((cfg.get('queue_item') or {}).get('queue_id') or '').strip()
    try: slot = int(cfg.get('global_worker_slot') or 0)
    except (TypeError, ValueError): slot = 0
    try: total = int(cfg.get('global_worker_count') or 0)
    except (TypeError, ValueError): total = 0
    if not queue_id: problems.append('missing-queue-id')
    if slot < 1: problems.append('bad-global-slot')
    if total < slot: problems.append('bad-global-count')
    if not prompt.startswith('Werk verder aan '): problems.append('prompt-missing-project-instruction')
    if 'Kijk in Notion in welke fase het project zit' not in prompt: problems.append('prompt-missing-notion-phase')
    return problems

def _age_seconds(ts):
    try: return max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(str(ts))).total_seconds()))
    except Exception: return None

def worker_debug_report():
    workers = runner_worker_targets()
    window = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    marks = ','.join('?' * len(WORKER_BLOCK_EVENTS))
    rows = []
    summary = {}
    with connect() as c:
        for key, cfg in workers.items():
            if cfg.get('global_worker_slot') is None: continue
            base = cfg['base_project_id']; slot = int(cfg['worker_slot'])
            last = c.execute('SELECT ts,event,reason,generating FROM runner_events WHERE project_id=? AND worker_slot=? ORDER BY id DESC LIMIT 1', (base, slot)).fetchone()
            sent = c.execute("SELECT ts FROM runner_events WHERE project_id=? AND worker_slot=? AND event='prompt-sent' ORDER BY id DESC LIMIT 1", (base, slot)).fetchone()
            blocked = c.execute('SELECT ts,event,reason FROM runner_events WHERE project_id=? AND ts>? AND event IN (' + marks + ') ORDER BY id DESC LIMIT 1', (base, window) + WORKER_BLOCK_EVENTS).fetchone()
            problems = worker_contract_failures(cfg)
            last_age = _age_seconds(last['ts']) if last else None
            sent_age = _age_seconds(sent['ts']) if sent else None
            reason = ''
            if not cfg.get('active'):
                verdict = 'paused'
            elif problems:
                verdict = 'blocked:contract'; reason = ','.join(problems)
            elif last and last['generating'] and last_age is not None and last_age <= 120:
                verdict = 'generating'
            elif blocked and (not sent or blocked['ts'] > sent['ts']):
                verdict = 'blocked:client'; reason = (blocked['event'] + ': ' + (blocked['reason'] or '')).strip()[:200]
            elif sent_age is not None and sent_age <= 900:
                verdict = 'prompt-sent'
            elif last_age is None or last_age > 120:
                verdict = 'no-heartbeat'; reason = 'geen signaal van een browser-tab in 2 min'
            elif not cfg.get('queue_item'):
                verdict = 'idle:no-assignment'
            else:
                verdict = 'waiting'
            summary[verdict] = summary.get(verdict, 0) + 1
            item = cfg.get('queue_item') or {}
            rows.append({'worker': key, 'project': base, 'slot': slot, 'global_slot': cfg.get('global_worker_slot'),
                         'provider': cfg.get('provider'), 'queue_id': item.get('queue_id'), 'priority': item.get('priority'),
                         'verdict': verdict, 'reason': reason, 'contract_failures': problems,
                         'last_event': last['event'] if last else None, 'last_event_age_s': last_age,
                         'last_prompt_age_s': sent_age, 'desired_state': cfg.get('desired_state')})
        events = {r['event']: r['n'] for r in c.execute('SELECT event,COUNT(*) n FROM runner_events WHERE ts>? GROUP BY event ORDER BY n DESC LIMIT 14', (window,)).fetchall()}
        failed = c.execute("SELECT COUNT(*) FROM runner_commands WHERE status='failed' AND created_at>?", (window,)).fetchone()[0]
        cmds = [{'status': r['status'], 'result': r['r'], 'count': r['n']} for r in c.execute("SELECT status,COALESCE(substr(result,1,90),'') r,COUNT(*) n FROM runner_commands WHERE created_at>? GROUP BY 1,2 ORDER BY n DESC LIMIT 8", (window,)).fetchall()]
    try: firefox = firefox_runner_status()
    except Exception as exc: firefox = {'active': None, 'error': str(exc)[:120]}
    return {'time': now(), 'workers': rows, 'summary': summary, 'events_15m': events, 'failed_commands_15m': failed,
            'commands_15m': cmds, 'firefox': firefox,
            'contract': {'prompt': 'project-first', 'queue_binding': 'structured-metadata', 'text_boilerplate_required': False}}

def dynamic_force_push():
    # Pool-level force push: enqueue a push for every active worker, bypassing the per-worker cooldown.
    # Workers whose prompt contract is broken are reported instead of pushed (they would be refused anyway).
    workers = runner_worker_targets()
    results = []
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for key, cfg in workers.items():
            if not cfg.get('active'): continue
            problems = [p for p in worker_contract_failures(cfg) if p != 'not-active']
            if problems:
                results.append({'worker': key, 'queued': False, 'reason': 'contract: ' + ','.join(problems)}); continue
            if (cfg.get('desired_state') or 'running') != 'running':
                results.append({'worker': key, 'queued': False, 'reason': 'desired_state=' + str(cfg.get('desired_state'))}); continue
            inflight = c.execute("SELECT id FROM runner_commands WHERE project_id=? AND action='push' AND status='pending' ORDER BY id DESC LIMIT 1", (key,)).fetchone()
            if inflight:
                results.append({'worker': key, 'queued': True, 'command_id': int(inflight['id']), 'deduplicated': True}); continue
            ts = now()
            cur = c.execute('INSERT INTO runner_commands(project_id,action,status,created_at,updated_at) VALUES(?,?,?,?,?)', (key, 'push', 'pending', ts, ts))
            results.append({'worker': key, 'queued': True, 'command_id': int(cur.lastrowid)})
    return {'ok': any(r.get('queued') for r in results), 'results': results, 'time': now()}


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
            if u.path=='/api/project-state-receipts':
                if not action_request_allowed(self):return self.reply({'error':'Alleen vertrouwde beheerclients'},403)
                project_id=str(payload.get('project_id') or '').strip().lower()
                if project_id not in PROJECT_INDEX:return self.reply({'error':'Onbekend project'},400)
                with connect() as c:
                    receipt=project_runtime.record_receipt(
                        c,project_id,
                        phase=payload.get('phase') or '',
                        action=payload.get('action') or '',
                        commit_sha=payload.get('commit_sha') or '',
                        ci_status=payload.get('ci_status') or '',
                        blocker=payload.get('blocker') or '',
                        next_gate=payload.get('next_gate') or '',
                        source=payload.get('source') or request_actor(self),
                        observed_at=payload.get('observed_at') or None,
                        evidence=payload.get('evidence') if isinstance(payload.get('evidence'),dict) else {},
                    )
                return self.reply({'ok':True,'receipt':receipt,'time':now()})
            if u.path=='/api/resource-governor':
                if not action_request_allowed(self):return self.reply({'error':'Alleen vertrouwde beheerclients'},403)
                action=str(payload.get('action') or 'acquire').strip().lower()
                project_id=str(payload.get('project_id') or '').strip().lower()
                owner_id=str(payload.get('owner_id') or '').strip()
                if project_id not in PROJECT_INDEX or not owner_id:return self.reply({'error':'project_id en owner_id zijn verplicht'},400)
                with connect() as c:
                    if action=='acquire':
                        result=project_runtime.acquire_resource(
                            c,project_id,owner_id,
                            lease_seconds=payload.get('lease_seconds') or 1800,
                            metadata=payload.get('metadata') if isinstance(payload.get('metadata'),dict) else {},
                        )
                    elif action=='release':
                        result=project_runtime.release_resource(c,project_id,owner_id)
                    else:
                        return self.reply({'error':'Ongeldige resource-governor actie'},400)
                return self.reply({'ok':bool(result.get('acquired') or result.get('released')),'result':result,'time':now()},200 if (result.get('acquired') or result.get('released')) else 409)
            if u.path=='/api/dynamic-workers/force-push':
                if not action_request_allowed(self):return self.reply({'error':'Acties zijn alleen toegestaan vanaf een vertrouwd beheer-IP'},403)
                return self.reply(dynamic_force_push())
            if u.path=='/api/runner-control':
                if not action_request_allowed(self):return self.reply({'error':'Acties zijn alleen toegestaan vanaf een vertrouwd beheer-IP'},403)
                project_id=str(payload.get('project_id') or '')
                action=str(payload.get('action') or '')
                force_start=action=='start' and payload.get('force') is True
                if action=='restart_firefox':
                    try:
                        status=restart_firefox_runtime()
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
                    if action in ('start','new_chat'):
                        # An explicit worker Start is also an explicit request to keep
                        # its parent project allocatable long enough to dispatch. Refresh
                        # the same bounded project intent used by project-level Start so
                        # scheduler churn cannot evict a just-started worker mid-send.
                        intent_ts=now()
                        actor=request_actor(self)
                        c.execute(
                            "INSERT INTO runtime_settings(key,value,updated_at,actor) VALUES(?,?,?,?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at,actor=excluded.actor",
                            (_manual_start_priority_key(base_project_id),intent_ts,intent_ts,actor),
                        )
                        if force_start:
                            c.execute(
                                "INSERT INTO runtime_settings(key,value,updated_at,actor) VALUES(?,?,?,?) "
                                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at,actor=excluded.actor",
                                (_manual_force_start_priority_key(base_project_id),intent_ts,intent_ts,actor),
                            )
                    elif not is_worker and action=='pause':
                        c.execute(
                            'DELETE FROM runtime_settings WHERE key IN (?,?)',
                            (
                                _manual_start_priority_key(base_project_id),
                                _manual_force_start_priority_key(base_project_id),
                            ),
                        )
                    # Health treats pending browser commands older than the
                    # canonical threshold as invalid. Keep admission fail-safe too,
                    # even though the scheduler now reconciles the same global set
                    # continuously in the background.
                    _reconcile_stale_runner_commands_locked(
                        c,
                        result='runner-control superseded stale pending command before dedupe',
                    )
                    inflight=c.execute(
                        "SELECT id,action FROM runner_commands WHERE project_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
                        (project_id,)
                    ).fetchone()
                    if force_start and inflight and inflight['action']==action:
                        forced_at=now()
                        c.execute(
                            "UPDATE runner_commands SET status='failed',updated_at=?,result=? WHERE id=? AND status='pending'",
                            (forced_at,'Superseded by explicit Force start',int(inflight['id'])),
                        )
                        inflight=None
                    if inflight and inflight['action']==action:
                        command_id=int(inflight['id']);deduplicated=True
                    else:
                        recent=c.execute("SELECT created_at FROM runner_commands WHERE project_id=? AND action=? AND status='completed' ORDER BY id DESC LIMIT 1",(project_id,action)).fetchone()
                        if recent and not force_start:
                            try:
                                seconds=(datetime.now(timezone.utc)-datetime.fromisoformat(recent['created_at'])).total_seconds()
                                cooldown=5 if action in ('push','start','pause','drain') else 30
                                rate_limited=seconds<cooldown
                            except Exception: pass
                        if not rate_limited:
                            if is_worker:
                                if not configs[base_project_id].get('active') and action!='pause':
                                    if action not in ('start','new_chat'):
                                        return self.reply({'error':'Start eerst het project voordat je deze worker bedient'},409)
                                    # Worker activation is authoritative for a queue-owned slot. Resume the
                                    # parent project in the same SQLite transaction instead of requiring a
                                    # separate project-level start command that can race after Firefox restarts.
                                    c.execute('INSERT OR IGNORE INTO autonomy_runtime(project_id,initialized_at,manual_pause,last_reason) VALUES(?,?,0,?)',(base_project_id,now(),'worker_auto_resume'))
                                    c.execute("UPDATE autonomy_runtime SET manual_pause=0,last_reason='worker_auto_resume' WHERE project_id=?",(base_project_id,))
                                    c.execute('UPDATE runner_targets SET active=1 WHERE project_id=?',(base_project_id,))
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
                return self.reply({'ok':True,'command_id':command_id,'status':'pending','active':action!='pause','desired_state':desired_state,'deduplicated':deduplicated,'forced':force_start})
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
                        payload.get('notion'),payload.get('github'),payload.get('vps_profile','default'),
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
            if u.path=='/api/portfolio-queue':
                if not action_request_allowed(self):return self.reply({'error':'Acties zijn alleen toegestaan vanaf een vertrouwd beheer-IP'},403)
                action=str(payload.get('action') or 'enqueue').lower()
                try:
                    if action=='enqueue':
                        item=portfolio_queue_enqueue(
                            payload.get('project_id'),payload.get('title'),payload.get('priority') or 'P2',
                            payload.get('completion_criteria') or '',payload.get('source_url') or '',
                            payload.get('parent_queue_id'),payload.get('queue_id')
                        )
                        return self.reply({'ok':True,'item':item,'backend':'sqlite','time':now()})
                    if action=='result':
                        item=portfolio_queue_finish(
                            payload.get('worker_slot'),payload.get('queue_id'),payload.get('result'),
                            payload.get('evidence') or '',payload.get('next_task')
                        )
                        return self.reply({'ok':bool(item.get('updated')),'result':item,'time':now()},200 if item.get('updated') else 409)
                    if action=='drop':
                        item=portfolio_queue_drop(payload.get('queue_id'),payload.get('evidence') or '')
                        return self.reply({'ok':True,'result':item,'time':now()})
                    return self.reply({'error':'Ongeldige portfolio-queue actie'},400)
                except ValueError as e:
                    return self.reply({'error':str(e)},400)
            if u.path=='/api/portfolio-attention':
                if not action_request_allowed(self):return self.reply({'error':'Acties zijn alleen toegestaan vanaf een vertrouwd beheer-IP'},403)
                action=str(payload.get('action') or '').lower()
                try:
                    if action=='resolve':
                        result=portfolio_attention_resolve(payload.get('attention_id'))
                        return self.reply({'ok':bool(result.get('resolved')),'result':result,'time':now()},200 if result.get('resolved') else 409)
                    if action=='add':
                        item=portfolio_attention_add(
                            payload.get('project_id'),payload.get('action_text') or payload.get('title'),
                            payload.get('detail') or '',payload.get('source_url') or '',
                            payload.get('severity') or 'attention',payload.get('attention_id')
                        )
                        return self.reply({'ok':True,'item':item,'time':now()})
                    return self.reply({'error':'Ongeldige attention actie'},400)
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
            if u.path=='/api/dynamic-workers':
                if not action_request_allowed(self):return self.reply({'error':'Acties zijn alleen toegestaan vanaf een vertrouwd beheer-IP'},403)
                actor=request_actor(self)
                try:
                    settings=set_dynamic_worker_settings(payload,actor)
                except ValueError as e:
                    return self.reply({'error':str(e)},400)
                return self.reply({'ok':True,'dynamic_workers':settings,'time':now()})
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
        if u.path=='/api/project-state-receipts':
            if self.client_address[0] not in ('127.0.0.1','::1') and not action_request_allowed(self):return self.reply({'error':'Niet toegestaan'},403)
            project_id=str(q.get('project',[''])[0] or '').strip().lower()
            with connect() as c:
                receipts=project_runtime.latest_receipts(c)
            if project_id:
                return self.reply({'project_id':project_id,'receipt':receipts.get(project_id),'time':now()})
            return self.reply({'receipts':receipts,'time':now()})
        if u.path=='/api/resource-governor':
            if self.client_address[0] not in ('127.0.0.1','::1') and not action_request_allowed(self):return self.reply({'error':'Niet toegestaan'},403)
            with connect() as c:
                return self.reply(project_runtime.resource_status(c))
        if u.path=='/api/dynamic-workers':
            if self.client_address[0] not in ('127.0.0.1','::1') and not action_request_allowed(self):return self.reply({'error':'Niet toegestaan'},403)
            return self.reply({'dynamic_workers':dynamic_worker_settings(),'time':now()})
        if u.path=='/api/worker-debug':
            if self.client_address[0] not in ('127.0.0.1','::1') and not action_request_allowed(self):return self.reply({'error':'Niet toegestaan'},403)
            return self.reply(worker_debug_report())
        if u.path=='/api/runner-targets':
            if self.client_address[0] not in ('127.0.0.1','::1'):return self.reply({'error':'Alleen lokaal'},403)
            allocation=global_worker_allocation()
            return self.reply({'projects':runner_worker_targets(allocation=allocation),'max_workers':GLOBAL_CHATGPT_WORKER_LIMIT,'global_allocation':allocation,'dynamic_workers':dynamic_worker_settings()})
        if u.path=='/api/runner-live':
            if self.client_address[0] not in ('127.0.0.1','::1'):return self.reply({'error':'Alleen lokaal'},403)
            target_cfg=runner_targets()
            return self.reply({'chatgpt_runners':runner_statuses(target_cfg=target_cfg),'chatgpt_firefox':firefox_runner_status(),'time':now()})
        if u.path=='/api/portfolio-queue':
            if self.client_address[0] not in ('127.0.0.1','::1') and not action_request_allowed(self):return self.reply({'error':'Niet toegestaan'},403)
            include_done=str(q.get('all',['0'])[0]).lower() in ('1','true','yes')
            return self.reply({'backend':'sqlite','items':portfolio_queue_items(include_done),'allocation':global_worker_allocation(),'time':now()})
        if u.path=='/api/portfolio-attention':
            if self.client_address[0] not in ('127.0.0.1','::1') and not action_request_allowed(self):return self.reply({'error':'Niet toegestaan'},403)
            include_resolved=str(q.get('all',['0'])[0]).lower() in ('1','true','yes')
            return self.reply({'items':portfolio_attention_items(include_resolved),'notion_url':PORTFOLIO_ATTENTION_NOTION_URL,'time':now()})
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
            # Browser workers must never be starved by an old pending backlog.
            # Read a bounded tail of the queue, then restore FIFO order inside
            # that recent window so fresh push/new_chat recovery commands are
            # always visible without returning an unbounded command history.
            try: command_limit=max(10,min(200,int(q.get('limit',['100'])[0])))
            except Exception: command_limit=100
            with connect() as c:
                rows=c.execute(
                    "SELECT id,project_id,action,created_at FROM ("
                    "SELECT id,project_id,action,created_at FROM runner_commands "
                    "WHERE status='pending' ORDER BY id DESC LIMIT ?"
                    ") ORDER BY id",
                    (command_limit,),
                ).fetchall()
            return self.reply({'commands':[dict(r) for r in rows]})
        with LOCK:data=CACHE
        if u.path.startswith('/api/'):
            if data is None:return self.reply({'error':'Monitor start op'},503)
            pid=q.get('project',[''])[0]
            if u.path.startswith('/api/v1/watch'):
                auth=self.headers.get('Authorization','')
                if WATCH_TOKEN and auth != 'Bearer '+WATCH_TOKEN:return self.reply({'error':'Unauthorized'},401)
            if u.path in ('/api/status','/api/v1/status'):
                target_cfg=runner_targets()
                runners=runner_statuses(target_cfg=target_cfg)
                incidents=enhancements.incident_center(DB,runners,data=data)
                return self.reply({**public_status(data),'chatgpt_runner':runner_status(target_cfg=target_cfg),'chatgpt_runners':runners,'chatgpt_firefox':firefox_runner_status(),'dynamic_workers':dynamic_worker_settings(),'incidents':incidents})
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
        files={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/enhancements.js':'enhancements.js','/style.css':'style.css','/enhancements.css':'enhancements.css','/ftmo-readiness.css':'ftmo-readiness.css','/favicon.svg':'favicon.svg','/manifest.webmanifest':'manifest.webmanifest','/zcloud-worker.user.js':'zcloud-worker.user.js'}
        if u.path not in files:return self.reply({'error':'Niet gevonden'},404)
        path=ROOT/'public'/files[u.path]
        return self.reply(path.read_bytes(),kind=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
    def log_message(self,*args):pass

if __name__=='__main__':
    init_db()
    threading.Thread(target=sampler,daemon=True,name='cloud-monitor').start()
    threading.Thread(target=autonomy_scheduler,daemon=True,name='zcloud-autonomy').start()
    ThreadingHTTPServer((os.getenv('ZENNAY_BIND','0.0.0.0'),int(os.getenv('ZENNAY_PORT','8765'))),Handler).serve_forever()
