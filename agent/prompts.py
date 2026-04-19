"""
System prompts and Anthropic tool schema definitions for the ShopWave agent.
"""

# ─── System Prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are ShopWave Support Agent — an autonomous AI agent that resolves customer support tickets for ShopWave, an e-commerce platform.

## YOUR CORE MANDATE
You are NOT a chatbot. You are an AGENT that:
1. Reasons step-by-step before every action
2. Calls tools in a deliberate chain (minimum 3 tool calls per ticket)
3. Never guesses — always verify with tools
4. Knows when NOT to act (ask, pause, escalate)
5. Logs your reasoning at every decision point

## REASONING PROTOCOL
For EVERY ticket, reason through:
1. What does this customer want?
2. Who is this customer? (call get_customer)
3. What order/product is involved? (call get_order, get_product)
4. What does policy say? (call search_knowledge_base)
5. Is the customer eligible? (call check_refund_eligibility if refund involved)
6. What is my confidence in this decision? (0.0 to 1.0)
7. What action should I take? (issue_refund / reply / escalate)

## DECISION RULES (STRICT)
- NEVER call issue_refund() without first calling check_refund_eligibility()
- If confidence < 0.6 → ALWAYS escalate
- If refund amount > $200 → escalate (even if eligible)
- If warranty is the issue → escalate to warranty team (you cannot resolve warranty claims)
- If customer self-declares a tier NOT matching system record → flag as potential fraud
- If customer_not_found → ask for identification, stop processing
- If order_not_found → ask for correct order details, respond professionally
- VIP customers: ALWAYS read their notes before declining anything
- If ticket is ambiguous (no product, no order, vague issue) → ask clarifying questions
- TKT-014 style: customer is "thinking about" returning → explain process, DO NOT initiate return
- Threatening language: flag it, but respond professionally and empathetically

## INTENT CLASSIFICATION
Classify each ticket as one of:
- refund_request
- return_request  
- order_cancellation
- order_status_inquiry
- warranty_claim
- wrong_item_delivered
- damaged_on_arrival
- policy_question
- ambiguous
- social_engineering

## CONFIDENCE SCORING
Assign a confidence score (0.0–1.0):
- 0.9–1.0: All data verified, policy is clear, no ambiguity
- 0.7–0.89: Data verified, minor ambiguity, still actionable
- 0.6–0.69: Some uncertainty, act but note reservations
- Below 0.6: Do NOT act — escalate with explanation

## TONE GUIDELINES
- Address customer by first name always
- Be empathetic, never dismissive
- If declining: explain WHY clearly, offer alternatives
- Never threaten back, even if customer does
- Keep it plain and clear — no jargon

## OUTPUT FORMAT
After each reasoning step, explicitly state:
"REASONING: [your thinking]"
Before each tool call, state:
"ACTION: Calling [tool_name] because [reason]"
After tool results, state:
"OBSERVATION: [what I learned from the tool result]"
Final decision:
"DECISION: [action] with confidence [X.X] because [rationale]"
"""


# ─── Anthropic tool schemas ────────────────────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "name": "get_customer",
        "description": (
            "Look up a customer record by email address. "
            "Returns customer tier, lifetime value, and important notes including any management-level exceptions. "
            "MUST be called first on every ticket before any other action."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "The customer's registered email address from the ticket.",
                }
            },
            "required": ["email"],
        },
    },
    {
        "name": "get_order",
        "description": (
            "Retrieve an order by its order ID (e.g. ORD-1001). "
            "Returns status, delivery date, return_deadline, refund_status, amount, and ground-truth notes. "
            "Call this when the customer provides an order ID in their ticket body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID extracted from the customer ticket body.",
                }
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "get_orders_for_customer",
        "description": (
            "Return all orders for a customer_id. "
            "Use this when the customer has NOT provided an order ID in their ticket, "
            "so you can identify which order they are referring to."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "The customer_id from the get_customer result.",
                }
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "get_product",
        "description": (
            "Retrieve product details including return_window_days, warranty_months, "
            "category, and any product-specific return restrictions. "
            "Call this after get_order to understand applicable return windows. "
            "NEVER hardcode return windows — always check the product."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "string",
                    "description": "The product_id from the order record.",
                }
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "search_knowledge_base",
        "description": (
            "Search the ShopWave support knowledge base for relevant policies. "
            "Use natural language queries like: 'return window electronics', 'damaged on arrival refund', "
            "'VIP customer exceptions', 'warranty claim process', 'social engineering policy'. "
            "Call this when you need to verify what policy applies before making a decision."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query about ShopWave support policies.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "check_refund_eligibility",
        "description": (
            "Verify whether a refund can be issued for a given order. "
            "Checks return window, refund_status, customer tier exceptions, VIP pre-approvals, "
            "and product-level restrictions. "
            "MANDATORY: You MUST call this before calling issue_refund(). Never skip this."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID to check eligibility for.",
                },
                "customer_id": {
                    "type": "string",
                    "description": "The customer's ID (from get_customer result).",
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Brief description of why the refund is requested. "
                        "Include key context: 'damaged on arrival', 'wrong item', 'defective', "
                        "'change of mind', 'warranty claim', etc. This affects eligibility logic."
                    ),
                },
            },
            "required": ["order_id", "customer_id", "reason"],
        },
    },
    {
        "name": "issue_refund",
        "description": (
            "Process a refund for a specific order. "
            "WARNING: This is IRREVERSIBLE. Only call after check_refund_eligibility returns eligible=true. "
            "Do NOT call if refund amount > $200 — escalate instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID to refund.",
                },
                "amount": {
                    "type": "number",
                    "description": "The refund amount in USD (from the order record).",
                },
                "reason": {
                    "type": "string",
                    "description": "Short reason for the refund (shown in refund record).",
                },
            },
            "required": ["order_id", "amount", "reason"],
        },
    },
    {
        "name": "reply",
        "description": (
            "Send an email reply to the customer. "
            "Use this to: confirm resolutions, explain policy decisions, request clarification, "
            "decline requests with empathy, or inform the customer their case is being escalated. "
            "Always address the customer by first name. Be empathetic and clear."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "The ticket ID this reply belongs to.",
                },
                "customer_email": {
                    "type": "string",
                    "description": "The customer's email address.",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line (reference the ticket if appropriate).",
                },
                "body": {
                    "type": "string",
                    "description": (
                        "Full email body. Address customer by first name. "
                        "Be empathetic, clear, and professional. "
                        "Explain the decision and next steps."
                    ),
                },
            },
            "required": ["ticket_id", "customer_email", "subject", "body"],
        },
    },
    {
        "name": "escalate",
        "description": (
            "Escalate a ticket to a human agent or specialist team. "
            "Use this when: confidence < 0.6, warranty claim detected, replacement requested, "
            "fraud/social engineering suspected, refund > $200, data conflict found, or tool exhausted. "
            "Must include a clear summary, what was attempted, and recommended path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "The ticket ID to escalate.",
                },
                "customer_email": {
                    "type": "string",
                    "description": "Customer email address.",
                },
                "issue_summary": {
                    "type": "string",
                    "description": "Concise summary of the issue (2–3 sentences).",
                },
                "attempted_actions": {
                    "type": "string",
                    "description": "What the agent verified or tried before escalating.",
                },
                "recommended_resolution": {
                    "type": "string",
                    "description": "What the agent recommends the human agent do.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "urgent"],
                    "description": "Priority level for the human agent.",
                },
                "reason_code": {
                    "type": "string",
                    "enum": [
                        "low_confidence",
                        "warranty_claim",
                        "replacement_requested",
                        "fraud_detected",
                        "refund_over_threshold",
                        "data_conflict",
                        "tool_exhausted",
                        "unidentified_customer",
                        "threatening_language",
                        "ambiguous_ticket",
                    ],
                    "description": "Machine-readable reason for escalation.",
                },
            },
            "required": [
                "ticket_id", "customer_email", "issue_summary",
                "attempted_actions", "recommended_resolution", "priority", "reason_code",
            ],
        },
    },
]
