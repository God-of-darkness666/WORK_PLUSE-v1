"""
HRMonitor Cloud Server - deploy ON RENDER (free web service).

Workers (EXE / phone app) POST /upload here.
The admin  HRAdmin.exe      GETs the data from here.

Storage - picked AUTOMATICALLY from environment variables:
  1. MONGO_URI set                        -> MongoDB (Atlas M0 free)
  2. MYSQL_HOST set                       -> TiDB Cloud / MySQL (pymysql)
     MYSQL_HOST, MYSQL_PORT(=4000), MYSQL_USER, MYSQL_PASSWORD
     optional: MYSQL_SSL_CA_PATH (verify TLS; else TLS used without CA verify)
  3. neither set                          -> memory fallback (demo only:
                                             data is wiped when the service restarts)

Env vars to set on Render (always):
  HR_API_KEY  = secret both sides must know
"""
import os
import ssl as _ssl
import json
from datetime import datetime

from flask import Flask, request, jsonify

API_KEY = os.environ.get("HR_API_KEY", "hr-secret-change-me")
MONGO_URI = os.environ.get("MONGO_URI", "")
MYSQL_HOST = os.environ.get("MYSQL_HOST", "")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "4000"))
MYSQL_USER = os.environ.get("MYSQL_USER", "")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "hrmonitor")
MYSQL_SSL_CA_PATH = os.environ.get("MYSQL_SSL_CA_PATH", "")

app = Flask(__name__)

MODE = "memory"


# ------------------------------------------------------------------ mongo
if MONGO_URI:
    try:
        from pymongo import MongoClient
        _db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)["hrmonitor"]
        _records, _status, _controls = _db["records"], _db["status"], _db["controls"]
        MODE = "mongo"
    except Exception as e:
        print("MongoDB unavailable -> other options:", e)


# ------------------------------------------------------------------ mysql / tidb
if MODE == "memory" and MYSQL_HOST:
    try:
        import pymysql

        def _conn():
            kwargs = dict(
                host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
                password=MYSQL_PASSWORD, charset="utf8mb4",
                autocommit=True, database=None,
            )
            if MYSQL_SSL_CA_PATH:                      # strict TLS verification
                kwargs["ssl"] = {"ca": MYSQL_SSL_CA_PATH}
            else:                                      # TLS required by TiDB, but no CA verify
                kwargs["ssl"] = {"cert_reqs": _ssl.CERT_NONE}
            return pymysql.connect(**kwargs)

        def _sql(filename_db=True):
            """Create db + tables once, return a fresh connection bound to the db."""
            c = _conn()
            cur = c.cursor()
            cur.execute(f"CREATE DATABASE IF NOT EXISTS {MYSQL_DATABASE}")
            cur.execute(f"USE {MYSQL_DATABASE}")
            cur.execute("""CREATE TABLE IF NOT EXISTS status_tbl (
                            identity VARCHAR(150) PRIMARY KEY,
                            data     TEXT NOT NULL,
                            last_seen VARCHAR(40))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS records_tbl (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            identity VARCHAR(150),
                            day VARCHAR(12),
                            ts VARCHAR(30),
                            data TEXT NOT NULL)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS controls_tbl (
                            identity VARCHAR(150) PRIMARY KEY,
                            disabled TINYINT NOT NULL DEFAULT 0)""")
            c.select_db(MYSQL_DATABASE)
            return c

        # warm-up: fail fast on bad credentials when the service starts
        _conn().close()
        MODE = "mysql"

        DB = {}
        DB["conn"] = _sql

        def m_save_records(ident, rows):
            c = _sql(); cur = c.cursor()
            for r in rows:
                cur.execute(
                    "INSERT INTO records_tbl (identity, day, ts, data) VALUES (%s,%s,%s,%s)",
                    (ident, str(r.get("ts", ""))[:10], str(r.get("ts", ""))[:30],
                     json.dumps(r, ensure_ascii=False)))
            c.close()
            return len(rows)

        def m_upsert_status(ident, st):
            c = _sql(); cur = c.cursor()
            cur.execute(
                "REPLACE INTO status_tbl (identity, data, last_seen) VALUES (%s,%s,%s)",
                (ident, json.dumps(st, ensure_ascii=False), st.get("last_seen", now_iso())))
            c.close()

        def m_list_status():
            c = _sql(); cur = c.cursor()
            cur.execute("SELECT data FROM status_tbl")
            out, last = [], {}
            for (data,) in cur.fetchall():
                try:
                    out.append(json.loads(data))
                except Exception:
                    pass
            c.close()
            return out

        def m_get_status(ident):
            c = _sql(); cur = c.cursor()
            cur.execute("SELECT data FROM status_tbl WHERE identity=%s", (ident,))
            row = cur.fetchone()
            c.close()
            return json.loads(row[0]) if row else None

        def m_get_records(ident, day):
            c = _sql(); cur = c.cursor()
            if day:
                cur.execute("SELECT data FROM records_tbl WHERE identity=%s AND day=%s ORDER BY ts",
                            (ident, day))
            else:
                cur.execute("SELECT data FROM records_tbl WHERE identity=%s ORDER BY ts", (ident,))
            out = []
            for (data,) in cur.fetchall():
                try:
                    out.append(json.loads(data))
                except Exception:
                    pass
            c.close()
            return out

        def m_list_identities():
            c = _sql(); cur = c.cursor()
            cur.execute("SELECT identity FROM status_tbl")
            out = sorted(i for (i,) in cur.fetchall())
            c.close()
            return out

        def m_set_disabled(ident, flag):
            c = _sql(); cur = c.cursor()
            cur.execute("REPLACE INTO controls_tbl (identity, disabled) VALUES (%s,%s)",
                        (ident, 1 if flag else 0))
            cur.execute("SELECT data FROM status_tbl WHERE identity=%s", (ident,))
            row = cur.fetchone()
            if row:
                st = json.loads(row[0]); st["enabled"] = not flag
                cur.execute("UPDATE status_tbl SET data=%s WHERE identity=%s",
                            (json.dumps(st, ensure_ascii=False), ident))
            c.close()

        def m_get_disabled(ident):
            c = _sql(); cur = c.cursor()
            cur.execute("SELECT disabled FROM controls_tbl WHERE identity=%s", (ident,))
            row = cur.fetchone()
            c.close()
            return bool(row) and bool(row[0])
    except Exception as e:
        print("MySQL/TiDB unavailable -> memory fallback:", e)


# ------------------------------------------------------------------ memory
if MODE == "memory":
    _records_rows = []
    _status_rows = {}
    _control_rows = {}
    print("WARNING: memory storage - set MONGO_URI or MYSQL_* env vars for persistent data!")


# ------------------------------------------------------------------ shared storage API
def now_iso():
    return datetime.now().isoformat(timespec="seconds")


if MODE == "mongo":
    def save_records(ident, rows):
        for r in rows:
            doc = {"identity": ident, "ts": r.get("ts", now_iso()),
                   "user": r.get("user", ""), "machine": r.get("machine", ""),
                   "exe": r.get("exe", ""), "app": r.get("app", ""),
                   "title": str(r.get("title", ""))[:200], "tab": r.get("tab"),
                   "secs": int(r.get("secs") or 0)}
            doc["day"] = str(doc["ts"])[:10]
            _records.insert_one(doc)
        return len(rows)

    def upsert_status(ident, st):
        doc = dict(st); doc["identity"] = ident
        doc.setdefault("last_seen", now_iso())
        _status.replace_one({"_id": ident}, {**doc, "_id": ident}, upsert=True)

    def list_status():
        return [d for d in _status.find({}, {"_id": 0})]

    def get_status(ident):
        return _status.find_one({"_id": ident}, {"_id": 0})

    def get_records(ident, day):
        q = {"identity": ident}
        if day:
            q["day"] = day
        return [{**d, "_id": str(d["_id"])} for d in _records.find(q).sort("ts", 1).limit(20000)]

    def list_identities():
        return sorted(d["_id"] for d in _status.find({}, {"_id": 1}))

    def set_disabled(ident, flag):
        _controls.replace_one({"_id": ident}, {"_id": ident, "disabled": bool(flag)}, upsert=True)
        _status.update_one({"_id": ident}, {"$set": {"enabled": not flag}})

    def get_disabled(ident):
        d = _controls.find_one({"_id": ident})
        return bool(d and d.get("disabled"))

elif MODE == "mysql":
    save_records = m_save_records
    upsert_status = m_upsert_status
    list_status = m_list_status
    get_status = m_get_status
    get_records = m_get_records
    list_identities = m_list_identities
    set_disabled = m_set_disabled
    get_disabled = m_get_disabled

else:  # memory
    def save_records(ident, rows):
        for r in rows:
            doc = {"identity": ident, "ts": r.get("ts", now_iso()),
                   "user": r.get("user", ""), "machine": r.get("machine", ""),
                   "exe": r.get("exe", ""), "app": r.get("app", ""),
                   "title": str(r.get("title", ""))[:200], "tab": r.get("tab"),
                   "secs": int(r.get("secs") or 0)}
            doc["day"] = str(doc["ts"])[:10]
            _records_rows.append(doc)
        return len(rows)

    def upsert_status(ident, st):
        doc = dict(st)
        doc["identity"] = ident
        doc.setdefault("last_seen", now_iso())
        _status_rows[ident] = doc

    def list_status():
        return list(_status_rows.values())

    def get_status(ident):
        return _status_rows.get(ident)

    def get_records(ident, day):
        return [r for r in _records_rows
                if r["identity"] == ident and (not day or r["day"] == day)]

    def list_identities():
        return sorted(_status_rows.keys())

    def set_disabled(ident, flag):
        _control_rows[ident] = bool(flag)
        st = _status_rows.get(ident)
        if st:
            st["enabled"] = not flag

    def get_disabled(ident):
        return bool(_control_rows.get(ident))


# ------------------------------------------------------------------ helpers
def authorized() -> bool:
    return request.headers.get("X-API-Key", "") == API_KEY


# ------------------------------------------------------------------ routes
@app.get("/")
def home():
    return f"""<h2>HRMonitor Cloud Server - RUNNING ({MODE} mode)</h2>
<p>Workers (EXE / phone app) POST /upload here with the secret API key.</p>
<p>HRAdmin.exe reads dashboards from /api/...</p>"""


@app.get("/ping")
def ping():
    return jsonify({"ok": True, "mode": MODE})


@app.post("/upload")
def upload():
    if not authorized():
        return jsonify({"error": "bad API key"}), 401
    body = request.get_json(silent=True) or {}
    st = body.get("status", {}) or {}
    recs = body.get("records", []) or []
    ident = (body.get("identity")
             or st.get("identity")
             or f"{st.get('user', 'phone')}@{st.get('device', 'mobile')}")
    if not ident:
        return jsonify({"error": "missing identity"}), 400
    received = save_records(ident, recs)
    upsert_status(ident, st)
    return jsonify({"received": received, "identity": ident})


@app.get("/api/identity_list")
def r_identities():
    if not authorized():
        return jsonify({"error": "bad API key"}), 401
    return jsonify({"identities": list_identities()})


@app.get("/api/status")
def r_status():
    if not authorized():
        return jsonify({"error": "bad API key"}), 401
    return jsonify({"status": list_status()})


@app.get("/api/status_one")
def r_status_one():
    if not authorized():
        return jsonify({"error": "bad API key"}), 401
    return jsonify({"status": get_status(request.args.get("identity", ""))})


@app.get("/api/records")
def r_records():
    if not authorized():
        return jsonify({"error": "bad API key"}), 401
    return jsonify({"records": get_records(request.args.get("identity", ""),
                                           request.args.get("day", ""))})


@app.get("/api/is_disabled")
def r_is_disabled():
    if not authorized():
        return jsonify({"error": "bad API key"}), 401
    return jsonify({"disabled": get_disabled(request.args.get("identity", ""))})


@app.post("/api/disable")
def r_disable():
    if not authorized():
        return jsonify({"error": "bad API key"}), 401
    body = request.get_json(silent=True) or {}
    ident = body.get("identity", "")
    set_disabled(ident, bool(body.get("disabled", False)))
    return jsonify({"ok": True, "identity": ident, "disabled": bool(body.get("disabled", False))})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8596")))
