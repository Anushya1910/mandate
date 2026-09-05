# demo/show_payment_events.py
"""Print every payment-related audit entry."""
import json
from mandate.db import db

EVENTS = ("PAYMENT_SETTLED", "PAYMENT_DECLINED", "PAYMENT_PENDING",
          "PAYMENT_FAILED", "PAYMENT_STATUS", "EXEC_FAILED")

con = db()
rows = con.execute("SELECT seq, event, mandate_id, payload FROM audit "
                   "ORDER BY seq").fetchall()
con.close()

for r in rows:
    if r["event"] not in EVENTS:
        continue
    print(f'\n--- seq {r["seq"]}  {r["event"]}  {r["mandate_id"]} ---')
    print(json.dumps(json.loads(r["payload"]), indent=2))