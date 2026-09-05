# demo/inspect_link.py
"""Dump Razorpay's raw view of a payment link, including failed attempts."""
import sys
import json
from mandate.executor import client

link_id = sys.argv[1]
link = client().payment_link.fetch(link_id)

print("status  :", link["status"])
print("amount  :", link["amount"])
payments = link.get("payments") or []
print("attempts:", len(payments))

for p in payments:
    print("\n--- attempt ---")
    print(json.dumps(p, indent=2))
    pid = p.get("payment_id")
    if pid:
        full = client().payment.fetch(pid)
        print("error fields:")
        for k in ("error_code", "error_description", "error_reason",
                  "error_source", "error_step"):
            print(f"  {k}: {full.get(k)}")