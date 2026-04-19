"""
ShopWave Agent — Real-Time API Server

Run via:
    python server.py
    OR: uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import sys
import time
import uuid
from typing import Any

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_log_level = os.environ.get("SHOPWAVE_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("shopwave_agent_api.log", encoding="utf-8"),
    ],
)
_log = logging.getLogger("shopwave.api")

from main import process_ticket, MAX_CONCURRENT, main as run_batch_processor
from agent.tracker import ticket_stage_tracker, batch_state, update_ticket_stage

app = FastAPI(
    title="ShopWave Autonomous Agent API",
    description="Production-grade agentic support ticket resolution.",
    version="2.0.0",
)

_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT)

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Schema ────────────────────────────────────────────────────────────────────

class IncomingTicket(BaseModel):
    ticket_id: str | None = None
    customer_id: str | None = None
    customer_email: str | None = None
    subject: str
    body: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_audit_log() -> list[dict]:
    """Read and parse audit_log.jsonl. Returns empty list on missing/corrupt file."""
    records = []
    for path in ("audit_log.jsonl", "audit_log.json"):
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    continue
                # JSONL format
                if path.endswith(".jsonl"):
                    for line in content.splitlines():
                        line = line.strip()
                        if line:
                            try:
                                records.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
                    if records:
                        return records
                # JSON array format
                else:
                    data = json.loads(content)
                    if isinstance(data, list):
                        return data
        except FileNotFoundError:
            pass
        except Exception as exc:
            _log.warning("Error reading %s: %s", path, exc)
    return records


# ── Cache ─────────────────────────────────────────────────────────────────────

_history_cache: list = []
_history_cache_ts: float = 0.0
_CACHE_TTL = 2.0


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_gui():
    return FileResponse("static/index.html")


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "llm_provider": os.environ.get("LLM_PROVIDER", "ollama"),
        "concurrent_limit": MAX_CONCURRENT,
        "version": "2.0.0",
    }


@app.get("/stats")
async def get_stats():
    records = _read_audit_log()
    outcomes = [e for e in records if e.get("event") == "ticket_complete"]
    total = len(outcomes)
    if total == 0:
        return {
            "total_processed": 0, "success_rate": 0.0, "avg_ms": 0,
            "escalated_count": 0, "resolved_count": 0,
            "refunds_issued": 0, "fraud_count": 0,
        }
    fraud_count    = sum(1 for e in outcomes if e.get("fraud_flag"))
    escalated      = sum(1 for e in outcomes if e.get("decision") == "escalate" or e.get("escalated"))
    resolved_count = total - escalated
    avg_ms         = sum(e.get("total_ms", 0) for e in outcomes) / total
    success_rate   = resolved_count / total
    return {
        "total_processed": total,
        "resolved_count":  resolved_count,
        "escalated_count": escalated,
        "fraud_count":     fraud_count,
        "refunds_issued":  sum(1 for e in outcomes if e.get("refund_id")),
        "success_rate":    round(success_rate, 2),
        "avg_ms":          round(avg_ms),
    }


@app.get("/tickets-history")
async def tickets_history():
    """
    FIX: reads from audit_log.jsonl as source of truth (not in-memory tracker).
    In-flight tickets are merged from tracker.
    """
    global _history_cache, _history_cache_ts
    now = time.time()

    # Build from audit log
    try:
        records = _read_audit_log()
        summaries: dict[str, dict] = {}

        for e in records:
            tid = e.get("ticket_id")
            if not tid:
                continue
            if tid not in summaries:
                summaries[tid] = {"ticket_id": tid, "status": "processing"}

            if e.get("event") in ("ticket_complete", "outcome"):
                summaries[tid].update({
                    "customer": e.get("customer_name") or e.get("customer", "Unknown"),
                    "tier": e.get("customer_tier", "standard"),
                    "urgency": e.get("urgency", "low"),
                    "intent": e.get("intent") or e.get("data", {}).get("intent", "general"),
                    "decision": e.get("decision") or e.get("data", {}).get("action_taken", "escalate"),
                    "confidence": e.get("confidence_score", 0.95),
                    "tool_call_count": e.get("tool_call_count", 0),
                    "total_ms": e.get("total_ms", 0),
                    "status": "escalated" if (e.get("decision") == "escalate" or e.get("escalated")) else "resolved",
                    "fraud_flag": e.get("fraud_flag", False),
                    "refund_id": e.get("refund_id"),
                })

        completed_ids = set(summaries.keys())

        # Merge in-flight tickets from tracker
        for tid, data in ticket_stage_tracker.items():
            if tid not in completed_ids and not data.get("complete"):
                summaries[tid] = {
                    "ticket_id": tid,
                    "status": "processing",
                    "customer": data.get("customer_name", "Processing..."),
                    "tier": data.get("customer_tier", "standard"),
                    "urgency": data.get("urgency", "low"),
                    "intent": data.get("intent", ""),
                    "decision": "",
                    "confidence": 0.0,
                    "tool_call_count": 0,
                    "total_ms": 0,
                }

        result = sorted(list(summaries.values()), key=lambda x: x.get("total_ms") or 0, reverse=True)
        _history_cache = result
        _history_cache_ts = now
        return result

    except Exception as exc:
        _log.warning("tickets_history error: %s", exc)
        return _history_cache


@app.get("/ticket-status/{ticket_id}")
async def get_ticket_status(ticket_id: str):
    """
    FIX: checks both in-memory tracker AND audit log.
    Returns full customer info, never UNKNOWN USER if audit log has data.
    """
    # Check tracker first (in-flight)
    state = ticket_stage_tracker.get(ticket_id, {})

    # If not complete or not in tracker, check audit log
    if not state.get("complete") or not state.get("customer_name"):
        records = _read_audit_log()
        for e in records:
            if e.get("ticket_id") == ticket_id and e.get("event") == "ticket_complete":
                state = {
                    "ticket_id": ticket_id,
                    "complete": True,
                    "decision": e.get("decision", ""),
                    "confidence": e.get("confidence_score", 0.0),
                    "customer_name": e.get("customer_name", "Unknown"),
                    "customer_tier": e.get("customer_tier", "standard"),
                    "urgency": e.get("urgency", "low"),
                    "fraud_flag": e.get("fraud_flag", False),
                    "fraud_reason": e.get("fraud_reason", ""),
                    "escalated": e.get("escalated", False),
                    "escalation_reason": e.get("escalation_reason", ""),
                    "action_taken": e.get("action_taken", ""),
                    "refund_id": e.get("refund_id"),
                    "tool_calls": e.get("tool_call_count", 0),
                }
                break

    return {
        "ticket_id": ticket_id,
        "current_stage": state.get("current_stage", 0),
        "stages_complete": state.get("stages_complete", []),
        "tool_calls": state.get("tool_calls", []),
        "fraud_flag": state.get("fraud_flag", False),
        "fraud_reason": state.get("fraud_reason", ""),
        "guard_fired": state.get("guard_fired", False),
        "guard_outcome": state.get("guard_outcome", ""),
        "complete": state.get("complete", False),
        "decision": state.get("decision", ""),
        "confidence": state.get("confidence", 0.0),
        "reply": state.get("reply", ""),
        "urgency": state.get("urgency", "low"),
        "customer_name": state.get("customer_name", ""),
        "customer_tier": state.get("customer_tier", "standard"),
        "action_taken": state.get("action_taken", ""),
        "refund_id": state.get("refund_id"),
    }


@app.get("/ticket-detail/{ticket_id}")
async def ticket_detail(ticket_id: str):
    try:
        records = _read_audit_log()
        events = [e for e in records if e.get("ticket_id") == ticket_id]
        summary = next((e for e in events if e.get("event") == "ticket_complete"), {})
        return {"ticket_id": ticket_id, "events": events, "summary": summary}
    except Exception:
        return {"ticket_id": ticket_id, "events": [], "summary": {}}


@app.get("/batch-status")
async def get_batch_status():
    return batch_state


@app.post("/batch")
async def start_batch(background_tasks: BackgroundTasks):
    if batch_state.get("running"):
        return {"status": "already_running", "completed": batch_state.get("completed", 0)}
    batch_state.update({"running": True, "completed": 0, "results": [], "total": 20})

    def _run():
        try:
            asyncio.run(run_batch_processor())
        except Exception as e:
            _log.error("Batch failure: %s", e)
        finally:
            batch_state["running"] = False

    background_tasks.add_task(_run)
    return {"status": "started", "total": 20}


@app.post("/ticket")
async def handle_new_ticket(ticket: IncomingTicket, background_tasks: BackgroundTasks):
    ticket_id = ticket.ticket_id or f"TKT-{str(uuid.uuid4())[:8].upper()}"
    update_ticket_stage(ticket_id, {"current_stage": 0, "complete": False})

    raw_dict = {
        "ticket_id": ticket_id,
        "customer_id": ticket.customer_id,
        "customer_email": ticket.customer_email,
        "subject": ticket.subject,
        "body": ticket.body,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    async def _run_ticket():
        try:
            await process_ticket(raw_dict, _SEMAPHORE)
        except Exception as e:
            _log.exception("Crash processing ticket %s: %s", ticket_id, e)
            update_ticket_stage(ticket_id, {"complete": True, "decision": "error"})

    background_tasks.add_task(_run_ticket)
    return {"ticket_id": ticket_id, "status": "processing"}


@app.get("/audit-export")
async def audit_export():
    for path in ("audit_log.jsonl", "audit_log.json"):
        if os.path.exists(path):
            return FileResponse(path, filename="shopwave_audit.jsonl", media_type="application/octet-stream")
    return {"error": "No audit log found. Run batch first."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
