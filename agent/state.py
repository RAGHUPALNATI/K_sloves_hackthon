"""
AgentState — single source of truth for everything flowing through the graph.
"""
from __future__ import annotations
from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # ── Input ──────────────────────────────────────────────────────────────
    ticket_id: str
    customer_id: str | None
    customer_email: str | None
    subject: str
    body: str
    timestamp: str

    # ── Lookup results ─────────────────────────────────────────────────────
    customer: dict | None
    customer_name: str
    customer_tier: str          # standard | premium | vip
    customer_ltv: float
    order: dict | None
    order_ids_in_ticket: list[str]
    product: dict | None

    # ── Classification ─────────────────────────────────────────────────────
    intent: str
    urgency: str                # low | medium | high | critical
    sentiment: str              # positive | neutral | negative | angry

    # ── Policy & guard rails ───────────────────────────────────────────────
    policy_excerpt: str
    refund_eligible: bool
    refund_reason: str
    fraud_flag: bool
    fraud_reason: str
    guard_fired: bool
    guard_outcome: str

    # ── Decision ───────────────────────────────────────────────────────────
    decision: str               # issue_refund | approve_return | deny | cancel_order | send_reply | request_info | escalate
    confidence: float
    reply_draft: str
    should_escalate: bool
    escalation_reason: str

    # ── Action output ──────────────────────────────────────────────────────
    action_taken: str
    refund_id: str | None
    reply_sent: bool

    # ── Tracing ────────────────────────────────────────────────────────────
    tool_call_log: list[dict]
    reasoning_steps: list[str]
    error: str | None
    start_ts: float


def initial_state(ticket: dict) -> AgentState:
    import time
    return AgentState(
        ticket_id=ticket["ticket_id"],
        customer_id=ticket.get("customer_id"),
        customer_email=ticket.get("customer_email"),
        subject=ticket.get("subject", ""),
        body=ticket.get("body", ""),
        timestamp=ticket.get("timestamp", ""),
        customer=None,
        customer_name="Unknown",
        customer_tier="standard",
        customer_ltv=0.0,
        order=None,
        order_ids_in_ticket=[],
        product=None,
        intent="",
        urgency="low",
        sentiment="neutral",
        policy_excerpt="",
        refund_eligible=False,
        refund_reason="",
        fraud_flag=False,
        fraud_reason="",
        guard_fired=False,
        guard_outcome="",
        decision="",
        confidence=0.0,
        reply_draft="",
        should_escalate=False,
        escalation_reason="",
        action_taken="",
        refund_id=None,
        reply_sent=False,
        tool_call_log=[],
        reasoning_steps=[],
        error=None,
        start_ts=time.monotonic(),
    )
