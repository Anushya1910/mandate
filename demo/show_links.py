# demo/show_links.py
"""Print every payment link issued in the current database, with status."""
import json
from mandate.db import db

con = db()
rows = con.execute(
    "SELECT mandate_id, payload FROM audit WHERE event='EXECUTED' ORDER BY seq"
).fetchall()
con.close()

if not rows:
    print("No payment links found. Run demo/run_demo.py first.")
else:
    for r in rows:
        p = json.loads(r["payload"])
        print(f'{r["mandate_id"]}  {p["payment_link_id"]}  '
              f'Rs {p["amount_paise"]/100:>8.2f}  {p["pay_url"]}')