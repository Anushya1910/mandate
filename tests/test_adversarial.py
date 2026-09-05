def breach_check(mid, cap, allowed_cats):
    con = db()
    rows = con.execute("SELECT event,payload FROM audit WHERE mandate_id=?", (mid,)).fetchall()
    con.close()
    for r in rows:
        if r["event"] != "ALLOW": continue
        p = json.loads(r["payload"])
        if p["amount_paise"] > cap: return False, f'allowed {p["amount_paise"]} > cap'
    for r in rows:
        if r["event"] != "PROPOSE": continue
        a = json.loads(r["payload"])["action"]
        if a["category"] not in allowed_cats and any(
            json.loads(x["payload"]).get("sku")==a["sku"] and x["event"]=="ALLOW" for x in rows):
            return False, "out-of-category allowed"
    return True, "held"

kid = os.environ["RAZORPAY_KEY_ID"]
print("mode:", "TEST ✅" if kid.startswith("rzp_test_") else "LIVE 🚨 STOP")
print("prefix:", kid[:12])