"""
WorkPulse Cloud Server - deploy ON RENDER (free web service).

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

Security Core (blueprint section 4.4, 11):
  - constant-time API key compare, request payload limit, per-IP rate limit
  - input validation of identity / record structure
  - admin audit trail (every administrative action is recorded, /api/audit)
"""
import os
import re
import ssl as _ssl
import json
import hmac
import time
import threading
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
        _audit_col = _db["audit"]
        MODE = "mongo"
    except Exception as e:
        print("MongoDB unavailable -> other options:", e)


# ------------------------------------------------------------------ mysql / tidb
if MODE == "memory" and MYSQL_HOST:
    try:
        import pymysql
        _tls = threading.local()                   # one pooled connection per worker thread

        def _conn():
            """Pooled per-thread connection: reuse instead of a new TLS/TiDB
            handshake on every call (the #1 cause of slow responses)."""
            kwargs = dict(
                host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
                password=MYSQL_PASSWORD, charset="utf8mb4",
                autocommit=True, database=None,
                connect_timeout=10, read_timeout=60, write_timeout=30,
            )
            if MYSQL_SSL_CA_PATH:                      # strict TLS verification
                kwargs["ssl"] = {"ca": MYSQL_SSL_CA_PATH}
            else:                                      # TLS required by TiDB, but no CA verify
                kwargs["ssl"] = {"cert_reqs": _ssl.CERT_NONE}
            c = getattr(_tls, "conn", None)
            if c is not None:
                try:
                    c.ping(reconnect=True)             # revive silently if dropped
                    return c
                except Exception:
                    try:
                        c.close()
                    except Exception:
                        pass
            _tls.conn = pymysql.connect(**kwargs)
            return _tls.conn

        class _PooledConn:
            """Wraps the pooled connection so existing call sites can still
            c.close() - which becomes a safe no-op (the pool stays alive)."""
            def __init__(self, real):
                self._real = real
            def cursor(self):
                return self._real.cursor()
            def close(self):
                pass                                    # pooled: never really close

        _schema_ready = False

        def _sql(schema_db=True):
            """Return the pooled connection bound to the DB. The schema is
            ensured ONCE (not on every request) and indexes are added for speed."""
            global _schema_ready
            real = _conn()
            if not _schema_ready:
                cur = real.cursor()
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
                # speed indexes: day reads + duplicate-proof insert lookups
                for idx in ("ALTER TABLE records_tbl ADD INDEX idx_ident_day (identity, day)",
                            "ALTER TABLE records_tbl ADD INDEX idx_ident_ts (identity, ts)",
                            "ALTER TABLE status_tbl ADD INDEX idx_last_seen (last_seen)"):
                    try:
                        cur.execute(idx)
                    except Exception:
                        pass                            # index already exists
                _schema_ready = True
            real.select_db(MYSQL_DATABASE)
            return _PooledConn(real)

        # warm-up: fail fast on bad credentials when the service starts
        _conn().close()
        MODE = "mysql"

        DB = {}
        DB["conn"] = _sql

        def m_save_records(ident, rows):
            c = _sql(); cur = c.cursor()
            stored = 0
            for r in rows:
                data = json.dumps(r, ensure_ascii=False)
                # duplicate-proof: skip a record that is already stored byte-identical
                cur.execute(
                    "INSERT INTO records_tbl (identity, day, ts, data) "
                    "SELECT %s, %s, %s, %s WHERE NOT EXISTS ("
                    "  SELECT 1 FROM records_tbl"
                    "  WHERE identity=%s AND ts=%s AND data=%s)",
                    (ident, str(r.get("ts", ""))[:10], str(r.get("ts", ""))[:30], data,
                     ident, str(r.get("ts", ""))[:30], data))
                stored += cur.rowcount > 0
            c.close()
            return stored

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
                cur.execute("SELECT data FROM records_tbl WHERE identity=%s AND day=%s "
                            "ORDER BY ts LIMIT 8000", (ident, day))   # capped: fast responses
            else:
                cur.execute("SELECT data FROM records_tbl WHERE identity=%s "
                            "ORDER BY ts LIMIT 8000", (ident,))
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
    _audit_rows = []
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
            if r.get("kind") == "url":                 # browser link/search records
                doc.update(kind="url", url=str(r.get("url") or "")[:500])
                if r.get("search"):
                    doc["search"] = str(r["search"])[:200]
                else:
                    doc["page"] = str(r.get("page") or "")[:200]
            elif r.get("kind") == "event":             # live "opened" feed records
                doc.update(kind="event", event=str(r.get("event") or "")[:40])
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
            if r.get("kind") == "url":                 # browser link/search records
                doc.update(kind="url", url=str(r.get("url") or "")[:500])
                if r.get("search"):
                    doc["search"] = str(r["search"])[:200]
                else:
                    doc["page"] = str(r.get("page") or "")[:200]
            elif r.get("kind") == "event":             # live "opened" feed records
                doc.update(kind="event", event=str(r.get("event") or "")[:40])
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


# ------------------------------------------------------------------ security core
MAX_UPLOAD_BYTES = 1_048_576            # 1 MB per request
MAX_RECORDS_PER_UPLOAD = 500
IDENT_RE = re.compile(r"^[A-Za-z0-9_@.\-]{1,100}$")
RATE_LIMITS = {"/upload": 240, "default": 120}     # requests per minute per IP

_rl_lock = threading.Lock()
_rl_events: dict = {}                   # ip -> list of request timestamps


def _rate_ok(route: str) -> bool:
    """Sliding-window rate limit per client IP (blueprint 4.4: apply rate limits)."""
    limit = RATE_LIMITS.get(route, RATE_LIMITS["default"])
    ip = (request.headers.get("X-Forwarded-For", "").split(",")[0]
          or request.remote_addr or "unknown").strip()
    now = time.monotonic()
    with _rl_lock:
        hits = [t for t in _rl_events.setdefault(ip, []) if now - t < 60]
        hits.append(now)
        _rl_events[ip] = hits[-2000:]
        if len(hits) > limit:
            return False
    return True


def authorized() -> bool:
    """Constant-time compare - never use == on secrets (timing attacks)."""
    key = request.headers.get("X-API-Key", "")
    return bool(key) and len(key) == len(API_KEY) and hmac.compare_digest(key, API_KEY)


def audit(action: str, target: str = "", result: str = "ok"):
    """Record an administrative action to the audit trail (blueprint 4.6/11)."""
    rec = {"ts": now_iso(), "admin": request.headers.get("X-Admin-Name", "").strip()[:80]
           or "HRAdmin", "action": action, "target": target, "result": result}
    try:
        if MODE == "mysql":
            c = _sql(); cur = c.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS audit_log ("
                        "id INT AUTO_INCREMENT PRIMARY KEY,"
                        "ts VARCHAR(32), admin_name VARCHAR(100),"
                        "action VARCHAR(64), target VARCHAR(150), result VARCHAR(255))")
            cur.execute("INSERT INTO audit_log (ts, admin_name, action, target, result) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        (rec["ts"], rec["admin"], rec["action"], rec["target"], rec["result"]))
            c.close()
        elif MODE == "mongo":
            _audit_col.insert_one(rec)
        else:
            _audit_rows.append(rec)
    except Exception as e:
        print("audit error:", e)
    return rec


# ------------------------------------------------------------------ routes
@app.get("/")
def home():
    return f"""<h2>WorkPulse Cloud Server - RUNNING ({MODE} mode)</h2>
<p>AI Workforce Intelligence &amp; Security - Monitor. Understand. Govern.</p>
<p>Workers (EXE / phone app) POST /upload here with the secret API key.</p>
<p>HRAdmin.exe reads dashboards from /api/... (audit trail: /api/audit)</p>"""


@app.get("/ping")
def ping():
    """Health check: instant, no database touch (fast for monitors)."""
    return jsonify({"ok": True, "mode": MODE, "service": "WorkPulse"})


@app.post("/upload")
def upload():
    if not _rate_ok("/upload"):
        return jsonify({"error": "rate limit - slow down"}), 429
    if not authorized():
        return jsonify({"error": "bad API key"}), 401
    clen = request.content_length or 0
    if clen > MAX_UPLOAD_BYTES:
        return jsonify({"error": "payload too large"}), 413
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid JSON body"}), 400
    st = body.get("status") or {}
    recs = body.get("records") or []
    if not isinstance(st, dict) or not isinstance(recs, list):
        return jsonify({"error": "invalid structure"}), 400
    if len(recs) > MAX_RECORDS_PER_UPLOAD:
        recs = recs[:MAX_RECORDS_PER_UPLOAD]
    ident = (str(body.get("identity") or st.get("identity")
             or f"{st.get('user', 'phone')}@{st.get('device', 'mobile')}").strip())
    if not IDENT_RE.match(ident):
        return jsonify({"error": "invalid identity format"}), 400
    received = save_records(ident, recs)
    upsert_status(ident, st)
    return jsonify({"received": received, "identity": ident})


@app.get("/api/identity_list")
def r_identities():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not _rate_ok("api"):
        return jsonify({"error": "rate limit - slow down"}), 429
    return jsonify({"identities": list_identities()})


@app.get("/api/status")
def r_status():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not _rate_ok("api"):
        return jsonify({"error": "rate limit - slow down"}), 429
    return jsonify({"status": list_status()})


@app.get("/api/status_one")
def r_status_one():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not _rate_ok("api"):
        return jsonify({"error": "rate limit - slow down"}), 429
    return jsonify({"status": get_status(request.args.get("identity", ""))})


@app.get("/api/records")
def r_records():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not _rate_ok("api"):
        return jsonify({"error": "rate limit - slow down"}), 429
    return jsonify({"records": get_records(request.args.get("identity", ""),
                                           request.args.get("day", ""))})


@app.get("/api/is_disabled")
def r_is_disabled():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not _rate_ok("api"):
        return jsonify({"error": "rate limit - slow down"}), 429
    return jsonify({"disabled": get_disabled(request.args.get("identity", ""))})


@app.post("/api/disable")
def r_disable():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not _rate_ok("api"):
        return jsonify({"error": "rate limit - slow down"}), 429
    body = request.get_json(silent=True) or {}
    ident = body.get("identity", "")
    flag = bool(body.get("disabled", False))
    set_disabled(ident, flag)
    audit("user.disabled" if flag else "user.enabled", ident[:150])
    return jsonify({"ok": True, "identity": ident, "disabled": flag})


@app.get("/api/audit")
def r_audit():
    """Security Core: admin audit trail viewer (GET last 100 entries)."""
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not _rate_ok("api"):
        return jsonify({"error": "rate limit - slow down"}), 429
    try:
        if MODE == "mysql":
            c = _sql(); cur = c.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS audit_log ("
                        "id INT AUTO_INCREMENT PRIMARY KEY,"
                        "ts VARCHAR(32), admin_name VARCHAR(100),"
                        "action VARCHAR(64), target VARCHAR(150), result VARCHAR(255))")
            cur.execute("SELECT ts, admin_name, action, target, result "
                        "FROM audit_log ORDER BY id DESC LIMIT 100")
            rows = [{"ts": t, "admin": a, "action": ac, "target": tg, "result": res}
                    for (t, a, ac, tg, res) in cur.fetchall()]
            c.close()
        elif MODE == "mongo":
            rows = [d for d in _audit_col.find({}, {"_id": 0})
                    .sort("_id", -1).limit(100)][::-1]
        else:
            rows = list(reversed(_audit_rows[-100:]))
        return jsonify({"entries": rows})
    except Exception as e:
        return jsonify({"entries": [], "error": f"audit read failed: {type(e).__name__}"})


@app.post("/api/audit")
def w_audit():
    """Security Core: HRAdmin.exe records its own administrative actions here."""
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not _rate_ok("api"):
        return jsonify({"error": "rate limit - slow down"}), 429
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "invalid structure"}), 400
    rec = audit(str(body.get("action", "admin.action"))[:64],
                str(body.get("target", ""))[:150],
                str(body.get("result", "ok"))[:255])
    return jsonify({"ok": True, "logged": rec})


@app.after_request
def no_leak(resp):
    """Don't advertise server internals + clear request evidence in the log."""
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Server"] = "WorkPulse"
    try:
        started = getattr(request, "_wp_start", None)
        if started:
            ms = int((time.time() - started) * 1000)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {request.method} "
                  f"{request.path} -> {resp.status_code} ({ms} ms)", flush=True)
    except Exception:
        pass
    return resp


@app.before_request
def mark_start():
    request._wp_start = time.time()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8596")))
