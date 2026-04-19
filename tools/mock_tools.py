"""
mock_tools.py — Async tool implementations for ShopWave agent.

Improvements vs original:
  - check_refund_eligibility now compares actual return_deadline dates (not a stub)
  - All tools have realistic 10% failure injection for resilience testing
  - cancel_order checks order status before allowing cancellation
  - Proper async signatures throughout
"""
from __future__ import annotations

import json
import logging
import random
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger("shopwave.tools")

tier_map = {
    "1": "standard",
    "2": "premium",
    "3": "vip",
    1: "standard",
    2: "premium",
    3: "vip",
}

# ── Data loading ──────────────────────────────────────────────────────────────

_DATA_DIR = Path(__file__).parent.parent / "data"

def _load(filename: str) -> dict | list:
    path = _DATA_DIR / filename
    if not path.exists():
        _log.warning("Data file not found: %s", path)
        return {} if "json" in filename else []
    with path.open(encoding="utf-8") as f:
        return json.load(f)

_CUSTOMERS: list[dict] = []
_ORDERS: list[dict] = []
_PRODUCTS: list[dict] = []
_KB_TEXT: str = ""

def _ensure_loaded():
    global _CUSTOMERS, _ORDERS, _PRODUCTS, _KB_TEXT
    if not _CUSTOMERS:
        raw = _load("customers.json")
        _CUSTOMERS = raw if isinstance(raw, list) else list(raw.values())
    if not _ORDERS:
        raw = _load("orders.json")
        _ORDERS = raw if isinstance(raw, list) else list(raw.values())
    if not _PRODUCTS:
        raw = _load("products.json")
        _PRODUCTS = raw if isinstance(raw, list) else list(raw.values())
    if not _KB_TEXT:
        kb_path = _DATA_DIR / "knowledge-base.md"
        if kb_path.exists():
            _KB_TEXT = kb_path.read_text(encoding="utf-8")


def _maybe_fail(tool_name: str) -> dict | None:
    """10% simulated failure for resilience testing."""
    if random.random() < 0.10:
        _log.warning("[TOOL FAIL] %s — simulated 10%% failure", tool_name)
        return {"error": f"{tool_name} transient failure (simulated)", "retry": True}
    return None


# ── Tool implementations ──────────────────────────────────────────────────────

async def get_customer(email: str) -> dict:
    _ensure_loaded()
    fail = _maybe_fail("get_customer")
    if fail:
        return fail
    if not email:
        return {"error": "No email provided"}
    email_lower = email.lower().strip()
    for c in _CUSTOMERS:
        if c.get("email", "").lower() == email_lower:
            return c
    return {"error": f"Customer not found: {email}"}


async def get_order(order_id: str) -> dict:
    _ensure_loaded()
    fail = _maybe_fail("get_order")
    if fail:
        return fail
    if not order_id:
        return {"error": "No order_id provided"}
    oid = order_id.upper().strip()
    for o in _ORDERS:
        if o.get("order_id", "").upper() == oid:
            return o
    return {"error": f"Order not found: {order_id}"}


async def get_orders_for_customer(customer_id: str) -> list[dict]:
    _ensure_loaded()
    fail = _maybe_fail("get_orders_for_customer")
    if fail:
        return [fail]
    orders = [o for o in _ORDERS if o.get("customer_id") == customer_id]
    # Sort by order_date descending
    orders.sort(key=lambda o: o.get("order_date", ""), reverse=True)
    return orders if orders else [{"error": f"No orders for customer {customer_id}"}]


async def get_product(product_id: str) -> dict:
    _ensure_loaded()
    fail = _maybe_fail("get_product")
    if fail:
        return fail
    pid = str(product_id).upper().strip()
    for p in _PRODUCTS:
        if str(p.get("product_id", "")).upper() == pid:
            return p
    return {"error": f"Product not found: {product_id}"}


async def search_knowledge_base(query: str) -> str:
    _ensure_loaded()
    fail = _maybe_fail("search_knowledge_base")
    if fail:
        return "Knowledge base temporarily unavailable."
    if not _KB_TEXT:
        return "No knowledge base loaded."
    query_lower = query.lower()
    lines = _KB_TEXT.split("\n")
    scored = []
    for line in lines:
        if not line.strip():
            continue
        score = sum(1 for word in query_lower.split() if word in line.lower())
        if score > 0:
            scored.append((score, line))
    scored.sort(reverse=True)
    top = [line for _, line in scored[:8]]
    return "\n".join(top) if top else "No relevant policy found for this query."


async def check_refund_eligibility(
    order_id: str,
    customer_tier: str,
    order: dict | None = None,
) -> dict:
    """
    FIXED VERSION — actually checks return_deadline against today's date.
    Original was a stub that always returned ineligible.
    """
    _ensure_loaded()
    fail = _maybe_fail("check_refund_eligibility")
    if fail:
        return fail

    # Fetch order if not provided
    if not order:
        order = await get_order(order_id)
    if not order or order.get("error"):
        return {"eligible": False, "reason": f"Order not found: {order_id}"}

    status = order.get("status", "").lower()
    refund_status = order.get("refund_status", "").lower()

    # Already refunded
    if refund_status in ("refunded", "processing"):
        return {"eligible": False, "reason": "Order already refunded or refund in progress"}

    # Cancelled orders can't be refunded (they weren't charged)
    if status == "cancelled":
        return {"eligible": False, "reason": "Order is already cancelled"}

    # Check return deadline
    deadline_str = order.get("return_deadline") or order.get("return_deadline_date")
    if deadline_str:
        try:
            deadline = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if now > deadline:
                days_over = (now - deadline).days
                return {
                    "eligible": False,
                    "reason": f"Return deadline passed {days_over} day(s) ago (deadline: {deadline_str})",
                }
        except ValueError:
            _log.warning("Could not parse return_deadline: %s", deadline_str)

    # VIP / premium tier — extended window exception
    raw_tier = customer_tier
    tier = tier_map.get(raw_tier, str(raw_tier).lower())
    if tier in ("vip", "premium"):
        # Check for standing exception note
        customer_notes = order.get("_customer_notes", "").lower()
        if "standing exception" in customer_notes or tier == "vip":
            return {"eligible": True, "reason": "VIP customer — standing return exception applies"}

    # Default: eligible (within window, no blocks)
    return {"eligible": True, "reason": "Within return window"}


async def issue_refund(order_id: str, amount: float, reason: str) -> dict:
    fail = _maybe_fail("issue_refund")
    if fail:
        return fail
    refund_id = f"REF-{uuid.uuid4().hex[:8].upper()}"
    _log.info("REFUND ISSUED: %s | order=%s | amount=$%.2f | reason=%s", refund_id, order_id, amount, reason)
    return {
        "refund_id": refund_id,
        "order_id": order_id,
        "amount": amount,
        "status": "processed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def cancel_order_tool(order_id: str) -> dict:
    _ensure_loaded()
    fail = _maybe_fail("cancel_order")
    if fail:
        return fail
    order = await get_order(order_id)
    if order.get("error"):
        return {"error": f"Cannot cancel — order not found: {order_id}"}
    status = order.get("status", "").lower()
    if status in ("shipped", "delivered"):
        return {"error": f"Cannot cancel order {order_id} — already {status}. Customer must request return instead."}
    if status == "cancelled":
        return {"error": f"Order {order_id} is already cancelled."}
    _log.info("ORDER CANCELLED: %s", order_id)
    return {
        "order_id": order_id,
        "status": "cancelled",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def send_reply(email: str, subject: str, body: str) -> dict:
    fail = _maybe_fail("send_reply")
    if fail:
        return fail
    _log.info("EMAIL SENT → %s | subject='%s'", email, subject)
    return {
        "delivered": True,
        "to": email,
        "subject": subject,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def escalate_ticket(
    ticket_id: str,
    reason: str,
    customer_name: str,
    customer_tier: str,
    summary: str,
) -> dict:
    fail = _maybe_fail("escalate")
    if fail:
        return fail
    raw_tier = customer_tier
    customer_tier = tier_map.get(raw_tier, str(raw_tier).lower())
    escalation_id = f"ESC-{uuid.uuid4().hex[:6].upper()}"
    _log.info("ESCALATED: %s → %s | %s | reason=%s", ticket_id, escalation_id, customer_name, reason)
    return {
        "escalation_id": escalation_id,
        "ticket_id": ticket_id,
        "assigned_to": "human_queue",
        "priority": "high" if customer_tier in ("vip", "premium") else "normal",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
