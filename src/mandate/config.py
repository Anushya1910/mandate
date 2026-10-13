# src/mandate/config.py
import os
from dotenv import load_dotenv
load_dotenv()

MERCHANT_ID = "merchant_demo_01"
DB_PATH = os.getenv("MANDATE_DB", "mandate.db")
DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"

# Verified working 2026-09-03. Groq retires model IDs periodically.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


def signing_key() -> bytes:
    k = os.getenv("MANDATE_SIGNING_KEY")
    if not k:
        raise RuntimeError("MANDATE_SIGNING_KEY not set. Create a .env file.")
    return k.encode()


def razorpay_creds():
    kid = os.getenv("RAZORPAY_KEY_ID")
    sec = os.getenv("RAZORPAY_KEY_SECRET")
    if not kid or not sec:
        raise RuntimeError("Razorpay credentials missing. Create a .env file.")
    if not kid.startswith("rzp_test_"):
        raise RuntimeError(f"Refusing non-test key ({kid[:9]}...). Test mode only.")
    return kid, sec


def groq_key() -> str:
    k = os.getenv("GROQ_API_KEY")
    if not k:
        raise RuntimeError("GROQ_API_KEY not set.")
    return k
