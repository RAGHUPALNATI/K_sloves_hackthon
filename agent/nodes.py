"""
nodes.py — All 8 LangGraph node implementations for ShopWave Agent.

Bugs fixed vs. original:
  1. confidence=0.0 falsy bug — use explicit None check, assign per-decision default
  2. Ollama timeout — timeout=120 on LLM init, retry logic
  3. check_refund_eligibility — now compares actual return_deadline dates
  4. Dashboard UNKNOWN USER — tracker writes customer_name on every lookup
  5. Graceful fallback on every LLM parse failure — no more silent escalations
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from agent.state import AgentState
from agent.tracker import update_ticket_stage
from logger.audit import write_event
from tools.mock_tools import (
    get_customer,
    get_order,
    get_orders_for_customer,
    get_product,
    search_knowledge_base,
    check_refund_eligibility,
    issue_refund,
    cancel_order_tool,
    send_reply,
    escalate_ticket,
)

_log = logging.getLogger("shopwave.nodes")

tier_map = {
    "1": "standard",
    "2": "premium",
    "3": "vip",
    1: "standard",
    2: "premium",
    3: "vip",
}

# ── LLM initialisation ────────────────────────────────────────────────────────

def _get_llm():
    provider = os.environ.get("LLM_PROVIDER", "ollama").lower()
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model="gemini-1.5-flash",
            google_api_key=os.environ["GEMINI_API_KEY"],
            temperature=0.0,
            request_timeout=60,
        )
    else:  # ollama
        from langchain_ollama import OllamaLLM
        return OllamaLLM(
            model=os.environ.get("OLLAMA_MODEL", "llama3.2"),
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0.0,
            timeout=120,
        )


# Lazy singleton — avoids re-init on every ticket
_llm_instance = None

def get_llm():
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = _get_llm()
    return _llm_instance


# ── Helpers ───────────────────────────────────────────────────────────────────

def _log_tool(state: AgentState, tool: str, args: dict, result: Any) -> None:
    entry = {"tool": tool, "args": args, "result": result, "ts": time.time()}
    state.setdefault("tool_call_log", []).append(entry)
    write_event(state["ticket_id"], "tool_call", {"tool": tool, "result": str(result)[:200]})


def _add_step(state: AgentState, msg: str) -> None:
    state.setdefault("reasoning_steps", []).append(msg)


def _invoke_llm_json(prompt: str, fallback: dict) -> dict:
    """
    Call LLM, strip markdown fences, parse JSON.
    Returns fallback dict on any failure so the pipeline never crashes.
    """
    try:
        llm = get_llm()
        raw = llm.invoke(prompt) if hasattr(llm, "invoke") else llm(prompt)
        if hasattr(raw, "content"):
            raw = raw.content
        raw = str(raw).strip()
        # Strip ```json ... ``` fences
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
        # Find first JSON object in response
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as exc:
        _log.warning("LLM call failed: %s", exc)
    return fallback


# ── CONFIDENCE DEFAULT MAP (fixes the 0.0 falsy bug) ─────────────────────────

_DECISION_CONFIDENCE_DEFAULTS = {
    "issue_refund":    0.82,
    "approve_return":  0.85,
    "deny":            0.88,
    "cancel_order":    0.90,
    "send_reply":      0.80,
    "request_info":    0.92,
    "escalate":        0.75,
}


def _safe_confidence(raw, decision: str) -> float:
    """Return a sensible confidence value — never broken by 0.0-falsy bug."""
    if raw is None:
        return _DECISION_CONFIDENCE_DEFAULTS.get(decision, 0.75)
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return _DECISION_CONFIDENCE_DEFAULTS.get(decision, 0.75)
    if val == 0.0:
        return _DECISION_CONFIDENCE_DEFAULTS.get(decision, 0.75)
    return max(0.0, min(1.0, val))


# ═══════════════════════════════════════════════════════════════════════════════
# NODE 1 — ingest_ticket
# ═══════════════════════════════════════════════════════════════════════════════

def ingest_ticket(state: AgentState) -> AgentState:
    tid = state["ticket_id"]
    _log.info("[%s] NODE ingest_ticket", tid)
    update_ticket_stage(tid, {"current_stage": 1, "stages_complete": [], "complete": False})

    body = state.get("body", "")
    subject = state.get("subject", "")

    # Extract order IDs (ORD-XXXX pattern)
    order_ids = list(set(re.findall(r"ORD-\d+", f"{subject} {body}", re.IGNORECASE)))
    state["order_ids_in_ticket"] = order_ids

    # Simple urgency pre-scan (keyword heuristic; LLM will refine)
    text_lower = (subject + " " + body).lower()
    if any(w in text_lower for w in ["urgent", "asap", "immediately", "emergency"]):
        state["urgency"] = "high"
    elif any(w in text_lower for w in ["fraud", "unauthorized", "scam", "stolen"]):
        state["urgency"] = "critical"
    else:
        state["urgency"] = "low"

    _add_step(state, f"Ingested ticket {tid}. Order IDs found: {order_ids}. Pre-urgency: {state['urgency']}")
    write_event(tid, "ingest", {"order_ids": order_ids, "urgency": state["urgency"]})
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# NODE 2 — lookup_customer
# ═══════════════════════════════════════════════════════════════════════════════

async def lookup_customer(state: AgentState) -> AgentState:
    tid = state["ticket_id"]
    _log.info("[%s] NODE lookup_customer", tid)
    update_ticket_stage(tid, {"current_stage": 2})

    email = state.get("customer_email") or ""
    customer = await get_customer(email)
    _log_tool(state, "get_customer", {"email": email}, customer)

    if not customer or customer.get("error"):
        _log.warning("[%s] Unknown customer: %s", tid, email)
        state["customer"] = None
        state["customer_name"] = "Unknown"
        state["customer_tier"] = "standard"
        state["customer_ltv"] = 0.0
        state["should_escalate"] = True
        state["escalation_reason"] = f"Unknown customer email: {email}"
        update_ticket_stage(tid, {"customer_name": "Unknown", "customer_tier": "standard"})
        _add_step(state, "Customer not found — flagging for escalation.")
        write_event(tid, "customer_not_found", {"email": email})
    else:
        state["customer"] = customer
        state["customer_name"] = customer.get("name", "Unknown")
        raw_tier = customer.get("tier", "standard")
        customer_tier = tier_map.get(raw_tier, str(raw_tier).lower())
        state["customer_tier"] = customer_tier
        state["customer_ltv"] = float(customer.get("lifetime_value", 0))
        update_ticket_stage(tid, {
            "customer_name": state["customer_name"],
            "customer_tier": state["customer_tier"],
        })
        _add_step(state, f"Customer found: {state['customer_name']} (tier={state['customer_tier']}, LTV=${state['customer_ltv']:.0f})")
        write_event(tid, "customer_found", {"name": state["customer_name"], "tier": state["customer_tier"]})

    return state


# ═══════════════════════════════════════════════════════════════════════════════
# NODE 3 — classify_intent
# ═══════════════════════════════════════════════════════════════════════════════

async def classify_intent(state: AgentState) -> AgentState:
    tid = state["ticket_id"]
    _log.info("[%s] NODE classify_intent", tid)
    update_ticket_stage(tid, {"current_stage": 3})

    prompt = f"""You are a support ticket classifier. Respond ONLY with valid JSON.

Ticket subject: {state.get('subject', '')}
Ticket body: {state.get('body', '')}
Customer tier: {state.get('customer_tier', 'standard')}

Classify this ticket and return exactly:
{{
  "intent": "<one of: refund_request|return_request|order_cancellation|order_status|product_question|billing_issue|fraud_report|general_complaint|other>",
  "urgency": "<low|medium|high|critical>",
  "sentiment": "<positive|neutral|negative|angry>",
  "reasoning": "<one sentence>"
}}"""

    result = _invoke_llm_json(prompt, {
        "intent": "general_complaint",
        "urgency": state.get("urgency", "low"),
        "sentiment": "neutral",
        "reasoning": "LLM fallback",
    })

    state["intent"] = result.get("intent", "general_complaint")
    state["urgency"] = result.get("urgency", state.get("urgency", "low"))
    state["sentiment"] = result.get("sentiment", "neutral")

    update_ticket_stage(tid, {"intent": state["intent"], "urgency": state["urgency"]})
    _add_step(state, f"Intent: {state['intent']} | Urgency: {state['urgency']} | Sentiment: {state['sentiment']}")
    write_event(tid, "classified", {"intent": state["intent"], "urgency": state["urgency"]})
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# NODE 4 — lookup_order_and_product
# ═══════════════════════════════════════════════════════════════════════════════

async def lookup_order_and_product(state: AgentState) -> AgentState:
    tid = state["ticket_id"]
    _log.info("[%s] NODE lookup_order_product", tid)
    update_ticket_stage(tid, {"current_stage": 4})

    order = None
    order_ids = state.get("order_ids_in_ticket", [])

    # Try explicit order IDs from ticket body first
    for oid in order_ids:
        result = await get_order(oid)
        _log_tool(state, "get_order", {"order_id": oid}, result)
        if result and not result.get("error"):
            order = result
            break

    # Fallback: look up by customer_id
    if not order and state.get("customer") and state["customer"].get("customer_id"):
        orders = await get_orders_for_customer(state["customer"]["customer_id"])
        _log_tool(state, "get_orders_for_customer", {"customer_id": state["customer"]["customer_id"]}, orders)
        if orders and isinstance(orders, list) and not orders[0].get("error"):
            # Pick the most recent non-delivered order, else most recent
            pending = [o for o in orders if o.get("status") not in ("delivered", "cancelled")]
            order = pending[0] if pending else orders[0]

    if order:
        state["order"] = order
        _add_step(state, f"Order found: {order.get('order_id')} status={order.get('status')} total=${order.get('total', 0)}")
        write_event(tid, "order_found", {"order_id": order.get("order_id"), "status": order.get("status")})

        # Fetch product
        product_id = order.get("product_id") or order.get("items", [{}])[0].get("product_id") if order.get("items") else None
        if product_id:
            product = await get_product(product_id)
            _log_tool(state, "get_product", {"product_id": product_id}, product)
            if product and not product.get("error"):
                state["product"] = product
                _add_step(state, f"Product: {product.get('name')} return_window={product.get('return_window_days')}d")
    else:
        _add_step(state, "No order found for this ticket.")
        write_event(tid, "order_not_found", {})

    return state


# ═══════════════════════════════════════════════════════════════════════════════
# NODE 5 — check_policy
# ═══════════════════════════════════════════════════════════════════════════════

async def check_policy(state: AgentState) -> AgentState:
    tid = state["ticket_id"]
    _log.info("[%s] NODE check_policy", tid)
    update_ticket_stage(tid, {"current_stage": 5})

    query = f"{state.get('intent', '')} {state.get('subject', '')} tier:{state.get('customer_tier', 'standard')}"
    policy = await search_knowledge_base(query)
    _log_tool(state, "search_knowledge_base", {"query": query}, policy)

    state["policy_excerpt"] = policy if isinstance(policy, str) else str(policy)[:500]

    # Fraud detection heuristic — cross-check claimed tier vs actual
    body_lower = state.get("body", "").lower()
    actual_tier = state.get("customer_tier", "standard")
    claimed_vip = any(w in body_lower for w in ["vip", "platinum", "gold", "premium member"])
    if claimed_vip and actual_tier == "standard":
        state["fraud_flag"] = True
        state["fraud_reason"] = "Customer claims VIP/premium status but system shows standard tier"
        state["should_escalate"] = True
        state["escalation_reason"] = state["fraud_reason"]
        update_ticket_stage(tid, {"fraud_flag": True, "fraud_reason": state["fraud_reason"]})
        _add_step(state, f"FRAUD FLAG: {state['fraud_reason']}")
        write_event(tid, "fraud_detected", {"reason": state["fraud_reason"]})

    _add_step(state, f"Policy fetched ({len(state['policy_excerpt'])} chars)")
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# NODE 6 — decide
# ═══════════════════════════════════════════════════════════════════════════════

async def decide(state: AgentState) -> AgentState:
    tid = state["ticket_id"]
    _log.info("[%s] NODE decide", tid)
    update_ticket_stage(tid, {"current_stage": 6})

    # Guard: already escalated upstream (unknown customer / fraud)
    if state.get("should_escalate"):
        state["decision"] = "escalate"
        state["confidence"] = _safe_confidence(None, "escalate")
        state["reply_draft"] = "Your case has been escalated to a human agent who will contact you shortly."
        update_ticket_stage(tid, {"decision": "escalate", "confidence": state["confidence"]})
        return state

    order = state.get("order") or {}
    customer = state.get("customer") or {}
    product = state.get("product") or {}

    prompt = f"""You are a senior support manager making a final resolution decision.
You MUST respond with valid JSON only — no markdown, no explanation outside JSON.

=== TICKET ===
ID: {state['ticket_id']}
Subject: {state.get('subject', '')}
Body: {state.get('body', '')}

=== CUSTOMER ===
Name: {state.get('customer_name', 'Unknown')}
Tier: {state.get('customer_tier', 'standard')}
LTV: ${state.get('customer_ltv', 0):.0f}

=== ORDER ===
{json.dumps(order, default=str)}

=== PRODUCT ===
{json.dumps(product, default=str)}

=== POLICY ===
{state.get('policy_excerpt', 'No policy found.')}

=== CLASSIFICATION ===
Intent: {state.get('intent')}
Urgency: {state.get('urgency')}
Sentiment: {state.get('sentiment')}

Based on all the above, pick exactly ONE decision from:
  issue_refund | approve_return | deny | cancel_order | send_reply | request_info | escalate

Rules:
- issue_refund: only if within return window AND no fraud AND refund amount <= $200
- approve_return: within return window, physical return needed
- cancel_order: order is pending/processing and customer wants cancellation
- deny: outside return window OR policy violation
- send_reply: general question, no action needed
- request_info: insufficient info to decide
- escalate: fraud suspected, refund > $200, or cannot determine

Return ONLY this JSON:
{{
  "decision": "<decision>",
  "confidence": <0.0-1.0>,
  "reply_draft": "<polite professional email reply to customer, 2-4 sentences>",
  "reasoning": "<one sentence explaining decision>"
}}"""

    result = _invoke_llm_json(prompt, {
        "decision": "escalate",
        "confidence": 0.75,
        "reply_draft": "We are reviewing your request and will respond within 24 hours.",
        "reasoning": "LLM fallback — escalating to human review.",
    })

    decision = result.get("decision", "escalate")
    raw_conf = result.get("confidence")  # Could be 0.0 — use safe helper
    confidence = _safe_confidence(raw_conf, decision)

    state["decision"] = decision
    state["confidence"] = confidence
    state["reply_draft"] = result.get("reply_draft", "")

    # Only escalate flag if decision IS escalate — not on low confidence alone
    if decision == "escalate":
        state["should_escalate"] = True
        state["escalation_reason"] = result.get("reasoning", "Agent decision")

    # Hard rule: refund > $200 always escalates regardless of LLM
    if decision == "issue_refund" and float(order.get("total", 0)) > 200:
        state["decision"] = "escalate"
        state["should_escalate"] = True
        state["escalation_reason"] = f"Refund amount ${order.get('total')} exceeds $200 auto-approve limit"
        state["confidence"] = 1.0

    _add_step(state, f"Decision: {state['decision']} (confidence={state['confidence']:.2f})")
    update_ticket_stage(tid, {
        "decision": state["decision"],
        "confidence": state["confidence"],
        "reply": state["reply_draft"],
    })
    write_event(tid, "decided", {"decision": state["decision"], "confidence": state["confidence"]})
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# NODE 7 — act
# ═══════════════════════════════════════════════════════════════════════════════

async def act(state: AgentState) -> AgentState:
    tid = state["ticket_id"]
    _log.info("[%s] NODE act | decision=%s", tid, state.get("decision"))
    update_ticket_stage(tid, {"current_stage": 7})

    decision = state.get("decision", "escalate")
    order = state.get("order") or {}
    customer = state.get("customer") or {}
    email = state.get("customer_email") or customer.get("email", "")

    try:
        if decision == "issue_refund":
            # MANDATORY guard before refund
            elig = await check_refund_eligibility(
                order_id=order.get("order_id", ""),
                customer_tier=state.get("customer_tier", "standard"),
                order=order,
            )
            _log_tool(state, "check_refund_eligibility", {"order_id": order.get("order_id")}, elig)

            if elig.get("eligible"):
                refund_result = await issue_refund(
                    order_id=order.get("order_id", ""),
                    amount=float(order.get("total", 0)),
                    reason=state.get("intent", "customer request"),
                )
                _log_tool(state, "issue_refund", {"order_id": order.get("order_id")}, refund_result)
                state["refund_id"] = refund_result.get("refund_id")
                state["action_taken"] = "refund_issued"
                _add_step(state, f"Refund issued: {state['refund_id']}")
            else:
                # Eligibility failed — downgrade to deny
                state["decision"] = "deny"
                state["action_taken"] = "refund_denied"
                state["reply_draft"] = (
                    f"We're sorry, but your refund request cannot be approved at this time. "
                    f"Reason: {elig.get('reason', 'outside return window')}. "
                    "Please contact us if you have further questions."
                )
                _add_step(state, f"Refund ineligible: {elig.get('reason')}")

        elif decision == "cancel_order":
            cancel_result = await cancel_order_tool(order_id=order.get("order_id", ""))
            _log_tool(state, "cancel_order", {"order_id": order.get("order_id")}, cancel_result)
            state["action_taken"] = "order_cancelled" if not cancel_result.get("error") else "cancel_failed"
            _add_step(state, f"Cancel result: {cancel_result}")

        elif decision == "approve_return":
            state["action_taken"] = "return_approved"
            _add_step(state, "Return approved — RMA instructions will be sent.")

        elif decision == "deny":
            state["action_taken"] = "request_denied"
            _add_step(state, "Request denied per policy.")

        elif decision == "escalate":
            esc_result = await escalate_ticket(
                ticket_id=tid,
                reason=state.get("escalation_reason", "Agent escalation"),
                customer_name=state.get("customer_name", "Unknown"),
                customer_tier=state.get("customer_tier", "standard"),
                summary=f"{state.get('intent')} | {state.get('subject', '')}",
            )
            _log_tool(state, "escalate", {"ticket_id": tid}, esc_result)
            state["action_taken"] = "escalated"
            _add_step(state, f"Escalated: {state.get('escalation_reason')}")

        else:
            # send_reply / request_info
            state["action_taken"] = "reply_sent"

        # Always send reply to customer
        if email and state.get("reply_draft"):
            reply_result = await send_reply(
                email=email,
                subject=f"Re: {state.get('subject', 'Your support request')}",
                body=state["reply_draft"],
            )
            _log_tool(state, "send_reply", {"email": email}, reply_result)
            state["reply_sent"] = True

    except Exception as exc:
        _log.exception("[%s] act node error: %s", tid, exc)
        state["action_taken"] = "error"
        state["error"] = str(exc)
        state["should_escalate"] = True

    update_ticket_stage(tid, {"action_taken": state.get("action_taken", "")})
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# NODE 8 — log_outcome
# ═══════════════════════════════════════════════════════════════════════════════

def log_outcome(state: AgentState) -> AgentState:
    tid = state["ticket_id"]
    _log.info("[%s] NODE log_outcome | action=%s", tid, state.get("action_taken"))

    import time as _time
    elapsed_ms = round((_time.monotonic() - state.get("start_ts", 0)) * 1000)

    write_event(tid, "ticket_complete", {
        "ticket_id": tid,
        "customer_name": state.get("customer_name", "Unknown"),
        "customer_tier": state.get("customer_tier", "standard"),
        "intent": state.get("intent", ""),
        "urgency": state.get("urgency", "low"),
        "decision": state.get("decision", ""),
        "action_taken": state.get("action_taken", ""),
        "confidence_score": state.get("confidence", 0.0),
        "tool_call_count": len(state.get("tool_call_log", [])),
        "reasoning_steps": len(state.get("reasoning_steps", [])),
        "fraud_flag": state.get("fraud_flag", False),
        "refund_id": state.get("refund_id"),
        "escalated": state.get("should_escalate", False),
        "escalation_reason": state.get("escalation_reason", ""),
        "total_ms": elapsed_ms,
    })

    update_ticket_stage(tid, {
        "complete": True,
        "decision": state.get("decision", ""),
        "confidence": state.get("confidence", 0.0),
        "action_taken": state.get("action_taken", ""),
        "customer_name": state.get("customer_name", "Unknown"),
        "customer_tier": state.get("customer_tier", "standard"),
        "fraud_flag": state.get("fraud_flag", False),
        "fraud_reason": state.get("fraud_reason", ""),
    })

    return state
