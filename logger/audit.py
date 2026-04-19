"""
audit.py — Thread-safe JSON-Lines audit logger.

Writes one JSON object per line during the run (crash-safe).
finalize_audit_log() converts the .jsonl to a pretty-printed .json array.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger("shopwave.audit")

AUDIT_LOG_PATH = Path("audit_log.jsonl")
_lock = threading.Lock()


def write_event(ticket_id: str, event: str, data: dict) -> None:
    record = {
        "ticket_id": ticket_id,
        "event": event,
        "ts": datetime.now(timezone.utc).isoformat(),
        **data,
    }
    line = json.dumps(record, default=str)
    with _lock:
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def finalize_audit_log() -> None:
    """
    Reads the .jsonl file and writes a pretty-printed audit_log.json array.
    Called once after all tickets complete.
    """
    out_path = AUDIT_LOG_PATH.with_suffix(".json")
    records = []
    if AUDIT_LOG_PATH.exists():
        with AUDIT_LOG_PATH.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, default=str)
    _log.info("Audit log finalized: %s (%d events)", out_path, len(records))
