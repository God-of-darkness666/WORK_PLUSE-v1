# Deploying the HRMonitor Cloud Server on Render

The worker EXE (and the phone app) post activity **from anywhere on the internet** to this small cloud service, and `HRAdmin.exe` reads the live data from it. No shared-folder setup needed anymore.

```
worker laptop / phone  --internet-->  Render server  <--internet--  HR admin PC (HRAdmin.exe)
```

Storage: **MongoDB Atlas free forever** (M0) — the server uses it automatically (`MONGO_URI`). Without it, the server still works **in memory mode** (good for testing; data disappears on restart).

---

## STEP 1 — Get a free MongoDB Atlas (3 min, once)

1. Go to https://www.mongodb.com/cloud/atlas → **Sign up**.
2. Create a **FREE M0 cluster** (512 MB, free forever).
3. **Database Access** → *Add Database User*: user `hr`, auto-generate a password → **copy it**.
4. **Network Access** → *Add IP Address* → **Allow access from anywhere (0.0.0.0/0)**.
5. **Clusters → Connect → Driver (Python)** → copy the connection string, replace `<password>` with the real password. It looks like:
   ```
   mongodb+srv://hr:YOURPASSWORD@cluster0.abcde.mongodb.net/?retryWrites=true&w=majority
   ```

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
5. **Environment → Add Environment Variable** (both are needed):
   ```
   HR_API_KEY = put along secret here, e.g.  ravi-secret-2026-xyz
   MONGO_URI  = the mongodb+srv://... string from STEP 1
   ```
6. **Create Web Service** → wait for "Deploy live".
7. Your server URL is now `https://YOUR-SERVICE.onrender.com`. Test by opening it in a browser — it must show **"HRMonitor Cloud Server - RUNNING (mongo mode)"**.

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
