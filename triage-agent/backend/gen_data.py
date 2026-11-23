"""Generates synthetic telemetry (logs/metrics/deploys) with 4 planted scenarios. Run: python gen_data.py"""
import json, os, random, datetime as dt
random.seed(7)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"); os.makedirs(OUT, exist_ok=True)
F = "%Y-%m-%dT%H:%M:%S"
NOW = dt.datetime(2026, 11, 23, 10, 0, 0)          # "now" for every alert
START = NOW - dt.timedelta(minutes=60)
at = lambda m: (START + dt.timedelta(minutes=m)).strftime(F)
ago = lambda d: (NOW - dt.timedelta(days=d)).strftime(F)
u = random.uniform
BASE = {"api-gateway": (18, 52, 120, .2), "auth-service": (12, 40, 90, .1),
        "payment-service": (22, 55, 180, .3), "database": (30, 60, 12, 0)}  # cpu, mem, p99ms, err%

series = {s: [] for s in BASE}
for s, (cpu, mem, p99, err) in BASE.items():
    for m in range(61):
        c, mm, p, e = cpu + u(-3, 3), mem + u(-2, 2), p99 * u(.9, 1.12), max(0, err + u(-.05, .1))
        if s == "payment-service" and m >= 45:            # bad deploy at 09:42 -> pool exhaustion
            k = min(1, (m - 45) / 3); c += 30 * k; p = 180 + 4000 * k * u(.9, 1.1); e = .3 + 37 * k * u(.9, 1.1)
        if s == "api-gateway" and m >= 46:                # cascade from payment-service
            k = min(1, (m - 46) / 3); p = 120 + 2500 * k * u(.9, 1.1); e = .2 + 11 * k * u(.9, 1.1)
        if s == "database" and m >= 20: mm = 60 + (m - 20) * .95 + u(-.5, .5)   # memory leak, no deploy
        if s == "auth-service" and m % 11 == 0: p *= 1.5  # harmless blips (noise scenario)
        series[s].append({"ts": at(m), "cpu": round(c, 1), "mem": round(mm, 1), "p99_ms": round(p, 1), "err_pct": round(e, 2)})

logs = []
def L(m, svc, lvl, msg): logs.append({"ts": at(m), "service": svc, "level": lvl, "message": msg, "trace_id": "tr-%08x" % random.getrandbits(32)})
INFO = {"api-gateway": ["GET /v1/orders 200 41ms", "POST /v1/charges 200 188ms", "GET /healthz 200 2ms"],
        "auth-service": ["token issued sub=u_N", "session validated"],
        "payment-service": ["charge captured amount=N", "refund queued"], "database": ["checkpoint complete", "autovacuum run"]}
for s, msgs in INFO.items():
    m = .5
    while m < 60:
        L(m, s, "INFO", random.choice(msgs).replace("N", str(random.randint(100, 999)))); m += u(1.5, 4)
L(12, "payment-service", "WARN", "slow card-network response 900ms")
for m in (8, 17, 26, 38, 49, 55): L(m, "auth-service", "WARN", f"token refresh retry attempt=1 user=u_{random.randint(100, 999)}")  # noise only
m = 45.5
while m < 60:
    L(m, "payment-service", "ERROR", random.choice(["HikariPool-1 - Connection is not available, request timed out after 30000ms (pool size=5)",
                                                    "Charge failed: SQLTransientConnectionException: could not acquire connection"]))
    if random.random() < .5: L(m + .1, "payment-service", "WARN", f"Connection pool exhausted: active=5 idle=0 waiting={random.randint(20, 60)}")
    m += u(.2, .5)
m = 46.5
while m < 60:
    L(m, "api-gateway", "ERROR", random.choice(["upstream payment-service returned 503 for POST /v1/charges", "upstream payment-service timed out after 5000ms"])); m += u(.3, .7)
for m in range(40, 52, 3): L(m, "database", "WARN", f"shared buffer cache growth rss={int(m * 160)}MB")
m = 52
while m < 60: L(m, "database", "ERROR", f"Memory usage above 95% (rss={random.randint(15500, 15900)}MB); OOM killer risk"); m += 1.5
logs.sort(key=lambda l: l["ts"])

deploys = [
 {"service": "payment-service", "version": "v2.14.0", "commit": "a1b2c3d", "author": "m.rao", "ts": at(42), "changes": "Reduced DB_POOL_SIZE from 50 to 5 (cost optimisation); enabled new retry policy"},
 {"service": "payment-service", "version": "v2.13.2", "commit": "9f8e7d6", "author": "s.cardoso", "ts": ago(3), "changes": "Fix rounding in refund totals"},
 {"service": "api-gateway", "version": "v5.8.1", "commit": "c4d5e6f", "author": "d.naik", "ts": ago(2), "changes": "Bump rate-limit defaults"},
 {"service": "auth-service", "version": "v3.2.0", "commit": "b7a6c5d", "author": "s.khobrekar", "ts": ago(4), "changes": "Upgrade JWT library"},
 {"service": "database", "version": "migration-0192", "commit": "e1f2a3b", "author": "d.naik", "ts": ago(9), "changes": "Add index on payments(created_at)"}]

for n, obj in (("logs.json", logs), ("metrics.json", {"now": NOW.strftime(F), "series": series}), ("deploys.json", deploys)):
    with open(os.path.join(OUT, n), "w") as f: json.dump(obj, f, indent=1)
print("wrote", len(logs), "log lines")
