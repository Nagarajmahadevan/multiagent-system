"""
FastAPI web server for the Multi-Agent Debate System.
Serves the UI and streams pipeline events via Server-Sent Events (SSE).

Run:  python server.py
Then open: http://localhost:8000
"""

import asyncio
import json
import logging
import os
import threading
from pathlib import Path

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Multi-Agent Debate System")

# Serve static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ── Razorpay config ───────────────────────────────────────────────────────────
RAZORPAY_KEY_ID     = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

# ── Credit config ─────────────────────────────────────────────────────────────
SUPABASE_URL         = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
QUERY_COST_PAISE     = 1000   # ₹10 per query
FREE_CREDITS_PAISE   = 1000   # 1 free query for new users

_sb_admin = None

def get_sb_admin():
    global _sb_admin
    if _sb_admin is None and SUPABASE_URL and SUPABASE_SERVICE_KEY:
        from supabase import create_client
        _sb_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _sb_admin


def get_user_id_from_token(token: str):
    sb = get_sb_admin()
    if not sb or not token:
        return None
    try:
        return sb.auth.get_user(token).user.id
    except Exception:
        return None


def get_or_create_balance(user_id: str) -> int:
    sb = get_sb_admin()
    if not sb:
        return 999999  # auth not configured — allow all
    try:
        result = sb.table("user_credits").select("balance_paise").eq("user_id", user_id).execute()
        if result.data:
            return result.data[0]["balance_paise"]
        # First time — give free query
        sb.table("user_credits").insert({"user_id": user_id, "balance_paise": FREE_CREDITS_PAISE}).execute()
        return FREE_CREDITS_PAISE
    except Exception as e:
        logger.error(f"Balance check error: {e}")
        return 999999


def deduct_balance(user_id: str):
    sb = get_sb_admin()
    if not sb:
        return
    try:
        result = sb.table("user_credits").select("balance_paise").eq("user_id", user_id).execute()
        if result.data:
            new_bal = max(0, result.data[0]["balance_paise"] - QUERY_COST_PAISE)
            sb.table("user_credits").update({"balance_paise": new_bal}).eq("user_id", user_id).execute()
    except Exception as e:
        logger.error(f"Deduct balance error: {e}")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/config")
def get_config():
    """Return public client-side config (safe to expose)."""
    return JSONResponse({
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": os.getenv("SUPABASE_ANON_KEY", ""),
    })


@app.post("/create-order")
async def create_order(request: Request):
    """Create a Razorpay order for the given amount."""
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        return JSONResponse({"error": "Payments not configured"}, status_code=503)

    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    user_id = get_user_id_from_token(token)
    if not user_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    body = await request.json()
    amount_inr = int(body.get("amount_inr", 0))
    if amount_inr < 10:
        return JSONResponse({"error": "Minimum recharge is ₹10"}, status_code=400)

    import razorpay
    client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    order = client.order.create({
        "amount": amount_inr * 100,  # paise
        "currency": "INR",
        "payment_capture": 1,
    })
    return JSONResponse({
        "order_id": order["id"],
        "amount": order["amount"],
        "key_id": RAZORPAY_KEY_ID,
    })


@app.post("/verify-payment")
async def verify_payment(request: Request):
    """Verify Razorpay payment signature and credit the user."""
    import razorpay, hmac, hashlib

    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    user_id = get_user_id_from_token(token)
    if not user_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    body = await request.json()
    order_id   = body.get("razorpay_order_id", "")
    payment_id = body.get("razorpay_payment_id", "")
    signature  = body.get("razorpay_signature", "")
    amount_inr = int(body.get("amount_inr", 0))

    # Verify signature
    msg = f"{order_id}|{payment_id}".encode()
    expected = hmac.new(RAZORPAY_KEY_SECRET.encode(), msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return JSONResponse({"error": "Invalid payment signature"}, status_code=400)

    # Add credits
    sb = get_sb_admin()
    if sb:
        try:
            result = sb.table("user_credits").select("balance_paise").eq("user_id", user_id).execute()
            add_paise = amount_inr * 100
            if result.data:
                new_bal = result.data[0]["balance_paise"] + add_paise
                sb.table("user_credits").update({"balance_paise": new_bal}).eq("user_id", user_id).execute()
            else:
                sb.table("user_credits").insert({"user_id": user_id, "balance_paise": add_paise}).execute()
        except Exception as e:
            logger.error(f"Credit top-up error: {e}")
            return JSONResponse({"error": "Failed to add credits"}, status_code=500)

    return JSONResponse({"success": True})


@app.get("/credits")
async def get_credits(request: Request):
    """Return current user balance."""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    user_id = get_user_id_from_token(token)
    if not user_id and SUPABASE_SERVICE_KEY:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    balance = get_or_create_balance(user_id) if user_id else 999999
    return JSONResponse({"balance_paise": balance, "balance_inr": round(balance / 100, 2)})


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the main UI."""
    html_path = static_dir / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.post("/run")
async def run_pipeline(request: Request):
    """
    Accept a JSON body {"idea": "..."} and stream pipeline events as SSE.
    Checks user credits before running. Deducts on success.
    """
    # ── Auth & credit check ───────────────────────────────────────────────────
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    user_id = get_user_id_from_token(token)

    if SUPABASE_SERVICE_KEY:
        if not user_id:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        balance = get_or_create_balance(user_id)
        if balance < QUERY_COST_PAISE:
            return JSONResponse({"error": "insufficient_credits"}, status_code=402)

    # ── Run pipeline ──────────────────────────────────────────────────────────
    body = await request.json()
    idea = (body.get("idea") or "").strip()
    if not idea:
        return HTMLResponse('{"error":"No idea provided"}', status_code=400)

    loop = asyncio.get_event_loop()
    event_queue: asyncio.Queue = asyncio.Queue()

    def emit(event: dict):
        loop.call_soon_threadsafe(event_queue.put_nowait, event)

    def worker():
        try:
            config = yaml.safe_load(Path("config.yaml").read_text())
            from pipeline import Pipeline
            p = Pipeline(config, on_event=emit)
            p.run(idea)
        except Exception as exc:
            logger.exception("Pipeline error")
            emit({"type": "error", "message": str(exc)})
        finally:
            emit({"type": "done"})

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    async def generate():
        pipeline_success = False
        while True:
            event = await event_queue.get()
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") == "pipeline_complete":
                pipeline_success = True
            if event.get("type") == "done":
                break
        # Deduct only on successful pipeline completion
        if pipeline_success and user_id and SUPABASE_SERVICE_KEY:
            deduct_balance(user_id)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


if __name__ == "__main__":
    import uvicorn
    print("\n  Multi-Agent Debate System UI")
    print("  Open: http://localhost:8000\n")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
