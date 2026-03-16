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
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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

# ── Razorpay config (India) ───────────────────────────────────────────────────
RAZORPAY_KEY_ID     = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")


# ── Alert email config ────────────────────────────────────────────────────────
ALERT_TO_EMAIL    = "nagarajmahadevanc@gmail.com"
ALERT_SMTP_USER   = os.getenv("ALERT_SMTP_USER", "")      # e.g. your Gmail address
ALERT_SMTP_PASS   = os.getenv("ALERT_SMTP_PASSWORD", "")  # Gmail App Password
ALERT_SMTP_HOST   = os.getenv("ALERT_SMTP_HOST", "smtp.gmail.com")
ALERT_SMTP_PORT   = int(os.getenv("ALERT_SMTP_PORT", "587"))

# ── Credit config ─────────────────────────────────────────────────────────────
SUPABASE_URL         = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
MARKUP_INR           = 2.0    # ₹2 profit per query (not shown to user)
MARKUP_PAISE         = 200    # same in paise
MIN_BALANCE_PAISE    = 300    # minimum balance required to start a query
FREE_CREDITS_PAISE   = 1000   # 1 free query worth of credits for new users

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


def deduct_balance_amount(user_id: str, paise: int):
    """Deduct an exact paise amount from the user's balance."""
    sb = get_sb_admin()
    if not sb:
        return
    try:
        result = sb.table("user_credits").select("balance_paise").eq("user_id", user_id).execute()
        if result.data:
            new_bal = max(0, result.data[0]["balance_paise"] - paise)
            sb.table("user_credits").update({"balance_paise": new_bal}).eq("user_id", user_id).execute()
    except Exception as e:
        logger.error(f"Deduct balance error: {e}")


def insert_analytics(
    user_id: str,
    prompt: str,
    actual_cost_inr: float,
    profit_inr: float,
    charged_inr: float,
    elapsed: float,
    agent_breakdown: list,
    total_input_tokens: int,
    total_output_tokens: int,
):
    """Insert a full query analytics record into Supabase."""
    sb = get_sb_admin()
    if not sb:
        return
    try:
        from datetime import datetime, timezone
        sb.table("query_analytics").insert({
            "user_id": user_id,
            "prompt": prompt[:2000],  # cap at 2000 chars
            "actual_cost_inr": round(actual_cost_inr, 6),
            "profit_inr": round(profit_inr, 6),
            "charged_inr": round(charged_inr, 6),
            "elapsed_seconds": round(elapsed, 2),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "agent_breakdown": agent_breakdown,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        logger.error(f"Analytics insert error: {e}")


def get_user_email(user_id: str) -> str:
    """Fetch the email address for a user from Supabase auth."""
    sb = get_sb_admin()
    if not sb or not user_id:
        return "unknown"
    try:
        result = sb.auth.admin.get_user_by_id(user_id)
        return result.user.email or "unknown"
    except Exception:
        return "unknown"


def send_alert_email(user_id: str, user_email: str, agent_name: str, error_msg: str, prompt: str):
    """Send an alert email when an LLM agent fails."""
    if not ALERT_SMTP_USER or not ALERT_SMTP_PASS:
        logger.warning(
            f"[ALERT] Agent failure (email not configured) — "
            f"agent={agent_name}, user={user_email}, error={error_msg[:300]}"
        )
        return

    subject = f"[Verd Alert] Agent failure: {agent_name}"
    body = (
        f"An LLM agent failed on Verd and the pipeline was stopped.\n\n"
        f"Agent:        {agent_name}\n"
        f"User ID:      {user_id}\n"
        f"User Email:   {user_email}\n\n"
        f"Error:\n{error_msg}\n\n"
        f"Prompt (first 500 chars):\n{prompt[:500]}\n"
    )

    msg = MIMEMultipart()
    msg["From"]    = ALERT_SMTP_USER
    msg["To"]      = ALERT_TO_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(ALERT_SMTP_HOST, ALERT_SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(ALERT_SMTP_USER, ALERT_SMTP_PASS)
            server.sendmail(ALERT_SMTP_USER, ALERT_TO_EMAIL, msg.as_string())
        logger.info(f"Alert email sent — agent={agent_name}, user={user_email}")
    except Exception as e:
        logger.error(f"Failed to send alert email: {e}")


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


@app.post("/create-payment-link")
async def create_payment_link(request: Request):
    """
    Create a Razorpay Payment Link — bypasses website domain restrictions.
    User is redirected to razorpay.com to complete payment, then back to our site.
    """
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

    # Encode user_id in callback URL so we know who paid
    import urllib.parse
    callback_url = body.get("callback_url", "")  # frontend passes its own origin

    import razorpay
    client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    link = client.payment_link.create({
        "amount": amount_inr * 100,
        "currency": "INR",
        "description": f"Multi-Agent Debate System — ₹{amount_inr} top-up",
        "customer": {
            "email": body.get("email", ""),
        },
        "notify": {"email": False, "sms": False},
        "reminder_enable": False,
        "callback_url": f"{callback_url}/payment-return?uid={urllib.parse.quote(user_id)}&amount={amount_inr}",
        "callback_method": "get",
    })
    return JSONResponse({"payment_url": link["short_url"]})


@app.get("/payment-return")
async def payment_return(request: Request):
    """
    Razorpay redirects here after payment link is completed.
    Verifies payment and credits the user, then redirects back to app.
    """
    import razorpay, hmac, hashlib

    params = dict(request.query_params)
    payment_id = params.get("razorpay_payment_id", "")
    payment_link_id = params.get("razorpay_payment_link_id", "")
    payment_link_ref_id = params.get("razorpay_payment_link_reference_id", "")
    payment_link_status = params.get("razorpay_payment_link_status", "")
    signature = params.get("razorpay_signature", "")
    user_id = params.get("uid", "")
    amount_inr = int(params.get("amount", 0))

    # Verify signature
    payload = f"{payment_link_id}|{payment_link_ref_id}|{payment_link_status}|{payment_id}"
    expected = hmac.new(RAZORPAY_KEY_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

    if payment_link_status == "paid" and hmac.compare_digest(expected, signature) and user_id and amount_inr > 0:
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
                logger.error(f"Payment link credit error: {e}")

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/?payment=success" if payment_link_status == "paid" else "/?payment=failed")


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



# ── GDPR ──────────────────────────────────────────────────────────────────────

@app.delete("/delete-account")
async def delete_account(request: Request):
    """GDPR: permanently delete all user data and auth record."""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    user_id = get_user_id_from_token(token)
    if not user_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    sb = get_sb_admin()
    if not sb:
        return JSONResponse({"error": "Not configured"}, status_code=503)

    try:
        sb.table("user_credits").delete().eq("user_id", user_id).execute()
        sb.table("query_analytics").delete().eq("user_id", user_id).execute()
        sb.table("conversations").delete().eq("user_id", user_id).execute()
        sb.auth.admin.delete_user(user_id)
        return JSONResponse({"success": True})
    except Exception as e:
        logger.error(f"Delete account error: {e}")
        return JSONResponse({"error": "Failed to delete account"}, status_code=500)


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the main UI."""
    html_path = static_dir / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/robots.txt", response_class=HTMLResponse)
def robots():
    return HTMLResponse((static_dir / "robots.txt").read_text(), media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap():
    from fastapi.responses import Response
    return Response((static_dir / "sitemap.xml").read_text(), media_type="application/xml")


@app.get("/llms.txt", response_class=HTMLResponse)
def llms():
    return HTMLResponse((static_dir / "llms.txt").read_text(), media_type="text/plain")


@app.post("/run")
async def run_pipeline(request: Request):
    """
    Accept a JSON body {"idea": "..."} and stream pipeline events as SSE.
    Checks user credits before running. Deducts actual cost + ₹2 markup on success.
    Stores full analytics in Supabase.
    """
    # ── Auth & credit check ───────────────────────────────────────────────────
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    user_id = get_user_id_from_token(token)

    if SUPABASE_SERVICE_KEY:
        if not user_id:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        balance = get_or_create_balance(user_id)
        if balance < MIN_BALANCE_PAISE:
            return JSONResponse({"error": "insufficient_credits"}, status_code=402)

    # ── Run pipeline ──────────────────────────────────────────────────────────
    body = await request.json()
    idea = (body.get("idea") or "").strip()
    if not idea:
        return HTMLResponse('{"error":"No idea provided"}', status_code=400)
    history  = body.get("history") or []
    language = (body.get("language") or "en").strip()[:10]  # sanitize

    loop = asyncio.get_event_loop()
    event_queue: asyncio.Queue = asyncio.Queue()

    def emit(event: dict):
        loop.call_soon_threadsafe(event_queue.put_nowait, event)

    def worker():
        try:
            config = yaml.safe_load(Path("config.yaml").read_text())
            from pipeline import Pipeline
            p = Pipeline(config, on_event=emit)
            result = p.run(idea, history=history, language=language)
            # Build per-agent cost breakdown for analytics
            breakdown = []
            ct = result.get("cost_tracker")
            if ct:
                for rec in ct.records:
                    breakdown.append({
                        "agent": rec.agent_name,
                        "model": rec.model,
                        "input_tokens": rec.input_tokens,
                        "output_tokens": rec.output_tokens,
                        "cost_inr": round(rec.total_cost_inr, 6),
                    })
            # Emit internal event (intercepted in generate(), never forwarded to client)
            emit({
                "type": "_internal_result",
                "actual_cost_inr": ct.total_cost_inr if ct else 0,
                "total_input_tokens": ct.total_input_tokens if ct else 0,
                "total_output_tokens": ct.total_output_tokens if ct else 0,
                "elapsed": result.get("elapsed_seconds", 0),
                "agent_breakdown": breakdown,
            })
        except Exception as exc:
            logger.exception("Pipeline error")
            emit({"type": "error", "message": str(exc)})
        finally:
            emit({"type": "done"})

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    async def generate():
        pipeline_success = False
        internal_result = None
        while True:
            try:
                # 15s timeout — send SSE keepalive comment to prevent Railway/proxy
                # from closing the connection during long-running agent calls
                event = await asyncio.wait_for(event_queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue

            if event.get("type") == "_internal_result":
                # Internal only — capture and never forward to client
                internal_result = event
                continue

            if event.get("type") == "pipeline_failed":
                # Agent failure — send alert email and return user-friendly error
                agent_name    = event.get("failed_agent", "unknown")
                agent_display = event.get("failed_agent_display", agent_name)
                error_msg     = event.get("error_message", "Unknown error")
                user_email    = get_user_email(user_id) if user_id else "anonymous"
                threading.Thread(
                    target=send_alert_email,
                    args=(user_id or "anonymous", user_email, agent_display, error_msg, idea),
                    daemon=True,
                ).start()
                yield f"data: {json.dumps({'type': 'error', 'message': 'One of our AI providers is temporarily unavailable. Please try again in a few minutes.'})}\n\n"
                continue

            yield f"data: {json.dumps(event)}\n\n"

            if event.get("type") == "pipeline_complete":
                pipeline_success = True
            if event.get("type") == "done":
                break

        # ── Billing & analytics on successful completion ──────────────────
        if pipeline_success and user_id and SUPABASE_SERVICE_KEY and internal_result:
            actual_cost_inr = internal_result.get("actual_cost_inr", 0)
            charged_inr = actual_cost_inr + MARKUP_INR
            charged_paise = int(round(charged_inr * 100))
            deduct_balance_amount(user_id, charged_paise)
            insert_analytics(
                user_id=user_id,
                prompt=idea,
                actual_cost_inr=actual_cost_inr,
                profit_inr=MARKUP_INR,
                charged_inr=charged_inr,
                elapsed=internal_result.get("elapsed", 0),
                agent_breakdown=internal_result.get("agent_breakdown", []),
                total_input_tokens=internal_result.get("total_input_tokens", 0),
                total_output_tokens=internal_result.get("total_output_tokens", 0),
            )
        elif pipeline_success and user_id and SUPABASE_SERVICE_KEY:
            # Fallback: no cost data, deduct minimum markup only
            deduct_balance_amount(user_id, MARKUP_PAISE)

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
