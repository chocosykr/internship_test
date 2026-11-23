from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Union


_LOG_PATTERNS = [
    # 1) Standard: 2026-11-23T09:45:30 ERROR payment-service HikariPool-1 ... (trace_id=tr-adfa09b0)
    re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+"
        r"(?P<level>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\s+"
        r"(?P<service>[a-zA-Z0-9._-]+)\s+"
        r"(?P<message>.*)$"
    ),
    # 2) Bracketed: [2026-11-23 09:45:30] [ERROR] [payment-service] message
    re.compile(
        r"^\[(?P<ts>[^\]]+)\]\s+\[(?P<level>[^\]]+)\]\s+\[(?P<service>[^\]]+)\]\s*(?P<message>.*)$"
    ),
    # 3) Timestamp + service in brackets: 2026-11-23 09:45:30 [payment-service] ERROR - message
    re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+"
        r"\[(?P<service>[^\]]+)\]\s+"
        r"(?P<level>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\s*(?:-\s*)?"
        r"(?P<message>.*)$"
    ),
    # 4) Key-value prefix: ERROR service=payment-service msg=...
    re.compile(
        r"^(?P<level>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\s+"
        r"(?:service=(?P<service>[a-zA-Z0-9._-]+)\s+)?(?P<message>.*)$"
    ),
    # 5) Timestamp + level + message (service omitted/default): 2026-11-23 09:45:30 ERROR connection timed out
    re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+"
        r"(?P<level>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\s*(?:-\s*)?"
        r"(?P<message>.*)$"
    ),
]

_TRACE_RE = re.compile(r"\btrace_id=([a-zA-Z0-9._-]+)\b")
_KV_RE = re.compile(r"\b([a-zA-Z0-9_]+)=([^\s\)]+)")
_STACK_START_RE = re.compile(
    r"^(\s+|Traceback \(most recent call last\):|Caused by:|at\s+[a-zA-Z0-9_.$]+\(|Exception in thread|\.\.\.\s+\d+\s+more)",
    re.IGNORECASE,
)


def normalize_timestamp(ts: str) -> str:
    """Normalize timestamp to ISO 8601 YYYY-MM-DDTHH:MM:SS format if possible."""
    ts = (ts or "").strip()
    if not ts:
        return ""
    # Replace space between date and time with T
    if len(ts) >= 19 and ts[10] == " ":
        ts = ts[:10] + "T" + ts[11:]
    return ts


def parse_log_line(line: str, default_service: str = "", default_level: str = "INFO") -> Dict[str, Any]:
    """Parse a single log line (plain text or JSON string) into structured fields."""
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

    # Handle JSON line format (NDJSON)
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                raw_ts = data.get("ts") or data.get("timestamp") or data.get("time") or ""
                lvl = (data.get("level") or data.get("severity") or default_level).upper()
                svc = data.get("service") or data.get("app") or default_service
                msg = data.get("message") or data.get("msg") or stripped
                tr = data.get("trace_id") or data.get("traceId")
                extra = {
                    k: v
                    for k, v in data.items()
                    if k not in {"ts", "timestamp", "time", "level", "severity", "service", "app", "message", "msg", "trace_id", "traceId"}
                }
                return {
                    "ts": normalize_timestamp(str(raw_ts)),
                    "level": "WARN" if lvl == "WARNING" else lvl,
                    "service": svc,
                    "message": str(msg),
                    "trace_id": tr,
                    "fields": extra,
                    "raw": raw,
                }
        except Exception:
            pass

    ts = ""
    level = default_level
    service = default_service
    message = stripped

    for pat in _LOG_PATTERNS:
        m = pat.match(stripped)
        if m:
            gd = m.groupdict()
            ts = normalize_timestamp(gd.get("ts") or "")
            lvl = (gd.get("level") or default_level).upper()
            level = "WARN" if lvl == "WARNING" else lvl
            svc = (gd.get("service") or "").strip()
            if svc:
                service = svc
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
    """Convert multi-line plain text logs into structured log entries.
    
    Supports multi-line exceptions (stack traces) by attaching continuation lines
    to the previous log entry.
    """
    entries: List[Dict[str, Any]] = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue

        # Check if line is a continuation / stack trace of previous line
        if entries and (_STACK_START_RE.match(line) or (not parse_log_line(line)["ts"] and (line.startswith(" ") or line.startswith("\t")))):
            prev = entries[-1]
            prev["message"] = f"{prev['message']}\n{line.rstrip()}"
            prev["raw"] = f"{prev['raw']}\n{line.rstrip()}"
            continue

        entry = parse_log_line(line, default_service=default_service, default_level=default_level)
        entries.append(entry)

    return entries


def infer_service_from_filename(filename: str) -> str:
    """Infer service name from filename if possible (e.g. payment-service.log -> payment-service)."""
    base = os.path.basename(filename).lower()
    base = re.sub(r"\.(log|txt|json|out)$", "", base)
    known = ["payment-service", "api-gateway", "auth-service", "order-service", "notification-service", "database"]
    for svc in known:
        if svc in base:
            return svc
    return ""


def parse_log_file(file_path: str, default_service: str = "") -> List[Dict[str, Any]]:
    """Read a log file (.txt, .log, .json) and return structured log entries."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Log file not found: {file_path}")

    svc = default_service or infer_service_from_filename(file_path)

    # Check if already a JSON file containing a list of records
    if file_path.endswith(".json"):
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    return [
                        item if isinstance(item, dict) and "ts" in item
                        else parse_log_line(str(item), default_service=svc)
                        for item in data
                    ]
            except Exception:
                pass

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    return parse_log_text(content, default_service=svc)


def ingest_log_sources(
    sources: Union[str, Iterable[str]],
    output_json: Optional[str] = None,
    merge: bool = False,
    default_service: str = "",
) -> List[Dict[str, Any]]:
    """Parse one or more text/log files (or directories) and write to output_json."""
    if isinstance(sources, str):
        sources = [sources]

    file_paths: List[str] = []
    for s in sources:
        if os.path.isdir(s):
            for root, _, files in os.walk(s):
                for fn in files:
                    if fn.endswith((".log", ".txt", ".json")):
                        file_paths.append(os.path.join(root, fn))
        elif os.path.isfile(s):
            file_paths.append(s)

    all_entries: List[Dict[str, Any]] = []

    # If merge is requested and output_json exists, load existing first
    if merge and output_json and os.path.exists(output_json):
        try:
            with open(output_json, "r", encoding="utf-8") as f:
                existing = json.load(f)
                if isinstance(existing, list):
                    all_entries.extend(existing)
        except Exception:
            pass

    for fp in file_paths:
        entries = parse_log_file(fp, default_service=default_service)
        all_entries.extend(entries)

    # Sort entries by timestamp if present
    all_entries.sort(key=lambda x: str(x.get("ts") or ""))

    if output_json:
        os.makedirs(os.path.dirname(os.path.abspath(output_json)), exist_ok=True)
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(all_entries, f, indent=1)

    return all_entries


def parse_alert_text(alert_text: str) -> Dict[str, Any]:
    """Extract service, severity, and summary from free-form alert text."""
    raw = (alert_text or "").strip()
    known_services = [
        "payment-service",
        "api-gateway",
        "order-service",
        "notification-service",
        "auth-service",
        "database",
    ]
    matched_svc = None
    for s in known_services:
        if re.search(rf"\b{re.escape(s)}\b", raw, re.IGNORECASE):
            matched_svc = s
            break

    if not matched_svc:
        service_match = re.search(r"\bservice\s*[:=]\s*([a-zA-Z0-9._-]+)\b", raw, re.IGNORECASE)
        if service_match:
            matched_svc = service_match.group(1).lower()

    severity_match = re.search(r"\b(critical|high|medium|low|sev[1-4])\b", raw, re.IGNORECASE)
    return {
        "service": matched_svc if matched_svc else "unknown-service",
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
        tr = f" (trace_id={item['trace_id']})" if item.get("trace_id") else ""
        out.append(f"{ts} {lvl} {svc} {msg}{tr}".strip())
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
