from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import sqlite3, os, re
from datetime import datetime, timedelta
from contextlib import contextmanager


# ===========================
# CONFIGURARE DE BAZĂ
# ===========================

app = Flask(__name__)
app.secret_key = "super-secret-key"  # cheia pentru sesiuni

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(BASE_DIR, "database.db")
EMAIL_FILE = os.path.join(BASE_DIR, "allowed_emails.txt")

# Parola adminului (poate fi setată și din variabilele de mediu)
ADMIN_PWD = os.environ.get("ADMIN_PWD", "admin1234")


# ===========================
# CONEXIUNE CU BAZA DE DATE
# ===========================

@contextmanager
def get_db_connection():
    """Creează o conexiune SQLite care se închide automat."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ===========================
# INITIALIZARE BAZĂ DE DATE
# ===========================

def init_db():
    """Creează tabela principală dacă nu există deja."""
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
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


# ===========================
# FUNCȚII UTILE
# ===========================

def load_allowed_emails():
    """Încarcă lista emailurilor autorizate din fișierul local."""
    if not os.path.exists(EMAIL_FILE):
        with open(EMAIL_FILE, "w") as f:
            f.write("test@example.com\n")

    with open(EMAIL_FILE, "r") as f:
        return [line.strip().lower() for line in f if line.strip()]


# ===========================
# PAGINI HTML
# ===========================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/calendar")
def calendar():
    if "email" not in session:
        return redirect(url_for("index"))
    return render_template("calendar.html")


# ===========================
# AUTENTIFICARE EMAIL
# ===========================

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
        return jsonify({"allowed": False})


# ===========================
# API: TIMP / REZERVĂRI
# ===========================

@app.route("/api/timeslots")
def api_timeslots():
    """Returnează lista orelor și statusul mașinilor pentru o zi."""
    if "email" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    date_str = request.args.get("date")
    if not date_str:
        return jsonify({"error": "Missing date"}), 400

    start_time = datetime.strptime("07:00", "%H:%M")
    ore = [(start_time + timedelta(hours=i)).strftime("%H:%M") for i in range(16)]

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT time, machine, room FROM reservations WHERE date=?", (date_str,))
        rows = c.fetchall()

    # Organizează rezervările pe oră
    rezervari = {}
    for row in rows:
        t = row["time"]
        if t not in rezervari:
            rezervari[t] = []
        rezervari[t].append({
            "machine": row["machine"],
            "room": row["room"]
        })

    # Creează structura pentru frontend
    timeslots = []
    for t in ore:
        masini = []
        for i in range(1, 5):
            nume = f"Mașina {i}"
            rezervare = next((r for r in rezervari.get(t, []) if r["machine"] == nume), None)
            masini.append({
                "name": nume,
                "booked": rezervare is not None,
                "booked_by": rezervare["room"] if rezervare else None
            })
        timeslots.append({"time": t, "machines": masini})

    return jsonify({"date": date_str, "timeslots": timeslots})


@app.route("/api/book", methods=["POST"])
def api_book():
    """Salvează o rezervare nouă în baza de date."""
    if "email" not in session:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    data = request.get_json()
    date    = data.get("date")
    time    = data.get("time")
    room    = data.get("room")
    machine = data.get("machine")
    email   = session["email"]

    if not date or not time or not room or not machine:
        return jsonify({"success": False, "error": "Missing data"}), 400

    with get_db_connection() as conn:
        c = conn.cursor()

        # Limită: max 2 rezervări pe zi per utilizator
        c.execute("SELECT COUNT(*) FROM reservations WHERE email=? AND date=?", (email, date))
        count = c.fetchone()[0]

        if count >= 2:
            return jsonify({"success": False, "error": "Maxim 2 rezervări/zi"}), 400

        try:
            c.execute("""
                INSERT INTO reservations (email, room, date, time, machine)
                VALUES (?, ?, ?, ?, ?)
            """, (email, room, date, time, machine))
            print(f"[BOOKED] {email} ({room}) -> {date} {time}, {machine}")
            return jsonify({"success": True})
        except sqlite3.IntegrityError:
            return jsonify({"success": False, "error": "Această mașină e deja rezervată la acea oră"})


# ===========================
# LOGOUT
# ===========================

@app.route("/logout", methods=["POST"])
def logout():
    """Șterge sesiunea curentă."""
    session.clear()
    return jsonify({"success": True})


# ===========================
# ADMIN PANEL
# ===========================

@app.route("/admin")
def admin_page():
    return render_template("admin.html")


@app.route("/admin/list", methods=["POST"])
def admin_list():
    """Returnează toate rezervările (vizibile doar pentru admin)."""
    data = request.get_json()
    pwd = data.get("admin_password")

    if pwd != ADMIN_PWD:
        return jsonify({"success": False, "error": "Parolă incorectă"}), 403

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id, email, room, date, time, machine FROM reservations ORDER BY date, time")
        rezervari = [
            {
                "id": r["id"],
                "email": r["email"],
                "camera": r["room"],
                "data": r["date"],
                "ora": r["time"],
                "masina": r["machine"]
            }
            for r in c.fetchall()
        ]

    return jsonify({"success": True, "reservations": rezervari})


@app.route("/admin/delete_one", methods=["POST"])
def admin_delete_one():
    """Șterge o rezervare după ID."""
    data = request.get_json()
    pwd = data.get("admin_password")
    rid = data.get("id")

    if pwd != ADMIN_PWD:
        return jsonify({"success": False, "error": "Parolă incorectă"}), 403

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM reservations WHERE id=?", (rid,))
        conn.commit()

    return jsonify({"success": True})


@app.route("/admin/delete_all", methods=["POST"])
def admin_delete_all():
    """Șterge toate rezervările."""
    data = request.get_json()
    pwd = data.get("admin_password")

    if pwd != ADMIN_PWD:
        return jsonify({"success": False, "error": "Parolă incorectă"}), 403

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM reservations")
        conn.commit()

    return jsonify({"success": True})


# ===========================
# RUN SERVER
# ===========================

if __name__ == "__main__":
    print("🚀 Server running at http://127.0.0.1:5000")
    print("🔑 Admin password:", ADMIN_PWD)
    app.run(debug=True)
