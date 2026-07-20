import os
import datetime
import psycopg2
import psycopg2.extras
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_conn():
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    return conn


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
            txn_no TEXT
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
            checked_in_at TIMESTAMP DEFAULT NOW()
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_contrib_phone ON contributors (phone);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_contrib_name ON contributors (LOWER(name));")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_checkin_phone ON checkins (phone);")
    for col, coltype in [("txn_date", "TEXT"), ("amount", "TEXT"), ("txn_no", "TEXT")]:
        cur.execute(f"ALTER TABLE contributors ADD COLUMN IF NOT EXISTS {col} {coltype};")
    conn.commit()
    cur.close()
    conn.close()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


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
               ci.id AS checkin_id, ci.checked_in_at, ci.desk
        FROM contributors c
        LEFT JOIN checkins ci ON ci.contributor_id = c.id
        WHERE c.phone ILIKE %s OR c.name ILIKE %s OR c.amb_id ILIKE %s
        ORDER BY (c.phone = %s) DESC, c.name
        LIMIT 20
    """, (f"%{q}%", f"%{q}%", f"%{q}%", q))
    contrib_rows = cur.fetchall()

    cur.execute("""
        SELECT id, name, phone, family_count, desk, checked_in_at
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
            "checked_in_at": r["checked_in_at"].strftime("%I:%M %p") if r["checked_in_at"] else None,
            "checked_in_desk": r["desk"],
        })
    for r in walkin_rows:
        results.append({
            "source": "walkin",
            "contributor_id": None,
            "amb_id": None,
            "name": r["name"],
            "phone": r["phone"],
            "village": None,
            "photo_url": None,
            "txn_date": None,
            "amount": None,
            "txn_no": None,
            "already_checked_in": True,
            "checked_in_at": r["checked_in_at"].strftime("%I:%M %p"),
            "checked_in_desk": r["desk"],
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

        cur.execute("""
            INSERT INTO checkins (contributor_id, name, phone, amb_id, is_walkin, village, family_count, desk)
            VALUES (%s, %s, %s, %s, FALSE, %s, %s, %s)
            ON CONFLICT (contributor_id) DO NOTHING
            RETURNING id, checked_in_at
        """, (contrib["id"], contrib["name"], contrib["phone"], contrib["amb_id"],
              contrib["village"], family_count, desk))
        row = cur.fetchone()
        conn.commit()

        if row is None:
            # already checked in by another desk
            cur.execute("""
                SELECT checked_in_at, desk FROM checkins WHERE contributor_id = %s
            """, (contributor_id,))
            existing = cur.fetchone()
            cur.close(); conn.close()
            return jsonify({
                "ok": False,
                "already_checked_in": True,
                "checked_in_at": existing["checked_in_at"].strftime("%I:%M %p"),
                "checked_in_desk": existing["desk"],
            })

        cur.close(); conn.close()
        return jsonify({"ok": True, "name": contrib["name"], "checked_in_at": row["checked_in_at"].strftime("%I:%M %p")})

    elif mode == "walkin":
        name = (data.get("name") or "").strip()
        phone = (data.get("phone") or "").strip()
        village = (data.get("village") or "").strip()
        family_count = int(data.get("family_count") or 1)

        if not name:
            cur.close(); conn.close()
            return jsonify({"ok": False, "error": "Name is required"}), 400

        cur.execute("""
            INSERT INTO checkins (contributor_id, name, phone, amb_id, is_walkin, village, family_count, desk)
            VALUES (NULL, %s, %s, NULL, TRUE, %s, %s, %s)
            RETURNING id, checked_in_at
        """, (name, phone, village, family_count, desk))
        row = cur.fetchone()
        conn.commit()
        cur.close(); conn.close()
        return jsonify({"ok": True, "name": name, "checked_in_at": row["checked_in_at"].strftime("%I:%M %p")})

    cur.close(); conn.close()
    return jsonify({"ok": False, "error": "Invalid mode"}), 400


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
        SELECT name, phone, village, family_count, desk, is_walkin, checked_in_at
        FROM checkins ORDER BY checked_in_at DESC LIMIT 25
    """)
    recent = cur.fetchall()
    for r in recent:
        r["checked_in_at"] = r["checked_in_at"].strftime("%I:%M %p")

    cur.close(); conn.close()

    return jsonify({
        "total_checkins": totals["c"],
        "total_people": totals["people"],
        "contributor_checkins": contrib_count,
        "walkin_checkins": walkin_count,
        "recent": recent,
    })


with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
