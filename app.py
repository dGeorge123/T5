from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_cors import CORS
from datetime import datetime, timedelta
from contextlib import contextmanager
import sqlite3, os, re

# ======================================
# CONFIGURARE APLICAȚIE
# ======================================

app = Flask(__name__)
app.secret_key = "super-secret-key"

# CORS + cookie-uri cross-origin
CORS(app, supports_credentials=True)

# Config cookie secure pentru producție
env = os.environ.get("DEPLOY_ENV", "development").lower()
if env == "production":
    app.config.update(
        SESSION_COOKIE_SAMESITE="None",
        SESSION_COOKIE_SECURE=True
    )
else:
    app.config.update(
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=False
    )

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")
EMAIL_FILE = os.path.join(BASE_DIR, "allowed_emails.txt")
ADMIN_PWD = os.environ.get("ADMIN_PWD", "admin1234")


# ======================================
# CONEXIUNE BAZĂ DE DATE
# ======================================

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                room TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                machine TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, time, machine)
            )
        """)
init_db()


# ======================================
# FUNCȚII UTILE
# ======================================

def load_allowed_emails():
    if not os.path.exists(EMAIL_FILE):
        with open(EMAIL_FILE, "w") as f:
            f.write("test@example.com\n")
    with open(EMAIL_FILE, "r") as f:
        return [line.strip().lower() for line in f if line.strip()]


# ======================================
# ROUTE HTML
# ======================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/calendar")
def calendar():
    if "email" not in session:
        return redirect(url_for("index"))
    return render_template("calendar.html")


@app.route("/admin")
def admin_page():
    return render_template("admin.html")


# ======================================
# AUTENTIFICARE EMAIL
# ======================================

@app.route("/check_email", methods=["POST"])
def check_email():
    data = request.get_json()
    email = data.get("email", "").strip().lower()
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        return jsonify({"allowed": False, "message": "Format email invalid"})

    allowed = load_allowed_emails()
    if email in allowed:
        session["email"] = email
        print(f"[LOGIN ✅] {email}")
        return jsonify({"allowed": True})
    else:
        print(f"[LOGIN ❌] {email}")
        return jsonify({"allowed": False, "message": "Email neautorizat"})


# ======================================
# API PRINCIPAL: TIMP / REZERVĂRI
# ======================================

@app.route("/api/timeslots")
def timeslots():
    if "email" not in session:
        return jsonify({"error": "Neautentificat"}), 401

    date_str = request.args.get("date")
    if not date_str:
        return jsonify({"error": "Lipsă dată"}), 400

    start = datetime.strptime("07:00", "%H:%M")
    ore = [(start + timedelta(hours=i)).strftime("%H:%M") for i in range(16)]

    with get_db() as conn:
        rows = conn.execute("SELECT time, machine, room FROM reservations WHERE date=?", (date_str,)).fetchall()

    rezervari = {}
    for r in rows:
        rezervari.setdefault(r["time"], []).append({"machine": r["machine"], "room": r["room"]})

    result = []
    for t in ore:
        masini = []
        for i in range(1, 5):
            nume = f"Mașina {i}"
            rez = next((x for x in rezervari.get(t, []) if x["machine"] == nume), None)
            masini.append({
                "name": nume,
                "booked": rez is not None,
                "booked_by": rez["room"] if rez else None
            })
        result.append({"time": t, "machines": masini})

    return jsonify({"success": True, "date": date_str, "timeslots": result})


@app.route("/api/book", methods=["POST"])
def book():
    if "email" not in session:
        return jsonify({"success": False, "error": "Neautentificat"}), 401

    data = request.get_json()
    date, time, room, machine = data.get("date"), data.get("time"), data.get("room"), data.get("machine")
    email = session["email"]

    if not all([date, time, room, machine]):
        return jsonify({"success": False, "error": "Date incomplete"}), 400

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM reservations WHERE email=? AND date=?", (email, date))
        if cur.fetchone()[0] >= 2:
            return jsonify({"success": False, "error": "Maxim 2 rezervări/zi"}), 400

        try:
            cur.execute("INSERT INTO reservations (email, room, date, time, machine) VALUES (?, ?, ?, ?, ?)",
                        (email, room, date, time, machine))
            print(f"[BOOKED] {email} ({room}) -> {date} {time}, {machine}")
            return jsonify({"success": True})
        except sqlite3.IntegrityError:
            return jsonify({"success": False, "error": "Această mașină este deja rezervată"})


@app.route("/api/my_reservations")
def my_reservations():
    if "email" not in session:
        return jsonify({"success": False, "error": "Neautentificat"}), 401
    email = session["email"]
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, date, time, machine, room FROM reservations WHERE email=? ORDER BY date, time",
            (email,)
        ).fetchall()
    reservations = [dict(r) for r in rows]
    return jsonify({"success": True, "reservations": reservations})


@app.route("/api/delete_reservation", methods=["POST"])
def delete_reservation():
    if "email" not in session:
        return jsonify({"success": False, "error": "Neautentificat"}), 401

    data = request.get_json()
    rid = data.get("id")
    email = session["email"]

    with get_db() as conn:
        cur = conn.execute("DELETE FROM reservations WHERE id=? AND email=?", (rid, email))
        if cur.rowcount == 0:
            return jsonify({"success": False, "error": "Rezervarea nu există sau nu aparține utilizatorului"})
    return jsonify({"success": True})


# ======================================
# ADMIN PANEL
# ======================================

@app.route("/admin/list", methods=["POST"])
def admin_list():
    data = request.get_json()
    pwd = data.get("admin_password")
    if pwd != ADMIN_PWD:
        return jsonify({"success": False, "error": "Parolă incorectă"}), 403

    with get_db() as conn:
        rows = conn.execute("SELECT * FROM reservations ORDER BY date, time").fetchall()
        result = [dict(r) for r in rows]
    return jsonify({"success": True, "reservations": result})


@app.route("/admin/delete_one", methods=["POST"])
def admin_delete_one():
    data = request.get_json()
    pwd, rid = data.get("admin_password"), data.get("id")
    if pwd != ADMIN_PWD:
        return jsonify({"success": False, "error": "Parolă incorectă"}), 403
    with get_db() as conn:
        conn.execute("DELETE FROM reservations WHERE id=?", (rid,))
    return jsonify({"success": True})


@app.route("/admin/delete_all", methods=["POST"])
def admin_delete_all():
    data = request.get_json()
    pwd = data.get("admin_password")
    if pwd != ADMIN_PWD:
        return jsonify({"success": False, "error": "Parolă incorectă"}), 403
    with get_db() as conn:
        conn.execute("DELETE FROM reservations")
    return jsonify({"success": True})


# ======================================
# RUN
# ======================================

if __name__ == "__main__":
    print("🚀 Server running on http://127.0.0.1:5000")
    print("🔑 Admin password:", ADMIN_PWD)
    app.run(host="0.0.0.0", port=5000, debug=True)
