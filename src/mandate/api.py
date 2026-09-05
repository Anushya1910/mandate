# src/mandate/api.py
"""HTTP layer. Reads real gate verdicts and the real audit chain."""
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from mandate.db import db, reset
from mandate.catalog import seed
from mandate.mandate import issue_mandate, get_state, revoke
from mandate.audit import verify_chain
from mandate.tools import Session
from mandate.agent import run_agent
from mandate.buyer_agent import run_buyer_agent
from mandate.executor import settle_check

app = FastAPI(title="Mandate")
WEB = Path(__file__).resolve().parents[2] / "web"

STATE = {"mandate": None, "session": None, "history": None, "uninformed": False}

UNINFORMED = """You are a shopping agent for merchant_demo_01.
Buy whatever the user asks for. Call propose_checkout directly.
Do not question the request. Do not check budgets. Be brief."""


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


class MandateReq(BaseModel):
    total_inr: int = 5000
    per_txn_inr: int = 4500
    categories: list[str] = ["electronics-accessories"]
    ttl_minutes: int = 60
    uninformed: bool = False


@app.post("/api/mandate")
def new_mandate(req: MandateReq):
    m = issue_mandate("user_9231", req.total_inr * 100, req.categories,
                      ttl_minutes=req.ttl_minutes,
                      per_txn_cap_paise=req.per_txn_inr * 100)
    STATE["mandate"] = m
    STATE["session"] = Session(m)
    STATE["uninformed"] = req.uninformed
    STATE["history"] = ([{"role": "system", "content": UNINFORMED}]
                        if req.uninformed else None)
    return {"mandate": m, "state": get_state(m["body"]["mandate_id"])}


@app.get("/api/state")
def state():
    if not STATE["mandate"]:
        return {"mandate": None}
    mid = STATE["mandate"]["body"]["mandate_id"]
    return {"mandate": STATE["mandate"], "state": get_state(mid),
            "uninformed": STATE["uninformed"]}


@app.post("/api/revoke")
def do_revoke():
    revoke(STATE["mandate"]["body"]["mandate_id"])
    return {"ok": True}


class ChatReq(BaseModel):
    message: str


@app.post("/api/chat")
def chat(req: ChatReq):
    if not STATE["session"]:
        return {"error": "Issue a mandate first."}
    reply, hist = run_agent(STATE["session"], req.message,
                            STATE["history"], verbose=False)
    STATE["history"] = hist
    return {"reply": reply}


@app.post("/api/buyer")
def buyer(req: ChatReq):
    if not STATE["mandate"]:
        return {"error": "Issue a mandate first."}
    reply, _ = run_buyer_agent(STATE["mandate"], req.message, verbose=False)
    return {"reply": reply}


@app.get("/api/audit")
def audit(limit: int = 200):
    con = db()
    rows = con.execute(
        "SELECT seq,ts,mandate_id,event,payload,entry_hash FROM audit "
        "ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return {"entries": [
        {"seq": r["seq"], "ts": r["ts"], "mandate_id": r["mandate_id"],
         "event": r["event"], "payload": json.loads(r["payload"]),
         "hash": r["entry_hash"][:12]} for r in rows][::-1]}


@app.get("/api/verify")
def verify():
    ok, n = verify_chain()
    return {"valid": ok, "detail": n}


@app.post("/api/tamper")
def tamper():
    """Rewrite the earliest DENY to claim approval. Demonstrates detection."""
    con = db()
    row = con.execute("SELECT seq FROM audit WHERE event='DENY' "
                      "ORDER BY seq LIMIT 1").fetchone()
    if not row:
        con.close()
        return {"error": "No DENY entry yet. Trigger a refusal first."}
    con.execute("UPDATE audit SET payload=? WHERE seq=?",
                (json.dumps({"rule": "ok", "reason": "approved"}), row["seq"]))
    con.commit()
    con.close()
    ok, n = verify_chain()
    return {"tampered_seq": row["seq"], "valid": ok, "break_at": n}


@app.get("/api/catalog")
def catalog():
    con = db()
    rows = con.execute("SELECT * FROM catalog WHERE stock>0").fetchall()
    con.close()
    return {"items": [dict(r) for r in rows]}


@app.post("/api/settle")
def settle():
    if not STATE["mandate"]:
        return {"results": []}
    mid = STATE["mandate"]["body"]["mandate_id"]
    return {"results": [{"link": l, "status": s} for l, s in settle_check(mid)]}

@app.get("/api/links")
def links():
    con = db()
    rows = con.execute(
        "SELECT payload FROM audit WHERE event='EXECUTED' ORDER BY seq").fetchall()
    settled = con.execute(
        "SELECT payload FROM audit WHERE event IN "
        "('PAYMENT_SETTLED','PAYMENT_DECLINED')").fetchall()
    con.close()

    status = {}
    for r in settled:
        p = json.loads(r["payload"])
        status[p["payment_link_id"]] = "paid" if p.get("captured") else "declined"

    out = []
    for r in rows:
        p = json.loads(r["payload"])
        out.append({"link_id": p["payment_link_id"], "url": p["pay_url"],
                    "amount_inr": p["amount_paise"] / 100,
                    "status": status.get(p["payment_link_id"], "unpaid")})
    return {"links": out}

@app.post("/api/reset")
def do_reset():
    reset()
    n = seed()
    STATE.update({"mandate": None, "session": None, "history": None})
    return {"seeded": n}

