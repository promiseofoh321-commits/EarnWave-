from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from datetime import datetime, date
import uuid

app = Flask(__name__)
CORS(app)

# Simple storage (replace with database later)
users = {}
withdrawals = []
payouts = [
    {"name": "Adaora", "amount": 4.03, "tx": "0x1a2b...c4d5e6", "time": "Just now"},
    {"name": "Chinedu", "amount": 1.25, "tx": "0x9f8e...a1b2c3", "time": "5 min ago"},
]

REWARD_PER_AD = 0.005
DAILY_CHECKIN_REWARD = 0.010
MIN_WITHDRAW = 0.250

def get_or_create_user(tg_id, username="User"):
    if tg_id not in users:
        users[tg_id] = {
            "id": tg_id,
            "username": username,
            "balance": 0.0,
            "total_earned": 0.0,
            "ads_watched": 0,
            "streak": 0,
            "last_checkin": None,
            "referral_code": str(uuid.uuid4())[:8].upper(),
            "wallet": ""
        }
    return users[tg_id]

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/user", methods=["POST"])
def get_user():
    data = request.json
    tg_id = str(data.get("telegram_id"))
    username = data.get("username", "User")
    user = get_or_create_user(tg_id, username)
    return jsonify(user)

@app.route("/api/watch-ad", methods=["POST"])
def watch_ad():
    data = request.json
    tg_id = str(data.get("telegram_id"))
    user = get_or_create_user(tg_id)

    user["balance"] = round(user["balance"] + REWARD_PER_AD, 4)
    user["total_earned"] = round(user["total_earned"] + REWARD_PER_AD, 4)
    user["ads_watched"] += 1

    return jsonify({
        "success": True,
        "balance": user["balance"],
        "total_earned": user["total_earned"],
        "ads_watched": user["ads_watched"],
        "reward": REWARD_PER_AD
    })

@app.route("/api/checkin", methods=["POST"])
def checkin():
    data = request.json
    tg_id = str(data.get("telegram_id"))
    user = get_or_create_user(tg_id)

    today = str(date.today())
    if user["last_checkin"] == today:
        return jsonify({"success": False, "message": "Already claimed today"})

    user["balance"] = round(user["balance"] + DAILY_CHECKIN_REWARD, 4)
    user["total_earned"] = round(user["total_earned"] + DAILY_CHECKIN_REWARD, 4)
    user["streak"] += 1
    user["last_checkin"] = today

    return jsonify({
        "success": True,
        "balance": user["balance"],
        "streak": user["streak"],
        "reward": DAILY_CHECKIN_REWARD
    })

@app.route("/api/withdraw", methods=["POST"])
def withdraw():
    data = request.json
    tg_id = str(data.get("telegram_id"))
    wallet = data.get("wallet", "").strip()
    user = get_or_create_user(tg_id)

    if user["balance"] < MIN_WITHDRAW:
        return jsonify({"success": False, "message": f"Minimum withdrawal is ${MIN_WITHDRAW}"})

    if not wallet or len(wallet) < 10:
        return jsonify({"success": False, "message": "Invalid wallet address"})

    amount = user["balance"]
    user["balance"] = 0.0
    user["wallet"] = wallet

    withdrawals.append({
        "id": str(uuid.uuid4()),
        "telegram_id": tg_id,
        "username": user["username"],
        "amount": amount,
        "wallet": wallet,
        "status": "pending",
        "created_at": datetime.now().isoformat()
    })

    return jsonify({"success": True, "message": "Withdrawal requested", "amount": amount})

@app.route("/api/payouts")
def get_payouts():
    return jsonify(payouts)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)