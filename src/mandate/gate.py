# src/mandate/gate.py
"""
The policy gate. Deterministic, no model, no network, no database.

Given a mandate, a proposed action, and the mandate's current spend state,
returns a verdict. This is the only component that decides whether money moves,
and it re-derives that decision from the mandate every time. It does not trust
the agent's judgment, its reasoning, or anything it says.
"""
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from mandate.mandate import sign


@dataclass
class Verdict:
    allowed: bool
    rule: str
    reason: str


def evaluate(mandate, action, state):
    """state: dict with amount_spent, use_count, revoked."""
    b = mandate["body"]

    if not hmac.compare_digest(sign(b), mandate.get("signature", "")):
        return Verdict(False, "signature", "Mandate signature invalid or body was modified.")

    if state is None:
        return Verdict(False, "unknown_mandate", "No server-side state for this mandate_id.")
    if state["revoked"]:
        return Verdict(False, "revoked", "Mandate has been revoked by the principal.")

    now = datetime.now(timezone.utc)
    if now > datetime.fromisoformat(b["expires_at"]):
        return Verdict(False, "expired", f'Mandate expired at {b["expires_at"]}.')
    if now < datetime.fromisoformat(b["issued_at"]) - timedelta(seconds=60):
        return Verdict(False, "not_yet_valid", "Mandate issued_at is in the future.")

    if action["merchant_id"] != b["merchant_id"]:
        return Verdict(False, "merchant", f'Merchant {action["merchant_id"]} not authorised.')

    if action["category"] not in b["allowed_categories"]:
        return Verdict(False, "category",
                       f'Category "{action["category"]}" not in {b["allowed_categories"]}.')

    amt = action["amount_paise"]
    if not isinstance(amt, int) or amt <= 0:
        return Verdict(False, "amount_shape", "Amount must be a positive integer in paise.")
    if b["per_txn_cap"] and amt > b["per_txn_cap"]:
        return Verdict(False, "per_txn_cap",
                       f'Rs {amt/100:.2f} exceeds per-transaction cap Rs {b["per_txn_cap"]/100:.2f}.')

    remaining = b["max_total_amount"] - state["amount_spent"]
    if amt > remaining:
        return Verdict(False, "budget",
                       f'Rs {amt/100:.2f} exceeds remaining budget Rs {remaining/100:.2f}.')

    if b["max_uses"] is not None and state["use_count"] >= b["max_uses"]:
        return Verdict(False, "max_uses", f'Mandate already used {state["use_count"]} time(s).')

    if not action.get("idempotency_key"):
        return Verdict(False, "idempotency", "Missing idempotency key.")

    return Verdict(True, "ok", f'Within mandate. Remaining after this: Rs {(remaining-amt)/100:.2f}.')