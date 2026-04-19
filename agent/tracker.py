import threading

_lock = threading.RLock()

ticket_stage_tracker = {}
batch_state = {"running": False, "completed": 0, "total": 20, "results": []}

def update_ticket_stage(ticket_id: str, updates: dict):
    with _lock:
        if ticket_id not in ticket_stage_tracker:
            ticket_stage_tracker[ticket_id] = {
                "current_stage": 0,
                "stages_complete": [],
                "tool_calls": [],
                "fraud_flag": False,
                "fraud_reason": "",
                "guard_fired": False,
                "guard_outcome": "",
                "complete": False,
                "decision": "",
                "confidence": 0.0,
                "reply": "",
                "urgency": "low",
                "customer_name": "",
                "customer_tier": "standard"
            }
        ticket_stage_tracker[ticket_id].update(updates)

def append_tool_call(ticket_id: str, tool_call: dict):
    with _lock:
        if ticket_id not in ticket_stage_tracker:
            update_ticket_stage(ticket_id, {})
        ticket_stage_tracker[ticket_id]["tool_calls"].append(tool_call)

def mark_stage_complete(ticket_id: str, stage_number: int, stage_name: str, duration_ms: int):
    with _lock:
        if ticket_id not in ticket_stage_tracker:
            update_ticket_stage(ticket_id, {})
        ticket_stage_tracker[ticket_id]["current_stage"] = stage_number
        ticket_stage_tracker[ticket_id]["stages_complete"].append({
            "stage": stage_number,
            "name": stage_name,
            "duration_ms": duration_ms
        })
