# ===== CELL 15 — audit chain verify + tamper detection =====
print("chain valid:", verify_chain())
con = db()
orig = con.execute("SELECT seq,payload FROM audit WHERE event='DENY' ORDER BY seq LIMIT 1").fetchone()
seq, saved = orig["seq"], orig["payload"]
con.execute("UPDATE audit SET payload=? WHERE seq=?",
            (json.dumps({"rule":"ok","reason":"approved"}), seq))
con.commit()
print("tampered seq", seq, "->", verify_chain())
con.execute("UPDATE audit SET payload=? WHERE seq=?", (saved, seq)); con.commit(); con.close()
print("restored      ->", verify_chain())