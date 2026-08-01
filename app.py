import os
import csv
import datetime
from zoneinfo import ZoneInfo
import psycopg2
import psycopg2.extras
from flask import Flask, request, jsonify, render_template, session, redirect, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "flash-dev-secret-change-me")

DATABASE_URL = os.environ.get("DATABASE_URL")
REGISTRATION_PIN = os.environ.get("REGISTRATION_PIN", "")
IST = ZoneInfo("Asia/Kolkata")
ALLOWED_COLORS = {"green", "yellow", "red", "blue"}
BAND_ORDER = ["green", "yellow", "red", "blue"]
BAND_SIZE = 170


def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def fmt_ist(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(IST).strftime("%I:%M %p")


def next_band_color(cur):
    """Atomically get the next sequence number and map it to a color band.
    Cycles green -> yellow -> red -> blue -> green ... every 170 check-ins."""
    cur.execute("SELECT nextval('band_seq')")
    n = cur.fetchone()[0]
    idx = (n - 1) % (BAND_SIZE * len(BAND_ORDER))
    band_index = idx // BAND_SIZE
    return BAND_ORDER[band_index]


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS contributors (
            id SERIAL PRIMARY KEY,
            amb_id TEXT UNIQUE,
            name TEXT NOT NULL,
            phone TEXT,
            village TEXT,
            photo_url TEXT,
            txn_date TEXT,
            amount TEXT,
            txn_no TEXT,
            color_band TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS checkins (
            id SERIAL PRIMARY KEY,
            contributor_id INTEGER UNIQUE REFERENCES contributors(id),
            name TEXT NOT NULL,
            phone TEXT,
            amb_id TEXT,
            is_walkin BOOLEAN DEFAULT FALSE,
            village TEXT,
            family_count INTEGER DEFAULT 1,
            desk TEXT,
            color_band TEXT,
            checked_in_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_contrib_phone ON contributors (phone);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_contrib_name ON contributors (LOWER(name));")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_checkin_phone ON checkins (phone);")
    for col, coltype in [("txn_date", "TEXT"), ("amount", "TEXT"), ("txn_no", "TEXT"), ("color_band", "TEXT")]:
        cur.execute(f"ALTER TABLE contributors ADD COLUMN IF NOT EXISTS {col} {coltype};")
    cur.execute("ALTER TABLE checkins ADD COLUMN IF NOT EXISTS color_band TEXT;")
    try:
        cur.execute("""
            ALTER TABLE checkins
            ALTER COLUMN checked_in_at TYPE TIMESTAMPTZ
            USING checked_in_at AT TIME ZONE 'UTC';
        """)
    except Exception:
        conn.rollback()

    cur.execute("CREATE SEQUENCE IF NOT EXISTS band_seq START 1;")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS commemorated_persons (
            id SERIAL PRIMARY KEY,
            sl_no INTEGER UNIQUE,
            name TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS commemorated_checkins (
            id SERIAL PRIMARY KEY,
            commemorated_id INTEGER UNIQUE REFERENCES commemorated_persons(id),
            visitor_name TEXT,
            visitor_phone TEXT,
            desk TEXT,
            checked_in_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_commem_name ON commemorated_persons (LOWER(name));")

    conn.commit()

    # Seed commemorated_persons from bundled CSV, only if table is empty
    cur.execute("SELECT COUNT(*) FROM commemorated_persons")
    count = cur.fetchone()[0]
    if count == 0:
        csv_path = os.path.join(os.path.dirname(__file__), "commemorated_persons.csv")
        if os.path.exists(csv_path):
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    cur.execute(
                        "INSERT INTO commemorated_persons (sl_no, name) VALUES (%s, %s) ON CONFLICT (sl_no) DO NOTHING",
                        (int(row["sl_no"]), row["name"])
                    )
            conn.commit()

    cur.close()
    conn.close()


@app.before_request
def require_pin():
    if not REGISTRATION_PIN:
        return
    if request.path in ("/login", "/static") or request.path.startswith("/static/"):
        return
    if not session.get("authed"):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Not authenticated"}), 401
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("pin") == REGISTRATION_PIN:
            session["authed"] = True
            return redirect(url_for("index"))
        error = "Wrong PIN"
    return f"""
    <html><body style="font-family:sans-serif;max-width:320px;margin:80px auto;text-align:center;">
    <h2>FLASH Registration</h2>
    <form method="post">
      <input name="pin" type="password" placeholder="Enter event PIN"
             style="font-size:20px;padding:14px;width:100%;box-sizing:border-box;margin-bottom:10px;" autofocus />
      <button style="font-size:18px;padding:14px;width:100%;background:#b91c1c;color:white;border:none;border-radius:8px;">Enter</button>
    </form>
    {'<p style="color:red;">' + error + '</p>' if error else ''}
    </body></html>
    """


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/commemorated")
def commemorated_page():
    return render_template("commemorated.html")


# ---------- Contributor / walk-in search & check-in ----------

@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT c.id, c.amb_id, c.name, c.phone, c.village, c.photo_url,
               c.txn_date, c.amount, c.txn_no,
               ci.id AS checkin_id, ci.checked_in_at, ci.desk, ci.color_band
        FROM contributors c
        LEFT JOIN checkins ci ON ci.contributor_id = c.id
        WHERE c.phone ILIKE %s OR c.name ILIKE %s OR c.amb_id ILIKE %s
        ORDER BY (c.phone = %s) DESC, c.name
        LIMIT 20
    """, (f"%{q}%", f"%{q}%", f"%{q}%", q))
    contrib_rows = cur.fetchall()

    cur.execute("""
        SELECT id, name, phone, family_count, desk, checked_in_at, color_band, village
        FROM checkins
        WHERE is_walkin = TRUE AND (phone ILIKE %s OR name ILIKE %s)
        ORDER BY checked_in_at DESC
        LIMIT 10
    """, (f"%{q}%", f"%{q}%"))
    walkin_rows = cur.fetchall()

    cur.close()
    conn.close()

    results = []
    for r in contrib_rows:
        results.append({
            "source": "contributor",
            "contributor_id": r["id"],
            "amb_id": r["amb_id"],
            "name": r["name"],
            "phone": r["phone"],
            "village": r["village"],
            "photo_url": r["photo_url"],
            "txn_date": r["txn_date"],
            "amount": r["amount"],
            "txn_no": (r["txn_no"] or "")[-4:] if r["txn_no"] else None,
            "already_checked_in": r["checkin_id"] is not None,
            "checkin_id": r["checkin_id"],
            "checked_in_at": fmt_ist(r["checked_in_at"]),
            "checked_in_desk": r["desk"],
            "color_band": r["color_band"],
        })
    for r in walkin_rows:
        results.append({
            "source": "walkin",
            "contributor_id": None,
            "amb_id": None,
            "name": r["name"],
            "phone": r["phone"],
            "village": r["village"],
            "photo_url": None,
            "txn_date": None,
            "amount": None,
            "txn_no": None,
            "already_checked_in": True,
            "checkin_id": r["id"],
            "checked_in_at": fmt_ist(r["checked_in_at"]),
            "checked_in_desk": r["desk"],
            "color_band": r["color_band"],
        })
    return jsonify(results)


@app.route("/api/checkin", methods=["POST"])
def api_checkin():
    data = request.get_json(force=True)
    desk = (data.get("desk") or "Unknown Desk").strip()
    mode = data.get("mode")

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if mode == "existing":
        contributor_id = data.get("contributor_id")
        family_count = int(data.get("family_count") or 1)

        cur.execute("SELECT * FROM contributors WHERE id = %s", (contributor_id,))
        contrib = cur.fetchone()
        if not contrib:
            cur.close(); conn.close()
            return jsonify({"ok": False, "error": "Contributor not found"}), 404

        color = next_band_color(cur)

        cur.execute("""
            INSERT INTO checkins (contributor_id, name, phone, amb_id, is_walkin, village, family_count, desk, color_band)
            VALUES (%s, %s, %s, %s, FALSE, %s, %s, %s, %s)
            ON CONFLICT (contributor_id) DO NOTHING
            RETURNING id, checked_in_at
        """, (contrib["id"], contrib["name"], contrib["phone"], contrib["amb_id"],
              contrib["village"], family_count, desk, color))
        row = cur.fetchone()
        conn.commit()

        if row is None:
            cur.execute("""
                SELECT checked_in_at, desk, color_band FROM checkins WHERE contributor_id = %s
            """, (contributor_id,))
            existing = cur.fetchone()
            cur.close(); conn.close()
            return jsonify({
                "ok": False,
                "already_checked_in": True,
                "checked_in_at": fmt_ist(existing["checked_in_at"]),
                "checked_in_desk": existing["desk"],
                "color_band": existing["color_band"],
            })

        cur.close(); conn.close()
        return jsonify({
            "ok": True, "name": contrib["name"],
            "checkin_id": row["id"],
            "checked_in_at": fmt_ist(row["checked_in_at"]),
            "color_band": color,
        })

    elif mode == "walkin":
        name = (data.get("name") or "").strip()
        phone = (data.get("phone") or "").strip()
        village = (data.get("village") or "").strip()
        family_count = int(data.get("family_count") or 1)
        confirm_dup = bool(data.get("confirm"))

        if not name:
            cur.close(); conn.close()
            return jsonify({"ok": False, "error": "Name is required"}), 400

        if not confirm_dup and phone:
            cur.execute("""
                SELECT id, name, desk, checked_in_at FROM checkins
                WHERE is_walkin = TRUE AND phone = %s AND LOWER(name) = LOWER(%s)
                ORDER BY checked_in_at DESC LIMIT 1
            """, (phone, name))
            existing = cur.fetchone()
            if existing:
                cur.close(); conn.close()
                return jsonify({
                    "ok": False,
                    "possible_duplicate": True,
                    "existing_name": existing["name"],
                    "existing_desk": existing["desk"],
                    "existing_at": fmt_ist(existing["checked_in_at"]),
                })

        color = next_band_color(cur)

        cur.execute("""
            INSERT INTO checkins (contributor_id, name, phone, amb_id, is_walkin, village, family_count, desk, color_band)
            VALUES (NULL, %s, %s, NULL, TRUE, %s, %s, %s, %s)
            RETURNING id, checked_in_at
        """, (name, phone, village, family_count, desk, color))
        row = cur.fetchone()
        conn.commit()
        cur.close(); conn.close()
        return jsonify({
            "ok": True, "name": name,
            "checkin_id": row["id"],
            "checked_in_at": fmt_ist(row["checked_in_at"]),
            "color_band": color,
        })

    cur.close(); conn.close()
    return jsonify({"ok": False, "error": "Invalid mode"}), 400


@app.route("/api/undo_checkin", methods=["POST"])
def api_undo_checkin():
    data = request.get_json(force=True)
    checkin_id = data.get("checkin_id")
    if not checkin_id:
        return jsonify({"ok": False, "error": "checkin_id required"}), 400

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM checkins
        WHERE id = %s AND checked_in_at > NOW() - INTERVAL '10 minutes'
        RETURNING id
    """, (checkin_id,))
    row = cur.fetchone()
    conn.commit()
    cur.close(); conn.close()

    if row:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Too late to undo (10 min window passed) or already removed"}), 400


@app.route("/api/color_stats")
def api_color_stats():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT color_band, COUNT(*) FROM checkins GROUP BY color_band")
    rows = cur.fetchall()
    cur.close(); conn.close()

    counts = {"green": 0, "yellow": 0, "red": 0, "blue": 0, "unassigned": 0}
    for color, c in rows:
        if color in counts:
            counts[color] = c
        else:
            counts["unassigned"] += c
    return jsonify(counts)


@app.route("/api/export")
def api_export():
    import io
    from flask import Response

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT amb_id, name, phone, village, family_count, is_walkin, desk, color_band, checked_in_at
        FROM checkins
        ORDER BY checked_in_at ASC
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()

    output = io.StringIO()
    writer = __import__("csv").writer(output)
    writer.writerow(["AMB ID", "Name", "Phone", "Area", "People Count", "Type", "Desk", "Color Band", "Checked In At (IST)"])
    for r in rows:
        writer.writerow([
            r["amb_id"] or "",
            r["name"],
            r["phone"] or "",
            r["village"] or "",
            r["family_count"],
            "Walk-in" if r["is_walkin"] else "Contributor",
            r["desk"] or "",
            r["color_band"] or "",
            r["checked_in_at"].astimezone(IST).strftime("%Y-%m-%d %I:%M %p") if r["checked_in_at"] else "",
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=smaranotsavam_checkins.csv"}
    )


@app.route("/api/stats")
def api_stats():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT COUNT(*) AS c, COALESCE(SUM(family_count),0) AS people FROM checkins")
    totals = cur.fetchone()

    cur.execute("SELECT COUNT(*) AS c FROM checkins WHERE is_walkin = FALSE")
    contrib_count = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM checkins WHERE is_walkin = TRUE")
    walkin_count = cur.fetchone()["c"]

    cur.execute("""
        SELECT name, phone, village, family_count, desk, is_walkin, checked_in_at, color_band
        FROM checkins ORDER BY checked_in_at DESC LIMIT 25
    """)
    recent = cur.fetchall()
    for r in recent:
        r["checked_in_at"] = fmt_ist(r["checked_in_at"])

    cur.close(); conn.close()

    return jsonify({
        "total_checkins": totals["c"],
        "total_people": totals["people"],
        "contributor_checkins": contrib_count,
        "walkin_checkins": walkin_count,
        "recent": recent,
    })


# ---------- Commemorated persons ----------

@app.route("/api/search_commemorated")
def api_search_commemorated():
    q = request.args.get("q", "").strip()
    if len(q) < 1:
        return jsonify([])

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT cp.id, cp.sl_no, cp.name,
               cc.id AS checkin_id, cc.visitor_name, cc.visitor_phone, cc.desk, cc.checked_in_at
        FROM commemorated_persons cp
        LEFT JOIN commemorated_checkins cc ON cc.commemorated_id = cp.id
        WHERE cp.name ILIKE %s OR CAST(cp.sl_no AS TEXT) = %s
        ORDER BY cp.name
        LIMIT 20
    """, (f"%{q}%", q))
    rows = cur.fetchall()
    cur.close(); conn.close()

    results = []
    for r in rows:
        results.append({
            "commemorated_id": r["id"],
            "sl_no": r["sl_no"],
            "name": r["name"],
            "already_checked_in": r["checkin_id"] is not None,
            "visitor_name": r["visitor_name"],
            "visitor_phone": r["visitor_phone"],
            "checked_in_desk": r["desk"],
            "checked_in_at": fmt_ist(r["checked_in_at"]),
        })
    return jsonify(results)


@app.route("/api/checkin_commemorated", methods=["POST"])
def api_checkin_commemorated():
    data = request.get_json(force=True)
    commemorated_id = data.get("commemorated_id")
    visitor_name = (data.get("visitor_name") or "").strip()
    visitor_phone = (data.get("visitor_phone") or "").strip()
    desk = (data.get("desk") or "Unknown Desk").strip()

    if not commemorated_id:
        return jsonify({"ok": False, "error": "commemorated_id required"}), 400

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        INSERT INTO commemorated_checkins (commemorated_id, visitor_name, visitor_phone, desk)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (commemorated_id) DO NOTHING
        RETURNING id, checked_in_at
    """, (commemorated_id, visitor_name, visitor_phone, desk))
    row = cur.fetchone()
    conn.commit()

    if row is None:
        cur.execute("""
            SELECT visitor_name, desk, checked_in_at FROM commemorated_checkins WHERE commemorated_id = %s
        """, (commemorated_id,))
        existing = cur.fetchone()
        cur.close(); conn.close()
        return jsonify({
            "ok": False,
            "already_checked_in": True,
            "visitor_name": existing["visitor_name"],
            "checked_in_desk": existing["desk"],
            "checked_in_at": fmt_ist(existing["checked_in_at"]),
        })

    cur.close(); conn.close()
    return jsonify({"ok": True, "checkin_id": row["id"], "checked_in_at": fmt_ist(row["checked_in_at"])})


@app.route("/api/commemorated_stats")
def api_commemorated_stats():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM commemorated_persons")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM commemorated_checkins")
    checked_in = cur.fetchone()[0]
    cur.close(); conn.close()
    return jsonify({"total": total, "checked_in": checked_in})


with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
