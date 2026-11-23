# Autonomous Incident Triage & Root-Cause Agent

FastAPI + SQLite service with a bounded (max 8 steps) read-only agent loop and a single-page dashboard.

## Run
```bash
pip install -r requirements.txt
cd backend
python gen_data.py            # writes data/logs.json, metrics.json, deploys.json
uvicorn main:app --reload     # open http://localhost:8000
python evaluate.py            # scores every scenario vs a naive baseline
```
`STEP_DELAY=0` removes the per-step pause used to make the UI timeline visibly stream.

## Planners
- **Rules (default):** deterministic, no key. A deploy is blamed only if its change set matches the error signature, so decoy deploys are not blamed on timing alone.
- **LLM (optional):** the model picks the tools and writes the report through the same tools, step cap and approval gate. Log evidence the tools never returned is dropped. Any LLM error falls back to the rule planner (audited). `AGENT_MODE=rules` forces rules.

Providers (auto-detected from whichever key is set; `LLM_PROVIDER` / `LLM_MODEL` override):
```powershell
# Free: Google Gemini (key from https://aistudio.google.com/apikey)
$env:GEMINI_API_KEY="..."; $env:LLM_MIN_INTERVAL="5"      # LLM_MIN_INTERVAL spaces calls to respect free-tier rate limits
# Anthropic
$env:ANTHROPIC_API_KEY="sk-ant-..."
# Any OpenAI-compatible host (Groq, OpenRouter, OpenAI, ...)
$env:LLM_PROVIDER="openai"; $env:OPENAI_API_KEY="..."; $env:OPENAI_BASE_URL="https://api.groq.com/openai/v1"; $env:LLM_MODEL="<model id>"
```
Then `python evaluate.py` or `uvicorn main:app --reload`. The report shows `Planner: llm:<provider>/<model>`.

## Scenarios (planted in gen_data.py)
| Alert | Tests | Expected |
|---|---|---|
| Payment 5xx spike | decoy deploy lands after the culprit | rollback v2.14.0 (not v2.14.1) |
| Gateway 5xx | cascade across services | payment v2.14.0 #1, cascade #2 |
| Order OOM | delayed onset (culprit 15 min earlier) + decoy | rollback v4.2.0 (not v4.2.1) |
| Database memory | no deploy involved | restart_pod |
| Email outage | external provider, decoy deploy | NO_CODE_CAUSE, no remediation |
| Auth latency blip | noise only | Insufficient evidence |

## API
`POST /api/alerts` `GET /api/incidents` `GET /api/incidents/{id}` `POST /api/incidents/{id}/approve` (`{"decision":"approve"|"reject"}`) `GET /api/tools`

## Where to change things (mid-day requirement)
- New tool: function + `TOOLS` + `TOOL_SCHEMAS` in agent.py (the LLM sees it automatically); add a step in `rules()` if the rule planner should use it.
- Ranking rules: `rank()`. Telemetry: `gen_data.py`. Lifecycle/audit: `db.py`, `main.py`. Add a case to `CASES` in evaluate.py.
- Guardrail: `propose_remediation` only inserts a PENDING row; `execute_remediation` runs only from the approve endpoint.
