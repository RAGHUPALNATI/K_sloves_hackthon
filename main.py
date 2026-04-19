"""
ShopWave Autonomous Support Resolution Agent — Entry Point

Usage:
    python main.py

Environment variables (set in .env file):
    LLM_PROVIDER=ollama   → requires Ollama running with llama3.2 pulled
    LLM_PROVIDER=gemini   → requires GEMINI_API_KEY

Optional:
    SHOPWAVE_MAX_CONCURRENT  — int, max concurrent LLM calls (default: 1 for Ollama, 5 for Gemini)
    SHOPWAVE_LOG_LEVEL       — DEBUG | INFO | WARNING (default INFO)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# ── Provider check ────────────────────────────────────────────────────────────

provider = os.environ.get("LLM_PROVIDER", "ollama").lower()

if provider == "gemini":
    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: LLM_PROVIDER=gemini but GEMINI_API_KEY is not set.", file=sys.stderr)
        print("  Get a free key at: https://aistudio.google.com/apikey", file=sys.stderr)
        sys.exit(1)
    print(f"✓ Using Gemini (gemini-1.5-flash)")
elif provider == "ollama":
    model = os.environ.get("OLLAMA_MODEL", "llama3.2")
    print(f"✓ Using Ollama (local) with model: {model}")
else:
    print(f"ERROR: Unknown LLM_PROVIDER='{provider}'. Use 'gemini' or 'ollama'.", file=sys.stderr)
    sys.exit(1)

# ── Logging ───────────────────────────────────────────────────────────────────

_log_level = os.environ.get("SHOPWAVE_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("shopwave_agent.log", encoding="utf-8"),
    ],
)
_log = logging.getLogger("shopwave.main")

# ── Internal imports ──────────────────────────────────────────────────────────

from agent.graph import AGENT_GRAPH
from agent.state import initial_state, AgentState
from agent.tracker import batch_state
from logger.audit import AUDIT_LOG_PATH, finalize_audit_log

# ── Configuration ─────────────────────────────────────────────────────────────

# Default to 1 for Ollama (avoids CPU overload), 5 for Gemini
_default_concurrent = "1" if provider == "ollama" else "5"
MAX_CONCURRENT = int(os.environ.get("SHOPWAVE_MAX_CONCURRENT", _default_concurrent))

DATA_DIR = Path(__file__).parent / "data"
TICKETS_FILE = DATA_DIR / "tickets.json"

# ── Ticket loading ────────────────────────────────────────────────────────────

def load_tickets() -> list[dict]:
    if not TICKETS_FILE.exists():
        _log.error("tickets.json not found at %s", TICKETS_FILE)
        sys.exit(1)
    with TICKETS_FILE.open(encoding="utf-8") as fh:
        tickets = json.load(fh)
    _log.info("Loaded %d tickets from %s", len(tickets), TICKETS_FILE)
    return tickets

# ── Single ticket processor ───────────────────────────────────────────────────

async def process_ticket(ticket: dict, semaphore: asyncio.Semaphore) -> dict:
    ticket_id = ticket["ticket_id"]
    async with semaphore:
        start_ts = time.monotonic()
        _log.info(">> [%s] Starting | subject='%s'", ticket_id, ticket.get("subject", ""))
        state: AgentState = initial_state(ticket)
        try:
            final_state: AgentState = await AGENT_GRAPH.ainvoke(state)
        except Exception as exc:
            _log.exception("[%s] UNHANDLED exception in graph: %s", ticket_id, exc)
            return {
                "ticket_id": ticket_id,
                "status": "crashed",
                "error": str(exc),
                "duration_s": round(time.monotonic() - start_ts, 2),
            }

        elapsed = round(time.monotonic() - start_ts, 2)
        _log.info(
            "✓ [%s] Done in %.2fs | decision=%s | confidence=%.2f | tools=%d",
            ticket_id, elapsed,
            final_state.get("decision", "?"),
            final_state.get("confidence", 0.0),
            len(final_state.get("tool_call_log", [])),
        )

        # Update batch state for dashboard
        batch_state["completed"] = batch_state.get("completed", 0) + 1

        return {
            "ticket_id": ticket_id,
            "status": "completed",
            "intent": final_state.get("intent"),
            "decision": final_state.get("decision"),
            "action_taken": final_state.get("action_taken"),
            "confidence": round(final_state.get("confidence", 0.0), 3),
            "tool_calls": len(final_state.get("tool_call_log", [])),
            "reasoning_steps": len(final_state.get("reasoning_steps", [])),
            "refund_id": final_state.get("refund_id"),
            "escalated": final_state.get("should_escalate", False),
            "escalation_reason": final_state.get("escalation_reason"),
            "duration_s": elapsed,
        }

# ── Batch processor ───────────────────────────────────────────────────────────

async def run_all_tickets(tickets: list[dict]) -> list[dict]:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    _log.info(
        "Starting batch: %d tickets | max_concurrent=%d | provider=%s",
        len(tickets), MAX_CONCURRENT, provider,
    )
    batch_state.update({"running": True, "total": len(tickets), "completed": 0, "results": []})
    tasks = [process_ticket(t, semaphore) for t in tickets]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[dict] = []
    for ticket, result in zip(tickets, raw_results):
        if isinstance(result, Exception):
            _log.error("[%s] gather() exception: %s", ticket["ticket_id"], result)
            results.append({"ticket_id": ticket["ticket_id"], "status": "exception", "error": str(result)})
        else:
            results.append(result)

    batch_state["running"] = False
    batch_state["results"] = results
    return results

# ── Summary report ────────────────────────────────────────────────────────────

def print_summary(results: list[dict], elapsed_total: float) -> None:
    completed = [r for r in results if r.get("status") == "completed"]
    crashed   = [r for r in results if r.get("status") in ("crashed", "exception")]
    escalated = [r for r in results if r.get("escalated")]
    refunded  = [r for r in results if r.get("refund_id")]

    print("\n" + "=" * 80)
    print("  SHOPWAVE AGENT — PROCESSING COMPLETE")
    print("=" * 80)
    print(f"  Tickets processed  : {len(results)}")
    print(f"  Completed          : {len(completed)}")
    print(f"  Crashed/Errored    : {len(crashed)}")
    print(f"  Escalated          : {len(escalated)}")
    print(f"  Refunds issued     : {len(refunded)}")
    print(f"  Total wall time    : {elapsed_total:.1f}s")
    print(f"  Audit log          : {AUDIT_LOG_PATH.with_suffix('.json')}")
    print()
    print(f"  {'TICKET':<10} {'STATUS':<12} {'INTENT':<25} {'DECISION':<20} {'CONF':>5} {'TOOLS':>5}")
    print(f"  {'-'*10} {'-'*12} {'-'*25} {'-'*20} {'-'*5} {'-'*5}")
    for r in sorted(results, key=lambda x: x["ticket_id"]):
        status_disp = "✓ OK     " if r.get("status") == "completed" else "✗ FAIL   "
        esc = " [ESC]" if r.get("escalated") else ""
        print(
            f"  {r['ticket_id']:<10} "
            f"{status_disp:<12} "
            f"{str(r.get('intent', '?')):<25} "
            f"{str(r.get('decision', r.get('error', '?')))[:18]:<20} "
            f"{r.get('confidence', 0):>5.2f} "
            f"{r.get('tool_calls', 0):>5}"
            f"{esc}"
        )
    print("=" * 80 + "\n")

# ── Entrypoint ────────────────────────────────────────────────────────────────

async def main() -> None:
    if AUDIT_LOG_PATH.exists():
        AUDIT_LOG_PATH.unlink()
        _log.info("Cleared previous audit log.")

    tickets = load_tickets()
    run_start = time.monotonic()
    results = await run_all_tickets(tickets)
    elapsed = time.monotonic() - run_start

    finalize_audit_log()

    summary_path = Path("run_summary.json")
    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "run_timestamp": datetime.now(timezone.utc).isoformat(),
                "provider": provider,
                "total_tickets": len(tickets),
                "total_duration_s": round(elapsed, 2),
                "results": results,
            },
            fh,
            indent=2,
            default=str,
        )
    _log.info("Run summary → %s", summary_path)
    print_summary(results, elapsed)


if __name__ == "__main__":
    asyncio.run(main())
