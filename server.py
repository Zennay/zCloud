"""zCloud: read-only project monitoring; stdlib only."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone
import json, os, sqlite3, subprocess, shutil, threading, time, mimetypes, logging
import enhancements

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'history.db'
LOCK = threading.Lock()
CACHE = None
CPU_PREV = None
RUNNERS = {'haxlab': 'actions.runner.Zennay-Haxlab.vps-bb300bba-haxlab.service', 'ftmo': 'actions.runner.Zennay-Ftmo.vps-bb300bba-ftmo.service'}
SUPA_SYNC = 'zennay-supa-sync.timer'
SERVICES = ['haxlab-analyzer.service', 'haxlab-ingest.service', 'haxlab-worker.service', 'ftmo-autonomous.service', 'ftmo-autonomous.timer', SUPA_SYNC, *RUNNERS.values()]
WATCH_TOKEN_FILE = ROOT / '.watch-token'
WATCH_TOKEN = WATCH_TOKEN_FILE.read_text().strip() if WATCH_TOKEN_FILE.exists() else ''
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
def git(path, *args): return cmd(['git', '-c', 'safe.directory='+path, '-C', path, *args])
def connect():
    c = sqlite3.connect(DB, timeout=4)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE IF NOT EXISTS project_samples(ts TEXT, project TEXT, progress REAL, commits INTEGER, hash TEXT, message TEXT, PRIMARY KEY(ts, project))')
        c.execute('CREATE TABLE IF NOT EXISTS host_samples(ts TEXT PRIMARY KEY, cpu REAL, memory REAL, disk REAL, load REAL)')
        c.execute('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, ts TEXT, project TEXT, kind TEXT, title TEXT, detail TEXT)')
        c.execute('CREATE INDEX IF NOT EXISTS events_ts ON events(ts)')
        c.execute('CREATE TABLE IF NOT EXISTS runner_events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, event TEXT, target TEXT, title TEXT, generating INTEGER, sending INTEGER, reason TEXT, tab_id INTEGER, error TEXT)')
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
    with sqlite3.connect('file:/var/lib/haxlab/state/haxlab.sqlite3?mode=ro',uri=True,timeout=1) as c:
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
        p['progress']=round(sum(m['progress'] for m in p['milestones'])/len(p['milestones']))
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
                for candidate in (repo_ref,'HEAD'):
                    try:
                        git(path,'rev-parse','--verify',candidate+'^{commit}')
                        active_ref=candidate
                        break
                    except Exception:
                        pass
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

def runner_record(payload):
    event=str(payload.get('event') or 'unknown')[:64]
    raw_ts=str(payload.get('at') or now())
    try: ts=datetime.fromisoformat(raw_ts.replace('Z','+00:00')).astimezone(timezone.utc).isoformat()
    except Exception: ts=now()
    target=str(payload.get('target') or payload.get('targetConversation') or '')[:500]
    title=str(payload.get('title') or '')[:250]
    reason=str(payload.get('reason') or '')[:250]
    error=str(payload.get('error') or '')[:500]
    tab_id=payload.get('tabId')
    with connect() as c:
        c.execute('INSERT INTO runner_events(ts,event,target,title,generating,sending,reason,tab_id,error) VALUES(?,?,?,?,?,?,?,?,?)',
                  (ts,event,target,title,int(bool(payload.get('generating'))),int(bool(payload.get('sending'))),reason,tab_id,error))
        cutoff=datetime.fromtimestamp(time.time()-14*86400,timezone.utc).isoformat()
        c.execute('DELETE FROM runner_events WHERE ts < ?', (cutoff,))

def runner_status():
    with connect() as c:
        latest=c.execute('SELECT * FROM runner_events ORDER BY id DESC LIMIT 1').fetchone()
        if not latest:
            return {'state':'offline','age_seconds':None,'last_event':None,'last_heartbeat':None,'last_prompt_sent':None,'last_generation_started':None,'last_generation_finished':None}
        heartbeat=c.execute("SELECT * FROM runner_events WHERE event='heartbeat' ORDER BY id DESC LIMIT 1").fetchone()
        prompt=c.execute("SELECT * FROM runner_events WHERE event='prompt-sent' ORDER BY id DESC LIMIT 1").fetchone()
        started=c.execute("SELECT * FROM runner_events WHERE event='generation-started' ORDER BY id DESC LIMIT 1").fetchone()
        finished=c.execute("SELECT * FROM runner_events WHERE event='generation-finished' ORDER BY id DESC LIMIT 1").fetchone()
        meta=c.execute("SELECT * FROM runner_events WHERE title<>'' OR target<>'' ORDER BY id DESC LIMIT 1").fetchone()
    def info(r):
        if not r:return None
        return {'time':r['ts'],'event':r['event'],'reason':r['reason'] or None,'error':r['error'] or None}
    try: age=max(0,int((datetime.now(timezone.utc)-datetime.fromisoformat(latest['ts'])).total_seconds()))
    except Exception: age=999999
    state='live' if age<=90 else 'stale' if age<=300 else 'offline'
    return {
        'state':state,'age_seconds':age,'event':latest['event'],
        'generating':bool(latest['generating']),'sending':bool(latest['sending']),
        'title':(meta['title'] if meta else '') or None,'target':(meta['target'] if meta else '') or None,
        'tab_id':latest['tab_id'],'error':latest['error'] or None,
        'last_event':info(latest),'last_heartbeat':info(heartbeat),'last_prompt_sent':info(prompt),
        'last_generation_started':info(started),'last_generation_finished':info(finished)
    }

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
            if u.path=='/api/resource-priority':
                if self.client_address[0] not in ('127.0.0.1','::1'):return self.reply({'error':'Alleen lokaal'},403)
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
        with LOCK:data=CACHE
        if u.path.startswith('/api/'):
            if data is None:return self.reply({'error':'Monitor start op'},503)
            pid=q.get('project',[''])[0]
            if u.path.startswith('/api/v1/watch'):
                auth=self.headers.get('Authorization','')
                if WATCH_TOKEN and auth != 'Bearer '+WATCH_TOKEN:return self.reply({'error':'Unauthorized'},401)
            if u.path in ('/api/status','/api/v1/status'):return self.reply({**public_status(data),'chatgpt_runner':runner_status()})
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
        files={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/style.css':'style.css','/favicon.svg':'favicon.svg','/manifest.webmanifest':'manifest.webmanifest'}
        if u.path not in files:return self.reply({'error':'Niet gevonden'},404)
        path=ROOT/'public'/files[u.path]
        return self.reply(path.read_bytes(),kind=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
    def log_message(self,*args):pass

if __name__=='__main__':
    init_db()
    threading.Thread(target=sampler,daemon=True,name='cloud-monitor').start()
    ThreadingHTTPServer((os.getenv('ZENNAY_BIND','0.0.0.0'),int(os.getenv('ZENNAY_PORT','8765'))),Handler).serve_forever()