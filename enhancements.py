from pathlib import Path
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
import hashlib, json, os, re, sqlite3, subprocess, time
import project_runtime

ROOT = Path("/home/ubuntu/zennay-cloud")
RESOURCE_FILE = ROOT / "resource-policy.json"
ALERT_STATE_FILE = ROOT / "alert-state.json"
SIGNALS_DIR = ROOT / "signals"
RECOVERY_DIR = Path(os.environ.get("ZCLOUD_RECOVERY_DIR", str(Path.home() / ".local/state/zcloud/recovery")))

PRIORITY_WEIGHTS = {"background": 100, "normal": 400, "high": 800, "turbo": 3000}
PROJECT_UNITS = {
    "haxlab": [
        "haxlab-analyzer.service",
        "haxlab-ingest.service",
        "haxlab-worker.service",
        "actions.runner.Zennay-Haxlab.vps-bb300bba-haxlab.service",
    ],
    "ftmo": [
        "ftmo-autonomous.service",
        "ftmo-autonomous-marathon.service",
        "actions.runner.Zennay-Ftmo.vps-bb300bba-ftmo.service",
    ],
    "cloud": ["zennay-cloud.service"],
    "supa": [],
    "raiseai": [],
    "ulab": [],
    "zssh": ["zssh.service"],
    "lightup": [],
}
_RESOURCE_PREV = {}
_RESOURCE_HOST_PREV = None

def _now():
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def _db_connect(db_path):
    c = sqlite3.connect(db_path)
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def _json(path, sudo=False):
    p = str(path)
    try:
        if sudo:
            raw = subprocess.check_output(["sudo", "-n", "cat", p], text=True, stderr=subprocess.DEVNULL, timeout=3)
            return json.loads(raw)
        return json.loads(Path(p).read_text())
    except Exception:
        return None

def load_resource_policy():
    try:
        contracts = project_runtime.load_contracts()
        default = {
            pid: {"priority": str((contract.get("compute") or {}).get("priority") or "normal")}
            for pid, contract in contracts["projects"].items()
        }
    except Exception:
        default = {
            "haxlab": {"priority": "background"},
            "ftmo": {"priority": "turbo"},
            "supa": {"priority": "normal"},
            "raiseai": {"priority": "normal"},
            "cloud": {"priority": "normal"},
            "ulab": {"priority": "normal"},
            "zssh": {"priority": "high"},
            "lightup": {"priority": "normal"},
        }
    try:
        raw = json.loads(RESOURCE_FILE.read_text())
    except Exception:
        raw = {}
    for pid, cfg in default.items():
        priority = str((raw.get(pid) or {}).get("priority") or cfg["priority"])
        if priority not in PRIORITY_WEIGHTS:
            priority = cfg["priority"]
        default[pid] = {"priority": priority}
    return default

def _cgroup_cpu_nsec(control_group):
    """Read cumulative cgroup-v2 CPU time, including child processes."""
    if not control_group or not control_group.startswith("/"):
        return None
    path = Path("/sys/fs/cgroup") / control_group.lstrip("/") / "cpu.stat"
    try:
        values = dict(
            line.split(None, 1)
            for line in path.read_text().splitlines()
            if len(line.split(None, 1)) == 2
        )
        return int(values.get("usage_usec") or 0) * 1000
    except Exception:
        return None

def _unit_numbers(unit):
    try:
        raw = subprocess.check_output(
            ["systemctl", "show", unit, "--property=CPUUsageNSec,MemoryCurrent,ActiveState,ControlGroup"],
            text=True, stderr=subprocess.DEVNULL, timeout=2
        )
        d = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        cpu = int(d.get("CPUUsageNSec") or 0)
        cgroup_cpu = _cgroup_cpu_nsec(d.get("ControlGroup") or "")
        if cgroup_cpu is not None:
            cpu = cgroup_cpu
        return cpu, int(d.get("MemoryCurrent") or 0), d.get("ActiveState") or "unknown"
    except Exception:
        return 0, 0, "unknown"

def _host_cpu_counter():
    """Return cumulative aggregate CPU ticks from /proc/stat for an aligned attribution window."""
    try:
        fields = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
        total = sum(fields)
        idle = fields[3] + fields[4]
        return total, idle
    except Exception:
        return None

def resource_snapshot():
    global _RESOURCE_HOST_PREV
    policy = load_resource_policy()
    now_mono = time.monotonic()
    cores = max(1, int(os.cpu_count() or 1))
    host_now = _host_cpu_counter()
    host_cpu_pct = None
    if _RESOURCE_HOST_PREV and host_now and host_now[0] > _RESOURCE_HOST_PREV[0]:
        delta_total = host_now[0] - _RESOURCE_HOST_PREV[0]
        delta_idle = host_now[1] - _RESOURCE_HOST_PREV[1]
        host_cpu_pct = round(100 * (1 - delta_idle / delta_total), 1)
    if host_now:
        _RESOURCE_HOST_PREV = host_now

    out = {}
    attributed = 0.0
    measured_projects = 0
    for pid, cfg in policy.items():
        units = PROJECT_UNITS.get(pid, [])
        total_cpu = total_mem = active = 0
        for unit in units:
            cpu, mem, state = _unit_numbers(unit)
            total_cpu += cpu
            total_mem += mem
            active += int(state in ("active", "activating"))
        cpu_pct = None
        prev = _RESOURCE_PREV.get(pid)
        if prev and now_mono > prev[0] and total_cpu >= prev[1]:
            # systemd/cgroup CPU time is expressed in "one fully busy core = 100%".
            # Normalize by the VPS core count so project CPU uses the same 0..100
            # host-share scale as /proc/stat host CPU.
            core_equiv_pct = ((total_cpu - prev[1]) / 1_000_000_000) / (now_mono - prev[0]) * 100
            cpu_pct = round(max(0.0, core_equiv_pct / cores), 1)
            attributed += cpu_pct
            measured_projects += 1
        _RESOURCE_PREV[pid] = (now_mono, total_cpu)
        out[pid] = {
            "priority": cfg["priority"],
            "weight": PRIORITY_WEIGHTS[cfg["priority"]],
            "managed": bool(units),
            "active_units": active,
            "unit_count": len(units),
            "cpu_percent": cpu_pct,
            "memory_bytes": total_mem,
            "mode": "host_share",
            "note": "CPU is aandeel van totale VPS-capaciteit; cgroup-metingen nemen child-processen mee.",
        }
        try:
            compute = project_runtime.project_contract(pid).get("compute") or {}
            out[pid]["compute_class"] = compute.get("class")
            out[pid]["resource_pool"] = compute.get("pool")
            out[pid]["cpu_soft_cores"] = compute.get("cpu_soft_cores")
            out[pid]["memory_soft_mb"] = compute.get("memory_soft_mb")
            out[pid]["protected"] = bool(compute.get("protected"))
        except Exception:
            out[pid]["contract_error"] = True

    unattributed = None
    if host_cpu_pct is not None:
        unattributed = round(max(0.0, host_cpu_pct - attributed), 1)
    out["_summary"] = {
        "host_cpu_percent": host_cpu_pct,
        "attributed_cpu_percent": round(attributed, 1) if measured_projects else None,
        "unattributed_cpu_percent": unattributed,
        "cores": cores,
        "measured_projects": measured_projects,
        "scale": "host_share",
    }
    return out

def set_priority(project, priority):
    if project not in PROJECT_UNITS:
        raise ValueError("Onbekend project")
    if priority not in PRIORITY_WEIGHTS:
        raise ValueError("Ongeldige prioriteit")
    policy = load_resource_policy()
    policy[project] = {"priority": priority}
    tmp = RESOURCE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(RESOURCE_FILE)

    # Persistence is the source of truth for the dashboard. Applying the live
    # systemd weight is best-effort: a sudo/helper failure must never make the
    # UI pretend the user's saved priority was rejected.
    applied = not bool(PROJECT_UNITS.get(project))
    apply_error = None
    if PROJECT_UNITS.get(project):
        try:
            subprocess.check_output(
                ["sudo", "-n", "/usr/local/sbin/zennay-resource-control", "apply", project],
                text=True, stderr=subprocess.STDOUT, timeout=12
            )
            applied = True
        except Exception as exc:
            apply_error = str(exc)[:300]

    saved = (load_resource_policy().get(project) or {}).get("priority")
    if saved != priority:
        raise RuntimeError("Prioriteit kon niet duurzaam worden opgeslagen")

    result = {
        "project": project,
        "priority": priority,
        "weight": PRIORITY_WEIGHTS[priority],
        "persisted": True,
        "applied": applied,
    }
    if apply_error:
        result["apply_error"] = apply_error
    return result

def _find_metrics_dict(data):
    if not isinstance(data, dict):
        return None
    for key in ("frozen_holdout", "holdout", "validation", "metrics"):
        val = data.get(key)
        if isinstance(val, dict) and "direction_accuracy" in val and "joint_accuracy" in val:
            return val
    if "direction_accuracy" in data and "joint_accuracy" in data:
        return data
    for val in data.values():
        if isinstance(val, dict):
            hit = _find_metrics_dict(val)
            if hit:
                return hit
    return None

def _source_observed_at(source):
    """Return an evidence timestamp for a local source path without inventing one."""
    if not source:
        return None
    try:
        path = Path(str(source))
        if not path.exists() or not path.is_file():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except Exception:
        return None


def _comparison_point(label, value, unit="", note="", source=None, observed_at=None, validated=False):
    if value is None or value == "":
        return None
    point = {
        "label": str(label),
        "value": value,
        "unit": str(unit or ""),
        "note": str(note or ""),
        "validated": bool(validated),
    }
    if source:
        point["source"] = str(source)
        observed_at = observed_at or _source_observed_at(source)
    if observed_at:
        point["observed_at"] = str(observed_at)
    return point

def _comparison_view(latest=None, current=None, best=None):
    return {
        "latest": latest,
        "current": current,
        "best": best,
        "available": any(x is not None for x in (latest, current, best)),
    }

def _hax_quality():
    current_path = Path("/var/lib/haxlab/derived/champions/elite-player/current.json")
    live_path = Path("/var/lib/haxlab/derived/champions/elite-player/live.json")
    current = _json(current_path, sudo=True) or {}
    live = _json(live_path, sudo=True) or {}
    metrics = _json(current.get("metrics_path", ""), sudo=True) if current.get("metrics_path") else None
    if not metrics:
        metrics = _json("/var/lib/haxlab/derived/training/elite-player-champion-candidate/pipeline-summary.json") or {}
    holdout = _find_metrics_dict(metrics) or {}
    direction = holdout.get("direction_accuracy")
    joint = holdout.get("joint_accuracy")
    kick_f1 = holdout.get("kick_f1")
    live_healthy = bool((live.get("live_health") or {}).get("healthy"))
    version = live.get("version_id") or current.get("version_id")
    items = []
    if direction is not None:
        items.append({"label": "Direction accuracy", "value": round(float(direction) * 100, 1), "unit": "%"})
    if joint is not None:
        items.append({"label": "Joint action accuracy", "value": round(float(joint) * 100, 1), "unit": "%"})
    if kick_f1 is not None:
        items.append({"label": "Kick F1", "value": round(float(kick_f1) * 100, 1), "unit": "%"})
    items.append({"label": "Live champion", "value": "Healthy" if live_healthy else "Not healthy", "unit": ""})
    headline = None
    if joint is not None:
        headline = {
            "label": "Player imitation",
            "value": round(float(joint) * 100, 1),
            "unit": "%",
            "note": "Frozen-holdout joint action accuracy; geen win-rate.",
        }
    latest_version = current.get("version_id") or version
    live_version = live.get("version_id")
    current_point = _comparison_point(
        "Huidige live champion",
        live_version,
        note="Live health groen" if live_healthy else "Live health niet groen",
        source=str(live_path),
        validated=live_healthy,
    )
    best_point = _comparison_point(
        "Beste gevalideerde champion",
        live_version,
        note="Gepromoveerde live champion" if live_healthy else "Champion bestaat, maar live health is niet groen",
        source=str(live_path),
        validated=live_healthy,
    )
    comparison = _comparison_view(
        latest=_comparison_point(
            "Nieuwste candidate",
            latest_version,
            note=("Frozen-holdout joint accuracy %.1f%%" % (float(joint) * 100)) if joint is not None else "Nieuwste offline candidate",
            source=current.get("metrics_path") or str(current_path),
            validated=joint is not None,
        ),
        current=current_point,
        best=best_point if live_healthy else None,
    )
    return {
        "available": bool(headline),
        "headline": headline,
        "items": items,
        "stage": "live champion" if live else "offline validation",
        "meta": {"version_id": version, "live_healthy": live_healthy},
        "comparison": comparison,
    }

def _gen_number(text):
    m = re.search(r"(?:alpha-|generation-?)(\d+)", text or "", re.I)
    return int(m.group(1)) if m else -1

def _ftmo_candidate():
    root = Path("/opt/ftmo-runner/_work/Ftmo/Ftmo/artifacts/research_outcomes")
    choices = []
    for path in root.glob("*/development-review-summary.json"):
        data = _json(path)
        if not data:
            continue
        gen = _gen_number(data.get("generation_id") or path.parent.name)
        for review in data.get("reviews", []):
            if review.get("outcome") != "candidate" or not review.get("selected_variant"):
                continue
            exp = review.get("experiment_id")
            for trial_path in (path.parent / str(exp) / "trials").glob("*.json"):
                trial = _json(trial_path) or {}
                result = trial.get("result") or {}
                if result.get("variant") == review.get("selected_variant"):
                    choices.append((gen, trial_path, trial, result, data))
    return max(choices, key=lambda x: x[0]) if choices else None

def _ftmo_release():
    root = Path("/opt/ftmo-runner/_work/Ftmo/Ftmo/artifacts/research_outcomes")
    releases = []
    for release_path in root.glob("*/paper-release.json"):
        release = _json(release_path) or {}
        for rel in release.get("released_candidates", []):
            exp = rel.get("experiment_id")
            run_path = release_path.parent / str(exp) / "holdout-run.json"
            holdout = _json(run_path) or {}
            if holdout:
                release = dict(release)
                holdout = dict(holdout)
                release["_zcloud_source_path"] = str(release_path)
                holdout["_zcloud_source_path"] = str(run_path)
                releases.append((_gen_number(release.get("generation_id") or release_path.parent.name), release, rel, holdout))
    return max(releases, key=lambda x: x[0]) if releases else None

def _pips(value):
    try:
        return round(float(value) / 0.0001, 1)
    except Exception:
        return None

def _as_pct(value):
    try:
        value = float(value)
        if abs(value) <= 1:
            value *= 100
        return round(value, 1)
    except Exception:
        return None

def _ftmo_readiness():
    root = Path("/opt/ftmo-runner/_work/Ftmo/Ftmo")
    simulator = root / "src/prop_trading_ai/simulator/batch.py"
    two_step = root / "configs/prop_firms/ftmo_cfd_2step_standard_2026_09_15.json"
    prop_configs = root / "configs/prop_firms"
    one_step = next(iter(prop_configs.glob("*1step*.json")), None) if prop_configs.exists() else None
    canonical = root / "artifacts/ftmo_readiness/latest.json"

    raw = _json(canonical) if canonical.exists() else None
    runs = []
    if isinstance(raw, dict):
        source_runs = raw.get("runs") or raw.get("risk_scenarios") or []
        if isinstance(source_runs, list):
            for row in source_runs:
                if not isinstance(row, dict):
                    continue
                risk = row.get("risk_pct")
                if risk is None and row.get("risk_fraction") is not None:
                    try:
                        risk = float(row["risk_fraction"]) * 100
                    except Exception:
                        risk = None
                runs.append({
                    "program": row.get("program") or row.get("product") or "2-Step",
                    "risk_pct": round(float(risk), 2) if risk is not None else None,
                    "pass_rate": _as_pct(row.get("pass_rate") if row.get("pass_rate") is not None else row.get("challenge_pass_rate")),
                    "daily_loss_breach_rate": _as_pct(row.get("daily_loss_breach_rate")),
                    "total_loss_breach_rate": _as_pct(row.get("total_loss_breach_rate") if row.get("total_loss_breach_rate") is not None else row.get("max_loss_breach_rate")),
                    "max_drawdown_p95": _as_pct(row.get("max_drawdown_p95") if row.get("max_drawdown_p95") is not None else row.get("p95_max_drawdown")),
                    "median_days_to_target": row.get("median_days_to_target"),
                })

    measured = any(row.get("pass_rate") is not None for row in runs)
    return {
        "available": simulator.exists() or two_step.exists(),
        "measured": measured,
        "status": "measured" if measured else "pending",
        "simulator_ready": simulator.exists(),
        "two_step_configured": two_step.exists(),
        "one_step_configured": bool(one_step),
        "artifact": str(canonical) if canonical.exists() else None,
        "runs": runs,
        "planned_risk_pct": [0.10, 0.25, 0.50, 0.75],
        "note": (
            "Kandidaat-specifieke FTMO path test gemeten."
            if measured else
            "Simulator + 2-Step-regels zijn aanwezig; kandidaat-specifieke realistische FTMO-simulatietests/pass-rates zijn nog niet gegenereerd. Geen score wordt afgeleid uit alleen pips of win-rate."
        ),
    }

def _ftmo_quality():
    cand = _ftmo_candidate()
    rel = _ftmo_release()
    items = []
    headline = None
    meta = {}
    stage = "research"
    latest_point = None
    current_point = None
    if cand:
        gen, trial_path, trial, result, review = cand
        pips = _pips(result.get("total_pnl"))
        stressed = _pips(result.get("cost_1_5x_pnl"))
        wr = result.get("win_rate")
        trades = result.get("closed_trades")
        note = "Generation %s development-only · %s trades" % (gen, trades or 0)
        if wr is not None:
            note += " · %.1f%% WR" % (float(wr) * 100)
        headline = {"label": "Current dev candidate", "value": pips, "unit": " pips", "note": note}
        items.extend([
            {"label": "1.5× cost PnL", "value": stressed, "unit": " pips"},
            {"label": "Dev win rate", "value": round(float(wr) * 100, 1) if wr is not None else None, "unit": "%"},
            {"label": "Closed trades", "value": trades, "unit": ""},
        ])
        meta["candidate_hash"] = trial.get("trial_hash")
        meta["candidate_generation"] = gen
        latest_point = _comparison_point(
            "Nieuwste development candidate",
            ("Generation %s" % gen),
            note=("%s pips · development-only" % pips) if pips is not None else "Development-only; nog geen validated release",
            source=str(trial_path),
            validated=False,
        )
        stage = "generation %s candidate" % gen
    if rel:
        gen, release, released, holdout = rel
        hres = holdout.get("result") or holdout
        items.extend([
            {"label": "Validated holdout PnL", "value": _pips(hres.get("total_pnl")), "unit": " pips"},
            {"label": "Validated 1.5× cost", "value": _pips(hres.get("cost_1_5x_pnl")), "unit": " pips"},
            {"label": "Validated win rate", "value": round(float(hres.get("win_rate")) * 100, 1) if hres.get("win_rate") is not None else None, "unit": "%"},
        ])
        meta["release_hash"] = release.get("paper_release_hash")
        meta["release_generation"] = gen
        validated_pips = _pips(hres.get("total_pnl"))
        current_point = _comparison_point(
            "Huidige gevalideerde release",
            ("Generation %s" % gen),
            note=("%s pips · frozen holdout" % validated_pips) if validated_pips is not None else "Frozen-holdout release",
            source=holdout.get("_zcloud_source_path") or release.get("_zcloud_source_path") or release.get("paper_release_hash") or released.get("trial_hash"),
            validated=True,
        )
    return {
        "available": bool(headline),
        "headline": headline,
        "items": [x for x in items if x.get("value") is not None],
        "stage": stage,
        "meta": meta,
        "comparison": _comparison_view(latest=latest_point, current=current_point),
        "readiness": _ftmo_readiness(),
    }

class ProjectTelemetryAdapter:
    """Project-specific evidence adapter contract used by the generic dashboard renderer."""

    project_id = None

    def snapshot(self):
        raise NotImplementedError


class HaxLabTelemetryAdapter(ProjectTelemetryAdapter):
    project_id = "haxlab"

    def snapshot(self):
        return _hax_quality()


class FTMOTelemetryAdapter(ProjectTelemetryAdapter):
    project_id = "ftmo"

    def snapshot(self):
        return _ftmo_quality()


_TELEMETRY_ADAPTERS = {
    adapter.project_id: adapter
    for adapter in (HaxLabTelemetryAdapter(), FTMOTelemetryAdapter())
}


def telemetry_adapter_projects():
    return tuple(sorted(_TELEMETRY_ADAPTERS))


def _empty_quality_snapshot():
    return {
        "available": False,
        "headline": None,
        "items": [],
        "stage": None,
        "meta": {},
        "comparison": _comparison_view(),
    }


def quality_for(project):
    adapter = _TELEMETRY_ADAPTERS.get(project)
    return adapter.snapshot() if adapter else _empty_quality_snapshot()

def init_db(c):
    c.execute("""CREATE TABLE IF NOT EXISTS alerts(
        id TEXT PRIMARY KEY, ts TEXT, project TEXT, kind TEXT, severity TEXT,
        title TEXT, detail TEXT, important INTEGER, fingerprint TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS alerts_ts ON alerts(ts)")

def _emit(c, project, kind, severity, title, detail, fingerprint, cooldown=0):
    effective_fingerprint = fingerprint
    if cooldown:
        row = c.execute("SELECT ts FROM alerts WHERE project=? AND kind=? ORDER BY ts DESC LIMIT 1", (project, kind)).fetchone()
        if row:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(row[0])).total_seconds()
                if age < cooldown:
                    return False
            except Exception:
                pass
        effective_fingerprint = fingerprint + ":" + str(int(time.time() // cooldown))
    if c.execute("SELECT 1 FROM alerts WHERE fingerprint=?", (effective_fingerprint,)).fetchone():
        return False
    ts = _now()
    aid = hashlib.sha256((effective_fingerprint + "|" + ts).encode()).hexdigest()[:20]
    c.execute("INSERT INTO alerts VALUES(?,?,?,?,?,?,?,?,?)", (aid, ts, project, kind, severity, title, detail, 1, effective_fingerprint))
    return True

def emit_incident(db_path, project, kind, severity, title, detail, fingerprint, cooldown=0):
    try:
        with _db_connect(db_path) as c:
            init_db(c)
            created = _emit(c, str(project or "cloud"), str(kind or "incident"), str(severity or "warning"),
                            str(title or "Aandacht nodig"), str(detail or ""), str(fingerprint), cooldown)
            c.commit()
        return created
    except Exception:
        return False

def recovery_status(recovery_dir=None):
    root = Path(recovery_dir) if recovery_dir else RECOVERY_DIR
    latest = _json(root / "last-known-good.json") or {}
    events = []
    try:
        lines=(root / "recovery.log").read_text(encoding="utf-8").splitlines()[-200:]
        for line in lines:
            try:
                event=json.loads(line)
                if isinstance(event,dict):
                    events.append(event)
            except Exception:
                continue
    except Exception:
        pass
    rollback_events=[e for e in events if str(e.get("event") or "").startswith("rollback_")]
    last_rollback=rollback_events[-1] if rollback_events else None
    failed=bool(last_rollback and "failed" in str(last_rollback.get("event") or ""))
    available=bool(latest.get("snapshot_id"))
    if failed:
        status="problem"; label="Herstelactie had een fout"
    elif last_rollback and last_rollback.get("event")=="rollback_succeeded":
        status="tested"; label="Herstelpad getest"
    elif last_rollback and last_rollback.get("event")=="rollback_started":
        status="in_progress"; label="Herstelactie loopt"
    elif available:
        status="ready"; label="Herstelpunt klaar"
    else:
        status="missing"; label="Geen herstelpunt beschikbaar"
    return {
        "available": available,
        "status": status,
        "label": label,
        "snapshot_id": latest.get("snapshot_id"),
        "updated_at": latest.get("updated_at"),
        "last_rollback": last_rollback,
    }

_INCIDENT_META = {
    "claim_conflict": {
        "type": "claim_conflict",
        "title": "Taakclaim botst",
        "cause": "Deze taak is al door een andere worker geclaimd.",
        "impact": "Dubbelwerk is voorkomen; deze worker kan deze taak nu niet veilig overnemen.",
        "action": "Laat de huidige eigenaar doorgaan of kies een andere vrije taak.",
        "ttl": 30 * 60,
    },
    "stale_handoff": {
        "type": "stale_handoff",
        "title": "Projecthandoff is verouderd",
        "cause": "De handoff loopt achter op recent projectwerk.",
        "impact": "Een nieuwe worker kan starten met verouderde projectinformatie.",
        "action": "Werk de handoff bij vóór de volgende zelfstandige projectstap.",
        "ttl": 24 * 3600,
    },
    "deploy_failed": {
        "type": "deploy_failed",
        "title": "Deploy is mislukt",
        "cause": "De laatste deploy kon niet veilig worden afgerond.",
        "impact": "De live versie kan achterlopen of extra controle nodig hebben.",
        "action": "Gebruik de last-known-good status en herstel of herhaal pas na een groene check.",
        "ttl": 24 * 3600,
    },
    "recovery_failed": {
        "type": "recovery_failed",
        "title": "Herstelactie heeft aandacht nodig",
        "cause": "Een rollback of herstelactie is niet schoon afgerond.",
        "impact": "De betrouwbaarheid van de live state moet opnieuw worden bevestigd.",
        "action": "Controleer recovery.log en valideer service, API en project/chat mapping.",
        "ttl": 24 * 3600,
    },
}

def _incident_from_alert(alert, recovery, now_ts):
    kind=str(alert.get("kind") or "").strip().lower().replace("-", "_")
    if kind=="breakthrough":
        return None
    if kind=="automation" and "stil" in str(alert.get("title") or "").lower():
        # Current runner state is a better source for stale/offline workers.
        return None
    meta=_INCIDENT_META.get(kind)
    if not meta:
        return None
    try:
        age=(now_ts-datetime.fromisoformat(str(alert.get("ts")))).total_seconds()
    except Exception:
        age=0
    if age > meta["ttl"]:
        return None
    severity=str(alert.get("severity") or "warning").lower()
    return {
        "id": str(alert.get("id") or hashlib.sha256((kind+str(alert.get("ts"))).encode()).hexdigest()[:16]),
        "project": str(alert.get("project") or "cloud"),
        "type": meta["type"],
        "severity": severity,
        "title": meta["title"],
        "cause": meta["cause"],
        "impact": meta["impact"],
        "health": "Kritiek" if severity in ("critical","error") else "Aandacht",
        "action": meta["action"],
        "detected_at": alert.get("ts"),
        "technical_detail": str(alert.get("detail") or ""),
        "rollback": recovery,
    }

def _claim_key_from_alert(alert):
    detail=str((alert or {}).get("detail") or "").strip()
    first=detail.split(" · ",1)[0].strip()
    return first if ":" in first and len(first) >= 4 else None

def _claim_conflict_is_active(db_path, alert, now_ts):
    claim_key=_claim_key_from_alert(alert)
    if not claim_key:
        # Backward-compatible for legacy alerts without structured claim detail.
        return True
    try:
        with _db_connect(db_path) as c:
            c.row_factory=sqlite3.Row
            if not c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_claims'"
            ).fetchone():
                return True
            row=c.execute(
                "SELECT lease_until FROM task_claims WHERE project_id=? AND claim_key=?",
                (str(alert.get("project") or "cloud"),claim_key),
            ).fetchone()
        if not row:
            return False
        try:
            return datetime.fromisoformat(str(row["lease_until"])).astimezone(timezone.utc) > now_ts
        except Exception:
            return True
    except Exception:
        # Attention filtering must never hide an issue because claim-state lookup failed.
        return True

def incident_center(db_path, runners=None, recovery_dir=None, limit=6, data=None):
    recovery=recovery_status(recovery_dir)
    now_ts=datetime.now(timezone.utc)
    items=[]
    for alert in list_alerts(db_path,80,False):
        if str(alert.get("kind") or "").strip().lower().replace("-","_")=="claim_conflict":
            if not _claim_conflict_is_active(db_path,alert,now_ts):
                continue
        incident=_incident_from_alert(alert,recovery,now_ts)
        if incident:
            items.append(incident)
    for project in (data or {}).get("projects", []):
        if project.get("health")=="healthy" or project.get("status")=="archived":
            continue
        bad=next((x for x in project.get("services",[]) if x.get("state") not in ("active","activating","waiting")),None) or {}
        service=str(bad.get("name") or "vereiste service")
        state=str(bad.get("state") or "aandacht")
        items.append({
            "id": "service:%s:%s" % (project.get("id"),service),
            "project": str(project.get("id") or "cloud"),
            "type": "service_health",
            "severity": "high",
            "title": "Projectservice heeft aandacht nodig",
            "cause": "%s staat op %s." % (service,state),
            "impact": "Projectwerk kan vertragen of tijdelijk stoppen.",
            "health": "Aandacht",
            "action": "Controleer deze service en herstel alleen als de oorzaak duidelijk is.",
            "detected_at": (bad.get("last_run") or (data or {}).get("time")),
            "technical_detail": "%s · state=%s · result=%s" % (service,state,bad.get("result")),
            "rollback": recovery,
        })
    for project,status in (runners or {}).items():
        for worker in status.get("workers") or []:
            state=str(worker.get("state") or "")
            age=worker.get("age_seconds")
            if state not in ("stalled","offline","stale"):
                continue
            if state=="stale" and (age is None or age < 180):
                continue
            label="Geen tekstvoortgang" if state=="stalled" else "Worker niet verbonden" if state=="offline" else "Worker is te lang niet actief geweest"
            items.append({
                "id": "worker:%s" % str(worker.get("worker_id") or project),
                "project": project,
                "type": "stale_worker",
                "severity": "high" if state in ("stalled","offline") else "warning",
                "title": label,
                "cause": "Deze worker stuurt geen recente gezonde voortgang meer.",
                "impact": "De taak van deze worker kan stilstaan terwijl het project actief lijkt.",
                "health": "Aandacht",
                "action": "Controleer de workerstatus; herstart alleen deze worker als hij echt vastzit.",
                "detected_at": (worker.get("last_event") or {}).get("time"),
                "technical_detail": "state=%s · age_seconds=%s" % (state,age),
                "rollback": recovery,
            })
    if recovery.get("status")=="problem":
        items.append({
            "id": "recovery:last-failed",
            "project": "cloud",
            "type": "recovery_failed",
            "severity": "critical",
            "title": "Herstelactie heeft aandacht nodig",
            "cause": "De meest recente rollback eindigde niet schoon.",
            "impact": "De live state moet worden geverifieerd vóór een nieuwe deploy.",
            "health": "Kritiek",
            "action": "Controleer recovery.log en bevestig service, API en project/chat mapping.",
            "detected_at": (recovery.get("last_rollback") or {}).get("time"),
            "technical_detail": json.dumps(recovery.get("last_rollback") or {},ensure_ascii=False,separators=(",",":")),
            "rollback": recovery,
        })
    priority={"critical":0,"error":0,"high":1,"warning":2}
    dedup={}
    for item in items:
        key=(item["project"],item["type"],item["title"])
        old=dedup.get(key)
        if old is None or str(item.get("detected_at") or "") > str(old.get("detected_at") or ""):
            dedup[key]=item
    ordered=list(dedup.values())
    ordered.sort(key=lambda x:str(x.get("detected_at") or ""),reverse=True)
    ordered.sort(key=lambda x:priority.get(str(x.get("severity") or "").lower(),3))
    return {"count":len(ordered),"items":ordered[:max(1,int(limit))],"recovery":recovery}

def list_alerts(db_path, limit=20, important_only=False):
    try:
        with _db_connect(db_path) as c:
            c.row_factory = sqlite3.Row
            where = "WHERE important=1" if important_only else ""
            rows = c.execute("SELECT * FROM alerts %s ORDER BY ts DESC LIMIT ?" % where, (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []

def _state_for(data):
    state = {"milestones": [], "quality": {}}
    for p in data.get("projects", []):
        for i, m in enumerate(p.get("milestones", [])):
            value = float(m.get("progress", 100 if m.get("done") else 0))
            if value >= 100:
                state["milestones"].append("%s:%s:%s" % (p["id"], i, p.get("milestone_revision", "")))
        q = p.get("quality") or {}
        state["quality"][p["id"]] = q.get("meta") or {}
    return state

def evaluate_alerts(data, runner, db_path):
    current = _state_for(data)
    try:
        previous = json.loads(ALERT_STATE_FILE.read_text())
    except Exception:
        previous = None
    with _db_connect(db_path) as c:
        init_db(c)
        for p in data.get("projects", []):
            if p.get("status") == "archived":
                continue
            if p.get("health") != "healthy":
                bad = next((s for s in p.get("services", []) if s.get("state") not in ("active", "activating", "waiting")), None)
                detail = (bad or {}).get("name", "service") + " · " + (bad or {}).get("state", "attention")
                _emit(c, p["id"], "freeze", "high", p["name"] + " heeft aandacht nodig", detail, "health:" + p["id"] + ":" + detail, 3 * 3600)
        if runner.get("state") in ("stale", "offline") and (runner.get("age_seconds") or 0) > 300:
            _emit(c, "cloud", "automation", "high", "ChatGPT automation lijkt stil te staan",
                  "Runner %s · laatste event %ss geleden" % (runner.get("state"), runner.get("age_seconds")),
                  "runner-stale", 2 * 3600)
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        blocked = c.execute("""SELECT reason,COUNT(*) c FROM runner_events
            WHERE event='startup-blocked' AND ts>=? AND reason NOT IN ('generation-active','draft-present','')
            GROUP BY reason HAVING c>=3 ORDER BY c DESC LIMIT 1""", (cutoff,)).fetchone()
        if blocked:
            _emit(c, "cloud", "input", "high", "Automatisering blijft geblokkeerd",
                  "%s · %sx in 30 min" % (blocked[0], blocked[1]), "blocked:" + str(blocked[0]), 2 * 3600)
        if previous:
            old = set(previous.get("milestones", []))
            for key in set(current["milestones"]) - old:
                pid = key.split(":", 1)[0]
                p = next((x for x in data.get("projects", []) if x["id"] == pid), None)
                if p and p.get("status") != "archived":
                    idx = int(key.split(":")[1])
                    title = p.get("milestones", [{}])[idx].get("title", "Milestone")
                    _emit(c, pid, "breakthrough", "normal", p["name"] + ": milestone afgerond", title, "milestone:" + key)
            oldq = previous.get("quality", {})
            hnew = (current["quality"].get("haxlab") or {}).get("version_id")
            hold = (oldq.get("haxlab") or {}).get("version_id")
            if hnew and hold and hnew != hold:
                _emit(c, "haxlab", "breakthrough", "high", "HaxLab heeft een nieuwe live champion", str(hnew), "hax-version:" + str(hnew))
            fnew = (current["quality"].get("ftmo") or {}).get("candidate_hash")
            fold = (oldq.get("ftmo") or {}).get("candidate_hash")
            if fnew and fold and fnew != fold:
                q = next((p.get("quality") for p in data["projects"] if p["id"] == "ftmo"), {}) or {}
                h = q.get("headline") or {}
                detail = "%s%s · %s" % (h.get("value", "?"), h.get("unit", ""), h.get("note", ""))
                _emit(c, "ftmo", "breakthrough", "high", "FTMO heeft een nieuwe research-candidate", detail, "ftmo-candidate:" + str(fnew))
            rnew = (current["quality"].get("ftmo") or {}).get("release_hash")
            rold = (oldq.get("ftmo") or {}).get("release_hash")
            if rnew and rold and rnew != rold:
                _emit(c, "ftmo", "breakthrough", "high", "FTMO heeft een nieuwe gevalideerde paper release",
                      "Nieuwe frozen-holdout release is vrijgegeven voor paper execution.", "ftmo-release:" + str(rnew))
        for path in SIGNALS_DIR.glob("*.json"):
            sig = _json(path) or {}
            sid = str(sig.get("id") or path.stem)
            _emit(c, str(sig.get("project") or "cloud"), str(sig.get("kind") or "input"),
                  str(sig.get("severity") or "high"), str(sig.get("title") or "Input nodig"),
                  str(sig.get("detail") or ""), "signal:" + sid)
        c.commit()
    ALERT_STATE_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n")
