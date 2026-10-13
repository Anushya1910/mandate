# demo/cleanup_links.py
"""Cancel unpaid test-mode payment links to free the 30-link quota."""
from mandate.executor import client

resp = client().payment_link.all({"count": 100})

# Razorpay returns either {"payment_links": [...]} or a bare list here.
if isinstance(resp, dict):
    links = resp.get("payment_links") or resp.get("items") or []
    if not links:
        print("unexpected shape, keys were:", list(resp.keys()))
else:
    links = resp

print(f"found {len(links)} links\n")

freed = 0
for l in links:
    status = l.get("status")
    if status in ("created", "partially_paid"):
        try:
            client().payment_link.cancel(l["id"])
            print(f'cancelled {l["id"]}  Rs {l["amount"]/100}')
            freed += 1
        except Exception as e:
            print(f'skip      {l["id"]}: {str(e)[:70]}')
    else:
        print(f'keep      {l["id"]}  {status}')

print(f"\nfreed {freed} link slot(s)")