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
import base64
import hashlib
import secrets
import threading
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash

API_KEY = os.environ.get("HR_API_KEY", "hr-secret-change-me")
MONGO_URI = os.environ.get("MONGO_URI", "")
MYSQL_HOST = os.environ.get("MYSQL_HOST", "")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "4000"))
MYSQL_USER = os.environ.get("MYSQL_USER", "")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "hrmonitor")
MYSQL_SSL_CA_PATH = os.environ.get("MYSQL_SSL_CA_PATH", "")

# --- Phase 1 (V3): auth + tenancy -------------------------------------------
JWT_SECRET = os.environ.get("JWT_SECRET") or (API_KEY + ":workpulse-jwt")
ACCESS_TTL = int(os.environ.get("ACCESS_TTL", "1800"))              # 30 min
REFRESH_TTL = int(os.environ.get("REFRESH_TTL", str(30 * 24 * 3600)))
BOOTSTRAP_KEY = os.environ.get("BOOTSTRAP_KEY", API_KEY)
TENANT_SLUG = os.environ.get("TENANT_SLUG", "workpulse")
TENANT_NAME = os.environ.get("TENANT_NAME", "WorkPulse HQ")
APP_VERSION = "3.0.0"
PILOT_TENANT_ID = 1          # replaced at startup by the real row id

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

        def _ensure_v1_schema(cur):
            """Phase 1 tenancy tables. Real data only: the pilot tenant is the
            customer organization that owns the existing records."""
            global PILOT_TENANT_ID
            cur.execute("""CREATE TABLE IF NOT EXISTS tenants (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            slug VARCHAR(60) NOT NULL UNIQUE,
                            name VARCHAR(150) NOT NULL,
                            status VARCHAR(20) NOT NULL DEFAULT 'active',
                            config TEXT NULL,
                            created_at VARCHAR(32))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS admins (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            tenant_id INT NOT NULL,
                            email VARCHAR(190) NOT NULL UNIQUE,
                            password_hash VARCHAR(255) NOT NULL,
                            role VARCHAR(20) NOT NULL DEFAULT 'hr_admin',
                            status VARCHAR(20) NOT NULL DEFAULT 'active',
                            totp_secret VARCHAR(64) NULL,
                            created_at VARCHAR(32),
                            last_login VARCHAR(32),
                            KEY idx_admins_tenant (tenant_id))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS admin_sessions (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            admin_id INT NOT NULL,
                            token_hash CHAR(64) NOT NULL,
                            expires_at VARCHAR(32) NOT NULL,
                            created_at VARCHAR(32),
                            revoked TINYINT NOT NULL DEFAULT 0,
                            KEY idx_sess_hash (token_hash))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS employees (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            tenant_id INT NOT NULL,
                            name VARCHAR(150) NOT NULL,
                            email VARCHAR(190) NULL,
                            department VARCHAR(100) NULL,
                            status VARCHAR(20) NOT NULL DEFAULT 'active',
                            created_at VARCHAR(32),
                            KEY idx_emp_tenant (tenant_id, status))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS devices (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            tenant_id INT NOT NULL,
                            employee_id INT NULL,
                            device_uid VARCHAR(190) NOT NULL,
                            identity VARCHAR(150) NOT NULL,
                            name VARCHAR(150) NULL,
                            os VARCHAR(60) NULL,
                            model VARCHAR(120) NULL,
                            agent_version VARCHAR(40) NULL,
                            last_seen VARCHAR(32) NULL,
                            status VARCHAR(20) NOT NULL DEFAULT 'active',
                            created_at VARCHAR(32),
                            UNIQUE KEY uq_device (tenant_id, device_uid),
                            KEY idx_dev_tenant (tenant_id, status))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS device_tokens (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            device_id INT NOT NULL,
                            token_hash CHAR(64) NOT NULL,
                            prefix VARCHAR(16) NOT NULL,
                            created_at VARCHAR(32),
                            revoked_at VARCHAR(32) NULL,
                            KEY idx_tok_hash (token_hash),
                            KEY idx_tok_device (device_id))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS enrollment_codes (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            tenant_id INT NOT NULL,
                            employee_id INT NULL,
                            code_hash CHAR(64) NOT NULL,
                            prefix VARCHAR(12) NOT NULL,
                            expires_at VARCHAR(32) NOT NULL,
                            used_at VARCHAR(32) NULL,
                            device_id INT NULL,
                            created_at VARCHAR(32),
                            KEY idx_code_hash (code_hash),
                            KEY idx_code_tenant (tenant_id))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS records_v2 (
                            id BIGINT AUTO_INCREMENT PRIMARY KEY,
                            tenant_id INT NOT NULL,
                            identity VARCHAR(150) NOT NULL,
                            day VARCHAR(12) NOT NULL,
                            ts VARCHAR(30) NOT NULL,
                            rec_uid CHAR(64) NOT NULL,
                            data TEXT NOT NULL,
                            UNIQUE KEY uq_rec (tenant_id, rec_uid),
                            KEY idx_rec_day (tenant_id, identity, day),
                            KEY idx_rec_ts (tenant_id, identity, ts))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS status_v2 (
                            tenant_id INT NOT NULL,
                            identity VARCHAR(150) NOT NULL,
                            data TEXT NOT NULL,
                            last_seen VARCHAR(40),
                            PRIMARY KEY (tenant_id, identity))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS controls_v2 (
                            tenant_id INT NOT NULL,
                            identity VARCHAR(150) NOT NULL,
                            disabled TINYINT NOT NULL DEFAULT 0,
                            PRIMARY KEY (tenant_id, identity))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS audit_v2 (
                            id BIGINT AUTO_INCREMENT PRIMARY KEY,
                            tenant_id INT NOT NULL,
                            ts VARCHAR(32),
                            actor_type VARCHAR(20),
                            actor_id VARCHAR(100),
                            action VARCHAR(64),
                            target VARCHAR(150),
                            result VARCHAR(255),
                            prev_hash CHAR(64),
                            row_hash CHAR(64),
                            KEY idx_audit_tenant (tenant_id, id))""")

            # --- pilot tenant = the real organization that owns today's data
            cur.execute("SELECT id FROM tenants WHERE slug=%s", (TENANT_SLUG,))
            row = cur.fetchone()
            if row:
                PILOT_TENANT_ID = int(row[0])
            else:
                cur.execute("INSERT INTO tenants (slug, name, status, created_at) "
                            "VALUES (%s,%s,'active',%s)",
                            (TENANT_SLUG, TENANT_NAME, now_iso()))
                PILOT_TENANT_ID = int(cur.lastrowid)

            # --- one-time, idempotent copy of the real legacy rows
            cur.execute("SELECT COUNT(*) FROM status_v2 WHERE tenant_id=%s",
                        (PILOT_TENANT_ID,))
            if not cur.fetchone()[0]:
                cur.execute("INSERT IGNORE INTO status_v2 "
                            "(tenant_id, identity, data, last_seen) "
                            "SELECT %s, identity, data, last_seen FROM status_tbl",
                            (PILOT_TENANT_ID,))
                cur.execute("INSERT IGNORE INTO controls_v2 "
                            "(tenant_id, identity, disabled) "
                            "SELECT %s, identity, disabled FROM controls_tbl",
                            (PILOT_TENANT_ID,))
            cur.execute("SELECT COUNT(*) FROM records_v2 WHERE tenant_id=%s",
                        (PILOT_TENANT_ID,))
            if not cur.fetchone()[0]:
                cur.execute("INSERT IGNORE INTO records_v2 "
                            "(tenant_id, identity, day, ts, rec_uid, data) "
                            "SELECT %s, identity, day, ts, "
                            "SHA2(CONCAT(identity,'|',ts,'|',data),256), data "
                            "FROM records_tbl", (PILOT_TENANT_ID,))
            cur.execute("SELECT COUNT(*) FROM audit_v2 WHERE tenant_id=%s",
                        (PILOT_TENANT_ID,))
            if not cur.fetchone()[0]:
                cur.execute("INSERT INTO audit_v2 "
                            "(tenant_id, ts, actor_type, actor_id, action, target, "
                            " result, prev_hash, row_hash) "
                            "SELECT %s, ts, 'admin', admin_name, action, target, "
                            " result, '', '' FROM audit_log ORDER BY id",
                            (PILOT_TENANT_ID,))

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
                cur.execute("""CREATE TABLE IF NOT EXISTS audit_log (
                                id INT AUTO_INCREMENT PRIMARY KEY,
                                ts VARCHAR(32),
                                admin_name VARCHAR(100),
                                action VARCHAR(64),
                                target VARCHAR(150),
                                result VARCHAR(255))""")
                # speed indexes: day reads + duplicate-proof insert lookups
                for idx in ("ALTER TABLE records_tbl ADD INDEX idx_ident_day (identity, day)",
                            "ALTER TABLE records_tbl ADD INDEX idx_ident_ts (identity, ts)",
                            "ALTER TABLE status_tbl ADD INDEX idx_last_seen (last_seen)"):
                    try:
                        cur.execute(idx)
                    except Exception:
                        pass                            # index already exists
                _ensure_v1_schema(cur)
                _schema_ready = True
            real.select_db(MYSQL_DATABASE)
            return _PooledConn(real)

        # warm-up: fail fast on bad credentials when the service starts
        _conn().close()
        MODE = "mysql"

        DB = {}
        DB["conn"] = _sql

        def _rec_uid(ident, rec, data):
            """Stable unique id per record: agent-provided uid, else content hash."""
            uid = str(rec.get("uid") or rec.get("rec_uid") or "").strip()
            if uid and len(uid) <= 64:
                return uid
            return hashlib.sha256(
                f"{ident}|{rec.get('ts', '')}|{data}".encode("utf-8")).hexdigest()

        def m_save_records(ident, rows, tenant_id=None):
            tenant_id = tenant_id or PILOT_TENANT_ID
            c = _sql(); cur = c.cursor()
            stored = 0
            for r in rows:
                data = json.dumps(r, ensure_ascii=False)
                uid = _rec_uid(ident, r, data)
                cur.execute(
                    "INSERT IGNORE INTO records_v2 "
                    "(tenant_id, identity, day, ts, rec_uid, data) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (tenant_id, ident, str(r.get("ts", ""))[:10],
                     str(r.get("ts", ""))[:30], uid, data))
                stored += cur.rowcount > 0
            c.close()
            return stored

        def m_upsert_status(ident, st, tenant_id=None):
            tenant_id = tenant_id or PILOT_TENANT_ID
            c = _sql(); cur = c.cursor()
            cur.execute(
                "REPLACE INTO status_v2 (tenant_id, identity, data, last_seen) "
                "VALUES (%s,%s,%s,%s)",
                (tenant_id, ident, json.dumps(st, ensure_ascii=False),
                 st.get("last_seen", now_iso())))
            c.close()

        def m_list_status(tenant_id=None):
            tenant_id = tenant_id or PILOT_TENANT_ID
            c = _sql(); cur = c.cursor()
            cur.execute("SELECT data FROM status_v2 WHERE tenant_id=%s", (tenant_id,))
            out = []
            for (data,) in cur.fetchall():
                try:
                    out.append(json.loads(data))
                except Exception:
                    pass
            c.close()
            return out

        def m_get_status(ident, tenant_id=None):
            tenant_id = tenant_id or PILOT_TENANT_ID
            c = _sql(); cur = c.cursor()
            cur.execute("SELECT data FROM status_v2 WHERE tenant_id=%s AND identity=%s",
                        (tenant_id, ident))
            row = cur.fetchone()
            c.close()
            return json.loads(row[0]) if row else None

        def m_get_records(ident, day, tenant_id=None):
            tenant_id = tenant_id or PILOT_TENANT_ID
            c = _sql(); cur = c.cursor()
            if day:
                cur.execute("SELECT data FROM records_v2 WHERE tenant_id=%s "
                            "AND identity=%s AND day=%s ORDER BY ts LIMIT 8000",
                            (tenant_id, ident, day))   # capped: fast responses
            else:
                cur.execute("SELECT data FROM records_v2 WHERE tenant_id=%s "
                            "AND identity=%s ORDER BY ts LIMIT 8000",
                            (tenant_id, ident))
            out = []
            for (data,) in cur.fetchall():
                try:
                    out.append(json.loads(data))
                except Exception:
                    pass
            c.close()
            return out

        def m_list_identities(tenant_id=None):
            tenant_id = tenant_id or PILOT_TENANT_ID
            c = _sql(); cur = c.cursor()
            cur.execute("SELECT identity FROM status_v2 WHERE tenant_id=%s "
                        "ORDER BY identity", (tenant_id,))
            out = sorted(i for (i,) in cur.fetchall())
            c.close()
            return out

        def m_set_disabled(ident, flag, tenant_id=None):
            tenant_id = tenant_id or PILOT_TENANT_ID
            c = _sql(); cur = c.cursor()
            cur.execute("REPLACE INTO controls_v2 (tenant_id, identity, disabled) "
                        "VALUES (%s,%s,%s)",
                        (tenant_id, ident, 1 if flag else 0))
            cur.execute("SELECT data FROM status_v2 WHERE tenant_id=%s AND identity=%s",
                        (tenant_id, ident))
            row = cur.fetchone()
            if row:
                st = json.loads(row[0]); st["enabled"] = not flag
                cur.execute("UPDATE status_v2 SET data=%s "
                            "WHERE tenant_id=%s AND identity=%s",
                            (json.dumps(st, ensure_ascii=False), tenant_id, ident))
            c.close()

        def m_get_disabled(ident, tenant_id=None):
            tenant_id = tenant_id or PILOT_TENANT_ID
            c = _sql(); cur = c.cursor()
            cur.execute("SELECT disabled FROM controls_v2 "
                        "WHERE tenant_id=%s AND identity=%s", (tenant_id, ident))
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
TZ_OFFSET_MINUTES = int(os.environ.get("TZ_OFFSET_MINUTES", "330"))   # IST


def now_dt():
    """Business clock = tenant timezone (default IST) regardless of host UTC."""
    return datetime.utcnow() + timedelta(minutes=TZ_OFFSET_MINUTES)


def now_iso():
    return now_dt().isoformat(timespec="seconds")


if MODE == "mongo":
    def save_records(ident, rows, tenant_id=None):
        tenant_id = tenant_id or PILOT_TENANT_ID
        for r in rows:
            doc = {"identity": ident, "tenant_id": tenant_id,
                   "ts": r.get("ts", now_iso()),
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

    def upsert_status(ident, st, tenant_id=None):
        tenant_id = tenant_id or PILOT_TENANT_ID
        doc = dict(st); doc["identity"] = ident; doc["tenant_id"] = tenant_id
        doc.setdefault("last_seen", now_iso())
        key = f"{tenant_id}:{ident}"
        _status.replace_one({"_id": key}, {**doc, "_id": key}, upsert=True)

    def list_status(tenant_id=None):
        tenant_id = tenant_id or PILOT_TENANT_ID
        return [d for d in _status.find({"tenant_id": tenant_id}, {"_id": 0})]

    def get_status(ident, tenant_id=None):
        tenant_id = tenant_id or PILOT_TENANT_ID
        return _status.find_one({"_id": f"{tenant_id}:{ident}"}, {"_id": 0})

    def get_records(ident, day, tenant_id=None):
        tenant_id = tenant_id or PILOT_TENANT_ID
        q = {"identity": ident, "tenant_id": tenant_id}
        if day:
            q["day"] = day
        return [{**d, "_id": str(d["_id"])} for d in _records.find(q).sort("ts", 1).limit(20000)]

    def list_identities(tenant_id=None):
        tenant_id = tenant_id or PILOT_TENANT_ID
        return sorted(d["_id"].split(":", 1)[1] for d in
                      _status.find({"tenant_id": tenant_id}, {"_id": 1}))

    def set_disabled(ident, flag, tenant_id=None):
        tenant_id = tenant_id or PILOT_TENANT_ID
        key = f"{tenant_id}:{ident}"
        _controls.replace_one({"_id": key}, {"_id": key, "disabled": bool(flag),
                                            "tenant_id": tenant_id}, upsert=True)
        _status.update_one({"_id": key}, {"$set": {"enabled": not flag}})

    def get_disabled(ident, tenant_id=None):
        tenant_id = tenant_id or PILOT_TENANT_ID
        d = _controls.find_one({"_id": f"{tenant_id}:{ident}"})
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
    def save_records(ident, rows, tenant_id=None):
        tenant_id = tenant_id or PILOT_TENANT_ID
        for r in rows:
            doc = {"identity": ident, "tenant_id": tenant_id,
                   "ts": r.get("ts", now_iso()),
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

    def upsert_status(ident, st, tenant_id=None):
        tenant_id = tenant_id or PILOT_TENANT_ID
        doc = dict(st)
        doc["identity"] = ident
        doc["tenant_id"] = tenant_id
        doc.setdefault("last_seen", now_iso())
        _status_rows[(tenant_id, ident)] = doc

    def list_status(tenant_id=None):
        tenant_id = tenant_id or PILOT_TENANT_ID
        return [v for (t, _), v in _status_rows.items() if t == tenant_id]

    def get_status(ident, tenant_id=None):
        tenant_id = tenant_id or PILOT_TENANT_ID
        return _status_rows.get((tenant_id, ident))

    def get_records(ident, day, tenant_id=None):
        tenant_id = tenant_id or PILOT_TENANT_ID
        return [r for r in _records_rows
                if r["identity"] == ident and r["tenant_id"] == tenant_id
                and (not day or r["day"] == day)]

    def list_identities(tenant_id=None):
        tenant_id = tenant_id or PILOT_TENANT_ID
        return sorted(i for (t, i) in _status_rows if t == tenant_id)

    def set_disabled(ident, flag, tenant_id=None):
        tenant_id = tenant_id or PILOT_TENANT_ID
        _control_rows[(tenant_id, ident)] = bool(flag)
        st = _status_rows.get((tenant_id, ident))
        if st:
            st["enabled"] = not flag

    def get_disabled(ident, tenant_id=None):
        tenant_id = tenant_id or PILOT_TENANT_ID
        return bool(_control_rows.get((tenant_id, ident)))


# ------------------------------------------------------------------ security core
MAX_UPLOAD_BYTES = 1_048_576            # 1 MB per request
MAX_RECORDS_PER_UPLOAD = 500
IDENT_RE = re.compile(r"^[A-Za-z0-9_@.\-]{1,100}$")
RATE_LIMITS = {"/upload": 240, "default": 120}     # requests per minute per IP

_rl_lock = threading.Lock()
_rl_events: dict = {}                   # ip -> list of request timestamps


def _rate_ok_key(key: str, limit: int, window: int = 60) -> bool:
    """Sliding-window rate limit per client IP for any logical bucket."""
    ip = (request.headers.get("X-Forwarded-For", "").split(",")[0]
          or request.remote_addr or "unknown").strip()
    now = time.monotonic()
    bucket = f"{key}:{ip}"
    with _rl_lock:
        hits = [t for t in _rl_events.setdefault(bucket, []) if now - t < window]
        hits.append(now)
        _rl_events[bucket] = hits[-2000:]
        if len(hits) > limit:
            return False
    return True


def _rate_ok(route: str) -> bool:
    limit = RATE_LIMITS.get(route, RATE_LIMITS["default"])
    return _rate_ok_key(route, limit)


def authorized() -> bool:
    """Constant-time compare - never use == on secrets (timing attacks)."""
    key = request.headers.get("X-API-Key", "")
    return bool(key) and len(key) == len(API_KEY) and hmac.compare_digest(key, API_KEY)


def audit(action: str, target: str = "", result: str = "ok", tenant_id=None,
          actor_type: str = "admin", actor_id: str = ""):
    """Append-only, hash-chained audit trail (blueprint 4.6/11)."""
    tenant_id = tenant_id or PILOT_TENANT_ID
    actor_id = (actor_id or request.headers.get("X-Admin-Name", "").strip()[:80]
                or "HRAdmin")
    rec = {"ts": now_iso(), "tenant_id": tenant_id, "actor_type": actor_type,
           "actor_id": actor_id, "action": action, "target": target,
           "result": result}
    try:
        if MODE == "mysql":
            c = _sql(); cur = c.cursor()
            cur.execute("SELECT row_hash FROM audit_v2 WHERE tenant_id=%s "
                        "ORDER BY id DESC LIMIT 1", (tenant_id,))
            row = cur.fetchone()
            prev = (row[0] or "") if row else ""
            row_hash = hashlib.sha256(
                f"{prev}|{tenant_id}|{rec['ts']}|{actor_type}|{actor_id}|"
                f"{action}|{target}|{result}".encode("utf-8")).hexdigest()
            cur.execute("INSERT INTO audit_v2 (tenant_id, ts, actor_type, actor_id, "
                        "action, target, result, prev_hash, row_hash) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (tenant_id, rec["ts"], actor_type, actor_id, action,
                         target, result, prev, row_hash))
            c.close()
            rec["row_hash"] = row_hash
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
    return jsonify({"ok": True, "mode": MODE, "service": "WorkPulse",
                    "version": APP_VERSION})


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
    received = save_records(ident, recs, PILOT_TENANT_ID)
    upsert_status(ident, st, PILOT_TENANT_ID)
    return jsonify({"received": received, "identity": ident})


@app.get("/api/identity_list")
def r_identities():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not _rate_ok("api"):
        return jsonify({"error": "rate limit - slow down"}), 429
    return jsonify({"identities": list_identities(PILOT_TENANT_ID)})


@app.get("/api/status")
def r_status():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not _rate_ok("api"):
        return jsonify({"error": "rate limit - slow down"}), 429
    return jsonify({"status": list_status(PILOT_TENANT_ID)})


@app.get("/api/status_one")
def r_status_one():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not _rate_ok("api"):
        return jsonify({"error": "rate limit - slow down"}), 429
    return jsonify({"status": get_status(request.args.get("identity", ""),
                                         PILOT_TENANT_ID)})


@app.get("/api/records")
def r_records():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not _rate_ok("api"):
        return jsonify({"error": "rate limit - slow down"}), 429
    return jsonify({"records": get_records(request.args.get("identity", ""),
                                           request.args.get("day", ""),
                                           PILOT_TENANT_ID)})


@app.get("/api/is_disabled")
def r_is_disabled():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not _rate_ok("api"):
        return jsonify({"error": "rate limit - slow down"}), 429
    return jsonify({"disabled": get_disabled(request.args.get("identity", ""),
                                             PILOT_TENANT_ID)})


@app.post("/api/disable")
def r_disable():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not _rate_ok("api"):
        return jsonify({"error": "rate limit - slow down"}), 429
    body = request.get_json(silent=True) or {}
    ident = body.get("identity", "")
    flag = bool(body.get("disabled", False))
    set_disabled(ident, flag, PILOT_TENANT_ID)
    audit("user.disabled" if flag else "user.enabled", ident[:150],
          tenant_id=PILOT_TENANT_ID)
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
            cur.execute("SELECT ts, actor_id, action, target, result, row_hash "
                        "FROM audit_v2 WHERE tenant_id=%s ORDER BY id DESC LIMIT 100",
                        (PILOT_TENANT_ID,))
            rows = [{"ts": t, "admin": a, "action": ac, "target": tg,
                     "result": res, "hash": rh}
                    for (t, a, ac, tg, res, rh) in cur.fetchall()]
            c.close()
        elif MODE == "mongo":
            rows = [d for d in _audit_col.find({"tenant_id": PILOT_TENANT_ID},
                                               {"_id": 0})
                    .sort("_id", -1).limit(100)][::-1]
        else:
            rows = [r for r in reversed(_audit_rows[-100:])
                    if r.get("tenant_id", PILOT_TENANT_ID) == PILOT_TENANT_ID]
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
                str(body.get("result", "ok"))[:255],
                tenant_id=PILOT_TENANT_ID)
    return jsonify({"ok": True, "logged": rec})


# ============================================================================
# PHASE 1 (V3) - AUTH, TENANTS, DEVICES, ENROLLMENT
# Real data only. Legacy endpoints above keep working (pilot tenant).
# ============================================================================
EMAIL_RE = re.compile(r"^[^@\s]{1,120}@[^@\s]{1,120}\.[A-Za-z]{2,}$")


def json_err(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_access(admin: dict):
    now = int(time.time())
    payload = {"sub": int(admin["id"]), "tid": int(admin["tenant_id"]),
               "role": admin.get("role", "hr_admin"), "typ": "access",
               "iat": now, "exp": now + ACCESS_TTL}
    head = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"},
                            separators=(",", ":")).encode())
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(JWT_SECRET.encode(), f"{head}.{body}".encode(),
                   hashlib.sha256).digest()
    return f"{head}.{body}.{_b64e(sig)}"


def read_access(token: str):
    try:
        head, body, sig = token.split(".")
        expect = hmac.new(JWT_SECRET.encode(), f"{head}.{body}".encode(),
                          hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(sig), expect):
            return None
        payload = json.loads(_b64d(body))
        if payload.get("typ") != "access":
            return None
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def mysql_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if MODE != "mysql":
            return json_err("storage_unavailable",
                            "auth and tenancy require MySQL/TiDB", 503)
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        payload = read_access(token)
        if not payload:
            return json_err("unauthorized", "missing or expired token", 401)
        if payload.get("role") == "holder":
            return json_err("forbidden", "holder scope cannot read tenant data", 403)
        payload["tenant_id"] = int(payload.get("tid", 0) or 0)
        if payload["tenant_id"] <= 0:
            return json_err("unauthorized", "invalid tenant", 401)
        g.admin = payload
        return fn(*args, **kwargs)
    return wrapper


def device_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-Device-Token", "").strip()
        if not token:
            return json_err("unauthorized", "missing device token", 401)
        c = _sql(); cur = c.cursor()
        cur.execute("SELECT d.id, d.tenant_id, d.employee_id, d.identity, d.status, "
                    "dt.id FROM device_tokens dt JOIN devices d ON d.id = dt.device_id "
                    "WHERE dt.token_hash=%s AND dt.revoked_at IS NULL "
                    "ORDER BY dt.id DESC LIMIT 1", (_sha(token),))
        row = cur.fetchone()
        c.close()
        if not row or row[4] != "active":
            return json_err("unauthorized", "device revoked or unknown", 401)
        g.device = {"id": int(row[0]), "tenant_id": int(row[1]),
                    "employee_id": row[2], "identity": row[3]}
        return fn(*args, **kwargs)
    return wrapper


def _future_iso(seconds: int) -> str:
    return (now_dt() + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _tenant_row(tenant_id: int):
    c = _sql(); cur = c.cursor()
    cur.execute("SELECT id, slug, name, status, config FROM tenants WHERE id=%s",
                (tenant_id,))
    row = cur.fetchone()
    c.close()
    if not row:
        return None
    return {"id": int(row[0]), "slug": row[1], "name": row[2],
            "status": row[3], "config": row[4]}


def _issue_refresh(admin_id: int):
    raw = secrets.token_urlsafe(48)
    expires = _future_iso(REFRESH_TTL)
    c = _sql(); cur = c.cursor()
    cur.execute("INSERT INTO admin_sessions (admin_id, token_hash, expires_at, created_at) "
                "VALUES (%s,%s,%s,%s)",
                (admin_id, _sha(raw), expires, now_iso()))
    c.close()
    return raw, expires


@app.get("/api/v1/auth/status")
def v1_auth_status():
    """Setup probe: has the first admin been created?"""
    initialized = False
    if MODE == "mysql":
        try:
            c = _sql(); cur = c.cursor()
            cur.execute("SELECT COUNT(*) FROM admins")
            initialized = cur.fetchone()[0] > 0
            c.close()
        except Exception:
            initialized = False
    return jsonify({"initialized": initialized, "version": APP_VERSION,
                    "mode": MODE, "service": "WorkPulse"})


@app.post("/api/v1/auth/bootstrap")
@mysql_required
def v1_bootstrap():
    """Create the first admin. Allowed only once, and only with the bootstrap key."""
    if not _rate_ok_key("bootstrap", 5, 300):
        return json_err("rate_limited", "too many attempts", 429)
    key = request.headers.get("X-Bootstrap-Key", "")
    if not (key and hmac.compare_digest(key, BOOTSTRAP_KEY)):
        return json_err("forbidden", "bad bootstrap key", 403)
    body = request.get_json(silent=True) or {}
    org = str(body.get("org_name") or TENANT_NAME).strip()[:150]
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    if not EMAIL_RE.match(email):
        return json_err("invalid_email", "enter a valid email", 400)
    if len(password) < 10:
        return json_err("weak_password", "password must be at least 10 characters", 400)
    c = _sql(); cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM admins")
    if cur.fetchone()[0] > 0:
        c.close()
        return json_err("already_initialized",
                        "an admin already exists - use the login endpoint", 409)
    cur.execute("SELECT id FROM tenants WHERE slug=%s", (TENANT_SLUG,))
    row = cur.fetchone()
    if row:
        tenant_id = int(row[0])
        cur.execute("UPDATE tenants SET name=%s WHERE id=%s", (org, tenant_id))
    else:
        cur.execute("INSERT INTO tenants (slug, name, status, created_at) "
                    "VALUES (%s,%s,'active',%s)", (TENANT_SLUG, org, now_iso()))
        tenant_id = int(cur.lastrowid)
    cur.execute("INSERT INTO admins (tenant_id, email, password_hash, role, status, "
                "created_at) VALUES (%s,%s,%s,'owner','active',%s)",
                (tenant_id, email, generate_password_hash(password), now_iso()))
    c.close()
    audit("auth.bootstrap", email, "ok", tenant_id=tenant_id,
          actor_type="system", actor_id="bootstrap")
    return jsonify({"ok": True, "tenant_id": tenant_id, "email": email}), 201


@app.post("/api/v1/auth/login")
@mysql_required
def v1_login():
    if not _rate_ok_key("login", 10, 900):
        return json_err("rate_limited", "too many attempts, wait 15 minutes", 429)
    body = request.get_json(silent=True) or {}
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    c = _sql(); cur = c.cursor()
    cur.execute("SELECT id, tenant_id, email, password_hash, role, status "
                "FROM admins WHERE email=%s", (email,))
    row = cur.fetchone()
    if not row or row[5] != "active" or not check_password_hash(row[3], password):
        c.close()
        audit("auth.login.failed", email[:150], "bad credentials",
              actor_type="admin", actor_id=email[:100])
        return json_err("invalid_credentials", "email or password is incorrect", 401)
    admin = {"id": int(row[0]), "tenant_id": int(row[1]), "email": row[2],
             "role": row[4]}
    cur.execute("UPDATE admins SET last_login=%s WHERE id=%s",
                (now_iso(), admin["id"]))
    c.close()
    access = make_access(admin)
    refresh, refresh_exp = _issue_refresh(admin["id"])
    tenant = _tenant_row(admin["tenant_id"])
    audit("auth.login", email, "ok", tenant_id=admin["tenant_id"],
          actor_id=email)
    return jsonify({"access_token": access, "refresh_token": refresh,
                    "token_type": "Bearer", "expires_in": ACCESS_TTL,
                    "refresh_expires_at": refresh_exp,
                    "admin": admin, "tenant": tenant})


@app.post("/api/v1/auth/refresh")
@mysql_required
def v1_refresh():
    if not _rate_ok_key("refresh", 30, 900):
        return json_err("rate_limited", "too many attempts", 429)
    body = request.get_json(silent=True) or {}
    raw = str(body.get("refresh_token") or "").strip()
    if not raw:
        return json_err("unauthorized", "refresh token required", 401)
    c = _sql(); cur = c.cursor()
    cur.execute("SELECT s.id, s.admin_id, s.expires_at, s.revoked, a.status, "
                "a.tenant_id, a.email, a.role FROM admin_sessions s "
                "JOIN admins a ON a.id = s.admin_id WHERE s.token_hash=%s",
                (_sha(raw),))
    row = cur.fetchone()
    if (not row or row[3] or row[4] != "active"
            or str(row[2]) < now_iso()):
        c.close()
        return json_err("unauthorized", "refresh token invalid or expired", 401)
    cur.execute("UPDATE admin_sessions SET revoked=1 WHERE id=%s", (row[0],))
    c.close()
    admin = {"id": int(row[1]), "tenant_id": int(row[5]),
             "email": row[6], "role": row[7]}
    access = make_access(admin)
    refresh, refresh_exp = _issue_refresh(admin["id"])
    return jsonify({"access_token": access, "refresh_token": refresh,
                    "token_type": "Bearer", "expires_in": ACCESS_TTL,
                    "refresh_expires_at": refresh_exp, "admin": admin,
                    "tenant": _tenant_row(admin["tenant_id"])})


@app.post("/api/v1/auth/logout")
@mysql_required
def v1_logout():
    body = request.get_json(silent=True) or {}
    raw = str(body.get("refresh_token") or "").strip()
    if raw:
        c = _sql(); cur = c.cursor()
        cur.execute("UPDATE admin_sessions SET revoked=1 WHERE token_hash=%s",
                    (_sha(raw),))
        c.close()
    return jsonify({"ok": True})


@app.get("/api/v1/auth/me")
@mysql_required
@admin_required
def v1_me():
    tid = g.admin["tenant_id"]
    c = _sql(); cur = c.cursor()
    cur.execute("SELECT id, email, role, status, created_at, last_login "
                "FROM admins WHERE id=%s AND tenant_id=%s",
                (g.admin["sub"], tid))
    row = cur.fetchone()
    c.close()
    if not row:
        return json_err("not_found", "admin not found", 404)
    return jsonify({"admin": {"id": int(row[0]), "email": row[1], "role": row[2],
                              "status": row[3], "created_at": row[4],
                              "last_login": row[5]},
                    "tenant": _tenant_row(tid)})


@app.post("/api/v1/auth/change-password")
@mysql_required
@admin_required
def v1_change_password():
    body = request.get_json(silent=True) or {}
    old = str(body.get("old_password") or "")
    new = str(body.get("new_password") or "")
    if len(new) < 10:
        return json_err("weak_password", "new password must be at least 10 characters", 400)
    c = _sql(); cur = c.cursor()
    cur.execute("SELECT password_hash FROM admins WHERE id=%s AND tenant_id=%s",
                (g.admin["sub"], g.admin["tenant_id"]))
    row = cur.fetchone()
    if not row or not check_password_hash(row[0], old):
        c.close()
        return json_err("invalid_credentials", "current password is incorrect", 401)
    cur.execute("UPDATE admins SET password_hash=%s WHERE id=%s",
                (generate_password_hash(new), g.admin["sub"]))
    cur.execute("UPDATE admin_sessions SET revoked=1 WHERE admin_id=%s",
                (g.admin["sub"],))
    c.close()
    audit("auth.password_changed", g.admin.get("email", ""), "ok")
    return jsonify({"ok": True, "message": "password changed - log in again"})


# ---------------------------------------------------------------- admin data
@app.get("/api/v1/admin/dashboard")
@mysql_required
@admin_required
def v1_dashboard():
    tid = g.admin["tenant_id"]
    cutoff = (now_dt() - timedelta(minutes=5)).isoformat(timespec="seconds")
    c = _sql(); cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM employees WHERE tenant_id=%s AND status='active'",
                (tid,))
    employees = int(cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM devices WHERE tenant_id=%s AND status='active'",
                (tid,))
    devices = int(cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM devices WHERE tenant_id=%s AND status='active' "
                "AND last_seen >= %s", (tid, cutoff))
    online = int(cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM status_v2 WHERE tenant_id=%s", (tid,))
    reporting = int(cur.fetchone()[0])
    c.close()
    return jsonify({"employees": employees, "devices": devices,
                    "online": online, "offline": max(devices - online, 0),
                    "reporting": reporting,
                    "alerts": 0, "agent_version": APP_VERSION,
                    "server_time": now_iso()})


@app.get("/api/v1/admin/employees")
@mysql_required
@admin_required
def v1_employees():
    tid = g.admin["tenant_id"]
    q = str(request.args.get("q") or "").strip()
    c = _sql(); cur = c.cursor()
    if q:
        like = f"%{q}%"
        cur.execute("SELECT id, name, email, department, status, created_at "
                    "FROM employees WHERE tenant_id=%s AND (name LIKE %s OR email LIKE %s) "
                    "ORDER BY id DESC LIMIT 500", (tid, like, like))
    else:
        cur.execute("SELECT id, name, email, department, status, created_at "
                    "FROM employees WHERE tenant_id=%s ORDER BY id DESC LIMIT 500",
                    (tid,))
    rows = [{"id": int(r[0]), "name": r[1], "email": r[2], "department": r[3],
             "status": r[4], "created_at": r[5]} for r in cur.fetchall()]
    c.close()
    return jsonify({"employees": rows, "count": len(rows)})


@app.post("/api/v1/admin/employees")
@mysql_required
@admin_required
def v1_employee_create():
    tid = g.admin["tenant_id"]
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()[:150]
    email = str(body.get("email") or "").strip().lower()[:190]
    department = str(body.get("department") or "").strip()[:100]
    if not name:
        return json_err("invalid_name", "employee name is required", 400)
    if email and not EMAIL_RE.match(email):
        return json_err("invalid_email", "enter a valid email", 400)
    c = _sql(); cur = c.cursor()
    cur.execute("INSERT INTO employees (tenant_id, name, email, department, status, "
                "created_at) VALUES (%s,%s,%s,%s,'active',%s)",
                (tid, name, email or None, department or None, now_iso()))
    emp_id = int(cur.lastrowid)
    c.close()
    audit("employee.created", f"{emp_id}:{name}", "ok")
    return jsonify({"ok": True, "employee": {"id": emp_id, "name": name,
                                             "email": email or None,
                                             "department": department or None,
                                             "status": "active"}}), 201


@app.patch("/api/v1/admin/employees/<int:emp_id>")
@mysql_required
@admin_required
def v1_employee_update(emp_id: int):
    tid = g.admin["tenant_id"]
    body = request.get_json(silent=True) or {}
    fields, values = [], []
    for key, col, limit in (("name", "name", 150), ("email", "email", 190),
                            ("department", "department", 100),
                            ("status", "status", 20)):
        if key in body:
            fields.append(f"{col}=%s")
            values.append(str(body.get(key) or "").strip()[:limit] or None)
    if not fields:
        return json_err("no_changes", "nothing to update", 400)
    values.extend([tid, emp_id])
    c = _sql(); cur = c.cursor()
    cur.execute(f"UPDATE employees SET {', '.join(fields)} "
                f"WHERE tenant_id=%s AND id=%s", tuple(values))
    changed = cur.rowcount
    c.close()
    if not changed:
        return json_err("not_found", "employee not found in this tenant", 404)
    audit("employee.updated", str(emp_id), "ok")
    return jsonify({"ok": True, "id": emp_id})


@app.get("/api/v1/admin/devices")
@mysql_required
@admin_required
def v1_devices():
    tid = g.admin["tenant_id"]
    c = _sql(); cur = c.cursor()
    cur.execute("SELECT d.id, d.device_uid, d.identity, d.name, d.os, d.model, "
                "d.agent_version, d.last_seen, d.status, e.name AS employee "
                "FROM devices d LEFT JOIN employees e ON e.id = d.employee_id "
                "WHERE d.tenant_id=%s ORDER BY d.id DESC LIMIT 500", (tid,))
    rows = [{"id": int(r[0]), "device_uid": r[1], "identity": r[2],
             "name": r[3], "os": r[4], "model": r[5],
             "agent_version": r[6], "last_seen": r[7], "status": r[8],
             "employee": r[9]} for r in cur.fetchall()]
    c.close()
    return jsonify({"devices": rows, "count": len(rows)})


@app.post("/api/v1/admin/devices/<int:device_id>/revoke")
@mysql_required
@admin_required
def v1_device_revoke(device_id: int):
    tid = g.admin["tenant_id"]
    c = _sql(); cur = c.cursor()
    cur.execute("SELECT identity FROM devices WHERE tenant_id=%s AND id=%s",
                (tid, device_id))
    row = cur.fetchone()
    if not row:
        c.close()
        return json_err("not_found", "device not found in this tenant", 404)
    cur.execute("UPDATE devices SET status='revoked' WHERE tenant_id=%s AND id=%s",
                (tid, device_id))
    cur.execute("UPDATE device_tokens SET revoked_at=%s WHERE device_id=%s",
                (now_iso(), device_id))
    c.close()
    audit("device.revoked", str(device_id), "ok")
    return jsonify({"ok": True, "device_id": device_id, "identity": row[0]})


@app.post("/api/v1/admin/enrollment-codes")
@mysql_required
@admin_required
def v1_enrollment_code():
    tid = g.admin["tenant_id"]
    body = request.get_json(silent=True) or {}
    employee_id = body.get("employee_id")
    try:
        minutes = int(body.get("expires_minutes") or 15)
    except Exception:
        minutes = 15
    minutes = max(5, min(minutes, 1440))
    c = _sql(); cur = c.cursor()
    if employee_id not in (None, ""):
        cur.execute("SELECT id FROM employees WHERE tenant_id=%s AND id=%s",
                    (tid, employee_id))
        if not cur.fetchone():
            c.close()
            return json_err("not_found", "employee not found in this tenant", 404)
        employee_id = int(employee_id)
    else:
        employee_id = None
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(8))
    cur.execute("INSERT INTO enrollment_codes (tenant_id, employee_id, code_hash, "
                "prefix, expires_at, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                (tid, employee_id, _sha(code), code[:2] + "******",
                 _future_iso(minutes * 60), now_iso()))
    c.close()
    audit("enrollment.code_created", (code[:2] + "******"), "ok")
    return jsonify({"ok": True, "code": code, "employee_id": employee_id,
                    "expires_in_seconds": minutes * 60}), 201


@app.get("/api/v1/admin/identities")
@mysql_required
@admin_required
def v1_identities():
    return jsonify({"identities": list_identities(g.admin["tenant_id"])})


@app.get("/api/v1/admin/activity")
@mysql_required
@admin_required
def v1_activity():
    identity = str(request.args.get("identity") or "").strip()
    day = str(request.args.get("day") or "").strip()
    if not identity:
        return json_err("invalid_identity", "identity is required", 400)
    return jsonify({"identity": identity, "day": day,
                    "records": get_records(identity, day,
                                           g.admin["tenant_id"])})


@app.get("/api/v1/admin/status")
@mysql_required
@admin_required
def v1_status_all():
    return jsonify({"status": list_status(g.admin["tenant_id"])})


@app.get("/api/v1/admin/status_one")
@mysql_required
@admin_required
def v1_status_one():
    identity = str(request.args.get("identity") or "").strip()
    if not identity:
        return json_err("invalid_identity", "identity is required", 400)
    return jsonify({"status": get_status(identity, g.admin["tenant_id"])})


@app.post("/api/v1/admin/identities/<path:identity>/disable")
@mysql_required
@admin_required
def v1_identity_disable(identity: str):
    """Per-worker kill switch. Scoped to the caller's tenant only."""
    identity = identity.strip()[:150]
    if not identity:
        return json_err("invalid_identity", "identity is required", 400)
    body = request.get_json(silent=True) or {}
    flag = bool(body.get("disabled", True))
    tid = g.admin["tenant_id"]
    known = identity in set(list_identities(tid))
    if not known:
        return json_err("not_found", "identity not found in this tenant", 404)
    set_disabled(identity, flag, tid)
    audit("user.disabled" if flag else "user.enabled", identity, "ok")
    return jsonify({"ok": True, "identity": identity, "disabled": flag})


@app.get("/api/v1/admin/audit")
@mysql_required
@admin_required
def v1_audit():
    tid = g.admin["tenant_id"]
    c = _sql(); cur = c.cursor()
    cur.execute("SELECT ts, actor_type, actor_id, action, target, result, row_hash "
                "FROM audit_v2 WHERE tenant_id=%s ORDER BY id DESC LIMIT 200", (tid,))
    rows = [{"ts": r[0], "actor_type": r[1], "actor": r[2], "action": r[3],
             "target": r[4], "result": r[5], "hash": r[6]}
            for r in cur.fetchall()]
    c.close()
    return jsonify({"entries": rows, "count": len(rows)})


@app.post("/api/v1/admin/audit")
@mysql_required
@admin_required
def v1_audit_write():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return json_err("invalid_structure", "invalid JSON body", 400)
    rec = audit(str(body.get("action", "admin.action"))[:64],
                str(body.get("target", ""))[:150],
                str(body.get("result", "ok"))[:255])
    return jsonify({"ok": True, "logged": rec})


@app.get("/api/v1/admin/license")
@mysql_required
@admin_required
def v1_license():
    tenant = _tenant_row(g.admin["tenant_id"]) or {}
    return jsonify({"status": tenant.get("status", "active"),
                    "plan": "pilot", "seats": None, "expires_at": None,
                    "tenant": tenant.get("name")})


# ---------------------------------------------------------------- agent API v3
@app.post("/api/v1/devices/enroll")
@mysql_required
def v1_enroll():
    if not _rate_ok_key("enroll", 30, 60):
        return json_err("rate_limited", "too many enrollment attempts", 429)
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "").strip().upper()
    device_uid = str(body.get("device_uid") or "").strip()[:190]
    if len(code) != 8 or not device_uid:
        return json_err("invalid_request", "code and device_uid are required", 400)
    c = _sql(); cur = c.cursor()
    cur.execute("SELECT id, tenant_id, employee_id, expires_at, used_at "
                "FROM enrollment_codes WHERE code_hash=%s", (_sha(code),))
    row = cur.fetchone()
    if not row or row[4] or str(row[3]) < now_iso():
        c.close()
        return json_err("invalid_code", "enrollment code is invalid or expired", 401)
    code_id, tid, employee_id = int(row[0]), int(row[1]), row[2]
    cur.execute("UPDATE enrollment_codes SET used_at=%s "
                "WHERE id=%s AND used_at IS NULL", (now_iso(), code_id))
    if cur.rowcount == 0:
        c.close()
        return json_err("invalid_code", "enrollment code already used", 401)
    identity = ""
    if employee_id:
        cur.execute("SELECT email, name FROM employees WHERE tenant_id=%s AND id=%s",
                    (tid, employee_id))
        emp = cur.fetchone()
        if emp:
            identity = (emp[0] or "").strip()
            if not identity:
                identity = f"employee{int(employee_id)}@workpulse"
    if not identity:
        safe_uid = re.sub(r"[^A-Za-z0-9_.-]", "", device_uid)[:60] or "device"
        identity = f"device.{safe_uid}@workpulse"
    # Pilot continuity: an agent already reporting under a legacy identity
    # (user@machine) may keep it, but only when that identity belongs to this
    # tenant, exists in real history, and is not owned by another live device.
    preferred = str(body.get("identity") or "").strip()[:150]
    claimed = ""
    if (not employee_id and preferred and IDENT_RE.match(preferred)
            and preferred != identity):
        cur.execute("SELECT COUNT(*) FROM devices WHERE tenant_id=%s AND identity=%s "
                    "AND status='active'", (tid, preferred))
        owned = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM status_v2 WHERE tenant_id=%s AND identity=%s",
                    (tid, preferred))
        in_history = cur.fetchone()[0]
        if not owned and in_history:
            claimed = identity
            identity = preferred
    identity = identity[:150]
    name = str(body.get("name") or "").strip()[:150] or device_uid
    os_name = str(body.get("os") or "").strip()[:60]
    model = str(body.get("model") or "").strip()[:120]
    version = str(body.get("version") or "").strip()[:40]
    cur.execute("SELECT id FROM devices WHERE tenant_id=%s AND device_uid=%s",
                (tid, device_uid))
    existing = cur.fetchone()
    if existing:
        device_id = int(existing[0])
        cur.execute("UPDATE devices SET employee_id=%s, identity=%s, name=%s, os=%s, "
                    "model=%s, agent_version=%s, last_seen=%s, status='active' "
                    "WHERE id=%s",
                    (employee_id, identity, name, os_name, model, version,
                     now_iso(), device_id))
    else:
        cur.execute("INSERT INTO devices (tenant_id, employee_id, device_uid, identity, "
                    "name, os, model, agent_version, last_seen, status, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s)",
                    (tid, employee_id, device_uid, identity, name, os_name, model,
                     version, now_iso(), now_iso()))
        device_id = int(cur.lastrowid)
    token = "wp_" + secrets.token_urlsafe(32)
    cur.execute("INSERT INTO device_tokens (device_id, token_hash, prefix, created_at) "
                "VALUES (%s,%s,%s,%s)",
                (device_id, _sha(token), token[:10], now_iso()))
    cur.execute("UPDATE enrollment_codes SET device_id=%s WHERE id=%s",
                (device_id, code_id))
    c.close()
    audit("device.enrolled", f"{device_id}:{device_uid}", "ok", tenant_id=tid,
          actor_type="device", actor_id=device_uid)
    if claimed:
        audit("identity.claimed", f"{claimed} -> {identity}", "ok", tenant_id=tid,
              actor_type="device", actor_id=device_uid)
    return jsonify({"ok": True, "device_id": device_id, "identity": identity,
                    "device_token": token,
                    "server_time": now_iso()}), 201


@app.post("/api/v1/agent/activity")
@mysql_required
@device_required
def v1_agent_activity():
    if not _rate_ok_key("agent", 240, 60):
        return json_err("rate_limited", "slow down", 429)
    clen = request.content_length or 0
    if clen > MAX_UPLOAD_BYTES:
        return json_err("too_large", "payload too large", 413)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return json_err("invalid_json", "invalid JSON body", 400)
    recs = body.get("records") or body.get("events") or []
    st = body.get("status") or {}
    if not isinstance(recs, list) or not isinstance(st, dict):
        return json_err("invalid_structure", "records must be a list", 400)
    recs = recs[:MAX_RECORDS_PER_UPLOAD]
    tenant_id = g.device["tenant_id"]
    identity = g.device["identity"]
    disabled = get_disabled(identity, tenant_id)
    if disabled:
        # kill switch is server-side too: a tampered agent cannot keep reporting
        return jsonify({"received": 0, "identity": identity, "disabled": True,
                        "server_time": now_iso()})
    received = save_records(identity, recs, tenant_id)
    old = get_status(identity, tenant_id) or {}
    merged = {**old, **st}
    merged["identity"] = identity
    merged.setdefault("last_seen", now_iso())
    upsert_status(identity, merged, tenant_id)
    c = _sql(); cur = c.cursor()
    cur.execute("UPDATE devices SET last_seen=%s, agent_version=%s "
                "WHERE id=%s AND tenant_id=%s",
                (now_iso(), str(body.get("version") or "")[:40] or None,
                 g.device["id"], tenant_id))
    c.close()
    return jsonify({"received": received, "identity": identity,
                    "server_time": now_iso()})


@app.post("/api/v1/agent/heartbeat")
@mysql_required
@device_required
def v1_agent_heartbeat():
    body = request.get_json(silent=True) or {}
    tenant_id = g.device["tenant_id"]
    c = _sql(); cur = c.cursor()
    cur.execute("UPDATE devices SET last_seen=%s, agent_version=%s "
                "WHERE id=%s AND tenant_id=%s",
                (now_iso(), str(body.get("version") or "")[:40] or None,
                 g.device["id"], tenant_id))
    c.close()
    st = get_status(g.device["identity"], tenant_id) or {
        "identity": g.device["identity"]}
    st["last_seen"] = now_iso()
    st["queue_depth"] = int(body.get("queue_depth") or 0)
    if body.get("current_app") is not None:
        st["current_app"] = str(body.get("current_app"))[:120]
    upsert_status(g.device["identity"], st, tenant_id)
    return jsonify({"ok": True, "server_time": now_iso(),
                    "identity": g.device["identity"],
                    "disabled": get_disabled(g.device["identity"], tenant_id),
                    "config": v1_agent_config_payload(tenant_id,
                                                      g.device["identity"])})


def v1_agent_config_payload(tenant_id: int, identity: str = "") -> dict:
    tenant = _tenant_row(tenant_id) or {}
    cfg = {}
    try:
        if tenant.get("config"):
            cfg = json.loads(tenant["config"]) or {}
    except Exception:
        cfg = {}
    cfg.setdefault("retention_days", 90)
    cfg.setdefault("heartbeat_seconds", 60)
    cfg.setdefault("screenshots", None)      # null = agent keeps its local setting
    if identity:
        cfg["disabled"] = get_disabled(identity, tenant_id)
    cfg["server_time"] = now_iso()
    return cfg


@app.get("/api/v1/agent/state")
@mysql_required
@device_required
def v1_agent_state():
    """Tiny poll used by the agent every cycle: is this worker disabled?"""
    tid = g.device["tenant_id"]
    return jsonify({"ok": True, "identity": g.device["identity"],
                    "disabled": get_disabled(g.device["identity"], tid),
                    "server_time": now_iso()})


@app.get("/api/v1/agent/config")
@mysql_required
@device_required
def v1_agent_config():
    return jsonify(v1_agent_config_payload(g.device["tenant_id"],
                                           g.device["identity"]))


@app.get("/api/v1/agent/release")
@mysql_required
@device_required
def v1_agent_release():
    return jsonify({"version": APP_VERSION, "url": "", "sha256": "",
                    "channel": "stable", "notes": []})


@app.after_request
def no_leak(resp):
    """Don't advertise server internals + clear request evidence in the log."""
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Server"] = "WorkPulse"
    if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https":
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    if request.path.startswith("/api/v1/auth"):
        resp.headers["Cache-Control"] = "no-store"
    try:
        started = getattr(request, "_wp_start", None)
        if started:
            ms = int((time.time() - started) * 1000)
            print(f"[{now_dt().strftime('%H:%M:%S')}] {request.method} "
                  f"{request.path} -> {resp.status_code} ({ms} ms)", flush=True)
    except Exception:
        pass
    return resp


@app.before_request
def mark_start():
    request._wp_start = time.time()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8596")))
