import contextlib, datetime as dt, json, os, sqlite3
PATH = os.environ.get("TRIAGE_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "triage.db"))
SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY AUTOINCREMENT, service TEXT, symptom TEXT, severity TEXT,
  status TEXT DEFAULT 'OPEN', report TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS steps(id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INT, idx INT, tool TEXT, args TEXT,
  thought TEXT, observation TEXT, result TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS remediations(id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INT, action TEXT, target TEXT,
  status TEXT DEFAULT 'PENDING_APPROVAL', created_at TEXT);
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INT, ts TEXT, actor TEXT, event TEXT, detail TEXT);
"""
now = lambda: dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _c():
    c = sqlite3.connect(PATH, timeout=10); c.row_factory = sqlite3.Row; return c
def run(sql, args=()):
    with contextlib.closing(_c()) as c, c: return c.execute(sql, args).lastrowid
def rows(sql, args=()):
    with contextlib.closing(_c()) as c: return [dict(r) for r in c.execute(sql, args)]
def init():
    with contextlib.closing(_c()) as c, c: c.executescript(SCHEMA)

def create_incident(service, symptom, severity):
    t = now(); i = run("INSERT INTO incidents(service,symptom,severity,created_at,updated_at) VALUES(?,?,?,?,?)", (service, symptom, severity, t, t))
    audit(i, "system", "alert_received", f"{service}: {symptom}"); return i
def set_status(i, s): run("UPDATE incidents SET status=?, updated_at=? WHERE id=?", (s, now(), i))
def set_report(i, rep): run("UPDATE incidents SET report=? WHERE id=?", (json.dumps(rep), i))
def reset_investigation(i):
    with contextlib.closing(_c()) as c, c:
        c.execute("DELETE FROM steps WHERE incident_id=?", (i,))
        c.execute("DELETE FROM remediations WHERE incident_id=?", (i,))
        c.execute("UPDATE incidents SET report=NULL, status='INVESTIGATING', updated_at=? WHERE id=?", (now(), i))
def add_step(i, idx, tool, args, thought, obs, result):
    run("INSERT INTO steps(incident_id,idx,tool,args,thought,observation,result,ts) VALUES(?,?,?,?,?,?,?,?)",
        (i, idx, tool, json.dumps(args), thought, obs, json.dumps(result), now()))
def add_remediation(i, action, target):
    return run("INSERT INTO remediations(incident_id,action,target,created_at) VALUES(?,?,?,?)", (i, action, target, now()))
def set_remediation_status(rid, s): run("UPDATE remediations SET status=? WHERE id=?", (s, rid))
def audit(i, actor, event, detail=""): run("INSERT INTO audit_log(incident_id,ts,actor,event,detail) VALUES(?,?,?,?,?)", (i, now(), actor, event, detail))

def list_incidents():
    return rows("SELECT id,service,symptom,severity,status,created_at FROM incidents ORDER BY id DESC")
def get_incident(i):
    r = rows("SELECT * FROM incidents WHERE id=?", (i,))
    if not r: return None
    inc = r[0]; inc["report"] = json.loads(inc["report"]) if inc["report"] else None
    inc["steps"] = rows("SELECT * FROM steps WHERE incident_id=? ORDER BY idx", (i,))
    for s in inc["steps"]: s["args"], s["result"] = json.loads(s["args"]), json.loads(s["result"])
    inc["remediation"] = (rows("SELECT * FROM remediations WHERE incident_id=? ORDER BY id DESC", (i,)) or [None])[0]
    inc["audit"] = rows("SELECT * FROM audit_log WHERE incident_id=? ORDER BY id", (i,))
    return inc