# 🌊 ShopWave — Autonomous Support Resolution Agent

> **Ksolves Agentic AI Hackathon 2026**
> Built by **Raghu Varma Palnati** — B.Tech CSE (AI & ML), Lovely Professional University

An autonomous AI agent that resolves e-commerce customer support tickets **end-to-end without any human involvement** — issuing refunds, cancelling orders, detecting fraud, and escalating edge cases through an 8-node LangGraph reasoning pipeline.

---

## 🌐 Live Demo

> **Two live links available. If one is not working, please use the other.**

---

### ✅ Primary — Azure VM (24/7, Always On)

[![Open Primary Demo](https://img.shields.io/badge/▶%20Primary%20Demo-Azure%20VM%20%7C%2024%2F7-brightgreen?style=for-the-badge&logo=microsoft-azure)](http://shopwaveksolves.duckdns.org/)

**🔗 http://shopwaveksolves.duckdns.org/**

| Detail | Info |
|--------|------|
| 🖥️ Host | Microsoft Azure VM (Ubuntu 22.04) |
| ⏰ Availability | 24/7 — always on, no laptop needed |
| 🤖 LLM | Ollama llama3.2 running on the server |
| 🔑 API Keys | None — fully local LLM, zero cost |
| 🌍 Domain | DuckDNS custom domain |

---

### 🔵 Backup — ngrok Mirror (Active During Review Period)

[![Open Backup Demo](https://img.shields.io/badge/▶%20Backup%20Demo-ngrok%20Mirror-blue?style=for-the-badge)](https://reburial-shun-isolation.ngrok-free.dev/)

**🔗 https://reburial-shun-isolation.ngrok-free.dev/**

| Detail | Info |
|--------|------|
| 🖥️ Host | Local machine tunnelled via ngrok |
| ⏰ Availability | Active while laptop is running during review |
| 🤖 LLM | Ollama llama3.2 running locally |
| 🔑 API Keys | None — fully local LLM, zero cost |

---

### 💡 How to Use the Dashboard

1. Open either link above
2. Click **▶ Run All 20 Tickets** to start the agent
3. Watch all 20 tickets get processed in real time — 🟢 green = resolved, 🟡 yellow = escalated, 🔴 red = fraud caught
4. Click any ticket row to see the full 8-node pipeline trace, confidence score, decision, and the actual reply sent to the customer
5. Check the **Resolved / Escalated / Fraud Detected** summary cards at the top

---

## 🎥 Demo Video

[![Watch Demo](https://img.shields.io/badge/▶%20Watch%20Full%20Demo-Google%20Drive-red?style=for-the-badge&logo=google-drive)](https://drive.google.com/file/d/1tNeCfdayiNIlBv0LkVufpOaTq3kRCag8/view?usp=sharing)

**[▶ Click here to watch the 5-minute demo](https://drive.google.com/file/d/1tNeCfdayiNIlBv0LkVufpOaTq3kRCag8/view?usp=sharing)**

The demo shows:
- Live processing of all 20 support tickets
- Fraud detection catching TKT-018 social engineering attempt
- Autonomous refund, cancellation, and escalation decisions
- Real-time dashboard with resolution status per ticket
- Full audit trail for every decision made

---

## 🤖 What This Agent Does

Most support teams drown in tickets. A refund decision that should take 3 seconds takes hours because a human has to verify the order, check the policy, cross-reference the customer tier, and write a reply.

ShopWave does all of that autonomously:

- Customer asks for a refund → agent checks the order, verifies the return window, confirms eligibility, **issues the refund and sends a reply** — no human needed
- Customer claims to be VIP to get special treatment → agent **cross-checks the system**, detects the fraud, and blocks the request
- Order is still in processing → agent **cancels it immediately** and confirms with the customer
- Ticket is too complex or refund exceeds $200 → agent **escalates with full context** to a human queue

All 20 tickets processed. Full audit trail. Every decision explained.

---

## 🏗️ Architecture in 3 Sentences

Every ticket flows through an **8-node LangGraph pipeline**: ingest → customer lookup → intent classification → order/product lookup → policy check → decision → action → audit. The agent makes a minimum of **3 tool calls per ticket** and reasons explicitly at each step before acting. All 20 tickets are processed **concurrently** using `asyncio.Semaphore`, with a real-time dashboard available at the live links above.

## Agent Graph

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
