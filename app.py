import os
import time
import hashlib
import random
import logging
import json
import ntplib
from functools import wraps
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "hyperos-unlocker-secret-2024")

# ── Admin credentials (set via env vars on Render) ────────────────────────────
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "JEPFX")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "JEPFXADMIN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

accounts = {}     # token -> account_info
job_results = {}  # token -> list of attempt logs

scheduler = BackgroundScheduler()
BEIJING_TZ = pytz.timezone("Asia/Shanghai")

# ── Real API endpoints ─────────────────────────────────────────────────────────
STATUS_URL = "https://sgp-api.buy.mi.com/bbs/api/global/user/bl-switch/state"
APPLY_URL  = "https://sgp-api.buy.mi.com/bbs/api/global/apply/bl-auth"

NTP_SERVERS = [
    "ntp.aliyun.com",
    "ntp.tencent.com",
    "time.google.com",
    "pool.ntp.org",
]

# ── Auth helpers ───────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("login"))

# ── Helpers ────────────────────────────────────────────────────────────────────
def generate_device_id():
    raw = f"{random.random()}-{time.time()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest().upper()

def get_ntp_beijing_time():
    client = ntplib.NTPClient()
    for server in NTP_SERVERS:
        try:
            resp = client.request(server, version=3)
            utc  = datetime.fromtimestamp(resp.tx_time, tz=timezone.utc)
            return utc.astimezone(BEIJING_TZ)
        except Exception:
            continue
    return datetime.now(BEIJING_TZ)

def _cookie(token: str, device_id: str) -> str:
    return f"new_bbs_serviceToken={token};versionCode=500411;versionName=5.4.11;deviceId={device_id};"

def _headers(token: str, device_id: str) -> dict:
    return {
        "Cookie": _cookie(token, device_id),
        "User-Agent": "okhttp/4.12.0",
        "Content-Type": "application/json; charset=utf-8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }

# ── Core API calls ─────────────────────────────────────────────────────────────
def check_unlock_status(token: str, device_id: str = None) -> dict:
    if device_id is None:
        device_id = generate_device_id()
    try:
        r    = requests.get(STATUS_URL, headers=_headers(token, device_id), timeout=15)
        data = r.json()
        code = data.get("code")

        if code == 100004:
            return {"ok": False, "expired": True, "error": "Cookie expired — please refresh your token.", "data": data}
        if code == 100001:
            return {"ok": False, "error": "Request rejected by server.", "data": data}

        bl        = data.get("data", {})
        is_pass   = bl.get("is_pass")
        btn_state = bl.get("button_state")
        deadline  = bl.get("deadline_format", "")

        status_text = {
            (4, 1): "✅ Eligible — ready to apply",
            (4, 2): f"⏳ Blocked until {deadline}",
            (4, 3): "🔴 Account < 30 days old",
            (1, None): f"✅ Already approved — unlock before {deadline}",
        }.get((is_pass, btn_state), f"ℹ️ Unknown state (is_pass={is_pass}, btn={btn_state})")

        return {
            "ok": True,
            "status_code": r.status_code,
            "eligible":    (is_pass == 4 and btn_state == 1),
            "is_pass":     is_pass,
            "btn_state":   btn_state,
            "status_text": status_text,
            "data":        data,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

def apply_for_unlock(token: str, device_id: str, attempt_num: int = 1) -> dict:
    try:
        body = json.dumps({"is_retry": True}).encode("utf-8")
        hdrs = _headers(token, device_id)
        hdrs["Content-Length"] = str(len(body))
        r    = requests.post(APPLY_URL, headers=hdrs, data=body, timeout=20)
        data = r.json()
        code = data.get("code")

        if code == 0:
            ar = data.get("data", {}).get("apply_result")
            if ar == 1:
                msg = "✅ Approved! Check Mi Unlock settings."
            elif ar == 3:
                dl  = data.get("data", {}).get("deadline_format", "")
                msg = f"⏳ Daily limit hit. Try after {dl}."
            elif ar == 4:
                dl  = data.get("data", {}).get("deadline_format", "")
                msg = f"🚫 Blocked until {dl}."
            else:
                msg = f"code=0, apply_result={ar}"
        elif code == 100001:
            msg = "❌ Rejected by server."
        elif code == 100003:
            msg = "⚠️ Possibly approved — check status."
        elif code == 100004:
            msg = "🔑 Token expired — please refresh."
        else:
            msg = f"code={code}: {data.get('message','')}"

        logger.info(f"[Attempt {attempt_num}] {msg} | raw={data}")
        return {
            "ok":          True,
            "status_code": r.status_code,
            "code":        code,
            "message":     msg,
            "data":        data,
            "timestamp":   get_ntp_beijing_time().isoformat(),
        }
    except Exception as e:
        logger.error(f"[Attempt {attempt_num}] {e}")
        return {
            "ok":        False,
            "error":     str(e),
            "timestamp": get_ntp_beijing_time().isoformat(),
        }

# ── Scheduler job ──────────────────────────────────────────────────────────────
def auto_unlock_job():
    logger.info("⏰ Midnight unlock job firing!")
    for token, info in list(accounts.items()):
        if not info.get("active"):
            continue
        label     = info.get("label", "?")
        device_id = info.get("device_id", generate_device_id())
        logger.info(f"Processing: {label}")
        results = []
        for i in range(1, 11):
            result = apply_for_unlock(token, device_id, i)
            results.append(result)
            code = result.get("code")
            if code == 0:
                ar = (result.get("data") or {}).get("data", {}).get("apply_result")
                if ar in (1, 3, 4):
                    break
            if code in (100004,):
                break
            time.sleep(0.5)
        job_results.setdefault(token, []).extend(results)

scheduler.add_job(
    auto_unlock_job,
    CronTrigger(hour=23, minute=59, second=55, timezone=BEIJING_TZ),
    id="midnight_unlock", replace_existing=True, misfire_grace_time=60,
)
scheduler.start()

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    return render_template("index.html")

@app.route("/api/time")
@login_required
def api_time():
    bt  = get_ntp_beijing_time()
    mid = bt.replace(hour=0, minute=0, second=0, microsecond=0)
    if bt >= mid:
        mid += timedelta(days=1)
    return jsonify({
        "beijing_time":          bt.strftime("%Y-%m-%d %H:%M:%S"),
        "utc_time":              datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "seconds_until_midnight": int((mid - bt).total_seconds()),
    })

@app.route("/api/account/add", methods=["POST"])
@login_required
def add_account():
    body      = request.get_json(silent=True) or {}
    token     = (body.get("token") or "").strip()
    label     = (body.get("label") or "My Account").strip()
    device_id = generate_device_id()

    if not token:
        return jsonify({"ok": False, "error": "Token is required"}), 400

    status = check_unlock_status(token, device_id)
    if not status["ok"]:
        if status.get("expired"):
            return jsonify({"ok": False, "error": status["error"]}), 400
        return jsonify({"ok": False, "error": f"Mi server error: {status.get('error','unknown')}"}), 400

    accounts[token] = {
        "label":       label,
        "active":      True,
        "device_id":   device_id,
        "added_at":    get_ntp_beijing_time().isoformat(),
        "last_status": status,
    }
    st = status.get("status_text", "")
    logger.info(f"Account added: {label} | {st}")
    return jsonify({"ok": True, "message": f"Registered '{label}' — {st}"})

@app.route("/api/account/remove", methods=["POST"])
@login_required
def remove_account():
    body  = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()
    if token in accounts:
        del accounts[token]
        job_results.pop(token, None)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Token not found"}), 404

@app.route("/api/account/toggle", methods=["POST"])
@login_required
def toggle_account():
    body  = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()
    if token not in accounts:
        return jsonify({"ok": False, "error": "Token not found"}), 404
    accounts[token]["active"] = not accounts[token].get("active", True)
    state = "enabled" if accounts[token]["active"] else "disabled"
    return jsonify({"ok": True, "active": accounts[token]["active"], "message": f"Account {state}"})

@app.route("/api/accounts")
@login_required
def list_accounts():
    return jsonify([
        {
            "token":         t,
            "token_preview": t[:6] + "..." + t[-4:] if len(t) > 10 else "****",
            "label":         info.get("label"),
            "active":        info.get("active"),
            "added_at":      info.get("added_at"),
            "status_text":   (info.get("last_status") or {}).get("status_text", ""),
            "attempt_count": len(job_results.get(t, [])),
        }
        for t, info in accounts.items()
    ])

@app.route("/api/status/<path:token>")
@login_required
def check_status(token):
    if token not in accounts:
        return jsonify({"ok": False, "error": "Token not registered"}), 404
    device_id = accounts[token].get("device_id", generate_device_id())
    result    = check_unlock_status(token, device_id)
    if result["ok"]:
        accounts[token]["last_status"] = result
    return jsonify(result)

@app.route("/api/apply_now", methods=["POST"])
@login_required
def apply_now():
    body      = request.get_json(silent=True) or {}
    token     = (body.get("token") or "").strip()
    if not token:
        return jsonify({"ok": False, "error": "Token required"}), 400
    device_id = (accounts.get(token) or {}).get("device_id") or generate_device_id()
    result    = apply_for_unlock(token, device_id, attempt_num=0)
    job_results.setdefault(token, []).append({**result, "manual": True})
    return jsonify(result)

@app.route("/api/logs/<path:token>")
@login_required
def get_logs(token):
    return jsonify({
        "token_preview": token[:6] + "...",
        "logs":          job_results.get(token, [])[-50:],
    })

@app.route("/api/scheduler/status")
@login_required
def scheduler_status():
    job      = scheduler.get_job("midnight_unlock")
    next_run = None
    if job and job.next_run_time:
        next_run = job.next_run_time.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S (Beijing)")
    return jsonify({
        "running":         scheduler.running,
        "next_run":        next_run,
        "total_accounts":  len(accounts),
        "active_accounts": sum(1 for a in accounts.values() if a.get("active")),
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
