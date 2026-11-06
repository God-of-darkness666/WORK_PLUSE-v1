# HRMonitor — Employee Activity Monitoring System (No Database, No Hosting)

A complete **cyber-security / workplace-monitoring project**:

- An **agent EXE** lives silently on the employee laptop and records:
  1. **Applications used** on the laptop (with time spent)
  2. **Chrome tabs** being used (tab titles + time spent)
  3. **Time & date** of every activity
  4. **Approximate location** (IP-based, city level)
- Records are stored as **flat files + daily PDF records** — **no database, no hosting, nothing to buy**.
- The HR side runs a **Command GUI (console app)** with:
  1. **Dashboard** — who is online, which app they use right now, Chrome tab usage, top applications across all employees
  2. **User management** — user name, laptop name, Active / Offline / Disabled
  3. **Export agent EXE** — copy the employee EXE kit (+ config so the employee needs zero setup) to give to a laptop / USB
  4. **Settings** — records folder location + **backup** to any local disk folder, backup-now button
  5. **Reports** — build a PDF record for any user / any date
  6. **Phone sync** — run a small local server so **worker ANDROID PHONES** appear on the same Dashboard (see `mobile\README-MOBILE.md`)

> ⚠️ **Ethical / legal note (say this in your project presentation!):** install this **only on company-owned laptops**, only with written **HR / management authorization**, and inform employees per your local law. Monitoring private devices without consent is illegal in most countries.

---

## 1. How it works (architecture)

```
worker laptop / phone                cloud (Render, YOUR account)         HR / ADMIN PC
┌─────────────────────┐    internet   ┌──────────────────────┐    internet   ┌──────────────────────┐
│  HRAgent.exe        │  ───────────► │  Flask API server    │  ◄─────────── │  HRAdmin.exe         │
│  (silent, hidden)   │   POST /upload│  + free MongoDB      │   GET /api/*  │  (Command GUI)       │
│ • apps, Chrome tabs │               │  (records + status)  │               │ 1 Dashboard          │
│ • time, location    │               └──────────────────────┘               │ 2 User management    │
│ • starts on boot    │   (works from ANY city - no shared folder needed!)   │ 3 Export agent EXE   │
└─────────────────────┘                                                     │ 4 Settings + Backup  │
                                                                            │ 5 Reports            │
   ALSO still supported: no-cloud mode via a shared folder (\\ADMIN-PC\... │ 6 Phone sync         │
   or C:\HRData) - see section "no-cloud mode"                             │ 7* Cloud settings    │
                                                                            └──────────────────────┘
```

Storage is still "no buying needed": Render **free** web service + MongoDB Atlas **M0 free forever**. The admin-side local backup (PDF/records) still works exactly as before.

Folder layout the system creates itself:

```
HRData\
 ├─ status\ASUS@DESKTOP-XYZ.json      ← live status (heartbeat every 30 s)
 ├─ users\ASUS@DESKTOP-XYZ\           ← daily raw records  activity_2026-11-03.jsonl
 ├─ pdf\ASUS@DESKTOP-XYZ\             ← daily PDF          activity_2026-11-03.pdf
 └─ control\ASUS@DESKTOP-XYZ.disabled ← created by HR to pause a user
```

---

## 2. Project files

```
hr-monitor\
 ├─ agent\agent.py        ← employee-side monitor (source)
 ├─ agent\config.json     ← agent settings (HR edits the shared-folder path here)
 ├─ admin\admin_cli.py    ← HR command GUI (source)
 ├─ admin\admin_config.json ← GUI settings (configured inside the GUI)
 ├─ mobile\               ← ANDROID app project for worker phones (+ README-MOBILE.md)
 ├─ requirements.txt
 ├─ build.bat             ← builds both EXE files into dist\
 └─ dist\
     ├─ HRAgent.exe       ← give this to employee laptops
     ├─ config.json       ← ALWAYS copy together with HRAgent.exe!
     ├─ admin_config.json ← settings for HRAdmin.exe
     └─ HRAdmin.exe       ← run this on the HR/admin laptop
```

Requires: **Windows 10/11** and **Python 3.10+** (only needed for building; the EXEs themselves need no Python installed).

---

## 3. STEP-BY-STEP setup (administrator / HR side)

### Step 1 — Get the project & build the EXEs

1. Copy the `hr-monitor` folder to your admin laptop (e.g. `C:\code\hr-monitor`).
2. Open **Command Prompt** in that folder.
3. Run:
   ```
   build.bat
   ```
   It installs `psutil`, `fpdf2`, `rich`, `PyInstaller`, then builds both EXEs.
4. When it prints **SUCCESS** you have:
   - `dist\HRAgent.exe` — the employee-side silent agent
   - `dist\HRAdmin.exe` — the HR command GUI

> If Windows Defender/SmartScreen warns about your own EXE, press *More info → Run anyway* (it is only unsigned because YOU compiled it).

### Step 2 — Create the records folder

- **Same-PC demo mode:** no step needed — use `C:\HRData` (the GUI creates it).
- **Real network mode:** on the admin PC
  1. Create folder `C:\HRData`, right-click → **Properties → Sharing → Share…** → share it (e.g. share name `HRData`).
  2. Note the network path, e.g. `\\ADMIN-PC\HRData`.
  3. IMPORTANT: on the **employee laptops** the share must be reachable — either with credentials in `Windows Credentials`, or set the share permission to allow the *user accounts* of the employee machines (for a class demo, giving Everyone read/write is fine).
  4. Test from an employee laptop: `dir \\ADMIN-PC\HRData` must work.

### Step 3 — First run of HRAdmin.exe (the Command GUI)

1. Double-click `dist\HRAdmin.exe` (or run it in **cmd** to keep the console).
2. You see the menu:

   ```
   1) Dashboard        (who is using what - live)
   2) User management  (user, laptop, active status)
   3) Export agent EXE (downloadable employee EXE kit)
   4) Settings         (records folder, backup folder, backup now)
   5) Reports          (build PDF record for a user / date)
   0) Exit
   ```

3. First go to **4) Settings → option 1** and set the records folder (`C:\HRData` or `\\ADMIN-PC\HRData`). Also set option **2 (Backup folder)**, e.g. `C:\HRBackup` — this is your *local disk storage location for automatic-style backups*.
4. Use **4 → 4 (Backup now)** anytime: it copies every record + PDF into the backup folder in a timestamped subfolder, e.g. `C:\HRBackup\backup_2026-11-03_103000\`. (To make it automatic, add one line to **Task Scheduler**: daily → run `HRAdmin.exe` is interactive, so instead schedule this command: `robocopy "C:\HRData" "D:\HRBackup\%DATE%" /E` — a pure "copy to another disk" backup with no database.)

### Step 4 — Export / prepare the employee EXE kit

1. In the GUI choose **3) Export agent EXE**.
2. Enter the path to `dist\HRAgent.exe` (opens a "downloadable" kit folder).
3. Enter a destination, e.g. `C:\HRAgentKit` (or your USB pen drive). The GUI copies:
   - `HRAgent.exe`
   - `config.json` (pre-filled with **your** records folder → employee needs zero setup)
   - `INSTALL-STEPS.txt`

---

## 4A. NEW: Cloud mode via Render (recommended — simplest!)

`server/README-SERVER.md` has every click-by-click step. Short version:

1. Free MongoDB Atlas → copy the connection string.
2. Upload the 3 small files of `hr-monitor\server\` to GitHub → **Render → New Web Service** (free):
   build `pip install -r requirements.txt`, start `gunicorn app:app --bind 0.0.0.0:$PORT`,
   env vars `HR_API_KEY` (any secret) and `MONGO_URI` (Atlas string).
3. You get a URL like `https://your-hr-server.onrender.com`.
4. **HRAdmin.exe → 4) Settings → 6) Cloud settings** → paste URL + the API key → shows **Cloud OK**.
5. **Export agent EXE** → the kit's `config.json` carries the cloud URL → give `HRAgent.exe + config.json` to worker laptops → they report from **any location** (no share setup at all).
6. Worker phones: in the mobile app use the same Render URL as the "server IP".

Cloud mode removes the network-share problems completely — the agent only needs internet.

### No-cloud mode (older setup, still works)

The worker and HR connect with a plain Windows folder instead — use `server_path` in `config.json` (`C:\\HRData` demo / `\\\\ADMIN-PC\\HRData` network). Follow the original Step-5 employee steps below.

---

## 5. STEP-BY-STEP setup (employee laptop)

Requirements met by the kit — nothing else to install.

1. Copy `HRAgent.exe` **and** `config.json` into the **same folder** on the employee laptop (e.g. `C:\Windows\SpeechTrainer\` — a boring name is good).
2. **Double-click `HRAgent.exe` once.** It:
   - starts hidden (no window),
   - logs itself into the Windows auto-start (Registry `HKCU\...\Run`, so it launches on **every boot/login** until shutdown, and again after restart),
   - begins recording immediately.
3. That's all. From now on it always runs. The employee can kill it only via Task Manager; the next login/startup revives it.

> **Same-PC demo mode:** if the laptop that runs the agent is the SAME laptop as the admin GUI (perfect for a college demo), set `config.json` → `"server_path": "C:\\HRData"` (double backslashes!) — done, nothing else.

> **Real network mode:** `config.json` must contain the share, e.g. `"server_path": "\\\\ADMIN-PC\\HRData"` (double backslashes!). Edit it on YOUR PC before exporting the kit so the employee never sees it.

To verify it's working on the HR laptop: open the GUI → **1) Dashboard** — within ~1 minute the user appears with **ACTIVE** state and their current app; their records folder appears in the share within ~30 s.

---

## 6. Using the admin Command GUI (HR daily work)

| Menu | What you get |
|---|---|
| **1) Dashboard** | Table of every employee: laptop name, state (ACTIVE/OFFLINE/DISABLED), current app, current Chrome tab, last seen, location. Plus **TOP APPLICATIONS** (all employees, with a bar graph) and **MOST USED CHROME TABS** ranking. |
| **2) User management** | Every user: user name, laptop name, active or not. `S` shows a user's detail (today + yesterday top apps, Chrome tabs, latest 10 events). `D` toggles **monitoring pause/resume** — the GUI writes a control flag file into the shared folder, the agent obeys within ~1 min (it keeps its heartbeat but stops recording). |
| **3) Export agent EXE** | The "downloadable" employee kit (see Step 4). |
| **4) Settings** | Records folder, backup folder, agent EXE path, **Backup now**, open records folder in Explorer. |
| **5) Reports** | Pick a user + any date → builds a full **PDF record** (apps table with time used, Chrome-tab table with time, activity timeline) into `backup_path\reports\` and opens it. |

**Daily PDF records with zero work:** the agent itself generates each employee's daily PDF automatically (into `HRData\pdf\<user>\`) at the day change, and Report 5 makes PDFs for any past date on demand. Nothing to buy, no database — just folders and files.

---

## 7. What exactly is stored (for your presentation)

- `activity_DATE.jsonl` — one JSON line per sample:
  ```json
  {"ts": "2026-11-03T10:06:33", "user": "Ravi", "machine": "OFFICE-07",
   "exe": "chrome.exe", "app": "Google Chrome", "title": "Inbox - Gmail - Google Chrome",
   "tab": "Inbox - Gmail", "secs": 10}
  ```
- `status\USER@MACHINE.json` — heartbeat: last seen, current app/tab, location, IP (updated every 30 s; admin marks the user **ACTIVE** if it's fresher than 3 min).
- `pdf\USER@MACHINE\activity_DATE.pdf` — the daily record.

Walkthrough for the demo: open Word/Chrome on the "employee" laptop → wait 1–2 min → Dashboard shows it live → Reports → open PDF → Settings → Backup now → show the backup folder.

Security notes worth mentioning: data path has \`control-flag\` pausing, all data stays inside the company (no internet service), only HR user can open the share, and records are plain text so they are also easy to protect with NTFS permissions / BitLocker.

---

## 8. Worker PHONES (Android) — same Dashboard, distributed as APK

Worker phones are tracked by a separate **Android app — installed from an .apk file** with Android's package installer (source ready in `mobile\`, full guide in `mobile\README-MOBILE.md`):

1. Build the APK once on your PC with Android Studio → `mobile\app\build\outputs\apk\debug\app-debug.apk`.
2. Send the APK to workers (WhatsApp/Drive/USB) → they tap it → **Package Installer** → allow *"Install unknown apps"* once → Install.
3. In the app enter: **worker name**, **server address = your Render URL** `https://your-hr-server.onrender.com` (or `http://HR-PC-IP:8595` for pure-office LAN mode), **API key** = same `HR_API_KEY` → **TEST connection** must say OK.
4. Grant **Usage access** + **Location** → **START TRACKING** → it runs 24/7 including after reboot, reporting through the cloud from anywhere.

Limitation (say this in your presentation): Android never exposes Chrome **tab titles** to apps, so phone tracking is **app-level only** (e.g. "Chrome 30 min", "WhatsApp 15 min") — Chrome-tab detail comes from the laptops.

---

## 9. Troubleshooting

### First tool: the self-check

On any laptop (HR or worker), open **cmd** in the agent's folder and run:

```
HRAgent.exe --check
```

It opens a report that shows exactly what is wrong (config missing? folder unreachable? recorder OK? auto-start OK?). Everything the agent does is also logged to `%APPDATA%\HRAgent\agent.log`.

| Problem | Fix |
|---|---|
| Agent "does nothing" on the worker laptop | It's hidden BY DESIGN. Run `HRAgent.exe --check` — it will tell the truth (config/share/recorder). 90% of the time: `server_path` in config.json is wrong/unreachable. |
| The EXE won't even start (SmartScreen) | "Windows protected your PC" → More info → Run anyway. |
| Antivirus deletes it | Add the folder to exclusions (normal for a self-built unsigned EXE). |
| `--check` says HR folder NOT REACHABLE | Test from the worker laptop in cmd: `dir \\ADMIN-PC\HRData` — must list files with the same Windows user. If not: sharing/permissions/firewall on the admin PC (or use demo mode `C:\HRData`). |
| Little/no data appears | The share write works but the agent can't reach it sometimes → it caches locally at `%APPDATA%\HRAgent` and syncs automatically when reachable. |
| Apps tracked but no Chrome tabs | Chrome tabs come from Chrome's **window title**; works on laptops when Chrome is open. Android phones can't legally expose tab titles — phone tracking is app-level only (see mobile README). |
| Two agents recording twice | Impossible — a single-instance guard refuses a second copy. |
| Worker got out of hand / pause | GUI → **User management → D**, or leave only the admin side running. |
| I want everything off the laptop (uninstall) | Remove autostart with `reg delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v HRAgent /f` |
| Everything must reach my backup disk automatically | Schedule a nightly `robocopy "C:\HRData" "D:\HRBackup\%DATE%" /E` in Task Scheduler (no database needed). |

Uninstall in one line (run on the employee laptop, classic cmd):

```
taskkill /IM HRAgent.exe /F & rd /s /q "%APPDATA%\HRAgent" & reg delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v HRAgent /f
```

---

## 10. Source / rebuild

- Server (Render): `server\app.py` — Flask + pymongo, deployed with `gunicorn` (see `server\README-SERVER.md`)
- Agent: `agent\agent.py` — only stdlib + `psutil`, `fpdf2`
- Admin GUI: `admin\admin_cli.py` — stdlib + `rich`, `fpdf2`
- Mobile app: `mobile\` (Android Studio project)
- Build EXEs: **`build.bat`** (uses PyInstaller `--onefile`; agent is `--noconsole` so it is fully hidden)
