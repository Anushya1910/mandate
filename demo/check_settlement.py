# demo/check_settlement.py
"""
Poll Razorpay for the status of every payment link issued in the last run and
append the outcome to the audit chain. Does NOT reset the database.

Run after completing (or deliberately failing) a link in the browser:
    python demo/check_settlement.py
"""
from mandate.db import db
from mandate.audit import verify_chain
from mandate.executor import settle_check

con = db()
rows = con.execute(
    "SELECT DISTINCT mandate_id FROM audit WHERE event='EXECUTED'").fetchall()
con.close()

if not rows:
    print("No executed payments found. Run demo/run_demo.py first.")
else:
    for r in rows:
        mid = r["mandate_id"]
        print(f"\nmandate {mid}")
        for link_id, status in settle_check(mid):
            print(f"  {link_id}  ->  {status}")

    print(f"\nchain: {verify_chain()}")