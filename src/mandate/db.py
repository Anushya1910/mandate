# src/mandate/db.py
import os
import sqlite3
from mandate.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog (
  sku TEXT PRIMARY KEY, title TEXT, category TEXT,
  price_paise INTEGER, stock INTEGER, description TEXT);
CREATE TABLE IF NOT EXISTS mandate_state (
  mandate_id TEXT PRIMARY KEY, amount_spent INTEGER DEFAULT 0,
  use_count INTEGER DEFAULT 0, revoked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS audit (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, mandate_id TEXT,
  event TEXT, payload TEXT, prev_hash TEXT, entry_hash TEXT);
CREATE TABLE IF NOT EXISTS idempotency (
  key TEXT PRIMARY KEY, request_hash TEXT, result TEXT);
"""


def db():
    c = sqlite3.connect(os.getenv("MANDATE_DB", DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init_schema():
    con = db()
    con.executescript(SCHEMA)
    con.commit()
    con.close()


def reset():
    con = db()
    for t in ("catalog", "mandate_state", "audit", "idempotency"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    con.commit()
    con.close()
    init_schema()