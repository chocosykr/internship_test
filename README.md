# Autonomous Incident Triage & Root-Cause Platform

An autonomous on-call agent platform that investigates production alerts using bounded, read-only telemetry tools, avoids decoy deploys, and pauses at a strict human approval gate before executing remediation.

## Quick Start

```powershell
# 1. Navigate to backend
cd triage-agent/backend

# 2. Generate telemetry datasets & ingest logs
python gen_data.py
python ingest_logs.py

# 3. Start the dashboard server
uvicorn main:app --reload
```
Open **http://localhost:8000** in your browser to access the live triage dashboard.

## Evaluation & Benchmarks
```powershell
cd triage-agent/backend
python evaluate.py
```
Scores the agent against a naive baseline across all 6 planted scenarios (Payment 5xx, Gateway Cascade, Order OOM, Database Memory Leak, External Email Outage, and Auth Latency Blip).
