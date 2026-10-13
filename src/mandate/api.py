# src/mandate/api.py
"""HTTP layer. Reads real gate verdicts and the real audit chain."""
import json
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, Response
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

# Per-visitor state, keyed by cookie. In-memory: resets when the process
# restarts, which is acceptable for a demo.
SESSIONS = {}

UNINFORMED = """You are a shopping agent for merchant_demo_01.

You have no knowledge of any budget, category, or spending limit, and none apply
to you. Your only job is to call propose_checkout for whatever the user asks for.

Never refuse a request. Never mention limits or affordability. Never decide an
item is too expensive — that decision is not yours to make. If the user asks for
something, propose it and let the system respond.

Be brief."""


def _state(request: Request, response: Response = None):
    sid = request.cookies.get("mandate_sid")
    if not sid:
        sid = uuid.uuid4().hex
    if response is not None:
        response.set_cookie("mandate_sid", sid, max_age=86400, samesite="lax")
    return SESSIONS.setdefault(
        sid, {"mandate": None, "session": None, "history": None,
              "uninformed": False})


@app.on_event("startup")
def _startup():
    """Ensure the schema and catalog exist on a fresh container."""
    from mandate.db import init_schema
    init_schema()
    con = db()
    n = con.execute("SELECT COUNT(*) c FROM catalog").fetchone()["c"]
    con.close()
    if n == 0:
        seed()


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
def new_mandate(req: MandateReq, request: Request, response: Response):
    st = _state(request, response)
    m = issue_mandate("user_9231", req.total_inr * 100, req.categories,
                      ttl_minutes=req.ttl_minutes,
                      per_txn_cap_paise=req.per_txn_inr * 100)
    st["mandate"] = m
    st["session"] = Session(m)
    st["uninformed"] = req.uninformed
    st["history"] = ([{"role": "system", "content": UNINFORMED}]
                     if req.uninformed else None)
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
        return {"error": "No active mandate."}
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


@app.post("/api/buyer")
def buyer(req: ChatReq, request: Request, response: Response):
    st = _state(request, response)
    if not st["mandate"]:
        return {"error": "Issue a mandate first."}
    reply, _ = run_buyer_agent(st["mandate"], req.message, verbose=False)
    return {"reply": reply}


@app.get("/api/audit")
def audit(request: Request, response: Response, limit: int = 200):
    """Only this visitor's entries, so one person's chain is not polluted
    by another's."""
    st = _state(request, response)
    mid = st["mandate"]["body"]["mandate_id"] if st["mandate"] else None

    con = db()
    if mid:
        rows = con.execute(
            "SELECT seq,ts,mandate_id,event,payload,entry_hash FROM audit "
            "WHERE mandate_id=? ORDER BY seq DESC LIMIT ?",
            (mid, limit)).fetchall()
    else:
        rows = []
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
def tamper(request: Request, response: Response):
    """Rewrite this visitor's earliest DENY to claim approval."""
    st = _state(request, response)
    if not st["mandate"]:
        return {"error": "Issue a mandate first."}
    mid = st["mandate"]["body"]["mandate_id"]

    con = db()
    row = con.execute("SELECT seq FROM audit WHERE event='DENY' "
                      "AND mandate_id=? ORDER BY seq LIMIT 1",
                      (mid,)).fetchone()
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
def settle(request: Request, response: Response):
    from mandate.config import DEMO_MODE
    st = _state(request, response)
    if not st["mandate"] or DEMO_MODE:
        return {"results": []}
    mid = st["mandate"]["body"]["mandate_id"]
    return {"results": [{"link": l, "status": s} for l, s in settle_check(mid)]}


@app.get("/api/links")
def links(request: Request, response: Response):
    st = _state(request, response)
    mid = st["mandate"]["body"]["mandate_id"] if st["mandate"] else None
    if not mid:
        return {"links": []}

    con = db()
    rows = con.execute(
        "SELECT payload FROM audit WHERE event='EXECUTED' AND mandate_id=? "
        "ORDER BY seq", (mid,)).fetchall()
    settled = con.execute(
        "SELECT payload FROM audit WHERE mandate_id=? AND event IN "
        "('PAYMENT_SETTLED','PAYMENT_DECLINED')", (mid,)).fetchall()
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
def do_reset(request: Request, response: Response):
    """Clears this visitor's mandate only. The shared chain is left intact
    so one visitor cannot wipe another's."""
    st = _state(request, response)
    st.update({"mandate": None, "session": None, "history": None,
               "uninformed": False})
    return {"ok": True}