# src/mandate/merchant.py
"""
The merchant's machine-readable surface.

merchant_endpoint stands in for HTTP: it takes JSON, returns JSON, and exposes
nothing else. A buyer agent on the other side cannot reach the gate, the
database, or the Razorpay client directly. The mandate is re-verified on every
request, so a caller that mutates its own copy gains nothing.
"""
from mandate.config import MERCHANT_ID
from mandate.db import db
from mandate.tools import Session, t_search_catalog, t_propose_checkout


def well_known_agent_manifest():
    """Advertises capability, not inventory. Items come from /search."""
    con = db()
    categories = [r["category"] for r in con.execute(
        "SELECT DISTINCT category FROM catalog WHERE stock > 0")]
    count = con.execute(
        "SELECT COUNT(*) AS c FROM catalog WHERE stock > 0").fetchone()["c"]
    con.close()

    return {
        "merchant_id": MERCHANT_ID,
        "version": "0.1",
        "auth": {
            "scheme": "mandate-hmac-sha256",
            "issue_endpoint": "/mandates/issue",
            "required_fields": ["max_total_amount", "allowed_categories",
                                "expires_at"],
        },
        "endpoints": {"search": "/search", "checkout": "/checkout"},
        "currency": "INR",
        "amount_unit": "paise",
        "categories": categories,
        "item_count": count,
        "next_step": "Call /search to retrieve items. Do not re-fetch this "
                     "manifest.",
    }


def merchant_endpoint(path, body=None, mandate=None):
    body = body or {}

    if path == "/.well-known/agent-manifest":
        return {"status": 200, "body": well_known_agent_manifest()}

    if mandate is None:
        return {"status": 401, "body": {"error": "mandate required"}}

    # Every request re-verifies the mandate from scratch via the gate.
    sess = Session(mandate)

    if path == "/search":
        out = t_search_catalog(sess, **{
            k: v for k, v in body.items()
            if k in ("query", "max_price_inr", "category") and v is not None})
        out["results"] = out["results"][:6]        # bounded response size
        return {"status": 200, "body": out}

    if path == "/checkout":
        if "sku" not in body:
            return {"status": 400, "body": {"error": "sku required"}}
        out = t_propose_checkout(sess,
                                 sku=body["sku"],
                                 qty=body.get("qty", 1),
                                 reasoning=body.get("reasoning", "(none supplied)"))
        return {"status": 200 if out.get("ok") else 402, "body": out}

    return {"status": 404, "body": {"error": f"no route {path}"}}