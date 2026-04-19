"""
graph.py — Builds and compiles the ShopWave LangGraph StateGraph.
"""
from __future__ import annotations

from langgraph.graph import StateGraph, END

from agent.state import AgentState
from agent.nodes import (
    ingest_ticket,
    lookup_customer,
    classify_intent,
    lookup_order_and_product,
    check_policy,
    decide,
    act,
    log_outcome,
)


def _route_after_customer(state: AgentState) -> str:
    """Skip classify→decide→act if customer is unknown; go straight to act (escalate)."""
    if state.get("should_escalate") and state.get("customer") is None:
        return "act"
    return "classify_intent"


def _route_after_decide(state: AgentState) -> str:
    return "act"


def build_graph() -> StateGraph:
    g = StateGraph(AgentState)

    g.add_node("ingest_ticket", ingest_ticket)
    g.add_node("lookup_customer", lookup_customer)
    g.add_node("classify_intent", classify_intent)
    g.add_node("lookup_order_product", lookup_order_and_product)
    g.add_node("check_policy", check_policy)
    g.add_node("decide", decide)
    g.add_node("act", act)
    g.add_node("log_outcome", log_outcome)

    g.set_entry_point("ingest_ticket")
    g.add_edge("ingest_ticket", "lookup_customer")

    # Short-circuit unknown customers directly to act (escalate path)
    g.add_conditional_edges(
        "lookup_customer",
        _route_after_customer,
        {
            "classify_intent": "classify_intent",
            "act": "act",
        },
    )

    g.add_edge("classify_intent", "lookup_order_product")
    g.add_edge("lookup_order_product", "check_policy")
    g.add_edge("check_policy", "decide")
    g.add_edge("decide", "act")
    g.add_edge("act", "log_outcome")
    g.add_edge("log_outcome", END)

    return g.compile()


AGENT_GRAPH = build_graph()
