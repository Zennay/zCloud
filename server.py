"""zCloud: read-only project monitoring; stdlib only."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone
import json, os, sqlite3, subprocess, shutil, threading, time, mimetypes, logging, hmac, secrets, re
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
SERVICES = ['haxlab-analyzer.service', 'haxlab-ingest.service', 'haxlab-worker.service', 'ftmo-autonomous.service', 'ftmo-autonomous.timer', SUPA_SYNC, *RUNNERS.values()]
WATCH_TOKEN_FILE = ROOT / '.watch-token'
WATCH_TOKEN = WATCH_TOKEN_FILE.read_text().strip() if WATCH_TOKEN_FILE.exists() else ''
ACTION_ALLOW_FILE = ROOT / '.action-allowed-ips'
MAX_CHATGPT_WORKERS = 8
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
    project=PROJECT_INDEX.get(project_id,{})
    sources=[]
    if project.get('notion_url'): sources.append('Notion project: '+project['notion_url'])
    if project.get('handoff_url'): sources.append('handoff: '+project['handoff_url'])
    if project.get('scorecard_url'): sources.append('scorecard: '+project['scorecard_url'])
    if project.get('repo_url'): sources.append('GitHub: '+project['repo_url'])
    source_context=(' Canonieke bronnen: '+'; '.join(sources)+'.') if sources else ''
    return (
        f'Ga verder met project {name}. Deze chat is uitsluitend voor project {name}; werk niet aan andere projecten. '
        'Controleer eerst via de gekoppelde Notion-workspace de actuele projectpagina, handoff, status, open taken, '
        'besluiten en relevante documentatie. Gebruik daarnaast de gekoppelde GitHub/repository- en VPS-context waar '
        'die voor dit project relevant is. Ga daarna zelfstandig verder met de eerstvolgende concrete stap die het '
        'project aantoonbaar vooruit helpt. Behoud bestaande architectuur en eerdere beslissingen tenzij de actuele '
        'projectdocumentatie expliciet iets anders aangeeft. Rapporteer kort wat je hebt gedaan, wat de nieuwe status '
        'is en wat logisch als volgende stap volgt.' + source_context
    )

def project_worker_prompt(project_id, name, base_prompt, slot, total):
    lane=WORKER_LANES[(slot-1) % len(WORKER_LANES)]
    coordination=(
        f' Je bent parallelle zCloud-worker {slot}/{total}. Jouw werk-lane is: {lane}. '
        'Voorkom dubbelwerk: controleer vóór iedere wijziging actuele Notion-taken/claims, open GitHub-PRs/branches '
        'en de live VPS-status. Pak alleen een concrete work-item die niet al actief door een andere worker wordt '
        'uitgevoerd. Gebruik waar beschikbaar de bestaande Claimed by/lease-velden in Notion en leg je claim vast '
        'voordat je schrijft. Als er geen veilige onafhankelijke write-taak beschikbaar is, doe alleen read-only '
        'validatie of voorbereidend werk en documenteer de bevindingen in plaats van hetzelfde werk opnieuw te doen.'
    )
    if project_id=='ftmo':
        coordination += (
            ' Voor FTMO blijven preregistration, chronologische splits, walk-forward en final holdout strikt gescheiden. '
            'Gebruik verborgen validation/holdout-resultaten nooit voor ontwerpkeuzes en red of retune afgewezen '
            'generaties niet. Parallel voorbereid werk is alleen toegestaan wanneer het outcome-free blijft.'
        )
    if project_id=='cloud':
        coordination += (
            ' Voor zCloud self-improvement geldt een persistent finish-protocol. Controleer vóór een write-iteratie '
            'via de live zCloud improvement-state hoeveel implementatie-iteraties al zijn afgerond. Als de teller op 9 '
            'staat, is dit de tiende/harde laatste iteratie en moet je in hetzelfde slotrapport ook de eind-audit doen. '
            'Alleen na een daadwerkelijk afgeronde implementatie-iteratie zet je exact de losse marker ZCLOUD_ITERATION_COMPLETE in je slotrapport. '
            'Wanneer je expliciet de senior finish-gate beoordeelt, voeg exact één marker toe: '
            'ZCLOUD_FINISH_REVIEW: GREEN_NO_P0P1 als de finish-gate groen is en er geen nieuwe P0/P1 is, anders '
            'ZCLOUD_FINISH_REVIEW: OPEN_P0P1. Bij de eind-audit na de harde iteratiegrens gebruik je exact '
            'ZCLOUD_FINAL_AUDIT: GREEN of ZCLOUD_FINAL_AUDIT: FAIL. Gebruik deze markers niet voor read-only prep.'
        )
    return base_prompt + coordination
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
        c.execute('CREATE TABLE IF NOT EXISTS runner_commands(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, result TEXT)')
        c.execute('CREATE INDEX IF NOT EXISTS runner_commands_status ON runner_commands(status,id)')
        c.execute("CREATE TABLE IF NOT EXISTS task_claims(project_id TEXT NOT NULL, claim_key TEXT NOT NULL, owner_id TEXT NOT NULL, worker_id TEXT NOT NULL DEFAULT '', acquired_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, lease_until TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(project_id,claim_key))")
        c.execute('CREATE INDEX IF NOT EXISTS task_claims_lease_until ON task_claims(lease_until)')
        c.execute("CREATE TABLE IF NOT EXISTS improvement_loops(project_id TEXT PRIMARY KEY, state TEXT NOT NULL, iteration_count INTEGER NOT NULL DEFAULT 0, clean_reviews INTEGER NOT NULL DEFAULT 0, stop_reason TEXT, last_green_commit TEXT, audit_result TEXT, updated_at TEXT NOT NULL)")
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
            names=['haxlab-analyzer.service','haxlab-ingest.service','haxlab-worker.service',RUNNERS['haxlab']]
            try:p['metrics']=replay_metrics()
            except Exception:p['metrics']={'available':False}
            try:p['quality']=enhancements.quality_for('haxlab')
            except Exception:p['quality']={'available':False,'items':[]}
        elif p['id']=='ftmo':
            names=['ftmo-autonomous.timer','ftmo-autonomous.service',RUNNERS['ftmo']]
            try:p['quality']=enhancements.quality_for('ftmo')
            except Exception:p['quality']={'available':False,'items':[]}
        elif p['id']=='supa': names=[SUPA_SYNC]
        else: names=[]
        for name in names:
            s=states.get(name,{})
            state=s.get('ActiveState','unknown')
            label=name.removesuffix('.service').removesuffix('.timer').replace('haxlab-','').replace('ftmo-autonomous','Research').split('.')[-1]
            if name in RUNNERS.values():label='GitHub runner'
            if name.endswith('.timer'):label='Research timer'
            if name==SUPA_SYNC:label='Repo sync'
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
            if state==IMPROVEMENT_RUNNING and bool(finish_gate_green) and not bool(p0p1_open):
                clean += 1; green_commit=canonical_main_sha() or green_commit
                if clean >= 2:
                    state='finished'; stop_reason='two_consecutive_clean_senior_reviews'
            elif state==IMPROVEMENT_RUNNING:
                clean=0
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

def task_claims(project_id=None):
    ts=now()
    with connect() as c:
        c.execute('DELETE FROM task_claims WHERE lease_until<=?',(ts,))
        if project_id:
            rows=c.execute('SELECT * FROM task_claims WHERE project_id=? ORDER BY claim_key',(project_id,)).fetchall()
        else:
            rows=c.execute('SELECT * FROM task_claims ORDER BY project_id,claim_key').fetchall()
    return [_claim_payload(row) for row in rows]

def task_claim_acquire(project_id,claim_key,owner_id,worker_id='',lease_seconds=300,metadata=None):
    project_id=str(project_id or '').strip()[:80]
    claim_key=str(claim_key or '').strip()[:240]
    owner_id=str(owner_id or '').strip()[:160]
    worker_id=str(worker_id or '').strip()[:160]
    if not project_id or not claim_key or not owner_id:
        raise ValueError('project_id, claim_key en owner_id zijn verplicht')
    metadata=metadata if isinstance(metadata,dict) else {}
    if project_id==IMPROVEMENT_PROJECT_ID and metadata.get('loop')=='self_improvement' and not improvement_loop_state(project_id)['auto_continue']:
        return {'acquired':False,'blocked':'improvement_loop_finished','claim':None}
    lease_seconds=_claim_lease_seconds(lease_seconds)
    ts=now()
    until=_claim_timestamp(lease_seconds)
    metadata_json=json.dumps(metadata,ensure_ascii=False,separators=(',',':'))[:4000]
    with connect() as c:
        cur=c.execute(
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
            (project_id,claim_key,owner_id,worker_id,ts,ts,until,metadata_json)
        )
        changed=cur.rowcount>0
        row=c.execute('SELECT * FROM task_claims WHERE project_id=? AND claim_key=?',(project_id,claim_key)).fetchone()
    return {'acquired':bool(changed and row and row['owner_id']==owner_id),'claim':_claim_payload(row)}

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

def runner_targets():
    with connect() as c:
        rows=c.execute('SELECT project_id,name,conversation_id,prompt,active,worker_count FROM runner_targets ORDER BY project_id').fetchall()
    out={}
    for r in rows:
        improvement=improvement_loop_state(r['project_id']) if r['project_id']==IMPROVEMENT_PROJECT_ID else None
        out[r['project_id']]={'project_id':r['project_id'],'name':r['name'],'conversation_id':r['conversation_id'],
                              'url':('https://chatgpt.com/c/'+r['conversation_id']) if r['conversation_id'] else 'https://chatgpt.com/',
                              'prompt':r['prompt'],'active':bool(r['active']),'worker_count':max(1,int(r['worker_count'] or 1)),
                              'auto_continue':bool(improvement['auto_continue']) if improvement else True,
                              'improvement':improvement}
    return out

def runner_worker_targets():
    base=runner_targets()
    out={}
    with connect() as c:
        for project_id,cfg in base.items():
            count=max(1,min(MAX_CHATGPT_WORKERS,int(cfg.get('worker_count') or 1)))
            for slot in range(1,count+1):
                c.execute('INSERT OR IGNORE INTO runner_workers(project_id,worker_slot,conversation_id) VALUES(?,?,?)',
                          (project_id,slot,cfg['conversation_id'] if slot==1 else ''))
                row=c.execute('SELECT conversation_id,desired_state FROM runner_workers WHERE project_id=? AND worker_slot=?',(project_id,slot)).fetchone()
                conversation_id=(row['conversation_id'] if row else '') or ''
                desired_state=(row['desired_state'] if row else 'running') or 'running'
                worker_key=f'{project_id}::w{slot}'
                out[worker_key]={
                    'project_id':worker_key,'base_project_id':project_id,'worker_slot':slot,'worker_count':count,
                    'name':f"{cfg['name']} · worker {slot}/{count}",'conversation_id':conversation_id,
                    'url':('https://chatgpt.com/c/'+conversation_id) if conversation_id else 'https://chatgpt.com/',
                    'prompt':project_worker_prompt(project_id,cfg['name'],cfg['prompt'],slot,count),
                    'desired_state':desired_state,'active':bool(cfg['active']) and desired_state!='paused',
                    'auto_continue':bool(cfg.get('auto_continue',True)),'improvement':cfg.get('improvement')
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
            active=bool(base.get('active')) and desired!='paused'
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
            if not base.get('active') or desired=='paused':
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
                with connect() as c:
                    recent=c.execute("SELECT created_at FROM runner_commands WHERE project_id=? AND action=? AND status IN ('pending','completed') ORDER BY id DESC LIMIT 1",(project_id,action)).fetchone()
                    if recent:
                        try:
                            seconds=(datetime.now(timezone.utc)-datetime.fromisoformat(recent['created_at'])).total_seconds()
                            cooldown=5 if action in ('push','start','pause','drain') else 30
                            if seconds<cooldown:return self.reply({'error':'Er is net al een actie voor deze worker of dit project gestart'},429)
                        except Exception: pass
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
                desired_state=('draining' if action=='drain' else 'paused' if action=='pause' else 'running')
                return self.reply({'ok':True,'command_id':command_id,'status':'pending','active':action!='pause','desired_state':desired_state})
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
            if u.path=='/api/task-claims':
                if not action_request_allowed(self):return self.reply({'error':'Acties zijn alleen toegestaan vanaf een vertrouwd beheer-IP'},403)
                action=str(payload.get('action') or 'acquire')
                project_id=str(payload.get('project_id') or '').strip()
                claim_key=str(payload.get('claim_key') or '').strip()
                owner_id=str(payload.get('owner_id') or '').strip()
                try:
                    if action=='acquire':
                        result=task_claim_acquire(project_id,claim_key,owner_id,payload.get('worker_id') or '',payload.get('lease_seconds') or 300,payload.get('metadata'))
                        return self.reply(result,200 if result['acquired'] else 409)
                    if action=='heartbeat':
                        result=task_claim_heartbeat(project_id,claim_key,owner_id,payload.get('lease_seconds') or 300)
                        return self.reply(result,200 if result['renewed'] else 409)
                    if action=='release':
                        result=task_claim_release(project_id,claim_key,owner_id)
                        return self.reply(result,200 if result['released'] else 409)
                    return self.reply({'error':'Ongeldige claimactie'},400)
                except ValueError as e:
                    return self.reply({'error':str(e)},400)
            if u.path=='/api/runner-workers':
                if not action_request_allowed(self):return self.reply({'error':'Acties zijn alleen toegestaan vanaf een vertrouwd beheer-IP'},403)
                project_id=str(payload.get('project_id') or '')
                try: worker_count=int(payload.get('worker_count'))
                except Exception: return self.reply({'error':'Aantal ChatGPT-tabs moet een geheel getal zijn'},400)
                if project_id not in runner_targets():return self.reply({'error':'Onbekend project'},404)
                if worker_count<1 or worker_count>MAX_CHATGPT_WORKERS:return self.reply({'error':f'Kies 1 t/m {MAX_CHATGPT_WORKERS} ChatGPT-tabs'},400)
                with connect() as c:
                    c.execute('UPDATE runner_targets SET worker_count=? WHERE project_id=?',(worker_count,project_id))
                    primary=c.execute('SELECT conversation_id FROM runner_targets WHERE project_id=?',(project_id,)).fetchone()
                    for slot in range(1,worker_count+1):
                        c.execute('INSERT OR IGNORE INTO runner_workers(project_id,worker_slot,conversation_id) VALUES(?,?,?)',
                                  (project_id,slot,(primary['conversation_id'] if slot==1 and primary else '') or ''))
                return self.reply({'ok':True,'project_id':project_id,'worker_count':worker_count,'max_workers':MAX_CHATGPT_WORKERS})
            if u.path=='/api/resource-priority':
                if not action_request_allowed(self):return self.reply({'error':'Acties zijn alleen toegestaan vanaf een vertrouwd beheer-IP'},403)
                project=str(payload.get('project') or '')
                priority=str(payload.get('priority') or '')
                try: result=enhancements.set_priority(project,priority)
                except ValueError as e: return self.reply({'error':str(e)},400)
                return self.reply({'ok':True,'resource':result,'time':now()})
            if u.path=='/api/project-layout':
                if 'application/json' not in self.headers.get('Content-Type',''):return self.reply({'error':'JSON vereist'},415)
                with LOCK:data=CACHE
                if data is None:return self.reply({'error':'Monitor start op'},503)
                layout=save_project_layout(payload,data['projects'])
                return self.reply({'ok':True,'layout':layout,'time':now()})
            return self.reply({'error':'Niet gevonden'},404)
        except Exception:
            logging.exception('POST failed');self.reply({'error':'Opslaan mislukt'},500)
    def route(self):
        u=urlparse(self.path);q=parse_qs(u.query)
        if u.path=='/api/runner-targets':
            if self.client_address[0] not in ('127.0.0.1','::1'):return self.reply({'error':'Alleen lokaal'},403)
            return self.reply({'projects':runner_worker_targets(),'max_workers':MAX_CHATGPT_WORKERS})
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
            if u.path in ('/api/status','/api/v1/status'):return self.reply({**public_status(data),'chatgpt_runner':runner_status(),'chatgpt_runners':runner_statuses(),'chatgpt_firefox':firefox_runner_status()})
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
    ThreadingHTTPServer((os.getenv('ZENNAY_BIND','0.0.0.0'),int(os.getenv('ZENNAY_PORT','8765'))),Handler).serve_forever()