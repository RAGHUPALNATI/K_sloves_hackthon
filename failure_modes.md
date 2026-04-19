# ShopWave Agent - Failure Mode Analysis

This document outlines key failure scenarios specifically engineered into the autonomous agent and how the agent's reasoning loop handles them gracefully without crashing.

## 1. Tool Exhaustion / Repeated Error Loop
**Scenario**: The agent attempts to query an order status, but the database API returns a timeout repeatedly, or the agent enters a loop of retrying the same incorrect tool arguments.
**Handling Mechanism**: The LangGraph state tracker limits maximum tool invocation attempts sequentially. If the same tool fails due to errors (e.g., malformed data, timeouts) multiple times, the node returns an `"error": "tool_exhausted"` flag. The `act_node` detects this through `_update_state_from_tool_result`, safely escapes the retry cycle, logs an `error_count`, increments the urgency parameter, and immediately triggers the `escalate` fallback, assigning the ticket to a human along with a log of the failed attempts. The agent never gets stuck in infinite recursion.

## 2. Irreversible Action with Eligibility Failure
**Scenario**: An LLM confidently (perhaps hallucinating) decides to issue a full refund for an order that is past its 30-day window, attempting to call `issue_refund(order_id, amount)` directly. 
**Handling Mechanism**: Critical deterministic guardrails exist outside the non-deterministic LLM layer. In the `act_node` refund path, the system enforces a strict requirement: even if `decision == "issue_refund"`, the system *must* sequentially await `check_refund_eligibility` first. If the return window is exceeded, the mock tool returns `{"error": "return_window_expired"}`. The agent detects this hard limit, overrides the LLM's raw intent, prevents the `issue_refund` execution, and alters the output string to escalate intelligently to tier-2 human operations with note: "Refund denied by strict policy—requires human override".

## 3. Fraud / Exploitation Attempts
**Scenario**: A bad actor attempts to socially engineer the agent, claiming they are a "VIP Platinum customer" who demands an instant refund with "no questions asked," banking on the LLM's tendency to be agreeable and trust user input.
**Handling Mechanism**: The `lookup_customer_node` performs a proactive semantic fraud verification *before* the prompt ever reaches the LLM. It compares the raw text of the ticket body against internal database truths. If a user utilizes `premium_keywords` but the database yields a `standard` customer tier, the agent sets `state["fraud_flag"] = True`. The graph topology (`_route_after_lookup_customer`) uses this dynamic flag to skip standard resolution, bypass all immediate trust actions, and directly force the decision engine into an anomaly handling state. The final logged outcome explicitly flags the suspicious discrepancy with `fraud_reason`.
