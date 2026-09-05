# src/mandate/executor.py
"""
Razorpay execution layer. Reached ONLY after the policy gate has approved an
action. Test mode is enforced in config.razorpay_creds().
"""
import json
import hashlib

import razorpay

from mandate.config import razorpay_creds
from mandate.db import db
from mandate.mandate import canon
from mandate.audit import audit_append

_client = None


def client():
    """Lazy client. Importing this module must not require credentials."""
    global _client
    if _client is None:
        _client = razorpay.Client(auth=razorpay_creds())
    return _client


def execute_payment(mandate_id, item, amount_paise, idem_key):
    """Create an order and a payment link. Idempotent on idem_key."""
    con = db()
    hit = con.execute(
        "SELECT result FROM idempotency WHERE key=?", (idem_key,)).fetchone()
    con.close()
    if hit:
        return {"status": "replayed", **json.loads(hit["result"])}

    order = client().order.create({
        "amount": amount_paise,
        "currency": "INR",
        "receipt": idem_key[:40],          # Razorpay enforces receipt uniqueness
        "notes": {"mandate_id": mandate_id, "sku": item["sku"]},
    })

    link = client().payment_link.create({
        "amount": amount_paise,
        "currency": "INR",
        "description": f'{item["title"]} (mandate {mandate_id})',
        "reference_id": idem_key[:40],
        "customer": {"name": "Agent Buyer",
                     "email": "buyer@example.com",
                     "contact": "+919812345678"},
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
        "notes": {"mandate_id": mandate_id},
    })

    result = {
        "order_id": order["id"],
        "payment_link_id": link["id"],
        "pay_url": link["short_url"],
        "amount_paise": amount_paise,
    }

    con = db()
    con.execute(
        "INSERT INTO idempotency VALUES (?,?,?)",
        (idem_key, hashlib.sha256(canon(result)).hexdigest(), json.dumps(result)))
    con.commit()
    con.close()
    return {"status": "created", **result}


def payment_link_status(link_id):
    return client().payment_link.fetch(link_id)["status"]


def record_payment_outcome(mandate_id, link_id):
    """Fetch the real provider outcome for a link and append it to the chain.

    Failed attempts do not appear in payment_link.payments, so we query the
    order's payments directly.
    """
    link = client().payment_link.fetch(link_id)
    order_id = link.get("order_id")

    attempts = []
    if order_id:
        try:
            resp = client().order.payments(order_id)
            attempts = resp.get("items", [])
        except Exception as e:
            attempts = [{"fetch_failed": str(e)[:120]}]

    captured = [p for p in attempts if p.get("status") == "captured"]
    failed = [p for p in attempts if p.get("status") == "failed"]

    detail = {
        "payment_link_id": link_id,
        "order_id": order_id,
        "link_status": link["status"],
        "attempts": len(attempts),
        "captured": len(captured),
        "failed": [
            {"payment_id": p.get("id"),
             "status": p.get("status"),
             "method": p.get("method"),
             "error_code": p.get("error_code"),
             "error_description": p.get("error_description"),
             "error_reason": p.get("error_reason"),
             "error_source": p.get("error_source"),
             "error_step": p.get("error_step")}
            for p in failed
        ],
    }

    if link["status"] == "paid" or captured:
        event = "PAYMENT_SETTLED"
    elif failed:
        event = "PAYMENT_DECLINED"
    else:
        event = "PAYMENT_PENDING"

    audit_append(mandate_id, event, detail)
    return detail


def settle_check(mandate_id, force=False):
    """Poll links under a mandate. Skips links already recorded as settled
    unless force=True, so repeated polling does not bloat the chain."""
    con = db()
    executed = con.execute(
        "SELECT payload FROM audit WHERE mandate_id=? AND event='EXECUTED'",
        (mandate_id,)).fetchall()
    settled = con.execute(
        "SELECT payload FROM audit WHERE mandate_id=? "
        "AND event IN ('PAYMENT_SETTLED','PAYMENT_DECLINED')",
        (mandate_id,)).fetchall()
    con.close()

    done = {json.loads(r["payload"])["payment_link_id"] for r in settled}

    out = []
    for r in executed:
        lid = json.loads(r["payload"])["payment_link_id"]
        if lid in done and not force:
            out.append((lid, "already recorded"))
            continue
        try:
            d = record_payment_outcome(mandate_id, lid)
            out.append((lid, f'{d["link_status"]} '
                             f'(captured {d["captured"]}, failed {len(d["failed"])})'))
        except Exception as e:
            out.append((lid, f"lookup_failed: {str(e)[:80]}"))
    return out