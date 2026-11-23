from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional


_LOG_PATTERNS = [
    # Example: 2026-11-23T09:45:30 ERROR payment-service HikariPool-1 ... (trace_id=tr-adfa09b0)
    re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+"
        r"(?P<level>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\s+"
        r"(?P<service>[a-zA-Z0-9._-]+)\s+"
        r"(?P<message>.*)$"
    ),
    # Example: [2026-11-23 09:45:30] [ERROR] [payment-service] message
    re.compile(
        r"^\[(?P<ts>[^\]]+)\]\s+\[(?P<level>[^\]]+)\]\s+\[(?P<service>[^\]]+)\]\s*(?P<message>.*)$"
    ),
    # Example: ERROR service=payment-service msg=...
    re.compile(
        r"^(?P<level>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\s+"
        r"(?:service=(?P<service>[a-zA-Z0-9._-]+)\s+)?(?P<message>.*)$"
    ),
]

_TRACE_RE = re.compile(r"\btrace_id=([a-zA-Z0-9._-]+)\b")
_KV_RE = re.compile(r"\b([a-zA-Z0-9_]+)=([^\s\)]+)")


def parse_log_line(line: str, default_service: str = "", default_level: str = "INFO") -> Dict[str, Any]:
    """Parse a single plain-text log line into structured fields while preserving raw text."""
    raw = line.rstrip("\r\n")
    stripped = raw.strip()
    if not stripped:
        return {
            "ts": "",
            "level": default_level,
            "service": default_service,
            "message": "",
            "trace_id": None,
            "fields": {},
            "raw": raw,
        }

    ts = ""
    level = default_level
    service = default_service
    message = stripped

    for pat in _LOG_PATTERNS:
        m = pat.match(stripped)
        if m:
            gd = m.groupdict()
            ts = (gd.get("ts") or "").strip()
            lvl = (gd.get("level") or default_level).upper()
            level = "WARN" if lvl == "WARNING" else lvl
            service = (gd.get("service") or default_service).strip()
            message = (gd.get("message") or "").strip()
            break

    trace_match = _TRACE_RE.search(stripped)
    trace_id = trace_match.group(1) if trace_match else None
    fields = {k: v for k, v in _KV_RE.findall(stripped)}

    return {
        "ts": ts,
        "level": level,
        "service": service,
        "message": message,
        "trace_id": trace_id,
        "fields": fields,
        "raw": raw,
    }


def parse_log_text(text: str, default_service: str = "", default_level: str = "INFO") -> List[Dict[str, Any]]:
    """Convert multi-line plain text logs into structured log entries."""
    entries: List[Dict[str, Any]] = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        entries.append(parse_log_line(line, default_service=default_service, default_level=default_level))
    return entries


def parse_alert_text(alert_text: str) -> Dict[str, Any]:
    """Extract service, severity, and summary from free-form alert text."""
    raw = (alert_text or "").strip()
    service_match = re.search(r"\bservice[=:\s]+([a-zA-Z0-9._-]+)\b", raw, re.IGNORECASE)
    if not service_match:
        service_match = re.search(
            r"\b(payment-service|order-service|notification-service|api-gateway|auth-service|database)\b",
            raw,
            re.IGNORECASE,
        )
    severity_match = re.search(r"\b(critical|high|medium|low|sev[1-4])\b", raw, re.IGNORECASE)
    return {
        "service": service_match.group(1) if service_match else "unknown-service",
        "severity": severity_match.group(1).lower() if severity_match else "high",
        "summary": raw,
        "raw": raw,
    }


def format_log_entries_text(entries: Iterable[Dict[str, Any]]) -> str:
    """Format parsed or structured log dicts back into readable log lines."""
    out: List[str] = []
    for item in entries:
        if isinstance(item, str):
            out.append(item)
            continue
        if item.get("raw"):
            out.append(str(item["raw"]))
            continue
        ts = item.get("ts") or "-"
        lvl = (item.get("level") or "INFO").upper()
        svc = item.get("service") or "-"
        msg = item.get("message") or ""
        out.append(f"{ts} {lvl} {svc} {msg}".strip())
    return "\n".join(out)


def format_incident_text(incident: Dict[str, Any]) -> str:
    """Render an incident record, steps, root-cause report, and audit trail as plain text & logs."""
    lines: List[str] = []
    inc_id = incident.get("id", "?")
    service = incident.get("service", "unknown")
    status = incident.get("status", "unknown")
    summary = incident.get("summary", "")

    lines.append(f"=== INCIDENT #{inc_id} | {service} | STATUS: {status} ===")
    if summary:
        lines.append(f"Summary: {summary}")

    report = incident.get("report") or {}
    if report:
        verdict = report.get("verdict") or report.get("title") or report.get("summary") or ""
        mode = report.get("mode") or ""
        conf = report.get("confidence")
        meta = f"mode={mode}" if mode else ""
        if conf is not None:
            meta = f"{meta}, confidence={conf}" if meta else f"confidence={conf}"
        lines.append(f"Verdict: {verdict}" + (f" ({meta})" if meta else ""))

        hypotheses = report.get("hypotheses") or []
        if hypotheses:
            lines.append("--- Ranked Hypotheses ---")
            for idx, h in enumerate(hypotheses, start=1):
                if isinstance(h, dict):
                    h_title = h.get("title") or h.get("cause") or str(h)
                    h_conf = h.get("confidence")
                    suffix = f" [confidence={h_conf}]" if h_conf is not None else ""
                    lines.append(f"  #{idx} {h_title}{suffix}")
                else:
                    lines.append(f"  #{idx} {h}")

        evidence = report.get("evidence") or []
        if evidence:
            lines.append("--- Evidence (Logs / Metrics / Deploys) ---")
            for ev in evidence:
                if isinstance(ev, dict):
                    ev_txt = ev.get("quote") or ev.get("line") or ev.get("detail") or ev.get("summary") or str(ev)
                else:
                    ev_txt = str(ev)
                lines.append(f"  [EVIDENCE] {ev_txt}")

    remediation = incident.get("remediation")
    if remediation:
        if isinstance(remediation, dict):
            action = remediation.get("action", "")
            target = remediation.get("target", "")
            r_status = remediation.get("status", "")
            lines.append(f"Remediation: action={action} target={target} status={r_status}")
        else:
            lines.append(f"Remediation: {remediation}")

    steps = incident.get("steps") or []
    if steps:
        lines.append("--- Investigation Steps ---")
        for s in steps:
            step_num = s.get("step_num") or s.get("step") or ""
            tool = s.get("tool") or ""
            args = s.get("args") or ""
            obs = s.get("observation") or s.get("summary") or ""
            prefix = f"Step {step_num}" if step_num != "" else "Step"
            lines.append(f"  {prefix} | {tool}({args}) -> {obs}")

    audit = incident.get("audit") or []
    if audit:
        lines.append("--- Audit Logs ---")
        for a in audit:
            ts = a.get("ts", "")
            actor = a.get("actor", "system")
            event = a.get("event", "")
            detail = a.get("detail", "")
            lines.append(f"  {ts} [{actor}] {event}: {detail}".rstrip(": "))

    return "\n".join(lines)