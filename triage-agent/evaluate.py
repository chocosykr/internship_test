"""Scores the agent on every planted scenario and compares it with a naive 'blame the latest deploy' baseline.
Usage: python evaluate.py   (rule planner; set GEMINI_API_KEY / ANTHROPIC_API_KEY to score the LLM planner)   Exit code 1 if any scenario fails."""
import os, sys, tempfile
os.environ.setdefault("STEP_DELAY", "0"); os.environ["TRIAGE_DB"] = os.path.join(tempfile.mkdtemp(), "eval.db")
import agent, db
db.init()
# service, symptom, expected verdict, expected remediation target, decoy target (the innocent deploy a lazy answer would blame)
CASES = [("payment-service", "5xx spike", "ROOT_CAUSE_FOUND", "payment-service@v2.14.0", "payment-service@v2.14.1"),
         ("api-gateway", "5xx at gateway", "ROOT_CAUSE_FOUND", "payment-service@v2.14.0", "payment-service@v2.14.1"),
         ("order-service", "OOM and latency", "ROOT_CAUSE_FOUND", "order-service@v4.2.0", "order-service@v4.2.1"),
         ("database", "memory climbing", "ROOT_CAUSE_FOUND", "database", None),
         ("notification-service", "emails failing", "NO_CODE_CAUSE", None, "notification-service@v1.9.3"),
         ("auth-service", "latency blip", "INSUFFICIENT_EVIDENCE", None, None)]

def baseline(svc):                                   # naive policy: blame the latest deploy before onset
    m = agent.get_metrics(svc, 30)
    if not m["anomalies"]: return "INSUFFICIENT_EVIDENCE", None
    ds = [d for d in agent.list_deploys(svc, 5)["deploys"] if 0 <= agent._mins(d["ts"], m["onset"]) <= agent.LOOKBACK_MIN]
    return "ROOT_CAUSE_FOUND", (f'{svc}@{ds[0]["version"]}' if ds else None)

def grade(v, t, ev, et, decoy):
    ok = v == ev and t == et; hit = decoy is not None and t == decoy
    return ok, hit, 1.0 if ok else 0.0 if hit else 0.25 if v == ev else 0.0

print(f"planner: {'llm:' + agent.PROVIDER + '/' + agent.MODEL if agent.llm_enabled() else 'rules'}\n")
print(f"{'scenario':22} {'verdict':22} {'target':26} steps conf  result")
tot = {"agent": [0, 0, 0.0], "base": [0, 0, 0.0]}
for svc, sym, ev, et, decoy in CASES:
    iid = db.create_incident(svc, sym, "high"); agent.investigate(iid); d = db.get_incident(iid); r = d["report"]
    top = r["hypotheses"][0] if r["hypotheses"] else None; t = top and top["remediation"] and top["remediation"]["target"]
    ok, hit, g = grade(r["verdict"], t, ev, et, decoy); bv, bt = baseline(svc); bok, bhit, bg = grade(bv, bt, ev, et, decoy)
    for k, (o, h, gg) in (("agent", (ok, hit, g)), ("base", (bok, bhit, bg))): tot[k][0] += o; tot[k][1] += h; tot[k][2] += gg
    print(f"{svc:22} {r['verdict']:22} {str(t):26} {r['steps_used']:>3}   {top['confidence'] if top else '-':>4}  {'PASS' if ok else 'FAIL'}{'  DECOY HIT' if hit else ''}  [{r['mode']}]")
n = len(CASES)
for k, label in (("agent", "Agent"), ("base", "Baseline (blame latest deploy)")):
    p, h, g = tot[k]; print(f"{label:32} pass {p}/{n}  decoy hits {h}  mean graded reward {g / n:.2f}")
sys.exit(0 if tot["agent"][0] == n else 1)
