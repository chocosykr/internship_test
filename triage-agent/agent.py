"""Bounded, read-only tool-calling agent with two planners behind the same tools, step cap and guardrails:
  - LLM planner (set ANTHROPIC_API_KEY, GEMINI_API_KEY or OPENAI_API_KEY): the model chooses tools and writes the report; evidence is verified against what the tools returned.
  - Rule planner (default / automatic fallback): deterministic, no API key needed.
Env: LLM_PROVIDER (anthropic|gemini|openai; auto-detected from the key), LLM_MODEL, OPENAI_BASE_URL (any OpenAI-compatible host),
LLM_MIN_INTERVAL (seconds between calls, for free-tier rate limits), AGENT_MODE=rules to force the rule planner, STEP_DELAY."""
import datetime as dt, json, os, re, time, urllib.error, urllib.request
import db

_D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
def _load(n):
    with open(os.path.join(_D, n)) as f: return json.load(f)
LOGS, DEPLOYS, _M = _load("logs.json"), _load("deploys.json"), _load("metrics.json")
SERIES, NOW = _M["series"], _M["now"]
SERVICES = list(SERIES)
MAX_STEPS = 8                                     # hard cap, includes the final propose_remediation call
STEP_DELAY = float(os.environ.get("STEP_DELAY", "0.9"))   # slows steps so the UI timeline visibly streams
_KEY = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}
PROVIDER = os.environ.get("LLM_PROVIDER", "").lower() or next((p for p, k in _KEY.items() if os.environ.get(k)), "")
MODEL = os.environ.get("LLM_MODEL") or (os.environ.get("ANTHROPIC_MODEL") if PROVIDER == "anthropic" else "") or {"anthropic": "claude-sonnet-5-5", "gemini": "gemini-3.8-flash"}.get(PROVIDER, "")
ALLOWED_ACTIONS = {"rollback_deploy", "restart_pod", "scale_up"}
LOOKBACK_MIN = 120                                # deploys up to 2h before onset are candidates (delayed-onset faults)
SIG = "pool|timeout|timed out|memory|oom|refused|deadlock"
EXTERNAL = re.compile(r"provider|dns|tls|certificate|outage|throttl|rate.?limit|quota|refused", re.I)
F = "%Y-%m-%dT%H:%M:%S"
_minus = lambda mins: (dt.datetime.strptime(NOW, F) - dt.timedelta(minutes=mins)).strftime(F)
_mins = lambda a, b: (dt.datetime.strptime(b, F) - dt.datetime.strptime(a, F)).total_seconds() / 60
llm_enabled = lambda: bool(os.environ.get(_KEY.get(PROVIDER, ""), "")) and os.environ.get("AGENT_MODE", "auto") != "rules"

TOOL_SCHEMAS = [
 {"name": "get_metrics", "description": "CPU, memory, p99 latency and 5xx rate for a service vs its baseline, with anomaly onset time.",
  "parameters": {"type": "object", "properties": {"service": {"type": "string", "enum": SERVICES}, "window": {"type": "integer", "description": "minutes"}}, "required": ["service"]}},
 {"name": "search_logs", "description": "Filter structured logs by service, regex and level. Returns total matches and the first lines.",
  "parameters": {"type": "object", "properties": {"service": {"type": "string", "enum": SERVICES}, "query": {"type": "string"}, "level": {"type": "string", "enum": ["ERROR", "WARN", "INFO"]}}, "required": ["service"]}},
 {"name": "list_deploys", "description": "Recent deployments for a service (version, commit, author, time, change set).",
  "parameters": {"type": "object", "properties": {"service": {"type": "string", "enum": SERVICES}, "limit": {"type": "integer"}}, "required": ["service"]}},
 {"name": "propose_remediation", "description": "Register a fix as a PENDING ticket. Never executes; a human must approve. One per incident.",
  "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": sorted(ALLOWED_ACTIONS)}, "target": {"type": "string", "description": "e.g. payment-service@v2.14.0 for rollback_deploy, or a service name"}}, "required": ["action", "target"]}}]

def _anom(k, v, base):
    if k in ("cpu", "mem"): return v >= 85 or v >= 1.4 * base
    if k == "p99_ms": return v >= 2 * base
    return v >= 2 and v >= 3 * base                  # err_pct

def get_metrics(service, window=30):
    pts = SERIES.get(service)
    if not pts: return {"error": f"unknown service {service}"}
    cut = _minus(window); base = [p for p in pts if p["ts"] < cut] or pts[:10]; rec = [p for p in pts if p["ts"] >= cut]
    out, anoms, onset = {}, [], None
    for k in ("cpu", "mem", "p99_ms", "err_pct"):
        b = sum(p[k] for p in base) / len(base); pk = max(rec, key=lambda p: p[k])
        bad = [p["ts"] for p in rec if _anom(k, p[k], b)]
        out[k] = {"baseline": round(b, 1), "recent_avg": round(sum(p[k] for p in rec) / len(rec), 1), "peak": pk[k], "peak_ts": pk["ts"], "anomalous": bool(bad)}
        if bad: anoms.append(k); onset = min(onset or bad[0], bad[0])
    return {"service": service, "window_min": window, "metrics": out, "anomalies": anoms, "onset": onset}

def search_logs(service, query="", level=None, limit=5, window=60):
    cut = _minus(window); rx = re.compile(query, re.I) if query else None
    hits = [l for l in LOGS if l["service"] == service and l["ts"] >= cut and (not level or l["level"] == level) and (not rx or rx.search(l["message"]))]
    return {"service": service, "query": query, "level": level, "total": len(hits), "lines": hits[:limit]}

def list_deploys(service, limit=3):
    d = sorted((x for x in DEPLOYS if x["service"] == service and x["ts"] <= NOW), key=lambda x: x["ts"], reverse=True)
    return {"service": service, "deploys": d[:limit]}

def propose_remediation(iid, action, target):    # registers only; execution lives in execute_remediation (approval endpoint)
    if action not in ALLOWED_ACTIONS: return {"error": f"action '{action}' is not allowed"}
    return {"ticket_id": db.add_remediation(iid, action, target), "status": "PENDING_APPROVAL", "action": action, "target": target}

def execute_remediation(rem):                    # SIMULATED executor: only called from POST /approve after a human click
    return f"[simulated] {rem['action']} executed on {rem['target']}"

TOOLS = {"get_metrics": get_metrics, "search_logs": search_logs, "list_deploys": list_deploys}

def describe(tool, r):
    if "error" in r: return "Tool error: " + r["error"]
    if tool == "get_metrics":
        if not r["anomalies"]: return "All metrics within baseline."
        return "Anomalous: " + ", ".join(f'{k} peak {r["metrics"][k]["peak"]} (baseline {r["metrics"][k]["baseline"]})' for k in r["anomalies"]) + f"; onset {r['onset']}"
    if tool == "search_logs":
        if not r["total"]: return f'No {r["level"] or "matching"} log lines.'
        return f'{r["total"]} {r["level"] or "matching"} lines; first: {r["lines"][0]["message"]}'
    if tool == "list_deploys":
        d = r["deploys"]; return f'{len(d)} recent deploys; latest {d[0]["version"]} at {d[0]["ts"]}' if d else "No deploys on record."
    return f'Ticket #{r["ticket_id"]} registered, awaiting human approval.'

_line = lambda l: f'{l["ts"]} {l["level"]} {l["service"]} {l["message"]} (trace_id={l["trace_id"]})'
_STOP = {"from", "with", "that", "this", "enabled", "added", "reduced", "bump", "update", "upgrade", "fix"}
_terms = lambda ch: {w for w in re.findall(r"[a-z]{4,}", ch.lower()) if w not in _STOP}

def rank(obs):
    """Rule planner's ranking. A deploy is blamed only if its change set shares terms with the error signature, so the
    'latest deploy before onset' decoy is not blamed on timing alone. Deploys up to LOOKBACK_MIN before onset are considered."""
    H = []
    for s, o in obs.items():
        m, e, d = o.get("metrics"), o.get("errors"), o.get("deploys")
        if not m or not m.get("anomalies"): continue
        errs = e["lines"] if e and e.get("level") == "ERROR" and e.get("total") else []
        if not errs: continue                      # anomaly without error logs is not enough evidence
        txt = " ".join(l["message"] for l in errs).lower()
        ev = [{"kind": "metric", "text": f'{s} {k}: baseline {m["metrics"][k]["baseline"]}, peak {m["metrics"][k]["peak"]} at {m["metrics"][k]["peak_ts"]}'} for k in m["anomalies"]]
        ev += [{"kind": "log", "text": _line(l)} for l in errs[:2]]
        up = re.search(r"upstream ([a-z-]+)", txt); up = up.group(1) if up else None
        um = (obs.get(up, {}).get("metrics") or {}) if up else {}
        cands = [x for x in (d or {}).get("deploys", []) if 0 <= _mins(x["ts"], m["onset"]) <= LOOKBACK_MIN]
        scored = sorted(((x, sorted(w for w in _terms(x["changes"]) if w in txt)) for x in cands), key=lambda t: (len(t[1]), t[0]["ts"]), reverse=True)
        hit = scored[0] if scored and scored[0][1] else None
        clear = [{"kind": "deploy", "text": f'Not implicated: {x["version"]} ({x["commit"]}) "{x["changes"]}" shares no terms with the error signature.'} for x, w in scored if not w]
        h = {"service": s, "evidence": ev, "remediation": None}
        if um.get("anomalies"):
            h.update(kind="cascade", confidence=55 + (10 if um["onset"] <= m["onset"] else 0), title=f"Cascading failure: {s} is failing because upstream {up} is unhealthy",
                     explanation=f"{s} errors name {up} as the failing dependency, and {up} showed anomalies starting at {um['onset']}, before or alongside {s}. Fix the upstream cause rather than {s} itself.")
        elif hit:
            dep, words = hit; gap = _mins(dep["ts"], m["onset"])
            h.update(kind="deploy_regression", confidence=55 + (10 if gap <= 15 else 5 if gap <= 60 else 0) + 25, title=f"Regression introduced by {dep['version']} deploy on {s}",
                     explanation=f"{s} anomalies began {gap:.0f} min after deploy {dep['version']} ({dep['commit']}). Its change set ({dep['changes']}) matches the error signature (terms: {', '.join(words)})."
                                 + (" Later deploys that do not match are treated as decoys." if clear else ""),
                     remediation={"action": "rollback_deploy", "target": f"{s}@{dep['version']}"})
            h["evidence"] = [{"kind": "deploy", "text": f'{dep["ts"]} {dep["version"]} ({dep["commit"]}) by {dep["author"]}: {dep["changes"]}'}] + ev + clear
        elif EXTERNAL.search(txt):
            h.update(kind="external", confidence=70, title=f"No code cause: external dependency failure affecting {s}",
                     explanation=f"Errors on {s} point at an external provider or dependency. No deploy in the {LOOKBACK_MIN} min before onset matches the failure signature, so a rollback would not help. Escalate to the provider or fail over.")
            h["evidence"] = ev + clear
        elif {"cpu", "mem"} & set(m["anomalies"]):
            h.update(kind="saturation", confidence=45 + (15 if re.search(r"memory|oom|cpu", txt) else 0) + 10, title=f"Resource saturation on {s}",
                     explanation=f"{s} resource metrics climbed from {m['onset']} with matching error logs and no matching deploy, which points at a leak or unhealthy instance.",
                     remediation={"action": "restart_pod", "target": s})
            h["evidence"] = ev + clear
        else: continue
        H.append(h)
    H.sort(key=lambda h: -h["confidence"]); return H[:3]

SYSTEM = """You are an on-call SRE agent investigating one production incident using read-only tools over metrics, logs and deploy history.
Method: start from the alert, query narrowly, follow dependencies named in errors, and use at most 7 tool calls before submit_report.
Rules:
- Do NOT blame the most recent deploy by default. Compare every candidate deploy's change set with the error signature; faults can detonate many minutes after the bad deploy, and decoy deploys often land right before onset.
- If errors point to an external provider or other non-code cause, set verdict NO_CODE_CAUSE and propose no remediation.
- If evidence is empty, unrelated or thin, set verdict INSUFFICIENT_EVIDENCE and do not invent a cause.
- Rank 1-3 hypotheses with calibrated confidence (0-100). Evidence entries of kind "log" must be copied verbatim from tool results.
- Call propose_remediation at most once, only for the top code or operational cause. It never executes; a human approves. Never claim a fix was applied.
Finish by calling submit_report."""
SUBMIT = {"name": "submit_report", "description": "Final ranked root-cause report.", "input_schema": {"type": "object", "properties": {
  "verdict": {"type": "string", "enum": ["ROOT_CAUSE_FOUND", "NO_CODE_CAUSE", "INSUFFICIENT_EVIDENCE"]}, "summary": {"type": "string"},
  "hypotheses": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}, "confidence": {"type": "integer"}, "explanation": {"type": "string"},
    "evidence": {"type": "array", "items": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["metric", "log", "deploy"]}, "text": {"type": "string"}}, "required": ["kind", "text"]}}},
    "required": ["title", "confidence", "explanation", "evidence"]}}}, "required": ["verdict", "hypotheses"]}}

_last = [0.0]
def _http(url, headers, body):
    gap = float(os.environ.get("LLM_MIN_INTERVAL", "0"))
    for attempt in range(4):                       # polite pacing + retry on rate limit / overload (free tiers)
        time.sleep(max(0, _last[0] + gap - time.time())); _last[0] = time.time()
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"content-type": "application/json", **headers})
            with urllib.request.urlopen(req, timeout=90) as r: return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 3: time.sleep(5 * (attempt + 1)); continue
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:300]}")

def _all_tools(): return TOOL_SCHEMAS + [{"name": SUBMIT["name"], "description": SUBMIT["description"], "parameters": SUBMIT["input_schema"]}]

class _Anthropic:                                   # Anthropic Messages API
    def __init__(s): s.msgs = []; s.tools = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in _all_tools()]
    def user(s, text): s.msgs.append({"role": "user", "content": text})
    def turn(s):
        c = _http("https://api.anthropic.com/v1/messages", {"anthropic-version": "2023-06-01", "x-api-key": os.environ["ANTHROPIC_API_KEY"]},
                  {"model": MODEL, "max_tokens": 1500, "system": SYSTEM, "messages": s.msgs, "tools": s.tools})["content"]
        s.msgs.append({"role": "assistant", "content": c})
        return " ".join(b["text"] for b in c if b["type"] == "text").strip(), [{"id": b["id"], "name": b["name"], "input": b.get("input") or {}} for b in c if b["type"] == "tool_use"]
    def results(s, res): s.msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": i, "content": o} for i, o in res]})

class _OpenAI:                                      # OpenAI-compatible chat completions: Gemini, OpenAI, Groq, OpenRouter, ...
    def __init__(s): s.msgs = [{"role": "system", "content": SYSTEM}]; s.tools = [{"type": "function", "function": t} for t in _all_tools()]
    def user(s, text): s.msgs.append({"role": "user", "content": text})
    def turn(s):
        base = "https://generativelanguage.googleapis.com/v1beta/openai" if PROVIDER == "gemini" else os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        msg = _http(base + "/chat/completions", {"authorization": "Bearer " + os.environ[_KEY[PROVIDER]]}, {"model": MODEL, "messages": s.msgs, "tools": s.tools, "tool_choice": "auto"})["choices"][0]["message"]
        for n, c in enumerate(msg.get("tool_calls") or []): c["id"] = c.get("id") or f"call_{len(s.msgs)}_{n}"
        s.msgs.append(msg)                          # echoed back unchanged, so provider extras (e.g. Gemini thought signatures) survive
        calls = []
        for c in msg.get("tool_calls") or []:
            try: a = json.loads(c["function"].get("arguments") or "{}")
            except ValueError: a = {}
            calls.append({"id": c["id"], "name": c["function"]["name"], "input": a if isinstance(a, dict) else {}})
        return (msg.get("content") or "").strip(), calls
    def results(s, res): s.msgs += [{"role": "tool", "tool_call_id": i, "content": o} for i, o in res]

_pct = lambda v: max(0, min(100, int(float(str(v).strip().rstrip("%") or 0))))
def _norm(sub, st):
    """Turn the model's report into ours; drop evidence that the tools never returned (anti-hallucination guardrail)."""
    H = []
    for h in (sub.get("hypotheses") or [])[:3]:
        ev = []
        for e in h.get("evidence") or []:
            t, k = str(e.get("text", "")), e.get("kind", "log")
            ok = (any(m in t or (len(t) > 15 and t in m) for m in st["logs"]) if k == "log"
                  else any(d["commit"] in t or d["version"] in t for d in st["deps"]) if k == "deploy" else True)
            if t and ok: ev.append({"kind": k, "text": t})
        if ev: H.append({"title": str(h.get("title", ""))[:140], "confidence": _pct(h.get("confidence", 0)), "explanation": str(h.get("explanation", "")), "evidence": ev, "remediation": None, "kind": "llm", "service": ""})
    H.sort(key=lambda h: -h["confidence"]); v = sub.get("verdict")
    if not H or v == "INSUFFICIENT_EVIDENCE": return {"verdict": "INSUFFICIENT_EVIDENCE", "hypotheses": [], "summary": "Insufficient evidence. " + str(sub.get("summary", ""))}
    for i, h in enumerate(H, 1): h["rank"] = i
    if st["tickets"]: t = st["tickets"][-1]; H[0]["remediation"] = {"action": t["action"], "target": t["target"], "ticket_id": t["ticket_id"]}
    return {"verdict": "NO_CODE_CAUSE" if v == "NO_CODE_CAUSE" else "ROOT_CAUSE_FOUND", "summary": str(sub.get("summary") or H[0]["title"]), "hypotheses": H}

def _run_llm(inc, call, st):
    if not MODEL: raise RuntimeError("set LLM_MODEL for this provider")
    c = _Anthropic() if PROVIDER == "anthropic" else _OpenAI()
    c.user(f"Alert on {inc['service']} (severity {inc['severity']}): {inc['symptom']}. Current time is {NOW}. Investigate, then call submit_report.")
    sub = None
    for _ in range(MAX_STEPS + 3):
        thought, calls = c.turn()
        if not calls: break
        res = []
        for b in calls:
            if b["name"] == "submit_report": sub = b["input"]; out = "Report received."
            else:
                r = call(b["name"], thought or f"Calling {b['name']}.", final=(b["name"] == "propose_remediation"), **b["input"])
                out = json.dumps(r) if r is not None else "Step budget exhausted. Call submit_report now."
            res.append((b["id"], out))
        c.results(res)
        if sub: break
    if sub is None: raise RuntimeError("model never called submit_report")
    return _norm(sub, st)

def investigate(iid):
    inc = db.get_incident(iid); svc = inc["service"]
    db.set_status(iid, "INVESTIGATING"); db.audit(iid, "agent", "investigation_started", inc["symptom"])
    st, obs = {"n": 0, "tickets": [], "logs": [], "deps": []}, {}
    def call(tool, thought, final=False, **args):
        if st["n"] >= (MAX_STEPS if final else MAX_STEPS - 1): return None     # hard step cap
        st["n"] += 1; time.sleep(STEP_DELAY)
        try:
            if tool == "propose_remediation":
                r = {"error": "a remediation is already registered for this incident"} if st["tickets"] else propose_remediation(iid, **args)
                if "ticket_id" in r: st["tickets"].append(r)
            else: r = TOOLS[tool](**args)
        except Exception as ex: r = {"error": str(ex)}
        st["logs"] += [l["message"] for l in r.get("lines", [])]; st["deps"] += r.get("deploys", [])
        db.add_step(iid, st["n"], tool, args, thought, describe(tool, r), r); return r
    def look(s, why):
        o = obs.setdefault(s, {})
        o["metrics"] = call("get_metrics", f"{why} Checking CPU, memory, p99 latency and 5xx rate for {s}.", service=s, window=30)
        o["errors"] = call("search_logs", f"Pulling ERROR logs for {s} to see what is failing.", service=s, query="", level="ERROR")
        if o["errors"] and o["errors"].get("total") == 0:
            o["errors"] = call("search_logs", f"No ERROR lines for {s}; widening to WARN.", service=s, query="", level="WARN")
        o["deploys"] = call("list_deploys", f"Checking recent deploys of {s}, including ones well before the anomaly.", service=s, limit=5)
    def rules():
        look(svc, f"Alert: {inc['symptom']}.")
        up = None
        for l in (obs[svc].get("errors") or {}).get("lines", []):
            mt = re.search(r"upstream ([a-z-]+)", l["message"])
            if mt and mt.group(1) in SERIES and mt.group(1) not in obs: up = mt.group(1); break
        if up: look(up, f"{svc} errors point to upstream {up}; following the dependency.")
        H = rank(obs)
        if not H:
            return {"verdict": "INSUFFICIENT_EVIDENCE", "hypotheses": [],
                    "summary": f"Insufficient evidence. Checked metrics, logs and deploys for {', '.join(obs)}: no metric anomaly is backed by ERROR logs and a matching cause, so no root cause is proposed."}
        t = H[0]; c = call("search_logs", f"Confirming the failure signature on {t['service']} with a targeted search.", service=t["service"], query=SIG, level=None)
        if c and c.get("total"): t["confidence"] = min(97, t["confidence"] + 5); t["explanation"] += f" A targeted search confirmed {c['total']} matching log lines."
        for i, h in enumerate(H, 1): h["rank"] = i
        if t["remediation"]:
            r = call("propose_remediation", "Registering the fix for the top hypothesis. This only creates a ticket; nothing runs until a human approves.", final=True, **t["remediation"])
            if r and "ticket_id" in r: t["remediation"]["ticket_id"] = r["ticket_id"]
        return {"verdict": "NO_CODE_CAUSE" if t["kind"] == "external" else "ROOT_CAUSE_FOUND", "summary": t["title"], "hypotheses": H}
    report, mode = None, "rules"
    try:
        if llm_enabled():
            try: report, mode = _run_llm(inc, call, st), "llm:" + PROVIDER + "/" + MODEL
            except Exception as ex:
                db.audit(iid, "system", "llm_failed_fallback_to_rules", str(ex)[:200]); db.reset_investigation(iid)
                st.update(n=0, tickets=[], logs=[], deps=[]); obs.clear()
        if report is None: report = rules()
    except Exception as ex:
        report = {"verdict": "INSUFFICIENT_EVIDENCE", "hypotheses": [], "summary": f"Insufficient evidence. Investigation aborted: {ex}"}
    if report["verdict"] == "INSUFFICIENT_EVIDENCE" and st["tickets"]:      # never leave an approvable fix behind an "insufficient evidence" verdict
        for t in st["tickets"]: db.set_remediation_status(t["ticket_id"], "REJECTED")
    report.update(steps_used=st["n"], max_steps=MAX_STEPS, mode=mode)
    db.set_report(iid, report); db.set_status(iid, "AWAITING_APPROVAL"); db.audit(iid, "agent", "investigation_finished", report["verdict"])
