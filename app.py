import os
import json
import random
import re
import smtplib
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from email.mime.text import MIMEText
from functools import wraps
from flask import Flask, request, session, redirect, url_for, render_template_string, flash, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "topfollowfaster_super_secret_key_2026")

RECEIPT_UPLOAD_DIR = os.path.join("static", "receipts")
ALLOWED_RECEIPT_EXTS = {"png", "jpg", "jpeg", "webp", "pdf"}
os.makedirs(RECEIPT_UPLOAD_DIR, exist_ok=True)

# ================= SOZLAMALAR =================
CONFIG = {
    "SITE_NAME": "TopFollowFaster",
    "ADVERTISER_PRICE": 70,              # Reklamachi 1 obunachi uchun to'laydi (so'm)
    "WORKER_REWARD": 50,                 # Bajaruvchi 1 obuna uchun oladi (so'm)
    "REFERRAL_BONUS": 500,               # Referal ilk vazifa bajarganda bonus (so'm)
    "MIN_DEPOSIT": 1000,                 # Minimal deposit (so'm)
    "MIN_WITHDRAW": 20000,               # Minimal pul chiqarish (so'm)
    "CARD_NUMBER": "9860 1701 1301 9658",  # Deposit karta raqami
    "CARD_HOLDER": "ADMIN KARTASI",        # Karta egasi
    "ADMIN_USER": "admin",
    "ADMIN_PASS": "admin123",
    "ADMIN_EMAIL": "maripjonovbahromjon@gmail.com",
    "SMTP_EMAIL": os.environ.get("SMTP_EMAIL", "maripjonovbahromjon@gmail.com"),
    "SMTP_APP_PASSWORD": os.environ.get("SMTP_APP_PASSWORD", ""),
    "GOOGLE_CLIENT_ID": os.environ.get("GOOGLE_CLIENT_ID", ""),
    "GOOGLE_CLIENT_SECRET": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
    "DB_NAME": "topfollowfaster.db"
}

PLATFORMS = [
    ("instagram", "Instagram (Obuna)"),
    ("youtube", "YouTube (Obuna)"),
    ("telegram_channel", "Telegram Kanal (Obuna)"),
    ("telegram_group", "Telegram Guruh (Qo'shilish)"),
]
PLATFORM_DICT = dict(PLATFORMS)

# ================= MA'LUMOTLAR BAZASI =================
def get_db():
    conn = sqlite3.connect(CONFIG["DB_NAME"])
    conn.row_factory = sqlite3.Row
    return conn

def generate_bio_code():
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "TFF-" + "".join(random.choice(chars) for _ in range(6))

def seed_initial_tasks(c, admin_id):
    existing = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    if existing > 0:
        return
    sample_tasks = [
        (admin_id, "instagram", "Instagram sahifaga obuna bo'ling (TopFollowFaster)", "https://www.instagram.com/instagram/", 100),
        (admin_id, "youtube", "YouTube kanalga obuna bo'ling", "https://www.youtube.com/", 100),
        (admin_id, "telegram_channel", "Telegram yangiliklar kanaliga a'zo bo'ling", "https://t.me/telegram", 100),
        (admin_id, "telegram_group", "Telegram muhokama guruhiga qo'shiling", "https://t.me/telegram", 100),
    ]
    for owner_id, platform, title, link, target_count in sample_tasks:
        c.execute("""
            INSERT INTO tasks (owner_id, platform, title, link, target_count, price_per_action, reward_per_action, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
        """, (owner_id, platform, title, link, target_count, CONFIG["ADVERTISER_PRICE"], CONFIG["WORKER_REWARD"]))

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE,
            google_id TEXT DEFAULT '',
            password_hash TEXT NOT NULL,
            referrer_id INTEGER DEFAULT NULL,
            ref_bonus_paid INTEGER DEFAULT 0,
            ig_nick TEXT DEFAULT '',
            ig_bio_code TEXT DEFAULT '',
            ig_verified INTEGER DEFAULT 0,
            ig_sessionid TEXT DEFAULT '',
            yt_nick TEXT DEFAULT '',
            tg_nick TEXT DEFAULT '',
            reset_code TEXT DEFAULT '',
            reset_expires INTEGER DEFAULT 0,
            work_balance INTEGER DEFAULT 0,
            ad_balance INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            title TEXT NOT NULL,
            link TEXT NOT NULL,
            target_count INTEGER NOT NULL,
            completed_count INTEGER DEFAULT 0,
            price_per_action INTEGER NOT NULL,
            reward_per_action INTEGER NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS completions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            reward INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, task_id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tx_type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            details TEXT NOT NULL,
            receipt_file TEXT DEFAULT '',
            admin_receipt TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    user_cols = [row[1] for row in c.execute("PRAGMA table_info(users)").fetchall()]
    for col, ddl in [
        ("email", "ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''"),
        ("google_id", "ALTER TABLE users ADD COLUMN google_id TEXT DEFAULT ''"),
        ("referrer_id", "ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT NULL"),
        ("ref_bonus_paid", "ALTER TABLE users ADD COLUMN ref_bonus_paid INTEGER DEFAULT 0"),
        ("ig_bio_code", "ALTER TABLE users ADD COLUMN ig_bio_code TEXT DEFAULT ''"),
        ("ig_verified", "ALTER TABLE users ADD COLUMN ig_verified INTEGER DEFAULT 0"),
        ("ig_sessionid", "ALTER TABLE users ADD COLUMN ig_sessionid TEXT DEFAULT ''"),
        ("reset_code", "ALTER TABLE users ADD COLUMN reset_code TEXT DEFAULT ''"),
        ("reset_expires", "ALTER TABLE users ADD COLUMN reset_expires INTEGER DEFAULT 0"),
    ]:
        if col not in user_cols:
            c.execute(ddl)

    tx_cols = [row[1] for row in c.execute("PRAGMA table_info(transactions)").fetchall()]
    for col, ddl in [
        ("receipt_file", "ALTER TABLE transactions ADD COLUMN receipt_file TEXT DEFAULT ''"),
        ("admin_receipt", "ALTER TABLE transactions ADD COLUMN admin_receipt TEXT DEFAULT ''"),
    ]:
        if col not in tx_cols:
            c.execute(ddl)

    rows = c.execute("SELECT id, ig_bio_code FROM users").fetchall()
    for r in rows:
        if not r["ig_bio_code"]:
            c.execute("UPDATE users SET ig_bio_code = ? WHERE id = ?", (generate_bio_code(), r["id"]))

    admin = c.execute("SELECT id FROM users WHERE username = ?", (CONFIG["ADMIN_USER"],)).fetchone()
    if not admin:
        cur = c.execute("""
            INSERT INTO users (username, email, password_hash, ig_bio_code, ad_balance, is_admin)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (CONFIG["ADMIN_USER"], CONFIG["ADMIN_EMAIL"], generate_password_hash(CONFIG["ADMIN_PASS"]), generate_bio_code(), 10000))
        admin_id = cur.lastrowid
    else:
        admin_id = admin["id"]
        c.execute("""
            UPDATE users SET email = ?, is_admin = 1 WHERE username = ?
        """, (CONFIG["ADMIN_EMAIL"], CONFIG["ADMIN_USER"]))

    seed_initial_tasks(c, admin_id)

    conn.commit()
    conn.close()

def is_user_admin(user):
    if not user:
        return False
    email = (user["email"] or "").strip().lower()
    if CONFIG["ADMIN_EMAIL"] and email == CONFIG["ADMIN_EMAIL"].lower():
        return True
    return bool(user["is_admin"]) and user["username"] == CONFIG["ADMIN_USER"]

def allowed_receipt(filename):
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_RECEIPT_EXTS

# ================= EMAIL VA INSTAGRAM BIO + FOLLOW TEKSHIRUV =================
def send_reset_email(to_email, code):
    smtp_email = CONFIG["SMTP_EMAIL"].strip()
    smtp_pass = CONFIG["SMTP_APP_PASSWORD"].replace(" ", "").strip()
    if not smtp_email or not smtp_pass:
        return False, "SMTP_APP_PASSWORD Render Environment bo'limiga kiritilmagan!"

    body = (
        f"Salom!\n\n"
        f"{CONFIG['SITE_NAME']} saytida parolni tiklash uchun tasdiqlash kodingiz: {code}\n"
        f"Kod 10 daqiqa davomida amal qiladi.\n\n"
        f"Agar siz so'rov yubormagan bo'lsangiz, bu xabarga e'tibor bermang."
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"{CONFIG['SITE_NAME']} — Parolni tiklash kodi: {code}"
    msg["From"] = smtp_email
    msg["To"] = to_email

    last_err = ""
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_email, smtp_pass)
            server.sendmail(smtp_email, [to_email], msg.as_string())
        return True, "Kod emailga yuborildi!"
    except Exception as e:
        last_err = str(e)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(smtp_email, smtp_pass)
            server.sendmail(smtp_email, [to_email], msg.as_string())
        return True, "Kod emailga yuborildi!"
    except Exception as e:
        last_err = str(e)

    return False, f"Email yuborishda xatolik: {last_err}"

def fetch_instagram_profile_info(ig_username):
    clean_user = ig_username.lstrip("@").strip().lower()
    if not clean_user:
        return None
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
        "X-IG-App-ID": "936619743392459",
        "Accept": "application/json, text/plain, */*",
    }
    try:
        api_url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={urllib.parse.quote(clean_user)}"
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        u = (data.get("data") or {}).get("user")
        if u:
            return {
                "username": u.get("username", clean_user),
                "biography": u.get("biography", "") or "",
                "is_private": bool(u.get("is_private", False)),
            }
    except Exception:
        pass

    try:
        page_url = f"https://www.instagram.com/{urllib.parse.quote(clean_user)}/"
        req = urllib.request.Request(page_url, headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        bio_match = re.search(r'"biography"\s*:\s*"((?:\\.|[^"\\])*)"', html)
        if bio_match:
            raw_bio = json.loads(f'"{bio_match.group(1)}"')
            return {
                "username": clean_user,
                "biography": raw_bio,
                "is_private": '"is_private":true' in html.lower(),
            }
        meta_match = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']', html, re.I)
        if meta_match:
            return {
                "username": clean_user,
                "biography": urllib.parse.unescape(meta_match.group(1)),
                "is_private": False,
            }
    except Exception:
        pass
    return None

def verify_ig_bio_code(ig_username, expected_code):
    clean_user = ig_username.lstrip("@").strip()
    if not clean_user or not expected_code:
        return False, "Instagram username va kod kiriting!"
    info = fetch_instagram_profile_info(clean_user)
    if not info:
        return False, "Instagram profilini o'qib bo'lmadi. Akkaunt ochiq (Public) ekanligini va username to'g'riligini tekshiring."
    bio_text = info.get("biography", "")
    if expected_code.upper() in bio_text.upper():
        return True, f"@{info['username']} akkaunti bio kodi ({expected_code}) orqali muvaffaqiyatli tasdiqlandi!"
    return False, f"@{info['username']} profilining Bio qismida '{expected_code}' kodi topilmadi. Avval Instagram Bio'ga '{expected_code}' ni yozib saqlang."

def extract_ig_username(link):
    parsed = urllib.parse.urlparse(link.strip())
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return ""
    if parts[0].lower() in ("p", "reel", "reels", "stories", "explore"):
        return ""
    return parts[0].lstrip("@").strip()

def check_instagram_follow(sessionid, target_username):
    if not sessionid or not target_username:
        return None
    try:
        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={urllib.parse.quote(target_username)}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36",
                "X-IG-App-ID": "936619743392459",
                "Cookie": f"sessionid={sessionid.strip()};",
                "Accept": "application/json",
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        user_obj = data.get("data", {}).get("user", {})
        if not user_obj:
            return None
        return bool(user_obj.get("followed_by_viewer") or user_obj.get("requested_by_viewer"))
    except Exception:
        return None

# ================= YORDAMCHI FUNKSIYALAR =================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
        conn.close()
        if not user or user["is_banned"]:
            session.clear()
            flash("Akkaunt topilmadi yoki bloklangan!", "danger")
            return redirect(url_for("login"))
        return f(user, *args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
        conn.close()
        if not is_user_admin(user):
            return redirect(url_for("dashboard"))
        return f(user, *args, **kwargs)
    return decorated

@app.route("/uploads/receipts/<path:filename>")
@login_required
def get_receipt_file(user, filename):
    safe_name = os.path.basename(filename)
    conn = get_db()
    tx = conn.execute("SELECT * FROM transactions WHERE receipt_file = ?", (safe_name,)).fetchone()
    conn.close()
    if not tx:
        return "Chek topilmadi", 404
    if not is_user_admin(user) and tx["user_id"] != user["id"]:
        return "Ruxsat berilmagan", 403
    return send_from_directory(RECEIPT_UPLOAD_DIR, safe_name)

# ================= MUKAMMAL ZAMONAVIY HTML SHABLON =================
BASE_HTML = """
<!DOCTYPE html>
<html lang="uz">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ config.SITE_NAME }} — Zamonaviy SMM Platforma</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-main: #070b14;
            --bg-card: rgba(17, 25, 40, 0.82);
            --bg-input: #0b1120;
            --border: rgba(148, 163, 184, 0.16);
            --primary-grad: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%);
            --success-grad: linear-gradient(135deg, #10b981 0%, #059669 100%);
            --ig-grad: linear-gradient(45deg, #f09433 0%, #e6683c 25%, #dc2743 50%, #cc2366 75%, #bc1888 100%);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', sans-serif; }
        body {
            background-color: var(--bg-main);
            background-image:
                radial-gradient(at 0% 0%, rgba(14, 165, 233, 0.14) 0px, transparent 50%),
                radial-gradient(at 100% 0%, rgba(99, 102, 241, 0.14) 0px, transparent 50%),
                radial-gradient(at 50% 100%, rgba(16, 185, 129, 0.08) 0px, transparent 50%);
            color: var(--text-main);
            min-height: 100vh;
            line-height: 1.55;
            padding-bottom: 50px;
        }
        .navbar {
            position: sticky;
            top: 0;
            z-index: 100;
            background: rgba(7, 11, 20, 0.88);
            backdrop-filter: blur(16px);
            padding: 14px 22px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            flex-wrap: wrap;
            gap: 12px;
        }
        .brand {
            font-size: 21px;
            font-weight: 800;
            background: linear-gradient(135deg, #38bdf8, #818cf8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-decoration: none;
        }
        .nav-links { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
        .nav-links a {
            color: #cbd5e1;
            text-decoration: none;
            font-size: 13.5px;
            font-weight: 600;
            padding: 8px 13px;
            border-radius: 10px;
            transition: all 0.2s ease;
        }
        .nav-links a:hover { background: rgba(56, 189, 248, 0.12); color: #fff; }
        .container { max-width: 1020px; margin: 26px auto; padding: 0 16px; }
        .card {
            background: var(--bg-card);
            backdrop-filter: blur(14px);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 22px;
            margin-bottom: 18px;
            box-shadow: 0 12px 32px rgba(0, 0, 0, 0.32);
        }
        .hero {
            text-align: center;
            padding: 46px 24px;
            background: linear-gradient(180deg, rgba(14, 165, 233, 0.12) 0%, rgba(17, 25, 40, 0.85) 100%);
        }
        .hero h1 { font-size: clamp(26px, 4vw, 38px); font-weight: 800; line-height: 1.2; margin-bottom: 14px; }
        .hero-pill {
            display: inline-block;
            padding: 6px 14px;
            border-radius: 999px;
            background: rgba(56, 189, 248, 0.15);
            border: 1px solid rgba(56, 189, 248, 0.35);
            color: #7dd3fc;
            font-size: 12.5px;
            font-weight: 700;
            margin-bottom: 14px;
        }
        .grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
        .grid-3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px; }
        .stat-box {
            background: rgba(11, 17, 32, 0.85);
            border: 1px solid var(--border);
            padding: 18px;
            border-radius: 15px;
        }
        .stat-title { font-size: 13px; color: var(--text-muted); font-weight: 600; }
        .stat-val { font-size: 25px; font-weight: 800; color: #38bdf8; margin-top: 6px; }
        .stat-val.green { color: #34d399; }
        input, select, textarea {
            width: 100%;
            padding: 12px 14px;
            margin-top: 6px;
            margin-bottom: 15px;
            background: var(--bg-input);
            border: 1px solid rgba(148, 163, 184, 0.25);
            border-radius: 11px;
            color: #fff;
            font-size: 14px;
        }
        input:focus, select:focus, textarea:focus {
            outline: none;
            border-color: #38bdf8;
            box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.18);
        }
        .sm-input { width: auto; min-width: 155px; padding: 7px 11px; margin: 0; font-size: 13px; }
        label { font-size: 13px; color: #cbd5e1; font-weight: 600; display: block; }
        .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            padding: 11px 18px;
            border: none;
            border-radius: 11px;
            font-weight: 700;
            font-size: 14px;
            cursor: pointer;
            text-decoration: none;
            transition: transform 0.15s ease, opacity 0.2s ease;
        }
        .btn:hover { transform: translateY(-1px); opacity: 0.95; }
        .btn-primary { background: var(--primary-grad); color: #fff; }
        .btn-success { background: var(--success-grad); color: #fff; }
        .btn-ig { background: var(--ig-grad); color: #fff; }
        .btn-danger { background: linear-gradient(135deg, #ef4444, #dc2626); color: #fff; }
        .btn-warning { background: linear-gradient(135deg, #f59e0b, #d97706); color: #fff; }
        .btn-google { background: #ffffff; color: #0f172a; }
        .btn-sm { padding: 7px 13px; font-size: 13px; border-radius: 9px; }
        .alert { padding: 13px 16px; border-radius: 12px; margin-bottom: 16px; font-size: 14px; font-weight: 600; }
        .alert-success { background: rgba(16, 185, 129, 0.15); color: #6ee7b7; border: 1px solid rgba(16, 185, 129, 0.4); }
        .alert-danger { background: rgba(239, 68, 68, 0.15); color: #fca5a5; border: 1px solid rgba(239, 68, 68, 0.4); }
        .alert-info { background: rgba(14, 165, 233, 0.15); color: #7dd3fc; border: 1px solid rgba(14, 165, 233, 0.4); }
        .task-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 15px;
            flex-wrap: wrap;
            background: rgba(11, 17, 32, 0.65);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 15px;
            margin-bottom: 10px;
        }
        .badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 8px;
            font-size: 11.5px;
            font-weight: 700;
            background: rgba(56, 189, 248, 0.15);
            color: #38bdf8;
        }
        .badge-ig { background: rgba(220, 39, 67, 0.18); color: #fda4af; }
        .badge-ok { background: rgba(16, 185, 129, 0.18); color: #6ee7b7; }
        .code-chip {
            display: inline-block;
            background: #070b14;
            border: 1px dashed #38bdf8;
            color: #38bdf8;
            font-family: monospace;
            font-size: 17px;
            font-weight: 800;
            letter-spacing: 1.5px;
            padding: 10px 16px;
            border-radius: 10px;
            margin: 8px 0 14px;
        }
        .receipt-thumb {
            width: 84px;
            height: 84px;
            object-fit: cover;
            border-radius: 10px;
            border: 1px solid var(--border);
            display: block;
        }
        table { width: 100%; border-collapse: collapse; font-size: 13.5px; margin-top: 10px; }
        th, td { padding: 11px 10px; text-align: left; border-bottom: 1px solid var(--border); vertical-align: middle; }
        th { color: var(--text-muted); font-weight: 700; font-size: 12px; text-transform: uppercase; }
        .table-wrap { overflow-x: auto; }
        .ref-box {
            background: var(--bg-input);
            border: 1px dashed #38bdf8;
            padding: 12px 14px;
            border-radius: 11px;
            font-size: 13px;
            color: #7dd3fc;
            word-break: break-all;
            margin-top: 10px;
        }
    </style>
</head>
<body>
    <div class="navbar">
        <a href="/" class="brand">⚡ {{ config.SITE_NAME }}</a>
        <div class="nav-links">
            {% if user %}
                <a href="{{ url_for('dashboard') }}">Asosiy</a>
                <a href="{{ url_for('earn') }}">Pul ishlash (+{{ config.WORKER_REWARD }} so'm)</a>
                <a href="{{ url_for('advertise') }}">Buyurtma ({{ config.ADVERTISER_PRICE }} so'm)</a>
                <a href="{{ url_for('wallet') }}">Hamyon</a>
                <a href="{{ url_for('profile') }}" style="border:1px solid rgba(220,39,67,0.45); color:#fda4af;">
                    Instagram & Bio
                </a>
                {% if show_admin %}
                    <a href="{{ url_for('admin_panel') }}" style="background:rgba(234,88,12,0.25); color:#fdba74; border:1px solid rgba(234,88,12,0.5);">Admin</a>
                {% endif %}
                <a href="{{ url_for('logout') }}" style="color:#f87171;">Chiqish</a>
            {% else %}
                <a href="{{ url_for('google_login') }}" class="btn btn-google btn-sm">Google orqali kirish</a>
                <a href="{{ url_for('login') }}">Kirish</a>
                <a href="{{ url_for('register') }}" class="btn btn-primary btn-sm">Ro'yxatdan o'tish</a>
            {% endif %}
        </div>
    </div>

    <div class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, msg in messages %}
                    <div class="alert alert-{{ category }}">{{ msg }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        {{ content | safe }}
    </div>
</body>
</html>
"""

def render_page(content_html, user=None, **kwargs):
    show_admin = is_user_admin(user)
    inner = render_template_string(
        content_html,
        user=user,
        show_admin=show_admin,
        config=CONFIG,
        platforms=PLATFORMS,
        platform_dict=PLATFORM_DICT,
        **kwargs
    )
    return render_template_string(BASE_HTML, content=inner, user=user, show_admin=show_admin, config=CONFIG)

# ================= GOOGLE OAUTH =================
def get_google_redirect_uri():
    return url_for("google_callback", _external=True)

@app.route("/auth/google")
def google_login():
    if not CONFIG["GOOGLE_CLIENT_ID"] or not CONFIG["GOOGLE_CLIENT_SECRET"]:
        flash("Google OAuth kalitlari hali kiritilmagan — pastdagi Gmail formasi orqali kiring yoki ro'yxatdan o'ting.", "info")
        return redirect(url_for("login"))

    state = os.urandom(16).hex()
    session["google_oauth_state"] = state
    params = urllib.parse.urlencode({
        "client_id": CONFIG["GOOGLE_CLIENT_ID"],
        "redirect_uri": get_google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    })
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")

@app.route("/auth/google/callback")
def google_callback():
    error = request.args.get("error")
    if error:
        flash("Google orqali kirish bekor qilindi.", "danger")
        return redirect(url_for("login"))

    state = request.args.get("state")
    if not state or state != session.pop("google_oauth_state", None):
        flash("Xavfsizlik tekshiruvi (state) xato!", "danger")
        return redirect(url_for("login"))

    code = request.args.get("code")
    if not code:
        flash("Google tasdiq kodi kelmadi!", "danger")
        return redirect(url_for("login"))

    token_data = urllib.parse.urlencode({
        "code": code,
        "client_id": CONFIG["GOOGLE_CLIENT_ID"],
        "client_secret": CONFIG["GOOGLE_CLIENT_SECRET"],
        "redirect_uri": get_google_redirect_uri(),
        "grant_type": "authorization_code",
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            token_json = json.loads(resp.read().decode("utf-8"))
        access_token = token_json.get("access_token")
        if not access_token:
            flash("Google token olinmadi!", "danger")
            return redirect(url_for("login"))

        info_req = urllib.request.Request(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        with urllib.request.urlopen(info_req, timeout=15) as info_resp:
            g_user = json.loads(info_resp.read().decode("utf-8"))
    except Exception:
        flash("Google serveri bilan bog'lanishda xatolik yuz berdi!", "danger")
        return redirect(url_for("login"))

    email = (g_user.get("email") or "").strip().lower()
    google_id = str(g_user.get("id") or "")
    base_name = (g_user.get("given_name") or (email.split("@")[0] if email else "user")).lower()
    clean_base = "".join(ch for ch in base_name if ch.isalnum() or ch == "_")[:16] or "user"

    if not email and not google_id:
        flash("Google akkaunt ma'lumotlari olinmadi!", "danger")
        return redirect(url_for("login"))

    conn = get_db()
    user = None
    if google_id:
        user = conn.execute("SELECT * FROM users WHERE google_id = ?", (google_id,)).fetchone()
    if not user and email:
        user = conn.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,)).fetchone()

    should_be_admin = 1 if (email and email == CONFIG["ADMIN_EMAIL"].lower()) else 0

    if user:
        if user["is_banned"]:
            conn.close()
            flash("Sizning akkauntingiz bloklangan!", "danger")
            return redirect(url_for("login"))
        conn.execute(
            "UPDATE users SET email = COALESCE(NULLIF(email, ''), ?), google_id = COALESCE(NULLIF(google_id, ''), ?), is_admin = CASE WHEN ? = 1 THEN 1 ELSE is_admin END WHERE id = ?",
            (email, google_id, should_be_admin, user["id"])
        )
        conn.commit()
        user_id = user["id"]
    else:
        username = clean_base
        idx = 1
        while conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            username = f"{clean_base}_{idx}"
            idx += 1

        referrer_id = None
        ref_code = session.pop("ref", None)
        if ref_code:
            ref_user = conn.execute("SELECT id FROM users WHERE username = ?", (ref_code,)).fetchone()
            if ref_user:
                referrer_id = ref_user["id"]

        cur = conn.execute("""
            INSERT INTO users (username, email, google_id, password_hash, referrer_id, ig_bio_code, is_admin)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (username, email, google_id, generate_password_hash(os.urandom(16).hex()), referrer_id, generate_bio_code(), should_be_admin))
        conn.commit()
        user_id = cur.lastrowid

    conn.close()
    session["user_id"] = user_id
    flash("Google email orqali kabinetga kirdingiz!", "success")
    return redirect(url_for("dashboard"))

# ================= YO'NALISHLAR (ROUTES) =================

@app.route("/")
def index():
    ref = request.args.get("ref")
    if ref:
        session["ref"] = ref
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    conn = get_db()
    users_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    tasks_done = conn.execute("SELECT COUNT(*) FROM completions").fetchone()[0]
    conn.close()

    html = """
    <div class="card hero">
        <div class="hero-pill">⚡ Tezkor SMM & Daromad Birjasi</div>
        <h1>{{ config.SITE_NAME }} — Haqiqiy Obunachilar va Kunlik Daromad</h1>
        <p style="color:#cbd5e1; max-width:650px; margin:0 auto 24px; font-size:15px;">
            Avval saytdan ro'yxatdan o'ting, Instagram Bio'ga maxsus kodni yozib akkauntingizni tasdiqlang va har bir obuna uchun <b>{{ config.WORKER_REWARD }} so'm</b> ishlang, do'stlarni chaqirib <b>+{{ config.REFERRAL_BONUS }} so'm</b> bonus oling yoki o'z sahifangizga <b>{{ config.ADVERTISER_PRICE }} so'm</b>dan obunachilar yig'ing!
        </p>
        <div style="display:flex; gap:12px; justify-content:center; flex-wrap:wrap;">
            <a href="{{ url_for('google_login') }}" class="btn btn-google">Google orqali kirish</a>
            <a href="{{ url_for('register') }}" class="btn btn-primary">Ro'yxatdan o'tish</a>
            <a href="{{ url_for('login') }}" class="btn btn-success">Kirish</a>
        </div>
    </div>

    <div class="grid-3">
        <div class="stat-box" style="text-align:center;">
            <div class="stat-title">Jami foydalanuvchilar</div>
            <div class="stat-val">{{ users_count }} ta</div>
        </div>
        <div class="stat-box" style="text-align:center;">
            <div class="stat-title">Bajarilgan obunalar</div>
            <div class="stat-val green">{{ tasks_done }} ta</div>
        </div>
        <div class="stat-box" style="text-align:center;">
            <div class="stat-title">1 obuna mukofoti</div>
            <div class="stat-val">{{ config.WORKER_REWARD }} so'm</div>
        </div>
    </div>
    """
    return render_page(html, users_count=users_count, tasks_done=tasks_done)

@app.route("/register", methods=["GET", "POST"])
def register():
    ref_param = request.args.get("ref")
    if ref_param:
        session["ref"] = ref_param

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        if not email or "@" not in email:
            flash("Iltimos, to'g'ri Google email (Gmail) kiriting!", "danger")
            return redirect(url_for("register"))

        if len(password) < 4:
            flash("Parol kamida 4 ta belgidan iborat bo'lsin!", "danger")
            return redirect(url_for("register"))

        conn = get_db()
        existing_email = conn.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,)).fetchone()
        if existing_email:
            conn.close()
            flash("Bu email allaqachon mavjud! Parolni unutgan bo'lsangiz 'Parolni unutdingizmi?' orqali tiklang.", "info")
            return redirect(url_for("login"))

        base = "".join(ch for ch in email.split("@")[0] if ch.isalnum() or ch == "_")[:16] or "user"
        username = base
        idx = 1
        while conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            username = f"{base}_{idx}"
            idx += 1

        referrer_id = None
        ref_code = session.get("ref")
        if ref_code:
            ref_user = conn.execute("SELECT id FROM users WHERE username = ?", (ref_code,)).fetchone()
            if ref_user:
                referrer_id = ref_user["id"]

        is_adm = 1 if email == CONFIG["ADMIN_EMAIL"].lower() else 0

        try:
            cur = conn.execute("""
                INSERT INTO users (username, email, password_hash, referrer_id, ig_bio_code, is_admin)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (username, email, generate_password_hash(password), referrer_id, generate_bio_code(), is_adm))
            conn.commit()
            session.pop("ref", None)
            session["user_id"] = cur.lastrowid
            flash("Ro'yxatdan o'tdingiz! Endi Instagram Bio'ga kod yozib akkauntingizni tasdiqlang.", "success")
            return redirect(url_for("profile"))
        except sqlite3.IntegrityError:
            flash("Ro'yxatdan o'tishda xatolik yuz berdi. Qayta urinib ko'ring.", "danger")
        finally:
            conn.close()

    html = """
    <div class="card" style="max-width:440px; margin:0 auto;">
        <h2 style="margin-bottom:8px;">Tezkor ro'yxatdan o'tish</h2>
        <p style="font-size:13px; color:#94a3b8; margin-bottom:16px;">
            Avval saytdan o'ting — keyin sayt bergan kodni Instagram Bio'ga yozib akkauntingizni tasdiqlaysiz.
        </p>
        <a href="{{ url_for('google_login') }}" class="btn btn-google" style="width:100%; margin-bottom:16px;">Google OAuth orqali 1 bosishda kirish</a>
        <form method="POST">
            <label>Google email (Gmail) *</label>
            <input type="email" name="email" required placeholder="sizning.email@gmail.com">

            <label>Sayt uchun parol yarating *</label>
            <input type="password" name="password" required placeholder="Kamida 4 ta belgi">

            <button type="submit" class="btn btn-primary" style="width:100%;">Ro'yxatdan o'tish</button>
        </form>
        <p style="margin-top:14px; font-size:13px; text-align:center; color:#94a3b8;">
            Akkauntingiz bormi? <a href="{{ url_for('login') }}" style="color:#38bdf8; font-weight:700;">Kirish</a>
        </p>
    </div>
    """
    return render_page(html)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip().lower()
        password = request.form.get("password", "").strip()
        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE LOWER(email) = ? OR LOWER(username) = ?",
            (identifier, identifier)
        ).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            if user["is_banned"]:
                flash("Sizning akkauntingiz bloklangan!", "danger")
                return redirect(url_for("login"))
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))
        flash("Email yoki parol noto'g'ri! Parolni unutgan bo'lsangiz 'Parolni unutdingizmi?' tugmasini bosing.", "danger")

    html = """
    <div class="card" style="max-width:420px; margin:0 auto;">
        <h2 style="margin-bottom:14px;">Kabinetga kirish</h2>
        <a href="{{ url_for('google_login') }}" class="btn btn-google" style="width:100%; margin-bottom:16px;">Google OAuth orqali kirish</a>
        <form method="POST">
            <label>Google email (yoki login)</label>
            <input type="text" name="identifier" required placeholder="example@gmail.com">
            <label>Parol</label>
            <input type="password" name="password" required placeholder="Parolingiz">
            <button type="submit" class="btn btn-success" style="width:100%;">Kabinetga kirish</button>
        </form>
        <div style="margin-top:16px; display:flex; justify-content:space-between; font-size:13px;">
            <a href="{{ url_for('forgot_password') }}" style="color:#fbbf24; font-weight:700;">Parolni unutdingizmi?</a>
            <a href="{{ url_for('register') }}" style="color:#38bdf8; font-weight:700;">Ro'yxatdan o'tish</a>
        </div>
    </div>
    """
    return render_page(html)

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,)).fetchone()
        if not user:
            conn.close()
            flash("Bu email bilan ro'yxatdan o'tilgan foydalanuvchi topilmadi!", "danger")
            return redirect(url_for("forgot_password"))

        code = f"{random.randint(100000, 999999)}"
        expires = int(time.time()) + 600
        conn.execute("UPDATE users SET reset_code = ?, reset_expires = ? WHERE id = ?", (code, expires, user["id"]))
        conn.commit()
        conn.close()

        ok, info_msg = send_reset_email(email, code)
        session["reset_email"] = email
        if ok:
            flash("6 xonali tasdiqlash kodi emailingizga yuborildi! Kodni kiriting va yangi parol o'rnating.", "success")
        else:
            flash(f"Email yuborilmadi ({info_msg}).", "danger")
        return redirect(url_for("reset_password"))

    html = """
    <div class="card" style="max-width:420px; margin:0 auto;">
        <h2 style="margin-bottom:10px;">Parolni tiklash</h2>
        <p style="font-size:13px; color:#94a3b8; margin-bottom:14px;">
            Ro'yxatdan o'tgan Google emailingizni kiriting — pochtangizga 6 xonali tasdiqlash kodi boradi.
        </p>
        <form method="POST">
            <label>Google email (Gmail)</label>
            <input type="email" name="email" required placeholder="sizning.email@gmail.com">
            <button type="submit" class="btn btn-primary" style="width:100%;">Kod yuborish</button>
        </form>
        <p style="margin-top:14px; font-size:13px; text-align:center;">
            <a href="{{ url_for('login') }}" style="color:#38bdf8;">← Kirish sahifasiga qaytish</a>
        </p>
    </div>
    """
    return render_page(html)

@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    saved_email = session.get("reset_email", "")
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        code = request.form.get("code", "").strip()
        new_password = request.form.get("new_password", "").strip()

        if len(new_password) < 4:
            flash("Yangi parol kamida 4 ta belgidan iborat bo'lsin!", "danger")
            return redirect(url_for("reset_password"))

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,)).fetchone()
        if not user or not user["reset_code"]:
            conn.close()
            flash("Avval emailga kod yuborishni bosing!", "danger")
            return redirect(url_for("forgot_password"))

        if int(time.time()) > int(user["reset_expires"] or 0):
            conn.close()
            flash("Kod muddati tugagan (10 daqiqa). Qaytadan kod yuboring!", "danger")
            return redirect(url_for("forgot_password"))

        if code != user["reset_code"]:
            conn.close()
            flash("Emailga kelgan tasdiqlash kodi noto'g'ri!", "danger")
            return redirect(url_for("reset_password"))

        conn.execute(
            "UPDATE users SET password_hash = ?, reset_code = '', reset_expires = 0 WHERE id = ?",
            (generate_password_hash(new_password), user["id"])
        )
        conn.commit()
        conn.close()
        session.pop("reset_email", None)
        flash("Parolingiz muvaffaqiyatli yangilandi! Endi yangi parol bilan kiring.", "success")
        return redirect(url_for("login"))

    html = """
    <div class="card" style="max-width:420px; margin:0 auto;">
        <h2 style="margin-bottom:10px;">Yangi parol o'rnatish</h2>
        <p style="font-size:13px; color:#94a3b8; margin-bottom:14px;">
            Emailingizga kelgan 6 xonali kodni va yangi parolni kiriting.
        </p>
        <form method="POST">
            <label>Google email</label>
            <input type="email" name="email" value="{{ saved_email }}" required placeholder="sizning.email@gmail.com">

            <label>Emailga kelgan 6 xonali kod</label>
            <input type="text" name="code" maxlength="6" required placeholder="Masalan: 482910">

            <label>Yangi parol</label>
            <input type="password" name="new_password" required placeholder="Kamida 4 ta belgi">

            <button type="submit" class="btn btn-success" style="width:100%;">Tasdiqlash va parolni saqlash</button>
        </form>
    </div>
    """
    return render_page(html, saved_email=saved_email)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard(user):
    conn = get_db()
    my_done = conn.execute("SELECT COUNT(*) FROM completions WHERE user_id = ?", (user["id"],)).fetchone()[0]
    my_tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE owner_id = ?", (user["id"],)).fetchone()[0]
    refs_count = conn.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user["id"],)).fetchone()[0]
    conn.close()

    ref_link = request.url_root.rstrip("/") + url_for("register", ref=user["username"])

    html = """
    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:16px;">
        <h2>Salom, {{ user.username }}! 👋</h2>
        {% if user.ig_verified and user.ig_nick %}
            <span class="badge badge-ok">✅ Instagram tasdiqlangan: {{ user.ig_nick }}</span>
        {% elif user.ig_nick %}
            <a href="{{ url_for('profile') }}" class="btn btn-ig btn-sm">⚠️ {{ user.ig_nick }} Bio kodini tasdiqlash</a>
        {% else %}
            <a href="{{ url_for('profile') }}" class="btn btn-ig btn-sm">📸 Instagram Bio orqali ulash</a>
        {% endif %}
    </div>

    <div class="grid-2">
        <div class="stat-box">
            <div class="stat-title">Ishlangan balans (Min. yechish: {{ config.MIN_WITHDRAW }} so'm)</div>
            <div class="stat-val green">{{ user.work_balance }} so'm</div>
            <a href="{{ url_for('earn') }}" class="btn btn-success btn-sm" style="margin-top:12px;">Pul ishlash (+{{ config.WORKER_REWARD }} so'm)</a>
        </div>
        <div class="stat-box">
            <div class="stat-title">Reklama balansi (Deposit)</div>
            <div class="stat-val">{{ user.ad_balance }} so'm</div>
            <a href="{{ url_for('wallet') }}" class="btn btn-primary btn-sm" style="margin-top:12px;">Hisob to'ldirish</a>
        </div>
    </div>

    <div class="card" style="margin-top:16px;">
        <h3>🎁 Referal tizimi (Har bir do'st uchun +{{ config.REFERRAL_BONUS }} so'm)</h3>
        <p style="font-size:13.5px; color:#cbd5e1; margin-top:6px;">
            Do'stingiz havolangiz orqali kirib ilk vazifani bajarganda balansingizga <b>+{{ config.REFERRAL_BONUS }} so'm</b> tushadi. Chaqirilgan do'stlar: <b>{{ refs_count }} ta</b>
        </p>
        <div class="ref-box">{{ ref_link }}</div>
    </div>

    <div class="grid-2">
        <div class="stat-box">
            <div class="stat-title">Siz bajargan obunalar</div>
            <div class="stat-val">{{ my_done }} ta</div>
        </div>
        <div class="stat-box">
            <div class="stat-title">Siz bergan buyurtmalar</div>
            <div class="stat-val">{{ my_tasks }} ta</div>
        </div>
    </div>
    """
    return render_page(html, user=user, my_done=my_done, my_tasks=my_tasks, refs_count=refs_count, ref_link=ref_link)

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile(user):
    bio_code = user["ig_bio_code"] or generate_bio_code()
    conn = get_db()
    if not user["ig_bio_code"]:
        conn.execute("UPDATE users SET ig_bio_code = ? WHERE id = ?", (bio_code, user["id"]))
        conn.commit()

    if request.method == "POST":
        action = request.form.get("action", "verify_bio")

        if action == "new_code":
            new_code = generate_bio_code()
            conn.execute("UPDATE users SET ig_bio_code = ?, ig_verified = 0 WHERE id = ?", (new_code, user["id"]))
            conn.commit()
            conn.close()
            flash(f"Yangi Bio kodi yaratildi: {new_code}. Uni Instagram Bio'ga yozib tasdiqlang!", "info")
            return redirect(url_for("profile"))

        elif action == "verify_bio":
            ig_user = request.form.get("ig_bio_username", "").strip().lstrip("@")
            if not ig_user:
                conn.close()
                flash("Instagram username kiriting!", "danger")
                return redirect(url_for("profile"))

            formatted_nick = "@" + ig_user
            ok, msg = verify_ig_bio_code(ig_user, bio_code)
            if ok:
                conn.execute(
                    "UPDATE users SET ig_nick = ?, ig_verified = 1 WHERE id = ?",
                    (formatted_nick, user["id"])
                )
                conn.commit()
                conn.close()
                flash(msg, "success")
            else:
                conn.execute("UPDATE users SET ig_nick = ? WHERE id = ?", (formatted_nick, user["id"]))
                conn.commit()
                conn.close()
                flash(msg, "danger")
            return redirect(url_for("profile"))

        elif action == "change_pass":
            new_pass = request.form.get("new_pass", "").strip()
            if len(new_pass) < 4:
                flash("Yangi parol kamida 4 ta belgi bo'lsin!", "danger")
            else:
                conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_pass), user["id"]))
                conn.commit()
                flash("Sayt paroli yangilandi!", "success")
            conn.close()
            return redirect(url_for("profile"))

        else:
            ig_sessionid = request.form.get("ig_sessionid", "").strip()
            yt_nick = request.form.get("yt_nick", "").strip()
            tg_nick = request.form.get("tg_nick", "").strip()
            conn.execute(
                "UPDATE users SET ig_sessionid=?, yt_nick=?, tg_nick=? WHERE id=?",
                (ig_sessionid, yt_nick, tg_nick, user["id"])
            )
            conn.commit()
            conn.close()
            flash("Sozlamalar saqlandi!", "success")
            return redirect(url_for("profile"))

    conn.close()

    html = """
    <div class="grid-2">
        <div class="card">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; gap:8px; flex-wrap:wrap;">
                <h3>📸 Instagram Bio Kod Generator & Tasdiqlash</h3>
                {% if user.ig_verified %}
                    <span class="badge badge-ok">✅ Tasdiqlangan: {{ user.ig_nick }}</span>
                {% else %}
                    <span class="badge badge-ig">Tasdiqlanmagan</span>
                {% endif %}
            </div>

            <p style="font-size:13px; color:#cbd5e1;">
                1. Quyidagi generator kodini nusxalang:<br>
                2. Instagram ilovasida <b>Edit profile → Bio</b> qismiga shu kodni yozib saqlang;<br>
                3. Pastga Instagram username'ingizni yozib <b>"Bio orqali tasdiqlash"</b> tugmasini bosing — tizim akkauntingizni o'zi tanib oladi.
            </p>

            <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
                <div class="code-chip" id="bioCodeText">{{ bio_code }}</div>
                <form method="POST" style="margin:0;">
                    <input type="hidden" name="action" value="new_code">
                    <button type="submit" class="btn btn-sm" style="background:rgba(148,163,184,0.18); color:#fff; margin-bottom:6px;">🔄 Yangi kod</button>
                </form>
            </div>

            <form method="POST">
                <input type="hidden" name="action" value="verify_bio">
                <label>Sizning Instagram username'ingiz *</label>
                <input type="text" name="ig_bio_username" value="{{ user.ig_nick.lstrip('@') }}" required placeholder="masalan: bahromjonmaripjonov">

                <button type="submit" class="btn btn-ig" style="width:100%;">Bio orqali akkauntni tanish va tasdiqlash</button>
            </form>
        </div>

        <div class="card">
            <h3 style="margin-bottom:10px;">⚙️ Boshqa tarmoqlar va qo'shimcha tekshiruv</h3>
            <p style="font-size:13px; color:#94a3b8; margin-bottom:14px;">
                Xohlasangiz Instagram <code>sessionid</code> ham kiriting (follow holatini to'g'ridan-to'g'ri API orqali tekshirish uchun), shuningdek YouTube va Telegram niklaringizni saqlang.
            </p>
            <form method="POST">
                <input type="hidden" name="action" value="extras">
                <label>Instagram Session ID (ixtiyoriy)</label>
                <input type="text" name="ig_sessionid" value="{{ user.ig_sessionid }}" placeholder="sessionid cookie qiymati">

                <label>YouTube kanal / akkaunt</label>
                <input type="text" name="yt_nick" value="{{ user.yt_nick }}" placeholder="@youtube_nik">

                <label>Telegram username</label>
                <input type="text" name="tg_nick" value="{{ user.tg_nick }}" placeholder="@telegram_nik">

                <button type="submit" class="btn btn-primary" style="width:100%;">Saqlash</button>
            </form>
        </div>
    </div>

    <div class="card" style="max-width:480px;">
        <h3 style="margin-bottom:10px;">🔑 Sayt parolini o'zgartirish</h3>
        <form method="POST">
            <input type="hidden" name="action" value="change_pass">
            <label>Yangi parol</label>
            <input type="password" name="new_pass" required placeholder="Kamida 4 ta belgi">
            <button type="submit" class="btn btn-warning btn-sm">Parolni yangilash</button>
        </form>
    </div>
    """
    return render_page(html, user=user, bio_code=bio_code)

@app.route("/earn")
@login_required
def earn(user):
    platform_filter = request.args.get("platform", "all")
    conn = get_db()

    query = """
        SELECT t.* FROM tasks t
        WHERE t.status = 'active'
          AND t.owner_id != ?
          AND t.completed_count < t.target_count
          AND t.id NOT IN (SELECT task_id FROM completions WHERE user_id = ?)
    """
    params = [user["id"], user["id"]]
    if platform_filter != "all":
        query += " AND t.platform = ?"
        params.append(platform_filter)
    query += " ORDER BY t.id DESC"

    tasks = conn.execute(query, params).fetchall()
    conn.close()

    html = """
    <div class="card">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
            <h2>Vazifalar birjasi</h2>
            <div style="display:flex; gap:6px; flex-wrap:wrap;">
                <a href="{{ url_for('earn', platform='all') }}" class="btn btn-sm btn-primary">Barchasi</a>
                {% for key, name in platforms %}
                    <a href="{{ url_for('earn', platform=key) }}" class="btn btn-sm" style="background:rgba(148,163,184,0.16);color:#fff;">{{ name.split(' ')[0] }}</a>
                {% endfor %}
            </div>
        </div>
        <p style="font-size:13.5px; color:#94a3b8; margin-top:10px;">
            1. <b>"1. Ochish"</b> tugmasini bosib sahifaga kiring va <b>Follow</b> bosing.<br>
            2. Saytga qaytib <b>"2. Tekshirish (+{{ config.WORKER_REWARD }} so'm)"</b> tugmasini bosing — tizim akkaunt va obunani tekshiradi.
        </p>
    </div>

    <div class="card">
        {% if tasks %}
            {% for t in tasks %}
                <div class="task-item">
                    <div>
                        <span class="badge {% if t.platform == 'instagram' %}badge-ig{% endif %}">{{ platform_dict[t.platform] }}</span>
                        <h4 style="margin-top:6px; font-size:16px;">{{ t.title }}</h4>
                        <small style="color:#94a3b8;">Bajarildi: {{ t.completed_count }} / {{ t.target_count }}</small>
                    </div>
                    <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
                        <a href="{{ t.link }}" target="_blank" onclick="markOpened({{ t.id }})" class="btn btn-primary btn-sm">1. Ochish ↗</a>
                        <form method="POST" action="{{ url_for('verify_task', task_id=t.id) }}" style="margin:0;">
                            <button type="submit" class="btn btn-success btn-sm">2. Tekshirish (+{{ t.reward_per_action }} so'm)</button>
                        </form>
                    </div>
                </div>
            {% endfor %}
        {% else %}
            <p style="color:#94a3b8; text-align:center; padding:24px;">Hozircha yangi vazifalar yo'q. Birozdan so'ng qayta tekshiring!</p>
        {% endif %}
    </div>
    <script>
        function markOpened(id) {
            fetch('/start_task/' + id, {method: 'POST'});
        }
    </script>
    """
    return render_page(html, user=user, tasks=tasks)

@app.route("/start_task/<int:task_id>", methods=["POST"])
@login_required
def start_task(user, task_id):
    started = session.get("started_tasks", {})
    started[str(task_id)] = time.time()
    session["started_tasks"] = started
    return {"ok": True}

@app.route("/verify_task/<int:task_id>", methods=["POST"])
@login_required
def verify_task(user, task_id):
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task or task["status"] != "active" or task["completed_count"] >= task["target_count"]:
        conn.close()
        flash("Bu vazifa yakunlangan!", "danger")
        return redirect(url_for("earn"))

    if task["platform"] == "instagram":
        if not user["ig_nick"] or not user["ig_verified"]:
            conn.close()
            flash("Avval 'Instagram & Bio' bo'limida kodni Instagram Bio'ga yozib akkauntingizni tasdiqlang!", "danger")
            return redirect(url_for("profile"))
    elif task["platform"] == "youtube" and not user["yt_nick"]:
        conn.close()
        flash("Avval 'Instagram & Bio' bo'limidan YouTube akkauntingizni kiriting!", "danger")
        return redirect(url_for("profile"))
    elif task["platform"].startswith("telegram") and not user["tg_nick"]:
        conn.close()
        flash("Avval 'Instagram & Bio' bo'limidan Telegram username'ingizni kiriting!", "danger")
        return redirect(url_for("profile"))

    started = session.get("started_tasks", {})
    start_time = started.get(str(task_id))
    if not start_time:
        conn.close()
        flash("Avval '1. Ochish' tugmasini bosib sahifaga kiring va Follow bosing!", "danger")
        return redirect(url_for("earn"))
    if time.time() - start_time < 4:
        conn.close()
        flash("Juda tez bosdingiz! Sahifaga kirib Follow bosing (kamida 4-5 soniya).", "danger")
        return redirect(url_for("earn"))

    if task["platform"] == "instagram":
        bio_ok, bio_msg = verify_ig_bio_code(user["ig_nick"], user["ig_bio_code"])
        if not bio_ok:
            conn.execute("UPDATE users SET ig_verified = 0 WHERE id = ?", (user["id"],))
            conn.commit()
            conn.close()
            flash(f"Bio tekshiruvi muvaffaqiyatsiz: {bio_msg}", "danger")
            return redirect(url_for("profile"))

        if user["ig_sessionid"]:
            target_ig = extract_ig_username(task["link"])
            followed = check_instagram_follow(user["ig_sessionid"], target_ig)
            if followed is False:
                conn.close()
                flash(f"{user['ig_nick']} akkauntingizdan @{target_ig} ga hali Follow bosilmagan! Avval obuna bo'ling.", "danger")
                return redirect(url_for("earn"))

    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO completions (user_id, task_id, reward) VALUES (?, ?, ?)",
            (user["id"], task_id, task["reward_per_action"])
        )
        new_count = task["completed_count"] + 1
        new_status = "completed" if new_count >= task["target_count"] else "active"
        conn.execute(
            "UPDATE tasks SET completed_count = ?, status = ? WHERE id = ?",
            (new_count, new_status, task_id)
        )
        conn.execute(
            "UPDATE users SET work_balance = work_balance + ? WHERE id = ?",
            (task["reward_per_action"], user["id"])
        )

        bonus_msg = ""
        if user["referrer_id"] and not user["ref_bonus_paid"] and CONFIG["REFERRAL_BONUS"] > 0:
            conn.execute(
                "UPDATE users SET work_balance = work_balance + ? WHERE id = ?",
                (CONFIG["REFERRAL_BONUS"], user["referrer_id"])
            )
            conn.execute(
                "UPDATE users SET ref_bonus_paid = 1 WHERE id = ?",
                (user["id"],)
            )
            bonus_msg = f" Taklif qilgan foydalanuvchiga +{CONFIG['REFERRAL_BONUS']} so'm bonus berildi!"

        conn.commit()
        started.pop(str(task_id), None)
        session["started_tasks"] = started
        flash(f"Tasdiqlandi! Hisobingizga +{task['reward_per_action']} so'm qo'shildi!{bonus_msg}", "success")
    except sqlite3.IntegrityError:
        conn.rollback()
        flash("Siz bu vazifani allaqachon bajargansiz!", "danger")
    finally:
        conn.close()

    return redirect(url_for("earn"))

@app.route("/advertise", methods=["GET", "POST"])
@login_required
def advertise(user):
    price = CONFIG["ADVERTISER_PRICE"]
    max_possible = user["ad_balance"] // price

    if request.method == "POST":
        platform = request.form.get("platform")
        title = request.form.get("title", "").strip()
        link = request.form.get("link", "").strip()
        try:
            count = int(request.form.get("count", 0))
        except ValueError:
            count = 0

        total_cost = count * price
        if platform not in PLATFORM_DICT or not title or not link.startswith("http"):
            flash("Barcha maydonlarni to'g'ri to'ldiring (havola http/https bilan boshlansin)!", "danger")
            return redirect(url_for("advertise"))
        if count < 1:
            flash("Kamida 1 ta obunachi kiriting!", "danger")
            return redirect(url_for("advertise"))
        if total_cost > user["ad_balance"]:
            flash(f"Reklama balansingiz yetarli emas! Kerak: {total_cost} so'm.", "danger")
            return redirect(url_for("advertise"))

        conn = get_db()
        conn.execute("UPDATE users SET ad_balance = ad_balance - ? WHERE id = ?", (total_cost, user["id"]))
        conn.execute("""
            INSERT INTO tasks (owner_id, platform, title, link, target_count, price_per_action, reward_per_action)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user["id"], platform, title, link, count, price, CONFIG["WORKER_REWARD"]))
        conn.commit()
        conn.close()
        flash(f"Buyurtma joylandi! {count} ta obunachi uchun {total_cost} so'm yechildi.", "success")
        return redirect(url_for("advertise"))

    conn = get_db()
    my_tasks = conn.execute("SELECT * FROM tasks WHERE owner_id = ? ORDER BY id DESC", (user["id"],)).fetchall()
    conn.close()

    html = """
    <div class="card">
        <h2>Yangi buyurtma berish</h2>
        <p style="font-size:13.5px; color:#94a3b8; margin-bottom:14px;">
            Reklama balansingiz: <b style="color:#38bdf8;">{{ user.ad_balance }} so'm</b> |
            1 ta obunachi narxi: <b>{{ config.ADVERTISER_PRICE }} so'm</b> |
            Maksimal buyurtma: <b>{{ max_possible }} ta</b>
        </p>
        <form method="POST">
            <label>Tarmoqni tanlang</label>
            <select name="platform">
                {% for key, name in platforms %}
                    <option value="{{ key }}">{{ name }}</option>
                {% endfor %}
            </select>

            <label>Vazifa sarlavhasi</label>
            <input type="text" name="title" required placeholder="Masalan: Instagram sahifamga obuna bo'ling">

            <label>Havola (Link)</label>
            <input type="url" name="link" required placeholder="https://instagram.com/username yoki https://t.me/kanal">

            <label>Nechta obunachi kerak? (1 tasi = {{ config.ADVERTISER_PRICE }} so'm)</label>
            <input type="number" name="count" min="1" max="{{ max_possible if max_possible > 0 else 1 }}" required placeholder="Masalan: 50">

            <button type="submit" class="btn btn-primary">Buyurtmani ishga tushirish</button>
        </form>
    </div>

    <div class="card">
        <h3>Mening buyurtmalarim</h3>
        {% if my_tasks %}
            <div class="table-wrap">
                <table>
                    <tr>
                        <th>Tarmoq</th>
                        <th>Sarlavha</th>
                        <th>Holat</th>
                        <th>Amal</th>
                    </tr>
                    {% for t in my_tasks %}
                    <tr>
                        <td>{{ platform_dict[t.platform] }}</td>
                        <td>{{ t.title }}</td>
                        <td>{{ t.completed_count }} / {{ t.target_count }} ({{ t.status }})</td>
                        <td>
                            {% if t.status == 'active' %}
                                <form method="POST" action="{{ url_for('cancel_task', task_id=t.id) }}" style="margin:0;">
                                    <button type="submit" class="btn btn-danger btn-sm">To'xtatish</button>
                                </form>
                            {% else %}
                                -
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
        {% else %}
            <p style="color:#94a3b8; margin-top:10px;">Hali buyurtma bermagansiz.</p>
        {% endif %}
    </div>
    """
    return render_page(html, user=user, max_possible=max_possible, my_tasks=my_tasks)

@app.route("/cancel_task/<int:task_id>", methods=["POST"])
@login_required
def cancel_task(user, task_id):
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks WHERE id = ? AND owner_id = ?", (task_id, user["id"])).fetchone()
    if task and task["status"] == "active":
        remaining = task["target_count"] - task["completed_count"]
        refund = remaining * task["price_per_action"]
        conn.execute("UPDATE tasks SET status = 'cancelled' WHERE id = ?", (task_id,))
        conn.execute("UPDATE users SET ad_balance = ad_balance + ? WHERE id = ?", (refund, user["id"]))
        conn.commit()
        flash(f"Buyurtma to'xtatildi. Bajarilmagan {remaining} ta obuna uchun {refund} so'm qaytarildi!", "info")
    conn.close()
    return redirect(url_for("advertise"))

@app.route("/wallet", methods=["GET", "POST"])
@login_required
def wallet(user):
    if request.method == "POST":
        action = request.form.get("action")
        try:
            amount = int(request.form.get("amount", 0))
        except ValueError:
            amount = 0

        conn = get_db()
        if action == "deposit":
            details = request.form.get("details", "").strip()
            receipt = request.files.get("receipt")
            saved_name = ""

            if receipt and receipt.filename:
                if not allowed_receipt(receipt.filename):
                    conn.close()
                    flash("Faqat rasm (JPG, PNG, WEBP) yoki PDF chek fayl yuklash mumkin!", "danger")
                    return redirect(url_for("wallet"))
                ext = receipt.filename.rsplit(".", 1)[1].lower()
                saved_name = secure_filename(f"u{user['id']}_{int(time.time())}_{random.randint(1000,9999)}.{ext}")
                receipt.save(os.path.join(RECEIPT_UPLOAD_DIR, saved_name))

            if amount < CONFIG["MIN_DEPOSIT"] or (not saved_name and not details):
                conn.close()
                flash(f"Minimal deposit {CONFIG['MIN_DEPOSIT']} so'm! Chek rasmi/PDF yoki chek ma'lumotini kiriting.", "danger")
                return redirect(url_for("wallet"))

            info_text = details or ("PDF chek yuklandi" if saved_name.endswith(".pdf") else "Rasm chek yuklandi")
            conn.execute(
                "INSERT INTO transactions (user_id, tx_type, amount, details, receipt_file) VALUES (?, 'deposit', ?, ?, ?)",
                (user["id"], amount, info_text, saved_name)
            )
            conn.commit()
            flash("Deposit cheki Admin panelga yuborildi! Tasdiqlangach Reklama balansingizga tushadi.", "success")

        elif action == "withdraw":
            card = request.form.get("card", "").strip()
            if amount < CONFIG["MIN_WITHDRAW"]:
                flash(f"Minimal pul chiqarish {CONFIG['MIN_WITHDRAW']} so'm!", "danger")
            elif amount > user["work_balance"]:
                flash("Ishlangan balansingizda mablag' yetarli emas!", "danger")
            elif len(card.replace(" ", "")) < 16:
                flash("Karta raqamini to'liq kiriting!", "danger")
            else:
                conn.execute("UPDATE users SET work_balance = work_balance - ? WHERE id = ?", (amount, user["id"]))
                conn.execute("INSERT INTO transactions (user_id, tx_type, amount, details) VALUES (?, 'withdraw', ?, ?)",
                             (user["id"], amount, card))
                conn.commit()
                flash("Pul chiqarish so'rovingiz Admin panelga yuborildi!", "success")

        elif action == "exchange":
            if amount < 70 or amount > user["work_balance"]:
                flash("O'tkazish uchun mablag' yetarli emas (kamida 70 so'm)!", "danger")
            else:
                conn.execute("UPDATE users SET work_balance = work_balance - ?, ad_balance = ad_balance + ? WHERE id = ?",
                             (amount, amount, user["id"]))
                conn.commit()
                flash(f"{amount} so'm Reklama balansiga o'tkazildi!", "success")

        conn.close()
        return redirect(url_for("wallet"))

    conn = get_db()
    txs = conn.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 20", (user["id"],)).fetchall()
    conn.close()

    html = """
    <div class="grid-2">
        <div class="card">
            <h3>💳 Hisobni to'ldirish (Rasm yoki PDF chek)</h3>
            <div class="alert alert-info" style="margin-top:12px;">
                Deposit karta: <b>{{ config.CARD_NUMBER }}</b><br>
                Egasi: <b>{{ config.CARD_HOLDER }}</b><br>
                Minimal: <b>{{ config.MIN_DEPOSIT }} so'm</b><br>
                <small>To'lov qilingach chek rasmini (JPG/PNG/WEBP) yoki PDF chekni yuklang.</small>
            </div>
            <form method="POST" enctype="multipart/form-data">
                <input type="hidden" name="action" value="deposit">
                <label>To'langan summa (so'm) *</label>
                <input type="number" name="amount" min="{{ config.MIN_DEPOSIT }}" required placeholder="Masalan: 7000">

                <label>Chek fayli (Rasm yoki PDF)</label>
                <input type="file" name="receipt" accept=".png,.jpg,.jpeg,.webp,.pdf">

                <label>Qo'shimcha izoh (ixtiyoriy)</label>
                <input type="text" name="details" placeholder="Masalan: Click / Payme chek">

                <button type="submit" class="btn btn-primary" style="width:100%;">Chekni yuborish</button>
            </form>
        </div>

        <div class="card">
            <h3>💸 Pul chiqarish ({{ user.work_balance }} so'm)</h3>
            <form method="POST" style="margin-top:12px;">
                <input type="hidden" name="action" value="withdraw">
                <label>Summa (Min: {{ config.MIN_WITHDRAW }} so'm)</label>
                <input type="number" name="amount" min="{{ config.MIN_WITHDRAW }}" required placeholder="Kamida 20000">
                <label>Karta raqamingiz</label>
                <input type="text" name="card" required placeholder="8600 / 9860 **** **** ****">
                <button type="submit" class="btn btn-success" style="width:100%;">Pul chiqarish so'rovi</button>
            </form>

            <hr style="border-color:rgba(148,163,184,0.16); margin:20px 0;">
            <h4 style="margin-bottom:8px;">🔄 Reklama balansiga o'tkazish</h4>
            <form method="POST">
                <input type="hidden" name="action" value="exchange">
                <input type="number" name="amount" min="70" max="{{ user.work_balance }}" placeholder="Summa (so'm)" required>
                <button type="submit" class="btn btn-warning btn-sm" style="width:100%;">Reklama balansiga o'tkazish</button>
            </form>
        </div>
    </div>

    <div class="card">
        <h3>To'lovlar tarixi</h3>
        <div class="table-wrap">
            <table>
                <tr><th>Turi</th><th>Summa</th><th>Chek fayl</th><th>Izoh</th><th>Holat</th></tr>
                {% for tx in txs %}
                <tr>
                    <td>{{ 'Deposit' if tx.tx_type == 'deposit' else 'Chiqarish' }}</td>
                    <td>{{ tx.amount }} so'm</td>
                    <td>
                        {% if tx.receipt_file %}
                            <a href="{{ url_for('get_receipt_file', filename=tx.receipt_file) }}" target="_blank" style="color:#38bdf8; font-weight:700;">Faylni ochish</a>
                        {% else %}
                            -
                        {% endif %}
                    </td>
                    <td>{{ tx.details }}</td>
                    <td>{{ tx.status }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </div>
    """
    return render_page(html, user=user, txs=txs)

# ================= ADMIN PANEL (FAQAT ADMIN UCHUN) =================
@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_panel(user):
    conn = get_db()
    if request.method == "POST":
        tx_id = request.form.get("tx_id")
        decision = request.form.get("decision")
        receipt_note = request.form.get("receipt_note", "").strip()
        tx = conn.execute("SELECT * FROM transactions WHERE id = ? AND status = 'pending'", (tx_id,)).fetchone()
        if tx:
            if decision == "approve":
                conn.execute("UPDATE transactions SET status = 'approved', admin_receipt = ? WHERE id = ?",
                             (receipt_note or "Tasdiqlandi", tx_id))
                if tx["tx_type"] == "deposit":
                    conn.execute("UPDATE users SET ad_balance = ad_balance + ? WHERE id = ?", (tx["amount"], tx["user_id"]))
                flash("So'rov tasdiqlandi!", "success")
            elif decision == "reject":
                conn.execute("UPDATE transactions SET status = 'rejected', admin_receipt = ? WHERE id = ?",
                             (receipt_note or "Rad etildi", tx_id))
                if tx["tx_type"] == "withdraw":
                    conn.execute("UPDATE users SET work_balance = work_balance + ? WHERE id = ?", (tx["amount"], tx["user_id"]))
                flash("So'rov rad etildi!", "danger")
            conn.commit()
        return redirect(url_for("admin_panel"))

    pending_txs = conn.execute("""
        SELECT t.*, u.username FROM transactions t
        JOIN users u ON t.user_id = u.id
        WHERE t.status = 'pending' ORDER BY t.id ASC
    """).fetchall()

    stats = {
        "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "tasks": conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
        "completions": conn.execute("SELECT COUNT(*) FROM completions").fetchone()[0],
    }
    stats["profit"] = stats["completions"] * (CONFIG["ADVERTISER_PRICE"] - CONFIG["WORKER_REWARD"])
    conn.close()

    html = """
    <div class="grid-3">
        <div class="stat-box"><div class="stat-title">Foydalanuvchilar</div><div class="stat-val">{{ stats.users }}</div></div>
        <div class="stat-box"><div class="stat-title">Jami vazifalar</div><div class="stat-val">{{ stats.tasks }}</div></div>
        <div class="stat-box"><div class="stat-title">Sayt sof foydasi (20 so'mdan)</div><div class="stat-val green">{{ stats.profit }} so'm</div></div>
    </div>

    <div class="card" style="margin-top:16px;">
        <h2>Kutilayotgan Deposit va Pul chiqarish so'rovlari</h2>
        {% if pending_txs %}
            <div class="table-wrap">
                <table>
                    <tr>
                        <th>User</th>
                        <th>Turi</th>
                        <th>Summa</th>
                        <th>Yuklangan chek (Rasm / PDF)</th>
                        <th>Amal</th>
                    </tr>
                    {% for tx in pending_txs %}
                    <tr>
                        <td>{{ tx.username }}</td>
                        <td>{{ '📥 DEPOSIT' if tx.tx_type == 'deposit' else '📤 CHIQARISH' }}</td>
                        <td><b>{{ tx.amount }} so'm</b></td>
                        <td>
                            {% if tx.receipt_file %}
                                {% if tx.receipt_file.lower().endswith('.pdf') %}
                                    <a href="{{ url_for('get_receipt_file', filename=tx.receipt_file) }}" target="_blank" class="btn btn-primary btn-sm">📄 PDF Chekni ochish</a>
                                {% else %}
                                    <a href="{{ url_for('get_receipt_file', filename=tx.receipt_file) }}" target="_blank">
                                        <img src="{{ url_for('get_receipt_file', filename=tx.receipt_file) }}" class="receipt-thumb" alt="Chek">
                                    </a>
                                {% endif %}
                            {% else %}
                                <span style="color:#94a3b8;">{{ tx.details }}</span>
                            {% endif %}
                        </td>
                        <td>
                            <form method="POST" style="display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin:0;">
                                <input type="hidden" name="tx_id" value="{{ tx.id }}">
                                <input type="text" name="receipt_note" class="sm-input" placeholder="Izoh...">
                                <button name="decision" value="approve" class="btn btn-success btn-sm">Tasdiqlash</button>
                                <button name="decision" value="reject" class="btn btn-danger btn-sm">Rad etish</button>
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
        {% else %}
            <p style="color:#94a3b8; margin-top:10px;">Hozircha kutilayotgan so'rovlar yo'q.</p>
        {% endif %}
    </div>
    """
    return render_page(html, user=user, pending_txs=pending_txs, stats=stats)

init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
