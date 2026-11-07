# Deploying the HRMonitor Cloud Server on Render

The worker EXE (and the phone app) post activity **from anywhere on the internet** to this small cloud service, and `HRAdmin.exe` reads the live data from it. No shared-folder setup needed anymore.

```
worker laptop / phone  --internet-->  Render server  <--internet--  HR admin PC (HRAdmin.exe)
```

Storage: **MongoDB Atlas free forever** (M0) — the server uses it automatically (`MONGO_URI`). Without it, the server still works **in memory mode** (good for testing; data disappears on restart).

---

## STEP 1 — Choose your free database (both free forever)

The server picks its database AUTOMATICALLY from env vars. Choose ONE:

### Option A — TiDB Cloud (MySQL-compatible, recommended)

1. Sign up free: https://tidbcloud.com (Google/GitHub login).
2. **Create cluster → Serverless (free)** → region closest to you (e.g. Singapore) → Create.
3. Click **Connect** on the cluster → it shows:
   ```
   Host:     gateway01.<region>.prod.aws.tidbcloud.com
   Port:     4000
   User:     4XxXXxXb.root        ← copy exactly
   Password: click to view/copy
   ```
   TiDB Serverless requires TLS — the server code already does TLS automatically (no CA file needed; optional strict verify via env `MYSQL_SSL_CA_PATH`).
4. You will turn those 4 values into env vars in STEP 4.

### Option B — MongoDB Atlas

1. https://www.mongodb.com/cloud/atlas → Sign up.
2. **FREE M0 cluster** → `Database Access` → add user `hr` (auto password, copy it).
3. `Network Access` → Allow 0.0.0.0/0.
4. `Clusters → Connect → Driver (Python)` → replace `<password>` with the real password:
   `mongodb+srv://hr:YOURPASSWORD@cluster0.abcde.mongodb.net/?retryWrites=true&w=majority`

## STEP 2 — Put the server code where Render can build it

You have two easy options:

**Option A — GitHub (recommended):**
1. Create a free GitHub account → **New repository** (e.g. `hr-monitor-server`, private OK).
2. Upload these 4 files from `C:\work\hr-monitor\server\`:
   `app.py`, `requirements.txt`, `Procfile`, plus this README (optional).

**Option B — use the CLI below instead, if you don't want GitHub.**

## STEP 3 — Create the Render service (5 min)

1. Sign up at https://render.com (free).
2. Dashboard → **New → Web Service**.
3. If GitHub: **connect your repo** → Root Directory `server`... (if you uploaded only the server files, leave root empty).
4. Settings:
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT`
   - **Instance type**: Free
5. **Environment → Add Environment Variable** (choose per your STEP 1 choice):

   Always:
   ```
   HR_API_KEY =                 (any secret, e.g. ravi-secret-2026-xyz)
   ```

   If you chose TiDB:
   ```
   MYSQL_HOST     = gateway01.<region>.prod.aws.tidbcloud.com   (from Connect page)
   MYSQL_PORT     = 4000
   MYSQL_USER     = 4XxXXxXb.root                               (from Connect page)
   MYSQL_PASSWORD = <the password from the Connect page>
   ```

   If you chose Mongo instead:
   ```
   MONGO_URI = mongodb+srv://hr:YOURPASSWORD@cluster0.abcde.mongodb.net/?retryWrites=true&w=majority
   ```
6. **Create Web Service** → wait for "Deploy live".
7. Your server URL is now `https://YOUR-SERVICE.onrender.com`. Open it in a browser — it must show **"HRMonitor Cloud Server - RUNNING (mysql mode)"** (or `mongo mode` per your choice).

## STEP 4 — Connect the admin side (HR PC)

1. Run `HRAdmin.exe` → **4) Settings → 6) Cloud settings**:
   - Cloud server URL: `https://YOUR-SERVICE.onrender.com`
   - Cloud API key: the exact `HR_API_KEY` value
   - It immediately tests and prints **"Cloud OK - server in 'mongo' mode"**.
2. Done. Every worker that reports through the cloud now shows up in Dashboard / User management / Reports.

## STEP 5 — Build a worker agent kit (worker laptop)

1. In `HRAdmin.exe` → **3) Export agent EXE** → destination folder (USB/etc.). The exported `config.json` now carries the cloud URL + key.
2. On the worker laptop run `HRAgent.exe --check` in cmd first → report should say **"Cloud server: REACHABLE"**.
3. Run the EXE once → hidden → Dashboard shows it within ~1 min.

## Notes

- Render's free web service sleeps after ~15 idle minutes and wakes on the first request (~30–50 s cold start). During the cold start the workers cache records locally and send later automatically — nothing is lost.
- The **Kumar phone app can also point at the same Render URL** (instead of your PC IP): in `mobile\app\src\main\java\com\hrmonitor\agent\TrackerService.kt` the upload URL is a plain `http://IP:PORT` — for the phone you must build with the HTTPS Render URL instead, and Android needs the HTTPS exception only if you keep plain HTTP; *use* `https://` and it works as-is.
- Security: only requests with your `HR_API_KEY` header are served; keep the key secret. All data is inside your own Atlas/Render accounts.
- If you restart/redeploy without `MONGO_URI`, upload data lives in memory and disappears — always set `MONGO_URI` for real use.
