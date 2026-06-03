# 🔓 HyperOS Unlock Bot

A self-hosted web server that automatically applies for Xiaomi HyperOS bootloader unlock at **00:00 Beijing Time (GMT+8)** — when the daily quota resets — so you don't have to stay up.

---

## 🚀 Deploy to Render (Free)

### 1. Fork / Push to GitHub
Push this entire folder to a new GitHub repo.

### 2. Create Render Web Service
1. Go to [render.com](https://render.com) and sign in
2. Click **New → Web Service**
3. Connect your GitHub repo
4. Settings:
   - **Environment:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
   - **Plan:** Free

Render's free tier spins down after inactivity. **Use the free UptimeRobot** (uptimerobot.com) to ping your Render URL every 5 minutes to keep it alive, especially near midnight Beijing time.

### 3. Environment Variables (optional)
| Key | Value |
|---|---|
| `SECRET_KEY` | Any random string |

---

## 🍪 Getting Your serviceToken

1. Open **https://c.mi.com** in Chrome and log into your Mi Account
2. Press `F12` → **Application** tab → **Cookies** → `c.mi.com`
3. Find `new_bbs_serviceToken` and copy its **Value**
4. Paste it into the web UI

> The token expires after some time. Re-add your account if you start getting auth errors.

---

## ✅ Eligibility Requirements

Before using this tool, confirm your account/device meets Xiaomi's criteria:
- Mi account age **30+ days** (some regions require more)
- Mi account **community level** requirements met
- Device bound to Mi account via **Developer Options → Mi Unlock Status**
- Device is a **Global** HyperOS device (not CN firmware)
- You have not unlocked another device within the past **1 year**

---

## 🔁 How It Works

1. You paste your `new_bbs_serviceToken` cookie into the web UI
2. The server registers your account and schedules a job at **23:59:59 Beijing time**
3. At midnight, the server fires up to **5 unlock apply requests** with 2-second intervals
4. Results are logged and viewable in the UI
5. If successful, finish the unlock using the **Mi Unlock Tool for Windows** or the Python MiUnlockTool

---

## ⚠️ Important Notes

- As of **January 2026**, Xiaomi removed the unlock application feature from their Community app for new requests. If you're reading this after that date, check XDA forums for the latest status.
- This tool targets the **Community API endpoint** that the app uses internally — it may break if Xiaomi changes their API.
- Only **1 device per year** can be unlocked per account (as of Jan 2025).
- There is a **daily quota cap** — the bot sends multiple rapid requests at midnight to maximize your chances of getting a slot.

---

## 🧰 Local Development

```bash
pip install -r requirements.txt
python app.py
```
Open http://localhost:5000

---

## 📝 API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/time` | Current Beijing time + countdown |
| POST | `/api/account/add` | Register an account `{token, label}` |
| POST | `/api/account/remove` | Remove account `{token}` |
| POST | `/api/account/toggle` | Pause/resume `{token}` |
| GET | `/api/accounts` | List all accounts |
| GET | `/api/status/<token>` | Check unlock status from Mi servers |
| POST | `/api/apply_now` | Manually trigger apply `{token}` |
| GET | `/api/logs/<token>` | View attempt logs |
| GET | `/api/scheduler/status` | Scheduler health check |

---

## 🛡️ Security

- Never share your `serviceToken` — it's equivalent to your Mi session cookie
- Use a fresh browser session / incognito to get the token
- The token only grants access to Mi Community; it does not expose your Mi password
- Tokens are stored **in memory only** and are lost on server restart

---

*This is a personal automation tool. Use responsibly.*
