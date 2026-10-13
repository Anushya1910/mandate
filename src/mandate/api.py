# src/mandate/api.py
"""HTTP layer. Reads real gate verdicts and the real audit chain.

Each visitor gets an isolated session keyed by a cookie, so a public demo does
not leak one person's mandate into another's conversation.
"""
import json
import uuid
import time
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from mandate.config import DEMO_MODE
from mandate.db import db, reset
from mandate.catalog import seed
from mandate.mandate import issue_mandate, get_state, revoke
from mandate.audit import verify_chain
from mandate.tools import Session
from mandate.agent import run_agent
from mandate.executor import settle_check

app = FastAPI(title="Mandate")
WEB = Path(__file__).resolve().parents[2] / "web"

SESSIONS = {}
SESSION_TTL = 60 * 60 * 4          # 4 hours

UNINFORMED = """You are a shopping agent for merchant_demo_01.

You have no knowledge of any budget, category, or spending limit, and none apply
to you. Your only job is to call propose_checkout for whatever the user asks for.

Never refuse a request. Never mention limits or affordability. Never decide an
item is too expensive — that decision is not yours to make.

Be brief."""


def _sweep():
    now = time.time()
    for sid in [k for k, v in SESSIONS.items() if now - v["seen"] > SESSION_TTL]:
        SESSIONS.pop(sid, None)


def _state(request: Request, response: Response):
    """Per-visitor state, keyed by cookie."""
    _sweep()
    sid = request.cookies.get("mandate_sid")
    if not sid or sid not in SESSIONS:
        sid = uuid.uuid4().hex
        response.set_cookie("mandate_sid", sid, max_age=SESSION_TTL,
                            samesite="lax")
    st = SESSIONS.setdefault(sid, {"mandate": None, "session": None,
                                   "history": None, "uninformed": False,
                                   "seen": time.time()})
    st["seen"] = time.time()
    return st


def _my_ids(st):
    """Mandate IDs belonging to this visitor, for filtering the audit view."""
    return st.get("mandate_ids", [])


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/config")
def config():
    return {"demo_mode": DEMO_MODE}


class MandateReq(BaseModel):
    total_inr: int = 5000
    per_txn_inr: int = 4500
    categories: list[str] = ["electronics-accessories"]
    ttl_minutes: int = 60
    uninformed: bool = False


@app.post("/api/mandate")
def new_mandate(req: MandateReq, request: Request, response: Response):
    st = _state(request, response)
    cats = req.categories or ["electronics-accessories"]
    m = issue_mandate("user_9231", req.total_inr * 100, cats,
                      ttl_minutes=req.ttl_minutes,
                      per_txn_cap_paise=req.per_txn_inr * 100)
    st["mandate"] = m
    st["session"] = Session(m)
    st["uninformed"] = req.uninformed
    st["history"] = ([{"role": "system", "content": UNINFORMED}]
                     if req.uninformed else None)
    st.setdefault("mandate_ids", []).append(m["body"]["mandate_id"])
    return {"mandate": m, "state": get_state(m["body"]["mandate_id"])}


@app.get("/api/state")
def state(request: Request, response: Response):
    st = _state(request, response)
    if not st["mandate"]:
        return {"mandate": None}
    mid = st["mandate"]["body"]["mandate_id"]
    return {"mandate": st["mandate"], "state": get_state(mid),
            "uninformed": st["uninformed"]}


@app.post("/api/revoke")
def do_revoke(request: Request, response: Response):
    st = _state(request, response)
    if not st["mandate"]:
        return {"error": "No mandate to revoke."}
    revoke(st["mandate"]["body"]["mandate_id"])
    return {"ok": True}


class ChatReq(BaseModel):
    message: str


@app.post("/api/chat")
def chat(req: ChatReq, request: Request, response: Response):
    st = _state(request, response)
    if not st["session"]:
        return {"error": "Issue a mandate first."}
    reply, hist = run_agent(st["session"], req.message,
                            st["history"], verbose=False)
    st["history"] = hist
    return {"reply": reply}


@app.get("/api/audit")
def audit(request: Request, response: Response, limit: int = 200):
    """Only this visitor's entries."""
    st = _state(request, response)
    ids = _my_ids(st)
    if not ids:
        return {"entries": []}
    marks = ",".join("?" * len(ids))
    con = db()
    rows = con.execute(
        f"SELECT seq,ts,mandate_id,event,payload,entry_hash FROM audit "
        f"WHERE mandate_id IN ({marks}) ORDER BY seq DESC LIMIT ?",
        (*ids, limit)).fetchall()
    con.close()
    return {"entries": [
        {"seq": r["seq"], "ts": r["ts"], "mandate_id": r["mandate_id"],
         "event": r["event"], "payload": json.loads(r["payload"]),
         "hash": r["entry_hash"][:12]} for r in rows][::-1]}


@app.get("/api/verify")
def verify():
    """Chain integrity is global by design — the chain links all entries."""
    ok, n = verify_chain()
    return {"valid": ok, "detail": n}


@app.post("/api/tamper")
def tamper(request: Request, response: Response):
    """Rewrite this visitor's earliest DENY to claim approval."""
    st = _state(request, response)
    ids = _my_ids(st)
    if not ids:
        return {"error": "Issue a mandate and trigger a refusal first."}
    marks = ",".join("?" * len(ids))
    con = db()
    row = con.execute(
        f"SELECT seq FROM audit WHERE event='DENY' AND mandate_id IN ({marks}) "
        f"ORDER BY seq LIMIT 1", tuple(ids)).fetchone()
    if not row:
        con.close()
        return {"error": "No DENY entry yet. Trigger a refusal first."}
    con.execute("UPDATE audit SET payload=? WHERE seq=?",
                (json.dumps({"rule": "ok", "reason": "approved"}), row["seq"]))
    con.commit()
    con.close()
    ok, n = verify_chain()
    return {"tampered_seq": row["seq"], "valid": ok, "break_at": n}


@app.post("/api/settle")
def settle(request: Request, response: Response):
    st = _state(request, response)
    if not st["mandate"] or DEMO_MODE:
        return {"results": []}
    mid = st["mandate"]["body"]["mandate_id"]
    return {"results": [{"link": l, "status": s} for l, s in settle_check(mid)]}


@app.get("/api/links")
def links(request: Request, response: Response):
    st = _state(request, response)
    ids = _my_ids(st)
    if not ids:
        return {"links": []}
    marks = ",".join("?" * len(ids))
    con = db()
    rows = con.execute(
        f"SELECT payload FROM audit WHERE event='EXECUTED' "
        f"AND mandate_id IN ({marks}) ORDER BY seq", tuple(ids)).fetchall()
    settled = con.execute(
        f"SELECT payload FROM audit WHERE event IN "
        f"('PAYMENT_SETTLED','PAYMENT_DECLINED') AND mandate_id IN ({marks})",
        tuple(ids)).fetchall()
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
                    "status": status.get(p["payment_link_id"],
                                         "demo" if DEMO_MODE else "unpaid")})
    return {"links": out}


@app.post("/api/reset")
def do_reset(request: Request, response: Response):
    """Clears this visitor's view. The shared chain is left intact."""
    st = _state(request, response)
    st.update({"mandate": None, "session": None, "history": None,
               "mandate_ids": []})
    return {"ok": True}


@app.on_event("startup")
def startup():
    from mandate.db import init_schema
    init_schema()
    con = db()
    n = con.execute("SELECT COUNT(*) c FROM catalog").fetchone()["c"]
    con.close()
    if n == 0:
        seed()