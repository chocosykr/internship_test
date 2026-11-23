import os
from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent
import db
import text_io

app = FastAPI(title="Incident Triage Agent")
db.init()


class AlertIn(BaseModel):
    service: str
    symptom: str
    severity: str = "high"


class DecisionIn(BaseModel):
    decision: str = "approve"  # approve | reject
    actor: str = "on-call"


@app.post("/api/alerts", status_code=202)
def create_alert(payload: AlertIn, bg: BackgroundTasks):
    if payload.service not in agent.SERVICES:
        raise HTTPException(status_code=422, detail=f"service must be one of {agent.SERVICES}")
    incident_id = db.create_incident(payload.service, payload.symptom, payload.severity)
    bg.add_task(agent.investigate, incident_id)
    return {"id": incident_id, "status": "OPEN"}


@app.post("/api/alerts/text", status_code=202, response_class=PlainTextResponse)
def create_alert_from_text(bg: BackgroundTasks, alert_text: str = Body(..., media_type="text/plain")):
    parsed = text_io.parse_alert_text(alert_text)
    service = parsed["service"]
    if service not in agent.SERVICES:
        raise HTTPException(
            status_code=422,
            detail=f"could not determine valid service from text; must be one of {agent.SERVICES}",
        )
    incident_id = db.create_incident(service, parsed["summary"], parsed["severity"])
    bg.add_task(agent.investigate, incident_id)
    return PlainTextResponse(f"Queued incident #{incident_id} for {service} (status=OPEN)", status_code=202)


@app.get("/api/incidents")
def list_incidents():
    return db.list_incidents()


@app.get("/api/incidents/{iid}")
def get_incident(iid: int):
    inc = db.get_incident(iid)
    if not inc:
        raise HTTPException(status_code=404, detail="incident not found")
    return inc


@app.get("/api/incidents/{iid}/text", response_class=PlainTextResponse)
def get_incident_text(iid: int):
    inc = db.get_incident(iid)
    if not inc:
        raise HTTPException(status_code=404, detail="incident not found")
    return PlainTextResponse(text_io.format_incident_text(inc), media_type="text/plain; charset=utf-8")


@app.post("/api/incidents/{iid}/approve")
def decide_remediation(iid: int, body: DecisionIn = DecisionIn()):
    inc = db.get_incident(iid)
    if not inc:
        raise HTTPException(status_code=404, detail="incident not found")
    if inc["status"] != "AWAITING_APPROVAL":
        raise HTTPException(
            status_code=409,
            detail=f"incident is {inc['status']}, not AWAITING_APPROVAL",
        )

    decision = body.decision.strip().lower()
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=422, detail="decision must be 'approve' or 'reject'")

    rem = inc.get("remediation")
    if decision == "approve":
        if not rem:
            raise HTTPException(status_code=409, detail="no remediation to approve; use decision=reject to close")
        output = agent.execute_remediation(rem)  # single execution point
        db.set_remediation_status(rem["id"], "EXECUTED")
        db.audit(iid, body.actor, "remediation_approved_and_executed", output)
    else:
        if rem:
            db.set_remediation_status(rem["id"], "REJECTED")
        db.audit(iid, body.actor, "remediation_rejected" if rem else "closed_without_action", "")

    db.set_status(iid, "RESOLVED")
    return db.get_incident(iid)


@app.post("/api/logs/parse-text")
def parse_logs_text(
    raw_text: str = Body(..., media_type="text/plain"),
    service: str = Query(default=""),
):
    entries = text_io.parse_log_text(raw_text, default_service=service)
    return {
        "count": len(entries),
        "entries": entries,
        "logs": text_io.format_log_entries_text(entries),
    }


@app.get("/api/tools")
def list_tools():
    return agent.TOOL_SCHEMAS


static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/", StaticFiles(directory=static_dir, html=True))