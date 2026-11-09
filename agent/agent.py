r"""
HRAgent - silent employee activity monitor (local, no database / no server).

Records every N seconds:
  1. The foreground application (e.g. Word, Chrome, VLC)
  2. If Chrome is open -> the current TAB title
  3. Time + date of every activity
  4. Approximate location (IP based, city level, optional)

Storage is pure flat files (NO database, NO hosting):
  - Daily activity  : <server_path>\users\<USER@PC>\activity_YYYY-MM-DD.jsonl
  - Status/heartbeat: <server_path>\status\<USER@PC>.json
  - Daily PDF report: <server_path>\pdf\<USER@PC>\activity_YYYY-MM-DD.pdf
  - Disable switch  : <server_path>\control\<USER@PC>.disabled

<server_path> can be:
  - a normal local folder   (C:\\HRData)                -> single-PC demo mode
  - a network share (UNC)   (\\\\ADMIN-PC\\HRData)      -> real HR scenario
If the share is unreachable, records are cached locally and synced later.

The agent starts on every boot (HKCU Run key) and runs silently with no window.

Diagnostic tools:
  cmd:  HRAgent.exe --check    -> runs a self test and opens a report file
        errors are always logged to %APPDATA%\HRAgent\agent.log
"""

import ctypes
import ctypes.wintypes
import getpass
import json
import os
import socket
import sys
import time
import urllib.request
import urllib.parse
import shutil
import traceback
import datetime as dt

import psutil
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# ----------------------------------------------------------------------------
# Constants / paths
# ----------------------------------------------------------------------------
HEARTBEAT_ACTIVE_WINDOW = 180        # admin marks user "Active" if last_seen < 180 s
CHROME_SUFFIX = " - Google Chrome"
FRIENDLY_NAMES = {
    "chrome.exe": "Google Chrome",
    "msedge.exe": "Microsoft Edge",
    "firefox.exe": "Firefox",
    "explorer.exe": "File Explorer",
    "notepad.exe": "Notepad",
    "winword.exe": "Microsoft Word",
    "excel.exe": "Microsoft Excel",
    "powerpnt.exe": "Microsoft PowerPoint",
    "outlook.exe": "Microsoft Outlook",
    "teams.exe": "Microsoft Teams",
    "code.exe": "Visual Studio Code",
    "cmd.exe": "Command Prompt",
    "powershell.exe": "PowerShell",
    "spotify.exe": "Spotify",
    "vlc.exe": "VLC Media Player",
    "skype.exe": "Skype",
    "telegram.exe": "Telegram",
    "thonny.exe": "Thonny IDE",
    "python.exe": "Python",
    "java.exe": "Java",
}


def app_dir() -> str:
    """Folder where the EXE (or this script) lives -> holds config.json."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def cache_dir() -> str:
    """Hidden local cache used when the shared folder is offline."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "HRAgent")


CONFIG_PATH = os.path.join(app_dir(), "config.json")
DEFAULT_CONFIG = {
    "server_path": r"\\ADMIN-PC\HRData",   # <-- shared-folder mode (HR edits before deploying)
    "cloud_url": "",                       # <-- CLOUD mode (Render server), e.g. https://hr-xxx.onrender.com
    "api_key": "",                         # <-- secret API key (must match the server's HR_API_KEY)
    "interval_seconds": 10,                # sample every 10 s
    "heartbeat_seconds": 30,               # status update every 30 s
    "location_enabled": True,              # IP based city-level location
}

# Anti-bypass: these are locked INSIDE the EXE at build time. If a worker
# deletes / empties / edits config.json (remove server & key -> "go invisible"),
# the agent silently reverts to these -> tampering cannot break monitoring.
BUILD_LOCK = {
    "cloud_url": "https://work-pluse-v1.onrender.com/",
    "api_key": "hr-secret-2026-naveen",
}

IDENTITY = f"{getpass.getuser()}@{socket.gethostname()}"


# ----------------------------------------------------------------------------
# Error log (visible proof for debugging)
# ----------------------------------------------------------------------------
def log_err(message: str) -> None:
    try:
        os.makedirs(cache_dir(), exist_ok=True)
        with open(os.path.join(cache_dir(), "agent.log"), "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now().isoformat()}  {message}\n")
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    tampered = False
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        else:
            save_config(cfg)                     # create default config next to EXE
    except Exception as e:
        log_err(f"config load error: {e}")
    if getattr(sys, "frozen", False):
        # tamper protection: a frozen agent can never lose the cloud settings
        if not str(cfg.get("cloud_url", "") or "").strip() and BUILD_LOCK["cloud_url"]:
            cfg["cloud_url"] = BUILD_LOCK["cloud_url"]
            tampered = True
        if not str(cfg.get("api_key", "") or "").strip() and BUILD_LOCK["api_key"]:
            cfg["api_key"] = BUILD_LOCK["api_key"]
            tampered = True
        if tampered:
            log_err("config.json was deleted/edited -> reverted to BUILT-IN locked settings")
    return cfg


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Screenshots on app/tab switch (blueprint: visual evidence, worker-consented)
# ----------------------------------------------------------------------------
def screenshots_root(cfg: dict) -> str:
    """Destination chosen by the admin (Settings) - fallback: local hidden cache."""
    p = str(cfg.get("screenshots_path", "") or "").strip()
    return p if p else os.path.join(cache_dir(), "screenshots")


def take_screenshot(cfg: dict, app: str, title: str) -> str:
    """One PNG per switch: <dest>/<day>/<HHMMSS>_<app>.png. Never crashes."""
    try:
        from PIL import ImageGrab
        root = screenshots_root(cfg)
        day = str(dt.date.today())
        d = os.path.join(root, IDENTITY.replace("@", "_at_"), day)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{dt.datetime.now().strftime('%H%M%S')}"
                               f"_{(''.join(c for c in app if c.isalnum()))[:32]}.png")
        ImageGrab.grab(all_screens=True).save(path)
        return path
    except Exception as e:
        log_err(f"screenshot error: {e}")
        return ""


# ----------------------------------------------------------------------------
# Foreground window + process  (pure ctypes, no pywin32 needed)
# ----------------------------------------------------------------------------
user32 = ctypes.windll.user32


def get_foreground():
    """Return (exe_name, window_title) of the foreground window, or None."""
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = "unknown.exe"
        try:
            exe = psutil.Process(pid.value).name()
        except Exception:
            pass
        if not title:
            title = exe
        return exe, title
    except Exception as e:
        log_err(f"get_foreground error: {e}")
        return None


def chrome_tab(title: str):
    """Extract the TAB name from a Chrome window title."""
    if title.endswith(CHROME_SUFFIX):
        tab = title[: -len(CHROME_SUFFIX)].strip()
        return tab if tab and tab.lower() != "new tab" else "New Tab"
    return None


# ----------------------------------------------------------------------------
# Browser history (real visited links + web searches) - Chrome & Edge local DBs
# ----------------------------------------------------------------------------
CHROME_EPOCH_DELTA = 11644473600          # seconds between 1601-01-01 and 1970-01-01

SEARCH_KEYS = ("q", "query", "p", "text", "wd", "k", "searchTerm", "search_query")


def classify_url(url: str):
    """True link analysis: ('search', typed search) or ('page', site/path)."""
    u = urllib.parse.urlparse(url)
    host = (u.netloc or "").lower()
    qs = urllib.parse.parse_qs(u.query)
    for k in SEARCH_KEYS:
        if k in qs and qs[k] and qs[k][0]:
            return "search", urllib.parse.unquote(qs[k][0])[:120]
    # some searches are /search/<terms> (duckduckgo lite) or path based
    if "yandex." in host and u.path.startswith("/search/"):
        return "search", urllib.parse.unquote(u.path[len("/search/"):])[:120]
    path = (u.path or "/").strip("/")
    return "page", (host + ("/" + path if path else ""))[:120]


def browser_dbs() -> list:
    """All Chrome/Edge History databases of every profile on this PC."""
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return []
    out = []
    for sub, app in ((os.path.join("Google", "Chrome"), "Chrome"),
                     (os.path.join("Microsoft", "Edge"), "Edge")):
        base = os.path.join(local, sub, "User Data")
        if not os.path.isdir(base):
            continue
        for d in sorted(os.listdir(base)):
            h = os.path.join(base, d, "History")
            if os.path.isfile(h):
                out.append((h, app))
    return out


def collect_browser_urls(s: dict) -> list:
    """Every visit since the last check -> records with real URL or the search text."""
    built = []
    dbs = browser_dbs()
    if not dbs:
        return built
    midnight = (dt.datetime.combine(dt.date.today(), dt.time.min).timestamp()
                + CHROME_EPOCH_DELTA) * 1_000_000
    tmpdir = os.path.join(cache_dir(), "hist_tmp")
    os.makedirs(tmpdir, exist_ok=True)
    import sqlite3
    for idx, (hist, app) in enumerate(dbs):
        seen = s.setdefault("url_last", {})
        since = seen.get(str(idx), 0)
        first = idx not in seen                      # first run -> only today's visits
        after = midnight if first else max(since, midnight)
        tmp = os.path.join(tmpdir, f"hist_{idx}.sqlite")
        try:
            shutil.copyfile(hist, tmp)               # original is locked by the browser
            con = sqlite3.connect(tmp)
            cur = con.cursor()
            cur.execute(
                "SELECT url, title, last_visit_time FROM urls "
                "WHERE last_visit_time > ? ORDER BY last_visit_time LIMIT 40",
                (int(after),))
            newest = after
            for url, title, t in cur.fetchall():
                if not url or not url.startswith("http"):
                    continue
                newest = max(newest or 0, t)
                ts = dt.datetime.fromtimestamp(t / 1_000_000 - CHROME_EPOCH_DELTA)
                kind, detail = classify_url(url)
                rec = {"ts": ts.isoformat(timespec="seconds"),
                       "user": getpass.getuser(), "machine": socket.gethostname(),
                       "exe": ("chrome.exe" if app == "Chrome" else "msedge.exe"),
                       "app": app, "title": (title or detail)[:200],
                       "secs": 0, "kind": "url",
                       "url": url[:500], (kind if kind == "search" else "page"): detail}
                built.append(rec)
            seen[str(idx)] = newest
            con.close()
            os.remove(tmp)
        except Exception as e:
            log_err(f"collect_browser_urls({app}) error: {e}")
    return built


def flush_url_records(s: dict):
    """Store / buffer the records produced by collect_browser_urls."""
    for rec in collect_browser_urls(s):
        append_record(os.path.join(cache_dir(), "users", IDENTITY, "day.jsonl"), rec)
        if s["cloud_url"]:
            s["cloud_buffer"].append(rec)
    save_url_state(s)                       # remember position even across restarts


def _url_state_file() -> str:
    return os.path.join(cache_dir(), "url_state.json")


def save_url_state(s: dict):
    try:
        write_json(_url_state_file(), s.get("url_last", {}))
    except Exception:
        pass


def load_url_state(s: dict):
    """Loading the persisted last-visit positions means a restart does NOT
    re-collect the whole day's browser history (no duplicate tabs)."""
    try:
        p = _url_state_file()
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                st = json.load(f)
            if isinstance(st, dict):
                s["url_last"] = st
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Location (IP based, no database, cached 1 hour)
# ----------------------------------------------------------------------------
def refresh_location(enabled: bool):
    if not enabled:
        return "Unknown", ""
    try:
        with urllib.request.urlopen("http://ip-api.com/json/?fields=status,city,regionName,country,query",
                                    timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
        if data.get("status") == "success":
            return (f'{data["city"]}, {data["regionName"]}, {data["country"]}',
                    data.get("query", ""))
    except Exception as e:
        log_err(f"location error: {e}")
    return "Unknown", ""


# ----------------------------------------------------------------------------
# Storage helpers
# ----------------------------------------------------------------------------
def day_file(server: str, day: str) -> str:
    return os.path.join(server, "users", IDENTITY, f"activity_{day}.jsonl")


def status_file(server: str) -> str:
    return os.path.join(server, "status", f"{IDENTITY}.json")


def pdf_file(server: str, day: str) -> str:
    return os.path.join(server, "pdf", IDENTITY, f"activity_{day}.pdf")


def append_record(path: str, record: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log_err(f"append_record error: {e}")


def write_json(path: str, obj: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_err(f"write_json({path}) error: {e}")


def server_reachable(server: str) -> bool:
    try:
        os.makedirs(os.path.join(server, "users", IDENTITY), exist_ok=True)
        probe = os.path.join(server, "users", IDENTITY, ".ok")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception as e:
        log_err(f"server_reachable({server}) error: {e}")
        return False


# ----------------------------------------------------------------------------
# Cloud mode (Render server) - wins over the shared folder when cloud_url set
# ----------------------------------------------------------------------------
def post_cloud(s: dict, status: dict, records: list) -> bool:
    try:
        url = s["cloud_url"].rstrip("/") + "/upload"
        body = json.dumps({"identity": IDENTITY, "status": status, "records": records}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", "X-API-Key": s["api_key"]})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as e:
        log_err(f"cloud upload error ({s['cloud_url']}): {e}")
        return False


def cloud_disabled(s: dict) -> bool:
    try:
        url = s["cloud_url"].rstrip("/") + "/api/is_disabled?identity=" + urllib.parse.quote(IDENTITY)
        req = urllib.request.Request(url, headers={"X-API-Key": s["api_key"]})
        with urllib.request.urlopen(req, timeout=10) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("disabled"))
    except Exception:
        return False


def sync_pending(server: str, cache_root: str) -> None:
    """Copy locally cached day-files to the share (offline -> retry later)."""
    local_users = os.path.join(cache_root, "users")
    if not os.path.isdir(local_users):
        return
    for name in os.listdir(local_users):
        dir_path = os.path.join(local_users, name)
        for fname in os.listdir(dir_path):
            src = os.path.join(dir_path, fname)
            target = fname
            if fname == "day.jsonl":                     # today's live file -> dated name on the share
                target = f"activity_{dt.date.today().isoformat()}.jsonl"
            dst = os.path.join(server, "users", name, target)
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
            except Exception:
                return          # share still down -> stop here, retry next time


# ----------------------------------------------------------------------------
# PDF generation
# ----------------------------------------------------------------------------
def to_latin(text) -> str:
    return str(text).encode("latin-1", "replace").decode("latin-1")


class ReportPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 8, to_latin("Employee Activity Report"), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
        self.ln(1)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 8, to_latin(f"Generated by HRAgent - page {self.page_no()}"), align="C")


def generate_pdf(out_path: str, day: str, records: list, app_secs: dict, tab_secs: dict,
                 location: str, ip: str) -> bool:
    """records: list of activity dicts for that day."""
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        pdf = ReportPDF("P", "mm", "A4")
        pdf.set_auto_page_break(auto=True, margin=16)
        pdf.add_page()

        user, host = IDENTITY.split("@", 1)
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 7, to_latin(f"Employee   : {user}     Machine: {host}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.cell(0, 7, to_latin(f"Date       : {day}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.cell(0, 7, to_latin(f"Location   : {location}   (IP: {ip or 'n/a'})"),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if records:
            pdf.cell(0, 7, to_latin(f"First      : {records[0].get('ts', '')}   Last: {records[-1].get('ts', '')}"),
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.cell(0, 7, to_latin(f"Records    : {len(records)}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(3)

        def hms(s):
            s = int(s)
            return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

        def table(title, pairs, total):
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(0, 8, to_latin(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font("Helvetica", "", 9)
            pdf.set_draw_color(120, 120, 120)
            pdf.cell(110, 7, "  Item", border=1)
            pdf.cell(35, 7, "  Time used", border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            for name, secs in pairs:
                pdf.cell(110, 7, to_latin(f"  {name[:55]}"), border=1)
                pdf.cell(35, 7, to_latin(f"  {hms(secs)}"), border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.cell(110, 7, "  TOTAL", border=1)
            pdf.cell(35, 7, to_latin(f"  {hms(total)}"), border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(4)

        apps, tabs = sorted(app_secs.items(), key=lambda x: -x[1]), sorted(tab_secs.items(), key=lambda x: -x[1])
        table("1. Applications used on this laptop", apps, sum(app_secs.values()))
        if tab_secs:
            table("2. Chrome tabs visited", tabs, sum(tab_secs.values()))

        # last 40 activity events (timeline)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, to_latin("3. Activity timeline (latest events)"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 8)
        for rec in records[-40:]:
            pdf.cell(0, 5, to_latin(f"{rec.get('ts','')}  {rec.get('app','')}  | {rec.get('title','')[:70]}"),
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        pdf.output(out_path)
        return True
    except Exception as e:
        log_err(f"generate_pdf error: {e}")
        return False


# ----------------------------------------------------------------------------
# Auto start on boot (HKCU Run key - no admin rights needed)
# ----------------------------------------------------------------------------
def ensure_autostart() -> None:
    try:
        import winreg
        if getattr(sys, "frozen", False):
            cmd = f'"{sys.executable}"'
        else:
            cmd = f'pythonw "{os.path.abspath(__file__)}"'
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_ALL_ACCESS)
        winreg.SetValueEx(key, "HRAgent", 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
    except Exception as e:
        log_err(f"autostart error: {e}")


def autostart_status() -> str:
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run")
        val, _ = winreg.QueryValueEx(key, "HRAgent")
        return f"registered: {val}"
    except FileNotFoundError:
        return "NOT registered yet (run the EXE once normally)"
    except Exception as e:
        return f"unknown ({e})"


def log_restart_event(cloud_url: str):
    """Tell the admin this agent (re)started - reveals taskkill/watchdog battles."""
    try:
        rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
               "kind": "event", "event": "agent_restarted",
               "user": getpass.getuser(), "machine": socket.gethostname(),
               "exe": "HRAgent.exe", "app": "(agent restarted)",
               "title": "agent was restarted (possible kill attempt or boot)", "secs": 0}
        append_record(os.path.join(cache_dir(), "users", IDENTITY, "day.jsonl"), rec)
    except Exception:
        pass


def install_elevated_tasks():
    """Runs as ADMIN (self-relaunched with -Verb RunAs). Creates the tasks that
    cannot be removed/ignored by normal users: minute watchdog + boot autostart."""
    try:
        import subprocess
        exe = sys.executable
        for args in (
            ["schtasks", "/Create", "/F", "/TN", "WorkPulseWatchdog",
             "/SC", "MINUTE", "/MO", "1", "/TR", exe, "/RL", "HIGHEST"],
            ["schtasks", "/Create", "/F", "/TN", "WorkPulseBoot",
             "/SC", "ONLOGON", "/DELAY", "0000:30", "/TR", exe, "/RL", "HIGHEST"],
        ):
            subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log_err("ELEVATED install done: WorkPulseWatchdog + WorkPulseBoot (RL HIGHEST)")
    except Exception as e:
        log_err(f"elevated install error: {e}")


def request_full_control() -> None:
    """First-run interactive: offer the FULL CONTROL install (one UAC prompt).
    Protects against normal-user bypasses; the watchdog runs with highest rights.
    NEVER blocks: the question self-skips after 15 s (hidden/started boots)."""
    if not getattr(sys, "frozen", False):
        return
    try:
        import ctypes
        if ctypes.windll.shell32.IsUserAnAdmin():
            install_elevated_tasks()          # already admin - create directly
            return
        print("  Install with FULL ADMIN control (stronger tamper protection)? [Y/n]"
              "  (auto-NO in 15 s)", end="", flush=True)
        import msvcrt
        buf, start = "", time.time()
        while time.time() - start < 15:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    break
                buf += ch
        print()
        if buf.strip().lower() in ("", "y", "yes"):
            import subprocess
            subprocess.Popen(["powershell", "-NoProfile", "-Command",
                              f"Start-Process '{sys.executable}' -ArgumentList "
                              f"'--install-elevated' -Verb RunAs -WindowStyle Hidden"])
            log_err("full-control install requested (UAC shown to worker)")
    except Exception as e:
        log_err(f"full-control request error: {e}")


def ensure_watchdog() -> None:
    """Anti-bypass: a Windows scheduled task restarts the agent every minute.

    taskkill /IM HRAgent.exe /F (or any other kill) only buys 60 seconds:
    the WorkPulseWatchdog task relaunches the agent and the admin's feed shows
    the restart event. Recreated on every agent start so the path stays correct.
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        import subprocess
        exe = sys.executable
        # launch the EXE DIRECTLY (cmd /c wrappers fail inside Task Scheduler);
        # the agent's single-instance mutex makes any extra launch self-terminate
        subprocess.run(["schtasks", "/Create", "/F", "/TN", "WorkPulseWatchdog",
                        "/SC", "MINUTE", "/MO", "1", "/TR", exe],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        chk = subprocess.run(["schtasks", "/Query", "/TN", "WorkPulseWatchdog"],
                             capture_output=True, text=True).stdout
        if "WorkPulseWatchdog" in chk:
            log_err("watchdog armed: WorkPulseWatchdog task restarts the agent "
                    "within 60s if it is killed")
        else:
            log_err("watchdog task could not be created (needs a normal user session)")
    except Exception as e:
        log_err(f"watchdog error: {e}")


# ----------------------------------------------------------------------------
# SELF CHECK:  cmd:  HRAgent.exe --check
# ----------------------------------------------------------------------------
def selfcheck() -> None:
    lines = []
    cfg = load_config()
    lines.append("HRAgent SELF-CHECK REPORT")
    lines.append("=" * 60)
    lines.append(f"Checked at   : {dt.datetime.now()}")
    lines.append(f"Identity     : {IDENTITY}")
    lines.append(f"EXE folder   : {app_dir()}")
    lines.append(f"Config file  : {CONFIG_PATH}")
    lines.append(f"               exists on disk: {os.path.exists(CONFIG_PATH)}")
    if not os.path.exists(CONFIG_PATH):
        lines.append("               !! CONFIG MISSING -> strong reason for improper or missing configuration.")
    lines.append(f"server_path  : {cfg.get('server_path', '?')}")
    if cfg.get("cloud_url"):
        lines.append(f"CLOUD MODE   : {cfg['cloud_url']}   API key set: {bool(cfg.get('api_key'))}")
    lines.append(f"interval     : {cfg.get('interval_seconds', '?')} s,  heartbeat: {cfg.get('heartbeat_seconds', '?')} s")

    cloud_ok = None
    if cfg.get("cloud_url"):
        try:
            req = urllib.request.Request(cfg["cloud_url"].rstrip("/") + "/ping",
                                         headers={"X-API-Key": cfg.get("api_key", "")})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
            cloud_ok = bool(data.get("ok"))
        except Exception as e:
            log_err(f"selfcheck cloud ping error: {e}")
            cloud_ok = False

    if cloud_ok is True:
        lines.append("Cloud server : REACHABLE - uploads will work")
        ok = True
    elif cloud_ok is False:
        lines.append("Cloud server : NOT REACHABLE !!! -> agents cannot send data (wrong URL/key or no internet)")
        ok = False
    else:
        ok = server_reachable(cfg.get("server_path", ""))
        lines.append(f"HR folder    : {'REACHABLE - writing works' if ok else 'NOT REACHABLE !!! -> agent cannot send data. It will cache locally only.'}")
    lines.append(f"Local cache  : {os.path.join(cache_dir(), 'users')}")
    lines.append(f"Recorder     : {'OK -> foreground app: ' + str(f) if (f := get_foreground()) else 'FAILED'}")
    lines.append(f"Auto-start   : {autostart_status()}")
    lines.append("")
    lines.append("WHAT TO DO:")
    if cfg.get("cloud_url"):
        lines.append(" 1. Cloud mode: if 'Cloud server NOT REACHABLE' -> wrong cloud_url / api_key / no internet.")
    else:
        lines.append(" 1. If 'HR folder: NOT REACHABLE' -> config.json server_path is wrong,")
        lines.append("    or the shared folder is not reachable from THIS laptop.")
        lines.append("    Test manually: cmd ->  dir \\\\ADMIN-PC\\HRData")
        lines.append("    (must work with the SAME user who runs HRAgent).")
    lines.append(" 2. If 'Config missing' -> copy config.json into the folder to the same path as HRAgent.exe.")
    lines.append(" 3. If recorder says FAILED -> this Windows does not allow")
    lines.append("    reading the foreground window (rare). Send in the report.")
    lines.append(" 4. All errors are also stored in:  %APPDATA%\\HRAgent\\agent.log")

    report = os.path.join(app_dir(), "selfcheck_report.txt")
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    if os.environ.get("HRCHECK_NOPEN") != "1":
        os.startfile(report)  # noqa: S606


# ----------------------------------------------------------------------------
# ONE monitoring cycle (exceptions here never kill the agent)
# ----------------------------------------------------------------------------
def step(s: dict) -> None:
    day = str(dt.date.today())

    # ---- day changed? -> build yesterday's PDF and reset counters ---------
    if day != s["today"]:
        recs = []
        try:
            if os.path.exists(os.path.join(cache_dir(), "users", IDENTITY, "day.jsonl")):
                os.makedirs(os.path.join(cache_dir(), "done"), exist_ok=True)
                shutil.copyfile(os.path.join(cache_dir(), "users", IDENTITY, "day.jsonl"),
                                os.path.join(cache_dir(), "done", f"activity_{s['today']}.jsonl"))
                with open(os.path.join(cache_dir(), "users", IDENTITY, "day.jsonl"), "r", encoding="utf-8") as f:
                    recs = [json.loads(l) for l in f if l.strip()]
        except Exception as e:
            log_err(f"day-change copy error: {e}")
        generate_pdf(pdf_file(s["server"], s["today"]), s["today"], recs,
                     s["app_secs"], s["tab_secs"], s["location"], s["ip"])
        # also keep a local PDF backup
        try:
            if os.path.exists(pdf_file(s["server"], s["today"])):
                os.makedirs(os.path.join(cache_dir(), "pdf"), exist_ok=True)
                shutil.copyfile(pdf_file(s["server"], s["today"]),
                                os.path.join(cache_dir(), "pdf", f"activity_{s['today']}.pdf"))
        except Exception:
            pass
        s["app_secs"], s["tab_secs"] = {}, {}
        s["today"] = day

    # ---- respect the HR disable switch ----------------------------------
    if s["cloud_url"]:
        disabled = cloud_disabled(s)
    else:
        disabled = os.path.exists(os.path.join(s["server"], "control", f"{IDENTITY}.disabled"))

    # ---- sample foreground application ----------------------------------
    fg = get_foreground()
    secs = s["interval"]
    if fg and not disabled:
        exe, title = fg
        app = FRIENDLY_NAMES.get(exe.lower(), exe)
        tab = chrome_tab(title) if exe.lower().startswith("chrome") else None
        opened_now = (app != s.get("last_open_app"))          # worker switched to a new app
        if opened_now:
            s["last_open_app"] = app
        tab_changed = bool(tab) and tab != s.get("last_shot_tab")   # switched TAB in browser
        if tab_changed:
            s["last_shot_tab"] = tab
        s["current_app"], s["current_title"], s["current_tab"] = app, title, tab
        s["app_secs"][app] = s["app_secs"].get(app, 0) + secs
        if tab:
            s["tab_secs"][tab] = s["tab_secs"].get(tab, 0) + secs
        ts_now = dt.datetime.now().isoformat(timespec="seconds")
        record = {"ts": ts_now,
                  "user": getpass.getuser(), "machine": socket.gethostname(),
                  "exe": exe, "app": app, "title": title[:200], "secs": secs}
        if tab:
            record["tab"] = tab[:200]
        # store locally (always) -> buffer for cloud too
        append_record(os.path.join(cache_dir(), "users", IDENTITY, "day.jsonl"), record)
        if s["cloud_url"]:
            s["cloud_buffer"].append(record)
            if len(s["cloud_buffer"]) > 2000:            # if the cloud is off for days
                del s["cloud_buffer"][:1000]
        if opened_now:                                   # instant "OPENED" feed for the admin
            event = {"ts": ts_now, "kind": "event", "event": "app_opened",
                     "user": getpass.getuser(), "machine": socket.gethostname(),
                     "exe": exe, "app": app, "title": title[:200]}
            if tab:
                event["tab"] = tab[:200]
            append_record(os.path.join(cache_dir(), "users", IDENTITY, "day.jsonl"), event)
            if s["cloud_url"]:
                s["cloud_buffer"].append(event)
                if time.time() - s.get("last_event_post", 0) >= 2:   # INSTANT live feed
                    s["last_event_post"] = time.time()
                    if post_cloud(s, {"current_app": app, "current_title": title,
                                      "current_tab": tab, "last_seen": ts_now},
                                  s["cloud_buffer"]):
                        s["cloud_buffer"].clear()
            # ---- screenshot evidence on app/tab switch (throttled) ----
            if (opened_now or tab_changed) and time.time() - s.get(
                    "last_shot", 0) >= max(5, int(s.get("shot_interval", 15))):
                s["last_shot"] = time.time()
                shot = take_screenshot(s.get("cfg") or {}, app, title)
                if shot:
                    s.setdefault("shot_count", 0)
                    s["shot_count"] += 1
    elif disabled:
        s["current_app"], s["current_tab"], s["current_title"] = "(disabled by HR)", None, ""

    # ---- every minute: collect real browser links + web searches ---------
    if s.get("browser_history", True) and time.time() - s.get("last_urls", 0) >= 60:
        s["last_urls"] = time.time()
        flush_url_records(s)

    # ---- periodical: sync cache -> share (shared-folder mode only) ------
    if not s["cloud_url"] and time.time() - s["last_sync"] >= 30:
        s["last_sync"] = time.time()
        if not s["online"]:
            s["online"] = server_reachable(s["server"])
        if s["online"]:
            sync_pending(s["server"], cache_dir())

    # ---- periodical: status / heartbeat ---------------------------------
    if time.time() - s["last_status"] >= s["heartbeat"]:
        s["last_status"] = time.time()
        if time.time() - s["last_loc"] >= 3600:          # refresh location hourly
            s["last_loc"] = time.time()
            s["location"], s["ip"] = refresh_location(s["loc"])
        status = {
            "identity": IDENTITY,
            "user": getpass.getuser(),
            "machine": socket.gethostname(),
            "last_seen": dt.datetime.now().isoformat(timespec="seconds"),
            "active": not disabled,
            "enabled": not disabled,
            "current_app": s["current_app"],
            "current_tab": s["current_tab"],
            "current_title": str(s["current_title"])[:120],
            "location": s["location"],
            "ip": s["ip"],
            "windows_user": os.environ.get("USERNAME", ""),
        }
        if s["cloud_url"]:
            if post_cloud(s, status, s["cloud_buffer"]):
                s["cloud_buffer"].clear()
        else:
            write_json(status_file(s["server"]), status)                 # may fail offline
        write_json(os.path.join(cache_dir(), "status.json"), status)
        if s["cloud_url"]:
            pass
        else:
            if not s["online"]:                                          # retry share
                s["online"] = server_reachable(s["server"])
            if s["online"]:
                sync_pending(s["server"], cache_dir())


# ----------------------------------------------------------------------------
# Main loop - never dies: every cycle error is logged and retried
# ----------------------------------------------------------------------------
def main() -> None:
    # ---- special mode: self-install with ADMIN rights (UAC asked once) ----
    if "--install-elevated" in sys.argv:
        install_elevated_tasks()
        return
    # ---- single instance guard: only ONE agent may run -------------------
    ctypes.windll.kernel32.CreateMutexW(None, False, "HRAgentSingleInstanceMutex")
    if ctypes.windll.kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
        log_err("second instance refused: agent already running")
        return
    log_err("========== agent started ==========")
    if not ensure_permissions():                      # first run needs worker consent
        return                                        # declined -> agent does nothing
    cfg = load_config()
    ensure_autostart()
    ensure_watchdog()
    log_restart_event(cfg.get("cloud_url") or "")
    flag = os.path.join(cache_dir(), "fullcontrol_asked.flag")
    fresh_ask = not os.path.isfile(flag) and sys.stdin is not None \
        and hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    try:                                          # mark asked BEFORE asking - a
        with open(flag, "w") as f:                # frozen prompt can never wedge
            f.write(dt.datetime.now().isoformat(timespec="seconds"))
    except Exception:
        pass
    if fresh_ask:
        try:
            request_full_control()
        except Exception:
            pass

    s = {
        "server": cfg["server_path"],
        "cloud_url": str(cfg.get("cloud_url", "") or "").strip(),
        "api_key": str(cfg.get("api_key", "") or "").strip(),
        "cloud_buffer": [],
        "interval": max(1, int(cfg.get("interval_seconds", 10))),
        "heartbeat": max(5, int(cfg.get("heartbeat_seconds", 30))),
        "loc": bool(cfg.get("location_enabled", True)),
        "today": str(dt.date.today()),
        "online": False,
        "last_sync": 0.0,
        "last_status": 0.0,
        "last_loc": 0.0,
        "location": "Unknown",
        "ip": "",
        "app_secs": {},
        "tab_secs": {},
        "current_app": "Idle",
        "current_tab": None,
        "current_title": "",
        "last_urls": 0.0,
        "url_last": {},
        "last_open_app": None,
        "last_event_post": 0.0,
        "last_shot": 0.0,
        "shot_interval": max(5, int(cfg.get("screenshot_seconds", 15))),
        "cfg": cfg,
    }
    load_url_state(s)                 # resume browser-history position (no duplicate tabs)
    s["location"], s["ip"] = refresh_location(s["loc"])
    s["last_loc"] = time.time()
    if s["cloud_url"]:
        s["online"] = True
        log_err(f"CLOUD mode -> {s['cloud_url']} (shared folder not used)")
    else:
        s["online"] = server_reachable(s["server"])
        log_err(f"config ok, server_path={s['server']}, online={s['online']}")

    while True:
        try:
            step(s)
        except Exception as e:
            log_err("cycle error: " + repr(e) + "\n" + traceback.format_exc())
            time.sleep(5)
        time.sleep(s["interval"])


# ----------------------------------------------------------------------------
# Permissions & consent (blueprint 11 "Privacy": clear notice, purpose limitation)
# What the worker allows the agent to observe - visible and inspectable anytime.
# ----------------------------------------------------------------------------
PERMISSION_ITEMS = [
    ("application", "Record which application is on screen (Chrome, Word, games, ...)"),
    ("browser",     "Record the pages opened and searches typed in Chrome / Edge"),
    ("tab",         "Record the current Chrome tab title"),
    ("screenshot",  "Capture a screenshot when you switch app/tab (timestamped evidence)"),
    ("location",    "Approximate city-level location (based on internet IP, optional)"),
    ("autostart",   "Start automatically after every boot so nothing is missed"),
    ("upload",      "Send the daily records to the company WorkPulse server"),
]
NOT_COLLECTED = ("passwords or keystrokes are NOT collected, "
                 "screenshots are NOT taken, personal files are NOT read")


def permission_file() -> str:
    return os.path.join(cache_dir(), "permissions.json")


def _permissions_granted() -> bool:
    return os.path.isfile(permission_file())


def _write_permission(method: str):
    data = {"accepted_at": dt.datetime.now().isoformat(timespec="seconds"),
            "identity": IDENTITY, "method": method,
            "permissions": [p[0] for p in PERMISSION_ITEMS],
            "not_collected": NOT_COLLECTED}
    try:
        write_json(permission_file(), data)
    except Exception:
        pass
    log_err(f"permissions {method}")


def ensure_permissions() -> bool:
    """First run: show a clear consent screen; every run after that: silent."""
    if _permissions_granted():
        return True
    w, h = 60, len(PERMISSION_ITEMS) + 12
    lines = []
    for code, desc in PERMISSION_ITEMS:
        lines.append(f"  - {code:<11} {desc}")
    box = "\n".join([
        "",
        "=" * h,
        "  WorkPulse - what this program is allowed to see",
        "=" * h,
        *lines,
        "",
        f"  * {NOT_COLLECTED}",
        "  * Every access needs the company's written approval.",
        "  * You can inspect this list anytime:  HRAgent.exe --permissions",
        "",
        '  Type  ACCEPT  and press Enter to allow these (anything else = OFF):',
        "  > ",
    ])
    auto = False
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            print(box, end="", flush=True)
            ans = input("    ").strip().lower()
            if ans in ("accept", "a", "y", "yes"):
                _write_permission("user typed ACCEPT at first run")
                log_audit_note()
                return True
            print("  Not accepted -> the agent stops. Nothing will be recorded.")
            log_err("permissions DECLINED by user - agent will exit")
            time.sleep(3)
            return False
        auto = True
    except Exception:
        auto = True
    if auto:                     # scheduled boot without a user watching the screen
        _write_permission("auto (company-authorized boot, consent screen shown at install)")
        return True


def log_audit_note():
    """Local audit note: drop a marker record that permissions were granted."""
    rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
           "user": getpass.getuser(), "machine": socket.gethostname(),
           "exe": "agent", "app": "WorkPulse", "title": "permissions granted by worker",
           "secs": 0, "kind": "permission"}
    append_record(os.path.join(cache_dir(), "users", IDENTITY, "day.jsonl"), rec)


def show_permissions() -> None:
    """--permissions : full transparency about what the agent does/doesn't do."""
    print()
    print("=" * 66)
    print("  WorkPulse Observer - permission & access report")
    print("=" * 66)
    print(f"  Device identity : {IDENTITY}")
    print(f"  Config location : {CONFIG_PATH}")
    print(f"  Local cache     : {cache_dir()}")
    print(f"  Cloud server    : {load_config().get('cloud_url') or '(shared-folder mode)'}")
    print()
    print("  Access allowed (accepted by worker / authorized boot):")
    for code, desc in PERMISSION_ITEMS:
        print(f"    [x] {code:<11} {desc}")
    print()
    print("  Never collected:")
    print("    [ ] Passwords or keystrokes")
    print("    [ ] Screenshots / screen recording")
    print("    [ ] Personal files outside activity context")
    if _permissions_granted():
        try:
            with open(permission_file(), "r", encoding="utf-8") as f:
                d = json.load(f)
            print()
            print(f"  Accepted on: {d.get('accepted_at')}   ({d.get('method')})")
        except Exception:
            pass
    else:
        print()
        print("  STATUS: permissions NOT accepted yet.")
    print("=" * 66)


if __name__ == "__main__":
    try:
        if "--permissions" in sys.argv:
            show_permissions()
        elif "--check" in sys.argv:
            selfcheck()
        else:
            main()
    except Exception as e:
        log_err("FATAL: " + repr(e) + "\n" + traceback.format_exc())
