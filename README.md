# 🌊 ShopWave — Autonomous Support Resolution Agent

> **Ksolves Agentic AI Hackathon 2026** submission by Raghu Varma Palnati

A production-grade autonomous agent that resolves e-commerce support tickets end-to-end using a multi-step LangGraph reasoning pipeline — with zero human intervention for routine cases.

---




## 🎥 Demo Video

[![ShopWave Demo](https://img.shields.io/badge/▶%20Watch%20Demo-Google%20Drive-blue?style=for-the-badge&logo=google-drive)](https://drive.google.com/file/d/1tNeCfdayiNIlBv0LkVufpOaTq3kRCag8/view?usp=sharing)

> Click the button above to watch the full 5-minute demo of the ShopWave Autonomous Support Agent processing all 20 tickets live.

## Architecture in 3 Sentences

Every ticket flows through an **8-node LangGraph pipeline**: ingest → customer lookup → intent classification → order/product lookup → policy check → decision → action → audit. The agent makes a minimum of **3 tool calls per ticket** and reasons explicitly at each step before acting. All 20 tickets are processed **concurrently** using `asyncio.Semaphore`, with a real-time dashboard at `localhost:8000`.

---

## Agent Graph

```
START
  → ingest_ticket         (extract order IDs, pre-scan urgency)
  → lookup_customer       (verify identity; short-circuit unknowns)
      ↓ [unknown customer] → act (auto-escalate) → log_outcome → END
  → classify_intent       (LLM: intent, urgency, sentiment)
  → lookup_order_product  (order chain + product lookup)
  → check_policy          (KB search + fraud detection)
  → decide                (LLM: decision + confidence + reply draft)
      ↓ [refund > $200]   → override to escalate (hard rule)
  → act                   (issue_refund | cancel | reply | escalate)
  → log_outcome           (structured JSONL audit entry)
  → END
```

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Agent Framework | LangGraph `StateGraph` |
| LLM (local) | Ollama `llama3.2` |
| LLM (cloud) | Google Gemini 1.5 Flash |
| Language | Python 3.11+ |
| API Server | FastAPI + Uvicorn |
| Concurrency | `asyncio.gather()` + `asyncio.Semaphore` |
| State | LangGraph `TypedDict` |
| Audit | Thread-safe JSON-Lines writer |
| Config | `python-dotenv` (no hardcoded secrets) |

---

## Key Design Decisions

1. **`issue_refund()` is structurally guarded** — `check_refund_eligibility()` is called in `act_node` before any refund, enforced in code not just the prompt.
2. **Refunds > $200 always escalate** — hard rule in `decide` node overrides LLM output.
3. **Customer tier is system-verified** — social engineering attempts (claiming VIP status) are caught by cross-checking `customer.tier` against the ticket body.
4. **`confidence=0.0` bug is fixed** — uses explicit `None` check, not falsy check. Zero confidence gets a per-decision default, not a blanket escalation.
5. **Dashboard reads audit log** — `/tickets-history` and `/ticket-status` read from `audit_log.jsonl` as source of truth, so UNKNOWN USER is never shown.
6. **Graceful degradation** — every LLM call has a fallback dict; the pipeline never crashes due to JSON parse failure.

---

## Quick Start

### Option A: Ollama (Local, Free)

```bash
# 1. Install Ollama from https://ollama.com and pull the model
ollama pull llama3.2

# 2. Install Python deps
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# .env already defaults to LLM_PROVIDER=ollama

# 4. Run batch (processes all 20 tickets)
python main.py

# 5. Start dashboard
python server.py
# Open http://localhost:8000
```

### Option B: Gemini (Cloud, Fast)

```bash
cp .env.example .env
# Edit .env:
#   LLM_PROVIDER=gemini
#   GEMINI_API_KEY=AIzaSy...  ← get free at aistudio.google.com/apikey
#   SHOPWAVE_MAX_CONCURRENT=5

pip install -r requirements.txt
python main.py
```

### Option C: Docker

```bash
docker-compose up server   # Dashboard at localhost:8000
docker-compose up batch    # Batch processor
```

---

## Project Structure

```
shopwave-agent/
├── main.py                  # Batch processor — runs all 20 tickets
├── server.py                # FastAPI dashboard + webhook API
├── requirements.txt
├── .env.example             # Configuration template
├── agent/
│   ├── graph.py             # LangGraph StateGraph + conditional edges
│   ├── nodes.py             # All 8 node implementations
│   ├── state.py             # AgentState TypedDict
│   └── tracker.py           # Shared in-memory state (dashboard ↔ agent)
├── tools/
│   └── mock_tools.py        # 8 async tools with 10% failure injection
├── logger/
│   └── audit.py             # Thread-safe JSONL audit writer
├── static/
│   └── index.html           # Real-time dashboard UI
├── data/
│   ├── tickets.json         # 20 support tickets
│   ├── customers.json       # 10 customers (tiers, LTV)
│   ├── orders.json          # 15 orders (deadlines, status)
│   ├── products.json        # 8 products (return windows)
│   └── knowledge-base.md   # Policy rules
└── (generated at runtime)
    ├── audit_log.jsonl      # Live append-safe event log
    ├── audit_log.json       # Finalized pretty-printed audit
    └── run_summary.json     # Per-ticket outcome table
```

---

## Tools (8 total)

| Tool | Type | Purpose |
|------|------|---------|
| `get_customer` | Read | Look up by email — tier, LTV, notes |
| `get_order` | Read | Order by ID — status, total, deadline |
| `get_orders_for_customer` | Read | All orders for a customer ID |
| `get_product` | Read | Return window, warranty details |
| `search_knowledge_base` | Read | Policy KB keyword search |
| `check_refund_eligibility` | Guard | Validates return window + tier rules (MUST run before refund) |
| `issue_refund` | Write | Issues refund (only after eligibility confirmed) |
| `cancel_order_tool` | Write | Cancels pending/processing orders |
| `send_reply` | Write | Sends email reply to customer |
| `escalate_ticket` | Write | Routes to human queue with full context |

---

## Output Files

| File | Description |
|------|-------------|
| `audit_log.jsonl` | Append-safe event stream (crash-safe) |
| `audit_log.json` | Pretty-printed full audit trail |
| `run_summary.json` | Per-ticket outcome table with timings |
| `shopwave_agent.log` | Live process log |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `ollama` | `ollama` or `gemini` |
| `OLLAMA_MODEL` | `llama3.2` | Ollama model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `GEMINI_API_KEY` | — | Required when using Gemini |
| `SHOPWAVE_MAX_CONCURRENT` | `1` (ollama) / `5` (gemini) | Max parallel LLM calls |
| `SHOPWAVE_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` |

---

## Failure Handling

See [`failure_modes.md`](failure_modes.md) for detailed coverage. Summary:

- **Tool failures** — 10% random failure injection; agents retry or escalate
- **LLM timeouts** — `timeout=120` on Ollama; JSON parse fallback on every call
- **Unknown customer** — short-circuit to escalate (< 1s resolution)
- **Refund eligibility failure** — blocks refund, replies with denial reason
- **Fraud detection** — cross-checks claimed tier vs system tier, auto-escalates
