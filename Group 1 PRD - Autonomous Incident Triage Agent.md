PRODUCT REQUIREMENTS DOCUMENT • GROUP 1 

# Autonomous Incident Triage & Root-Cause Agent 

|Team Members|Suyash Khobrekar, Dattaraj Naik, Sunny<br>Cardoso|Build Window|5 Hours (Code Freeze:<br>3:00 PM)|
|---|---|---|---|
|Target<br>Architecture|Full-Stack Web Dashboard + FastAPI/Node +<br>Agentic Tool Loop|Date|November 23, 2026|



## 1. Product Vision & Problem Statement 

During production outages, on-call engineers lose critical minutes manually correlating spikes across metrics dashboards, scattered application logs, and recent deployment histories. Dumping raw telemetry into a single LLM prompt fails due to token limits, high latency, and hallucinated diagnoses. 

Product Goal: Build a stateful, full-stack Incident Triage & Investigation Platform. When an alert fires, an autonomous agent dynamically queries targeted telemetry tools, builds a step-by-step investigation trail, ranks suspected root causes with exact log/metric evidence, and pauses at a human approval gate before executing any remediation. 

Core Product Principle: Build a persistent service—not a one-shot CLI script. Incidents must move through explicit lifecycle states ( OPEN → INVESTIGATING → AWAITING_APPROVAL → RESOLVED ) stored in a database and visible in a live web interface. 

## 2. Full-Stack System Architecture 

|Layer|Component & Responsibility|
|---|---|
|Frontend UI|Single-page web dashboard displaying: (1) Live incident queue with status badges,<br>(2) Real-time agent reasoning & tool-call timeline, (3) Ranked root-cause report with<br>evidence snippets and confidence scores, and (4) Human-in-the-loopApprove /<br>Reject Remediationcontrols plus a trigger button to simulate incoming alerts.|
|Backend API & State|REST API (FastAPI or Express) backed by SQLite/JSON persistence. Exposes<br>endpoints:<br>POST /api/alerts(trigger investigation),<br>GET /api/incidents ,<br>GET /api/incidents/{id} , and<br>POST /api/incidents/{id}/approve .|
|Agent Runtime|Bounded tool-calling reasoning loop (capped at 6–8 steps) that inspects alert<br>metadata, selects targeted read-only tools based on intermediatefindings, and<br>handles empty or noisy log responses gracefully.|
|Telemetry Store|Structured local datasets (<br>logs.json ,<br>metrics.json ,<br>deploys.json )<br>modeling a multi-service application (e.g., API Gateway, Auth Service, Payment<br>Service, Database) with realistic timestamps and error bursts.|



Page 1 of 2 

ATX Round 2 Build Day • Group 1 PRD 

3. Agent Tool Specifications 

The agent must never ingest the entire log corpus at once. Instead, it must invoke narrow, parameterized tools iteratively: 

|Tool Signature|Behavior & Filtering Logic|Expected Return|
|---|---|---|
|get_metrics(service,<br>window)|Queries CPU, memory, latency (p99), and<br>HTTP 5xx error rates for a specific service<br>over a time window.|Time-series summary<br>highlighting anomalies or<br>baseline metrics.|
|search_logs(service, query,<br>level)|Filters structured application logs by<br>service name, keyword/regex, severity<br>(<br>ERROR ,<br>WARN ), and timestamp range.|Top N matching log lines<br>with exact timestamps<br>and trace IDs.|
|list_deploys(service,<br>limit)|Retrieves recent deployments, commit<br>hashes, authors, and configuration<br>changes for a service.|Chronological list of<br>recent releases prior to<br>the alert.|
|propose_remediation(action,<br>target)|Registers a recommendedfix (e.g.,<br>rollback_deploy ,<br>restart_pod ) in<br>the database without executing it.|Pending approval ticket<br>ID awaiting human<br>confirmation.|



## 4. Functional Requirements & Guardrails 

- Evidence-Backed Ranking: Output must present 1–3 ranked root causes, each containing a confidence score (0–100%), a plain-English explanation, and verbatim log lines or metric readings proving the hypothesis. 

Strict Execution Guardrail: Remediation actions (restarts, rollbacks, config changes) must never auto-execute. Execution occurs only after an explicit human click in the UI hits the approval endpoint, recording an audit log entry. 

- Step Cap & Noise Resilience: The agent loop must enforce a hard maximum step limit (e.g., 7 iterations) and explicitly report "Insufficient evidence" rather than fabricating a cause when logs are empty or unrelated. 

## 5. 5-Hour Delivery Milestones & Demo Acceptance Criteria 

|Time Window|Target Deliverable|
|---|---|
|10:00–10:30 AM|Finalize SQLite schema, tool JSON schemas, synthetic telemetry dataset, and module<br>ownership across all 3 members.|
|10:30–12:15 PM|Complete telemetry search functions, bounded agent tool-calling loop, and core<br>FastAPI/Node endpoints.|
|12:15–1:45 PM|Build web dashboard showing live step-by-step tool trace, ranked hypotheses, and<br>human approval workflow.|
|1:45–3:00 PM|Adapt system to mid-day requirements, test edge cases (empty logs, multi-service<br>noise), and freeze code at 3:00 PM.|



Page 2 of 2 

ATX Round 2 Build Day • Group 1 PRD 

