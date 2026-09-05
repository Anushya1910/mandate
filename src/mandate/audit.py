# src/mandate/audit.py
"""
Tamper-evident audit log.

Each entry hashes the previous entry's hash together with its own canonical
body, forming a chain. Editing any historical row breaks every hash after it,
so retroactive tampering is detectable rather than merely discouraged.
"""
import json
import hashlib
from datetime import datetime, timezone

from mandate.db import db
from mandate.mandate import canon

GENESIS = "0" * 64


def audit_append(mandate_id, event, payload):
    con = db()
    last = con.execute(
        "SELECT entry_hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
    prev = last["entry_hash"] if last else GENESIS

    ts = datetime.now(timezone.utc).isoformat()
    body = {"ts": ts, "mandate_id": mandate_id,
            "event": event, "payload": payload}
    entry_hash = hashlib.sha256(prev.encode() + canon(body)).hexdigest()

    con.execute(
        "INSERT INTO audit (ts,mandate_id,event,payload,prev_hash,entry_hash) "
        "VALUES (?,?,?,?,?,?)",
        (ts, mandate_id, event, json.dumps(payload), prev, entry_hash))
    con.commit()
    con.close()
    return entry_hash


def verify_chain():
    """Recompute the whole chain. Returns (valid, seq_of_first_break_or_count)."""
    con = db()
    rows = con.execute("SELECT * FROM audit ORDER BY seq").fetchall()
    con.close()

    prev = GENESIS
    for r in rows:
        body = {"ts": r["ts"], "mandate_id": r["mandate_id"],
                "event": r["event"], "payload": json.loads(r["payload"])}
        expected = hashlib.sha256(prev.encode() + canon(body)).hexdigest()
        if expected != r["entry_hash"] or r["prev_hash"] != prev:
            return False, r["seq"]
        prev = expected
    return True, len(rows)


def print_audit(limit=40):
    con = db()
    rows = con.execute(
        "SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
    con.close()

    tag = {"ALLOW": "[ALLOW]", "DENY": "[DENY ]", "EXECUTED": "[EXEC ]",
           "EXEC_FAILED": "[FAIL ]", "PROPOSE": "[PROP ]", "SEARCH": "[SRCH ]",
           "PAYMENT_SETTLED": "[PAID ]", "PAYMENT_FAILED": "[DECL ]"}
    for r in reversed(rows):
        print(f'{r["seq"]:>3} {tag.get(r["event"], "[     ]")} '
              f'{r["event"]:<16} {r["payload"][:110]}')