# src/mandate/tools.py
"""
The tools the agent may call.

The agent's only power is to PROPOSE. propose_checkout routes every proposal
through the policy gate before anything reaches Razorpay, and the mandate is
bound to the Session rather than passed as a tool argument, so the model cannot
name or substitute one.
"""
import json
from mandate.config import MERCHANT_ID, DEMO_MODE
from mandate.db import db
from mandate.mandate import get_state, commit_spend
from mandate.gate import evaluate
from mandate.audit import audit_append
from mandate.executor import execute_payment


class Session:
    """Binds a mandate to a conversation. The agent never sees or sets this."""

    def __init__(self, mandate):
        self.mandate = mandate
        self.n = 0


def t_search_catalog(sess, query="", max_price_inr=None, category=None):
    con = db()
    rows = con.execute("SELECT * FROM catalog WHERE stock > 0").fetchall()
    con.close()

    out = []
    for r in rows:
        if category and r["category"] != category:
            continue
        if max_price_inr and r["price_paise"] > int(max_price_inr) * 100:
            continue
        if query and query.lower() not in (
                r["title"] + " " + r["description"]).lower():
            continue
        out.append({
            "sku": r["sku"],
            "title": r["title"],
            "category": r["category"],
            "price_inr": r["price_paise"] / 100,
            "description": r["description"],
        })

    audit_append(sess.mandate["body"]["mandate_id"], "SEARCH",
                 {"query": query, "max_price_inr": max_price_inr,
                  "category": category, "results": len(out)})
    return {"results": out[:8]}


def t_propose_checkout(sess, sku, qty=1, reasoning=""):
    con = db()
    row = con.execute("SELECT * FROM catalog WHERE sku=?", (sku,)).fetchone()
    con.close()

    mid = sess.mandate["body"]["mandate_id"]
    if not row:
        return {"ok": False, "reason": f"Unknown SKU {sku}."}

    qty = max(1, int(qty))
    sess.n += 1
    idem = f"{mid}-{sku}-{qty}-{sess.n}"

    action = {
        "sku": sku,
        "category": row["category"],
        "merchant_id": MERCHANT_ID,
        "amount_paise": row["price_paise"] * qty,
        "qty": qty,
        "idempotency_key": idem,
    }

    audit_append(mid, "PROPOSE",
                 {"action": action, "agent_reasoning": reasoning[:300]})

    verdict = evaluate(sess.mandate, action, get_state(mid))

    audit_append(mid, "ALLOW" if verdict.allowed else "DENY",
                 {"rule": verdict.rule, "reason": verdict.reason,
                  "sku": sku, "amount_paise": action["amount_paise"]})

    if not verdict.allowed:
        return {"ok": False,
                "blocked_by_policy_gate": True,
                "rule": verdict.rule,
                "reason": verdict.reason,
                "gateway_error": None}
    try:
        if DEMO_MODE:
            res = {"order_id": "order_DEMO" + idem[-8:],
                   "payment_link_id": "plink_DEMO" + idem[-8:],
                   "pay_url": None,
                   "amount_paise": action["amount_paise"]}
        else:
            res = execute_payment(mid, dict(row), action["amount_paise"], idem)

        commit_spend(mid, action["amount_paise"])
        audit_append(mid, "EXECUTED", res)

        return {"ok": True,
                "status": "SIMULATED" if DEMO_MODE else "AWAITING_PAYMENT",
                "note": ("Demo mode: no real payment was created."
                         if DEMO_MODE else
                         "An order and payment link were created. Money has NOT "
                         "moved yet. The link must be completed to pay."),
                "order_id": res["order_id"],
                "pay_url": res["pay_url"],
                "amount_inr": action["amount_paise"] / 100,
                "title": row["title"]}

    except Exception as e:
        audit_append(mid, "EXEC_FAILED", {"sku": sku, "error": str(e)[:300]})
        return {"ok": False,
                "blocked_by_policy_gate": False,
                "gateway_error": str(e)[:200],
                "note": "The mandate ALLOWED this. The payment provider failed.",
                "instruction": "STOP. Do not call any more tools. Reply to the buyer "
                               "now: state that the payment provider failed, that this "
                               "was not a policy refusal, and offer one cheaper "
                               "in-category item you already saw in earlier results."}