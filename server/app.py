"""
HRMonitor Cloud Server - deploy ON RENDER (free web service).

The worker EXE / phone app POST /upload here.
The admin HRAdmin.exe GETs the data from here.

Storage (automatic):
  - MONGO_URI env var set  -> MongoDB (Atlas M0 free tier) : data is PERMANENT
  - not set                -> memory fallback : works for demos but data is
                              wiped when the service restarts/sleeps

Env vars to set on Render:
  HR_API_KEY = a secret string both agent + admin GUI must use
  MONGO_URI  = (optional) Atlas connection string, recommended
"""
import os
from datetime import datetime

from flask import Flask, request, jsonify

API_KEY = os.environ.get("HR_API_KEY", "hr-secret-change-me")
MONGO_URI = os.environ.get("MONGO_URI", "")

app = Flask(__name__)

MODE = "memory"
if MONGO_URI:
    try:
        from pymongo import MongoClient
        _db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)["hrmonitor"]
        _records, _status, _controls = _db["records"], _db["status"], _db["controls"]
        MODE = "mongo"
    except Exception as e:
        print("MongoDB unavailable -> memory fallback:", e)

if MODE == "memory":
    _records_rows = []
    _status_rows = {}
    _control_rows = {}

# ---------------------------------------------------------------- storage
def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def save_records(ident: str, rows: list) -> int:
    for r in rows:
        doc = {
            "identity": ident,
            "ts": r.get("ts", now_iso()),
            "user": r.get("user", ""),
            "machine": r.get("machine", ""),
            "exe": r.get("exe", ""),
            "app": r.get("app", ""),
            "title": str(r.get("title", ""))[:200],
            "tab": r.get("tab"),
            "secs": int(r.get("secs") or 0),
        }
        doc["day"] = str(doc["ts"])[:10]
        if MODE == "mongo":
            _records.insert_one(doc)
        else:
            _records_rows.append(doc)
    return len(rows)


def upsert_status(ident: str, st: dict) -> None:
    doc = dict(st)
    doc["identity"] = ident
    doc.setdefault("last_seen", now_iso())
    if MODE == "mongo":
        _status.replace_one({"_id": ident}, {**doc, "_id": ident}, upsert=True)
    else:
        _status_rows[ident] = doc


def list_status() -> list:
    if MODE == "mongo":
        return [d for d in _status.find({}, {"_id": 0})]
    return list(_status_rows.values())


def get_status(ident: str):
    if MODE == "mongo":
        d = _status.find_one({"_id": ident}, {"_id": 0})
        return d
    return _status_rows.get(ident)


def get_records(ident: str, day: str) -> list:
    if MODE == "mongo":
        q = {"identity": ident}
        if day:
            q["day"] = day
        return [{**d, "_id": str(d["_id"])} for d in _records.find(q).sort("ts", 1).limit(20000)]
    return [r for r in _records_rows if r["identity"] == ident and (not day or r["day"] == day)]


def list_identities() -> list:
    if MODE == "mongo":
        return sorted(d["_id"] for d in _status.find({}, {"_id": 1}))
    return sorted(_status_rows.keys())


def set_disabled(ident: str, flag: bool) -> None:
    if MODE == "mongo":
        _controls.replace_one({"_id": ident}, {"_id": ident, "disabled": bool(flag)}, upsert=True)
        if ident in [d.get("identity") for d in list_status()]:
            _status.update_one({"_id": ident}, {"$set": {"enabled": not flag}})
    else:
        _control_rows[ident] = bool(flag)
        st = _status_rows.get(ident)
        if st:
            st["enabled"] = not flag


def get_disabled(ident: str) -> bool:
    if MODE == "mongo":
        d = _controls.find_one({"_id": ident})
        return bool(d and d.get("disabled"))
    return bool(_control_rows.get(ident))


# ---------------------------------------------------------------- helpers
def authorized() -> bool:
    return request.headers.get("X-API-Key", "") == API_KEY


# ---------------------------------------------------------------- routes
@app.get("/")
def home():
    return f"""<h2>HRMonitor Cloud Server - RUNNING ({MODE} mode)</h2>
<p>Workers (EXE / phone app)  POST /upload - here with the secret API key.</p>
<p>HRAdmin.exe reads the dashboards from /api/... .</p>"""


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
    identity = request.args.get("identity", "")
    day = request.args.get("day", "")
    return jsonify({"records": get_records(identity, day)})


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
    # local testing only; on Render we use gunicorn (see Procfile)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8596")))
