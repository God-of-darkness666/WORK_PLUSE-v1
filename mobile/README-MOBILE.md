# HRMonitor Mobile — Android app for worker phones (APK)

The worker's phone records **which apps are used and for how long**, plus **time/date and location**, and uploads to the **same Render cloud server** (cloud mode is the standard clean option) **or** to the office LAN sync service (`http://<HR PC IP>:8595`, via Admin GUI menu 6). Phone users appear **on the same Dashboard** as laptops.

> Honest limitation (say this in your presentation): Android does **not** let any normal app read Chrome **tab titles** inside the browser. Mobile tracking is therefore **app-level** (including "Chrome" time) — tab detail comes from laptops.

The mobile side is an **.apk file**, not an EXE — it installs with Android's **package installer**.

---

## 1. Build the APK on the HR/admin PC (one time, ~10 min)

1. Install **Android Studio** (free): https://developer.android.com/studio
2. In Android Studio: **Open** → select the folder `mobile` (inside hr-monitor).
3. If it offers to sync/download the Gradle wrapper → accept and wait (first sync is slow).
4. Then **Build → Build Bundle(s)/APK(s) → Build APK(s)**.
5. APK lands here:
   ```
   mobile\app\build\outputs\apk\debug\app-debug.apk
   ```
   (Tip: Build → **Generate Signed APK** creates `app-release.apk` which is what you finally distribute.)

## 2. Distribute the APK to workers (package installer flow)

Any way works — the worker simply opens the file and Android's **Package Installer** handles it:

- send the APK on **WhatsApp / Teams / email / Google Drive link**,
- or share **USB**/Bluetooth.
- On the phone: tap the download → Android says *"This app is not from the Play Store"* → allow **"Install unknown apps"** for that browser/manager once → tap **Install**. This is normal for any self-built/apk-distributed app.

## 3. On the worker's phone — setup (2 min)

1. Open the **HRMonitor** app.
2. Fill in:
   - **Worker name**: e.g. `Kumar`
   - **Server address**: for cloud mode → your Render URL, exactly `https://your-hr-server.onrender.com`
     (LAN mode → `http://<HR PC IP>:8595` shown by Admin GUI menu 6)
   - **API key**: the same `HR_API_KEY` value as your RENDER server environment variable.
3. Tap **TEST connection** → you should see **"Connection OK ✓"**.
4. Tap **1) Grant Usage access** → find *HRMonitor* in the list → **Allow** (this is Android's official permission for app-usage monitoring).
5. Tap **2) Grant Location + Notifications** → Allow.
6. Tap **START TRACKING** → close the app. A small permanent notification "Work activity manager" confirms it is recording — **it keeps recording 24/7, also after reboots.**

No same-Wi-Fi requirement in cloud mode — phones report from **anywhere** with mobile data or any Wi-Fi.

## 4. What HR sees (same GUI as laptops)

- **Dashboard** — phone users listed like laptops: current app, last seen, location, ACTIVE/OFFLINE.
- **User management** — worker name + phone model (e.g. `Kumar@Redmi-Note12`).
- **Reports** — same PDF (apps table + timeline) for phones.
- **User management → D** — pause/resume phone monitoring, delivered automatically on the next upload.

## 5. Troubleshooting (phones)

| Problem | Fix |
|---|---|
| TEST connection fails | Server address must include `https://` (Render) or `http://IP:PORT` (LAN). Check exact API key. Check mobile data/Wi-Fi. |
| "App not installed" blocked | Settings → allow **Install unknown apps** for the app used to download. |
| Phone data stops after a while | Phone makers (Xiaomi/Oppo/Samsung) kill background apps: Settings → Battery → **HRMonitor** → no restrictions / allow background activity. This is required for any monitoring app. |
| Location shows "Unknown" | Location permission not granted or location services off. |
| Phone says HTTP 401 | API key doesn't match the Render `HR_API_KEY` env value. |
| I used LAN mode with http (Android blocks it) | `usesCleartextTraffic` is already enabled in this project, so plain http works — but prefer the cloud https URL. |

## 6. Why this fits the "no hosting cost" model

- Cloud mode: only Render free + Atlas M0 free — nothing paid.
- LAN mode (`http://<HR-PC>:8595`): pure-office approach, zero internet account.
