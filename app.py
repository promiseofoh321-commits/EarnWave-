import os
import sqlite3
import logging
import requests
from datetime import date, timedelta
from flask import Flask, request, jsonify, render_template

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

DB_PATH = os.environ.get("DB_PATH", "earnwave.db")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

REWARD_PER_AD = 0.003
CHECKIN_REWARD = 0.01
DAILY_AD_LIMIT = 50
MIN_WITHDRAWAL = 0.25


# ---------- Database ----------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            balance REAL DEFAULT 0,
            total_earned REAL DEFAULT 0,
            ads_today INTEGER DEFAULT 0,
            last_ad_date TEXT DEFAULT '',
            last_checkin_date TEXT DEFAULT '',
            checkin_streak INTEGER DEFAULT 0,
            referred_by TEXT DEFAULT '',
            referral_count INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            label TEXT,
            amount REAL,
            type TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def get_or_create_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row)


def reset_daily_if_needed(user_id, user):
    today = date.today().isoformat()
    if user["last_ad_date"] != today:
        conn = get_db()
        conn.execute("UPDATE users SET ads_today = 0, last_ad_date = ? WHERE user_id = ?", (today, user_id))
        conn.commit()
        conn.close()
        user["ads_today"] = 0
        user["last_ad_date"] = today
    return user


def log_activity(user_id, label, amount, type_):
    conn = get_db()
    conn.execute("INSERT INTO activity (user_id, label, amount, type) VALUES (?, ?, ?, ?)", (user_id, label, amount, type_))
    conn.commit()
    conn.close()


# ---------- Mini App page ----------

@app.route("/")
def home():
    return render_template("index.html")


# ---------- API ----------

@app.route("/api/user/<user_id>", methods=["GET"])
def get_user(user_id):
    user = get_or_create_user(user_id)
    user = reset_daily_if_needed(user_id, user)
    conn = get_db()
    activity = conn.execute(
        "SELECT label, amount, type, created_at FROM activity WHERE user_id = ? ORDER BY id DESC LIMIT 15",
        (user_id,),
    ).fetchall()
    conn.close()
    return jsonify({
        "balance": user["balance"],
        "total_earned": user["total_earned"],
        "ads_today": user["ads_today"],
        "daily_limit": DAILY_AD_LIMIT,
        "checkin_streak": user["checkin_streak"],
        "last_checkin_date": user["last_checkin_date"],
        "referral_count": user["referral_count"],
        "activity": [dict(a) for a in activity],
    })


@app.route("/api/reward", methods=["GET", "POST"])
def adsgram_reward():
    """Adsgram calls this when a user finishes a rewarded ad.
    Set as Reward URL in Adsgram: https://YOUR-URL/api/reward?userId=[userId]
    """
    user_id = request.args.get("userId") or request.form.get("userId")
    if not user_id:
        return jsonify({"error": "missing userId"}), 400

    user = get_or_create_user(user_id)
    user = reset_daily_if_needed(user_id, user)

    if user["ads_today"] >= DAILY_AD_LIMIT:
        return jsonify({"error": "daily limit reached"}), 403

    conn = get_db()
    conn.execute(
        "UPDATE users SET balance = balance + ?, total_earned = total_earned + ?, ads_today = ads_today + 1 WHERE user_id = ?",
        (REWARD_PER_AD, REWARD_PER_AD, user_id),
    )
    conn.commit()
    conn.close()
    log_activity(user_id, "Ad reward", REWARD_PER_AD, "ad")
    return jsonify({"status": "ok", "reward": REWARD_PER_AD})


@app.route("/api/checkin", methods=["POST"])
def daily_checkin():
    data = request.get_json(force=True)
    user_id = str(data.get("user_id", ""))
    if not user_id:
        return jsonify({"error": "missing user_id"}), 400

    user = get_or_create_user(user_id)
    today = date.today().isoformat()
    if user["last_checkin_date"] == today:
        return jsonify({"error": "already checked in today"}), 400

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    streak = user["checkin_streak"] + 1 if user["last_checkin_date"] == yesterday else 1

    conn = get_db()
    conn.execute(
        "UPDATE users SET balance = balance + ?, total_earned = total_earned + ?, checkin_streak = ?, last_checkin_date = ? WHERE user_id = ?",
        (CHECKIN_REWARD, CHECKIN_REWARD, streak, today, user_id),
    )
    conn.commit()
    conn.close()
    log_activity(user_id, "Daily check-in", CHECKIN_REWARD, "checkin")
    return jsonify({"status": "ok", "reward": CHECKIN_REWARD, "streak": streak})


@app.route("/api/withdraw", methods=["POST"])
def request_withdrawal():
    data = request.get_json(force=True)
    user_id = str(data.get("user_id", ""))
    wallet = data.get("wallet", "").strip()
    amount = float(data.get("amount", 0))

    if not user_id or not wallet or amount <= 0:
        return jsonify({"error": "invalid request"}), 400

    if amount < MIN_WITHDRAWAL:
        return jsonify({"error": f"minimum withdrawal is ${MIN_WITHDRAWAL:.3f}"}), 400

    user = get_or_create_user(user_id)
    if amount > user["balance"]:
        return jsonify({"error": "amount exceeds balance"}), 400

    conn = get_db()
    conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()
    log_activity(user_id, f"Withdrawal requested to {wallet}", -amount, "withdraw")
    return jsonify({"status": "ok"})


@app.route("/api/register_referral", methods=["POST"])
def register_referral():
    data = request.get_json(force=True)
    new_user_id = str(data.get("user_id", ""))
    referrer_id = str(data.get("referrer_id", ""))

    if not new_user_id or not referrer_id or new_user_id == referrer_id:
        return jsonify({"error": "invalid referral"}), 400

    new_user = get_or_create_user(new_user_id)
    if new_user["referred_by"]:
        return jsonify({"error": "already referred"}), 400

    get_or_create_user(referrer_id)

    conn = get_db()
    conn.execute("UPDATE users SET referred_by = ? WHERE user_id = ?", (referrer_id, new_user_id))
    conn.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?", (referrer_id,))
    conn.commit()
    conn.close()
    log_activity(referrer_id, "Referral joined", 0, "referral")
    return jsonify({"status": "ok"})


# ---------- Telegram bot webhook ----------

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=5)
    except requests.RequestException:
        logging.warning("Failed to send Telegram message")


@app.route("/bot-webhook", methods=["POST"])
def bot_webhook():
    update = request.get_json(force=True)
    message = update.get("message")
    if not message:
        return jsonify({"ok": True})

    chat_id = message["chat"]["id"]
    user = message["from"]
    text = message.get("text", "")

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].startswith("ref_"):
            referrer_id = parts[1].replace("ref_", "")
            try:
                requests.post(
                    request.url_root + "api/register_referral",
                    json={"user_id": str(user["id"]), "referrer_id": referrer_id},
                    timeout=5,
                )
            except requests.RequestException:
                logging.warning("Failed to register referral")

        webapp_url = request.url_root  # same domain serves the Mini App at "/"
        reply_markup = {
            "inline_keyboard": [[
                {"text": "Open EarnWave", "web_app": {"url": webapp_url}}
            ]]
        }
        first_name = user.get("first_name", "there")
        send_message(
            chat_id,
            f"Welcome to EarnWave, {first_name}!\n\n"
            "Watch ads, earn small crypto rewards, and cash out once you hit the minimum.\n\n"
            "Tap below to open the app.",
            reply_markup=reply_markup,
        )

    return jsonify({"ok": True})


@app.route("/set-webhook")
def set_webhook():
    """Visit this URL once after deploying to register the bot webhook."""
    webhook_url = request.url_root + "bot-webhook"
    resp = requests.get(f"{TELEGRAM_API}/setWebhook", params={"url": webhook_url})
    return jsonify(resp.json())


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)