from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
import sqlite3
import os
from datetime import datetime, timedelta

# ==========================================================
# CONFIGURARE DE BAZĂ
# ==========================================================
app = Flask(__name__)
app.secret_key = "supersecretkey"
CORS(app, supports_credentials=True)

DB_FILE = "rezervari.db"
ADMIN_PASSWORD = "t5admin2025"  # parolă admin
MAX_REZERVARI_PE_ZI = 2

# ==========================================================
# INITIALIZARE BAZĂ DE DATE
# ==========================================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            camera TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            machine TEXT NOT NULL,
            UNIQUE(date, time, machine)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ==========================================================
# PAGINI HTML
# ==========================================================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/calendar")
def calendar_page():
    if "email" not in session:
        return render_template("index.html")
    return render_template("calendar.html")

@app.route("/admin")
def admin_panel():
    return render_template("admin.html")

# ==========================================================
# RUTE API UTILIZATORI
# ==========================================================
@app.route("/check_email", methods=["POST"])
def check_email():
    data = request.get_json()
    email = data.get("email", "").strip()

    # verifică dacă email-ul este autorizat
    if not os.path.exists("allowed_emails.txt"):
        return jsonify({"allowed": False, "message": "Lista de emailuri lipsește"})

    with open("allowed_emails.txt") as f:
        allowed = [e.strip().lower() for e in f.readlines()]

    if email.lower() not in allowed:
        return jsonify({"allowed": False, "message": "Email neautorizat"})

    session["email"] = email
    return jsonify({"allowed": True})

@app.route("/api/timeslots")
def timeslots():
    date = request.args.get("date")
    if not date:
        return jsonify({"error": "Lipsă dată"}), 400

    # intervale orare
    ore = [f"{h:02d}:00" for h in range(8, 23)]
    masini = ["M1", "M2", "M3", "M4"]

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT time, machine, email FROM reservations WHERE date = ?", (date,))
    rezervari = c.fetchall()
    conn.close()

    rezultat = []
    for ora in ore:
        entry = {"time": ora, "machines": []}
        for masina in masini:
            gasit = next((r for r in rezervari if r[0] == ora and r[1] == masina), None)
            if gasit:
                entry["machines"].append({"name": masina, "booked": True, "booked_by": gasit[2].split("@")[0]})
            else:
                entry["machines"].append({"name": masina, "booked": False})
        rezultat.append(entry)

    return jsonify({"timeslots": rezultat})

@app.route("/api/book", methods=["POST"])
def book():
    data = request.get_json()
    date = data.get("date")
    time = data.get("time")
    machine = data.get("machine")
    camera = data.get("room")
    email = session.get("email")

    if not all([date, time, machine, camera, email]):
        return jsonify({"success": False, "error": "Date lipsă"}), 400

    try:
        # nu permite rezervări în trecut
        if datetime.strptime(date, "%d-%m-%Y") < datetime.now():
            return jsonify({"success": False, "error": "Nu poți rezerva în trecut"}), 400

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # max 2 rezervări/zi/email
        c.execute("SELECT COUNT(*) FROM reservations WHERE email = ? AND date = ?", (email, date))
        count = c.fetchone()[0]
        if count >= MAX_REZERVARI_PE_ZI:
            conn.close()
            return jsonify({"success": False, "error": "Ai atins limita de 2 rezervări/zi"}), 400

        # verifică dacă e liber
        c.execute("SELECT * FROM reservations WHERE date = ? AND time = ? AND machine = ?", (date, time, machine))
        if c.fetchone():
            conn.close()
            return jsonify({"success": False, "error": "Slotul este deja ocupat"}), 400

        c.execute("INSERT INTO reservations (email, camera, date, time, machine) VALUES (?, ?, ?, ?, ?)",
                  (email, camera, date, time, machine))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        print("Eroare:", e)
        return jsonify({"success": False, "error": "Eroare la salvare"}), 500

@app.route("/api/my_reservations")
def my_reservations():
    email = session.get("email")
    if not email:
        return jsonify({"success": False, "error": "Nelogat"}), 403

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, date, time, machine FROM reservations WHERE email = ? ORDER BY date, time", (email,))
    data = [{"id": r[0], "date": r[1], "time": r[2], "machine": r[3]} for r in c.fetchall()]
    conn.close()
    return jsonify({"success": True, "reservations": data})

@app.route("/api/delete_reservation", methods=["POST"])
def delete_reservation():
    email = session.get("email")
    if not email:
        return jsonify({"success": False, "error": "Nelogat"}), 403

    data = request.get_json()
    rid = data.get("id")
    if not rid:
        return jsonify({"success": False, "error": "ID lipsă"}), 400

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM reservations WHERE id = ? AND email = ?", (rid, email))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# ==========================================================
# RUTE ADMIN
# ==========================================================
@app.route("/admin/list", methods=["POST"])
def admin_list():
    data = request.get_json()
    if not data or data.get("admin_password") != ADMIN_PASSWORD:
        return jsonify({"success": False, "error": "Parolă incorectă"}), 401

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, email, camera, date, time FROM reservations ORDER BY date, time")
    rows = c.fetchall()
    conn.close()

    reservations = [
        {"id": r[0], "email": r[1], "camera": r[2], "data": r[3], "ora": r[4]}
        for r in rows
    ]
    return jsonify({"success": True, "reservations": reservations})

@app.route("/admin/delete_one", methods=["POST"])
def admin_delete_one():
    data = request.get_json()
    if not data or data.get("admin_password") != ADMIN_PASSWORD:
        return jsonify({"success": False, "error": "Parolă incorectă"}), 401

    rid = data.get("id")
    if not rid:
        return jsonify({"success": False, "error": "ID lipsă"}), 400

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM reservations WHERE id = ?", (rid,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route("/admin/delete_all", methods=["POST"])
def admin_delete_all():
    data = request.get_json()
    if not data or data.get("admin_password") != ADMIN_PASSWORD:
        return jsonify({"success": False, "error": "Parolă incorectă"}), 401

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM reservations")
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# ==========================================================
# HEALTH CHECK (pentru Render)
# ==========================================================
@app.route("/health")
def health():
    return "OK", 200

# ==========================================================
# PORNIRE SERVER
# ==========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
