# tests/test_gate.py
"""
Policy gate enforcement tests.

These run with NO API keys, NO network, and NO model. That is the point:
enforcement must be provable independently of anything the LLM does.
"""
import pytest
from datetime import datetime, timedelta, timezone

from mandate.config import MERCHANT_ID
from mandate.mandate import issue_mandate, revoke, commit_spend, get_state
from mandate.gate import evaluate


def _act(cat="electronics-accessories", amt=100000, idem="k1", merch=MERCHANT_ID):
    return {"sku": "X", "category": cat, "merchant_id": merch,
            "amount_paise": amt, "qty": 1, "idempotency_key": idem}


def _mandate(**kw):
    kw.setdefault("principal", "test_user")
    kw.setdefault("max_total_paise", 500000)
    kw.setdefault("categories", ["electronics-accessories"])
    return issue_mandate(**kw)


def _verdict(m, action):
    return evaluate(m, action, get_state(m["body"]["mandate_id"]))


def test_happy_path():
    v = _verdict(_mandate(per_txn_cap_paise=450000), _act())
    assert v.allowed and v.rule == "ok"


def test_wrong_category_blocked():
    v = _verdict(_mandate(), _act(cat="apparel"))
    assert not v.allowed and v.rule == "category"


def test_per_transaction_cap_blocked():
    v = _verdict(_mandate(per_txn_cap_paise=450000), _act(amt=460000))
    assert not v.allowed and v.rule == "per_txn_cap"


def test_total_budget_blocked():
    v = _verdict(_mandate(), _act(amt=600000))
    assert not v.allowed and v.rule == "budget"


def test_wrong_merchant_blocked():
    v = _verdict(_mandate(), _act(merch="merchant_evil"))
    assert not v.allowed and v.rule == "merchant"


def test_missing_idempotency_key_blocked():
    v = _verdict(_mandate(), _act(idem=""))
    assert not v.allowed and v.rule == "idempotency"


def test_negative_amount_blocked():
    v = _verdict(_mandate(), _act(amt=-100))
    assert not v.allowed and v.rule == "amount_shape"


def test_tampered_body_blocked():
    """Raising the budget in the body must invalidate the signature."""
    m = _mandate()
    tampered = {"body": {**m["body"], "max_total_amount": 99999999},
                "signature": m["signature"]}
    v = _verdict(tampered, _act(amt=9000000))
    assert not v.allowed and v.rule == "signature"


def test_expired_mandate_blocked():
    v = _verdict(_mandate(ttl_minutes=-1), _act())
    assert not v.allowed and v.rule == "expired"


def test_budget_depletes_after_spend():
    m = _mandate()
    commit_spend(m["body"]["mandate_id"], 480000)
    v = _verdict(m, _act(amt=100000))
    assert not v.allowed and v.rule == "budget"


def test_revoked_mandate_blocked():
    m = _mandate()
    revoke(m["body"]["mandate_id"])
    v = _verdict(m, _act())
    assert not v.allowed and v.rule == "revoked"