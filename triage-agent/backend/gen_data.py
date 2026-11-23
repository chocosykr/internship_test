"""Generates synthetic telemetry (logs/metrics/deploys) with 6 planted scenarios. Run: python gen_data.py"""
import json, os, random, datetime as dt
random.seed(7)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"); os.makedirs(OUT, exist_ok=True)
F = "%Y-%m-%dT%H:%M:%S"
NOW = dt.datetime(2026, 11, 23, 10, 0, 0)          # "now" for every alert
START = NOW - dt.timedelta(minutes=60)
at = lambda m: (START + dt.timedelta(minutes=m)).strftime(F)
ago = lambda d: (NOW - dt.timedelta(days=d)).strftime(F)
u = random.uniform
BASE = {
    "api-gateway": (18, 52, 120, .2),
    "auth-service": (12, 40, 90, .1),
    "payment-service": (22, 55, 180, .3),
    "database": (30, 60, 12, 0),
    "order-service": (20, 48, 110, .15),
    "notification-service": (14, 38, 85, .1),
}  # cpu, mem, p99ms, err%

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
        if s == "order-service" and m >= 45:              # delayed onset OOM (culprit deploy at 09:30, decoy at 09:43)
            k = min(1, (m - 45) / 3); mm = 48 + 42 * k; p = 110 + 2000 * k * u(.9, 1.1); c += 15 * k
        if s == "notification-service" and m >= 44:       # external provider outage (SendGrid 503, decoy at 09:41)
            k = min(1, (m - 44) / 3); e = .1 + 32 * k * u(.9, 1.1); p = 85 + 400 * k
        series[s].append({"ts": at(m), "cpu": round(c, 1), "mem": round(mm, 1), "p99_ms": round(p, 1), "err_pct": round(e, 2)})

logs = []
def L(m, svc, lvl, msg): logs.append({"ts": at(m), "service": svc, "level": lvl, "message": msg, "trace_id": "tr-%08x" % random.getrandbits(32)})
INFO = {
    "api-gateway": ["GET /v1/orders 200 41ms", "POST /v1/charges 200 188ms", "GET /healthz 200 2ms"],
    "auth-service": ["token issued sub=u_N", "session validated"],
    "payment-service": ["charge captured amount=N", "refund queued"],
    "database": ["checkpoint complete", "autovacuum run"],
    "order-service": ["order created id=N", "order validated customer=u_N"],
    "notification-service": ["notification sent recipient=u_N", "push dispatch queued"],
}
for s, msgs in INFO.items():
    m = .5
    while m < 60:
        L(m, s, "INFO", random.choice(msgs).replace("N", str(random.randint(100, 999)))); m += u(1.5, 4)

L(12, "payment-service", "WARN", "slow card-network response 900ms")
for m in (8, 17, 26, 38, 49, 55): L(m, "auth-service", "WARN", f"token refresh retry attempt=1 user=u_{random.randint(100, 999)}")  # noise only

# payment-service error burst
m = 45.5
while m < 60:
    L(m, "payment-service", "ERROR", random.choice(["HikariPool-1 - Connection is not available, request timed out after 30000ms (pool size=5)",
                                                    "Charge failed: SQLTransientConnectionException: could not acquire connection"]))
    if random.random() < .5: L(m + .1, "payment-service", "WARN", f"Connection pool exhausted: active=5 idle=0 waiting={random.randint(20, 60)}")
    m += u(.2, .5)

# api-gateway cascade burst
m = 46.5
while m < 60:
    L(m, "api-gateway", "ERROR", random.choice(["upstream payment-service returned 503 for POST /v1/charges", "upstream payment-service timed out after 5000ms"])); m += u(.3, .7)

# database memory leak logs
for m in range(40, 52, 3): L(m, "database", "WARN", f"shared buffer cache growth rss={int(m * 160)}MB")
m = 52
while m < 60: L(m, "database", "ERROR", f"Memory usage above 95% (rss={random.randint(15500, 15900)}MB); OOM killer risk"); m += 1.5

# order-service OOM logs (matches changes in v4.2.0: memory, buffer, cache, allocation)
for m in (40, 42, 44): L(m, "order-service", "WARN", f"Heap buffer usage elevated active_objects={int(m * 220)}")
m = 45.5
while m < 60:
    L(m, "order-service", "ERROR", random.choice([
        "java.lang.OutOfMemoryError: Java heap space in batch buffer allocation cache",
        "Failed to allocate memory buffer for order processing: JVM OOM killer triggered"
    ]))
    if random.random() < .5: L(m + .1, "order-service", "WARN", "Garbage collection pause exceeded 2500ms; memory buffer full")
    m += u(.3, .8)

# notification-service external provider outage logs (matches EXTERNAL: provider, outage, rate-limit, 503)
for m in (39, 41, 43): L(m, "notification-service", "INFO", "Dispatching alert notification batch")
m = 44.5
while m < 60:
    L(m, "notification-service", "ERROR", random.choice([
        "External provider outage: SendGrid API returned 503 Service Unavailable (rate-limit quota exceeded)",
        "Third-party provider outage: connection refused by mail gateway; external service unreachable"
    ]))
    if random.random() < .5: L(m + .2, "notification-service", "WARN", "Retrying delivery after external provider rate-limit backoff")
    m += u(.4, .9)

logs.sort(key=lambda l: l["ts"])

deploys = [
 {"service": "payment-service", "version": "v2.14.0", "commit": "a1b2c3d", "author": "m.rao", "ts": at(42), "changes": "Reduced DB_POOL_SIZE from 50 to 5 (cost optimisation); enabled new retry policy"},
 {"service": "payment-service", "version": "v2.13.2", "commit": "9f8e7d6", "author": "s.cardoso", "ts": ago(3), "changes": "Fix rounding in refund totals"},
 {"service": "api-gateway", "version": "v5.8.1", "commit": "c4d5e6f", "author": "d.naik", "ts": ago(2), "changes": "Bump rate-limit defaults"},
 {"service": "auth-service", "version": "v3.2.0", "commit": "b7a6c5d", "author": "s.khobrekar", "ts": ago(4), "changes": "Upgrade JWT library"},
 {"service": "database", "version": "migration-0192", "commit": "e1f2a3b", "author": "d.naik", "ts": ago(9), "changes": "Add index on payments(created_at)"},
 # order-service deploys: culprit v4.2.0 (15 min before onset), decoy v4.2.1 (2 min before onset)
 {"service": "order-service", "version": "v4.2.1", "commit": "d4e5f6a", "author": "d.naik", "ts": at(43), "changes": "Updated checkout button label styling and CSS color theme"},
 {"service": "order-service", "version": "v4.2.0", "commit": "c1b2a3f", "author": "s.cardoso", "ts": at(30), "changes": "Introduced in-memory batch order caching buffer; increased preallocation memory threshold"},
 # notification-service deploy: decoy v1.9.3 (innocent footer/header styling change, external provider outage)
 {"service": "notification-service", "version": "v1.9.3", "commit": "f9e8d7c", "author": "s.khobrekar", "ts": at(41), "changes": "Updated html template copyright footer and localized greeting header"}
]

for n, obj in (("logs.json", logs), ("metrics.json", {"now": NOW.strftime(F), "series": series}), ("deploys.json", deploys)):
    with open(os.path.join(OUT, n), "w") as f: json.dump(obj, f, indent=1)
txt_lines = [f"{l['ts']} {l['level']} {l['service']} {l['message']} (trace_id={l['trace_id']})" for l in logs]
with open(os.path.join(OUT, "logs.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(txt_lines) + "\n")
print(f"wrote {len(logs)} log lines to logs.json and logs.txt")
