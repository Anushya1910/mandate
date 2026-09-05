# src/mandate/mandate.py
import hmac
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from mandate.config import MERCHANT_ID, signing_key
from mandate.db import db
import hmac, hashlib, uuid
from datetime import datetime, timedelta, timezone

def canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":")).encode()


def sign(body):
    return hmac.new(signing_key(), canon(body), hashlib.sha256).hexdigest()

def issue_mandate(principal, max_total_paise, categories, ttl_minutes=60,
                  max_uses=None, per_txn_cap_paise=None):
    now = datetime.now(timezone.utc)
    body = {
        "mandate_id": "mnd_" + uuid.uuid4().hex[:10],
        "principal": principal,
        "merchant_id": MERCHANT_ID,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=ttl_minutes)).isoformat(),
        "max_total_amount": max_total_paise,
        "per_txn_cap": per_txn_cap_paise,
        "allowed_categories": sorted(categories),
        "max_uses": max_uses,
        "nonce": uuid.uuid4().hex,
    }
    mandate = {"body": body, "signature": sign(body)}
    con = db()
    con.execute("INSERT INTO mandate_state (mandate_id) VALUES (?)", (body["mandate_id"],))
    con.commit(); con.close()
    return mandate

def get_state(mid):
    con = db()
    r = con.execute("SELECT * FROM mandate_state WHERE mandate_id=?", (mid,)).fetchone()
    con.close()
    return dict(r) if r else None

def revoke(mid):
    con = db(); con.execute("UPDATE mandate_state SET revoked=1 WHERE mandate_id=?", (mid,))
    con.commit(); con.close()

def commit_spend(mid, amount):
    con = db()
    con.execute("UPDATE mandate_state SET amount_spent=amount_spent+?, use_count=use_count+1 "
                "WHERE mandate_id=?", (amount, mid))
    con.commit(); con.close()