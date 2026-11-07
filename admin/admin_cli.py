"""
HRAdmin - the HR / admin command GUI (runs in the command line, no database).

Reads everything from the shared HRData folder that HRAgent (employee side)
writes to. Provides:

  1. Dashboard          - who is online, what app they use, Chrome tab usage,
                          top applications across all employees
  2. User management    - user name, laptop name, Active / Offline / Disabled
  3. Export agent EXE   - copy the employee EXE (+ ready config) to give away
  4. Settings           - shared data folder location + backup folder + backup now
  5. Reports            - generate PDF record for any user / any date
"""

import json
import os
import shutil
import sys
import datetime as dt
import http.server
import socket
import threading
import urllib.request
import urllib.parse
import time as _time
import msvcrt          # windows keyboard poll (this GUI is Windows-only)

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from fpdf import FPDF
from fpdf.enums import XPos, YPos

console = Console()

# make output safe on old Windows consoles (cp1252) that cannot print unicode
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
os.system("")   # enable ANSI escape codes on Windows

ACTIVE_WINDOW = 180          # last_seen < 180 s  ->  "ACTIVE"


def _app_dir() -> str:
    """Folder of the EXE when frozen, else folder of this script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(_app_dir(), "admin_config.json")
DEFAULT_CONFIG = {
    "data_path": r"C:\HRData",      # same folder agents write to
    "backup_path": r"C:\HRBackup",  # where backups / reports are stored
    "agent_exe": r"",               # full path to dist\HRAgent.exe (for export)
    "mobile_apk": r"",              # full path to the built phone APK (for export)
    "cloud_url": "",                # e.g. https://hr-monitor-xxxx.onrender.com
    "api_key": "",                  # same secret as the server's HR_API_KEY
}


def load_cfg() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_cfg(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ----------------------------------------------------------------------------
# Cloud access (Render server) - used when cloud_url is configured
# ----------------------------------------------------------------------------
def cloud_get(cfg: dict, path: str) -> dict:
    url = cfg["cloud_url"].rstrip("/") + path
    req = urllib.request.Request(url, headers={"X-API-Key": cfg.get("api_key", "")})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def cloud_post(cfg: dict, path: str, obj: dict) -> dict:
    url = cfg["cloud_url"].rstrip("/") + path
    body = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-API-Key": cfg.get("api_key", "")})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


# ----------------------------------------------------------------------------
# Data reading (local shared folder AND/OR cloud - both are merged)
# ----------------------------------------------------------------------------
def list_identities(cfg: dict) -> list:
    out = set()
    users_dir = os.path.join(cfg["data_path"], "users")
    if os.path.isdir(users_dir):
        for name in sorted(os.listdir(users_dir)):
            if os.path.isdir(os.path.join(users_dir, name)) and "@" in name:
                out.add(name)
    if cfg.get("cloud_url"):
        try:
            for ident in cloud_get(cfg, "/api/identity_list").get("identities", []):
                if ident:
                    out.add(ident)
        except Exception:
            console.print("[dim yellow](cloud not reachable now)[/dim yellow]")
    return sorted(out)


def read_status(cfg: dict, identity: str) -> dict:
    path = os.path.join(cfg["data_path"], "status", f"{identity}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        pass
    if cfg.get("cloud_url"):
        try:
            return cloud_get(cfg, "/api/status_one?identity=" + urllib.parse.quote(identity)).get("status") or {}
        except Exception:
            pass
    return {}


def read_day_records(cfg: dict, identity: str, day: str) -> list:
    recs = []
    path = os.path.join(cfg["data_path"], "users", identity, f"activity_{day}.jsonl")
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    recs.append(json.loads(line))
    except Exception:
        pass
    if not recs and cfg.get("cloud_url"):
        try:
            recs = cloud_get(cfg, f"/api/records?identity={urllib.parse.quote(identity)}&day={day}").get("records", [])
        except Exception:
            pass
    return recs


def state_of(cfg: dict, identity: str, status: dict) -> str:
    if status.get("enabled") is False:
        return "DISABLED"
    if os.path.exists(os.path.join(cfg["data_path"], "control", f"{identity}.disabled")):
        return "DISABLED"
    last = status.get("last_seen", "")
    if last:
        try:
            delta = (dt.datetime.now() - dt.datetime.fromisoformat(last)).total_seconds()
            if delta <= ACTIVE_WINDOW:
                return "ACTIVE"
            return "OFFLINE"
        except Exception:
            pass
    return "NO REPORT"


def aggregate(records: list):
    """Return app_secs, tab_secs aggregated from raw records."""
    app_secs, tab_secs = {}, {}
    for r in records:
        app = r.get("app") or r.get("exe") or "Unknown"
        app_secs[app] = app_secs.get(app, 0) + (r.get("secs") or 0)
        tab = r.get("tab")
        if tab:
            tab_secs[tab] = tab_secs.get(tab, 0) + (r.get("secs") or 0)
    return app_secs, tab_secs


def hms(s) -> str:
    s = int(s)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


# ----------------------------------------------------------------------------
# Screens
# ----------------------------------------------------------------------------
def pause():
    console.input("\n[dim]Press Enter to return to menu...[/dim]")


def render_dashboard(cfg: dict):
    identities = list_identities(cfg)
    day = str(dt.date.today())
    console.rule(f"[bold cyan]DASHBOARD - today ({day})")

    if not identities:
        console.print("[red]No employee data found. Check Settings -> data folder, "
                      "and make sure the agent EXE is running on employee laptops.[/red]")
        pause()
        return

    # --- live table: who is using what right now ---
    t = Table(box=box.ROUNDED, header_style="bold white on dark_blue", show_lines=False)
    t.add_column("Employee")
    t.add_column("Laptop")
    t.add_column("State")
    t.add_column("Current app")
    t.add_column("Chrome tab")
    t.add_column("Last seen")
    t.add_column("Location")
    for ident in identities:
        st = read_status(cfg, ident)
        state = state_of(cfg, ident, st)
        color = {"ACTIVE": "green", "OFFLINE": "red", "DISABLED": "yellow", "NO REPORT": "grey"}[state]
        t.add_row(ident.split("@")[0], ident.split("@", 1)[1],
                  f"[{color}]{state}[/{color}]",
                  st.get("current_app", "-")[:28],
                  (st.get("current_tab") or "-")[:30],
                  st.get("last_seen", "-")[:19],
                  st.get("location", "-")[:28])
    console.print(t)

    # --- top applications across all employees today ---
    total_apps, total_tabs = {}, {}
    for ident in identities:
        app_secs, tab_secs = aggregate(read_day_records(cfg, ident, day))
        for a, s in app_secs.items():
            total_apps[a] = total_apps.get(a, 0) + s
        for tab, s in tab_secs.items():
            total_tabs[tab] = total_tabs.get(tab, 0) + s

    apps = sorted(total_apps.items(), key=lambda x: -x[1])[:10]
    ta = Table(title="TOP APPLICATIONS (all employees, today)", box=box.SIMPLE, title_style="bold cyan")
    ta.add_column("#"); ta.add_column("Application"); ta.add_column("Total time")
    for i, (a, s) in enumerate(apps, 1):
        bar = "#" * max(1, round(s / (apps[0][1] or 1) * 30))
        ta.add_row(str(i), a, f"{hms(s)}  [cyan]{bar}[/cyan]")
    console.print(ta)

    tabs = sorted(total_tabs.items(), key=lambda x: -x[1])[:10]
    tt = Table(title="MOST USED CHROME TABS (all employees, today)", box=box.SIMPLE, title_style="bold cyan")
    tt.add_column("#"); tt.add_column("Tab"); tt.add_column("Total time")
    for i, (tab, s) in enumerate(tabs, 1):
        tt.add_row(str(i), tab[:60], hms(s))
    console.print(tt)


def live_dashboard(cfg: dict, refresh: int = 10):
    """LIVE WATCH: redrew the dashboard every `refresh` seconds until a key is pressed."""
    try:
        console.print(f"[yellow]LIVE WATCH started - auto refresh every {refresh}s."
                      " Press any key to return to the menu...[/yellow]")
        while True:
            console.clear()
            render_dashboard(cfg)
            console.print(f"[yellow]LIVE WATCH - refreshing every {refresh}s. Press any key to return...[/yellow]")
            deadline = _time.time() + refresh
            while _time.time() < deadline:
                if msvcrt.kbhit():
                    msvcrt.getch()
                    return
                _time.sleep(0.2)
    except KeyboardInterrupt:
        return


def screen_dashboard(cfg: dict):
    render_dashboard(cfg)
    ch = console.input("\n  [b]L[/b] = LIVE WATCH (auto-refresh)      [Enter] = back : ").strip().lower()
    if ch == "l":
        live_dashboard(cfg)


def screen_worker_detail(cfg: dict):
    """Deep per-worker view: EVERY app they used today + the actual links & searches,
    refreshing live (any key returns)."""
    identities = list_identities(cfg)
    if not identities:
        console.print("[yellow]No workers have reported yet. Run an agent first.[/yellow]")
        pause(); return
    console.rule("[bold cyan]WORKER DETAIL")
    for i, ident in enumerate(identities, 1):
        console.print(f"   {i}) {ident.replace('@', '  ')[:60]}")
    ch = console.input("  Select worker # (Enter = 1): ").strip()
    idx = 0
    if ch:
        try:
            idx = int(ch) - 1
        except ValueError:
            return
    if not (0 <= idx < len(identities)):
        console.print("bad choice"); pause(); return
    ident = identities[idx]
    day = str(dt.date.today())
    short = lambda url: (urllib.parse.urlparse(url).netloc + urllib.parse.urlparse(url).path)[:38]

    console.print("[yellow]LIVE worker view - refreshes every 12s. Press any key to return...[/yellow]")
    _time.sleep(1.0)
    while True:
        try:
            console.clear()
            st = read_status(cfg, ident)
            state = state_of(cfg, ident, st)
            console.rule(f"[bold cyan]DETAIL - {ident} - {day}")
            console.print(f"  State: [bold]{state}[/bold]   Last seen: {st.get('last_seen','-')}   "
                          f"Location: {st.get('location','-')}   IP: {st.get('ip','-')}")
            console.print(f"  Right now: {st.get('current_app','-')}   "
                          f"Chrome tab: {st.get('current_tab') or '-'}   "
                          f"Window: {st.get('current_title') or '-'}")

            recs = read_day_records(cfg, ident, day)
            app_secs, _tab_secs = aggregate(recs)
            apps = sorted(app_secs.items(), key=lambda x: -x[1])
            t = Table(title=f"ALL APPLICATIONS USED TODAY ({len(apps)} apps)", box=box.SIMPLE, title_style="bold cyan")
            t.add_column("#"); t.add_column("Application"); t.add_column("Time"); t.add_column("Usage")
            max_sec = max([s for _, s in apps] + [1])
            for i, (a, sec) in enumerate(apps, 1):
                t.add_row(str(i), a[:34], hms(sec), "#" * max(1, int(sec * 30 / max_sec)))
            console.print(t)

            brows = [r for r in recs if r.get("url")]
            console.print(f"[bold cyan]BROWSER ACTIVITY (real links + searches) - "
                          f"{len(brows)} entries today[/bold cyan]")
            bt = Table(box=box.SIMPLE)
            bt.add_column("Time"); bt.add_column("Type"); bt.add_column("What"); bt.add_column("Page title")
            for r in sorted(brows, key=lambda x: x.get("ts", ""))[-30:][::-1]:
                if r.get("search"):
                    typ, what = "[bright yellow]SEARCH[/bright yellow]", r["search"][:42]
                    where = f"@ {short(r['url'])}"
                else:
                    typ, what = "LINK", short(r.get("url", ""))
                    where = ""
                bt.add_row(r.get("ts", "-")[11:16], typ, what, (r.get("title") or "")[:46])
            console.print(bt)

            console.print("[yellow]LIVE - updating every 12s. Press any key to return...[/yellow]")
            deadline = _time.time() + 12
            while _time.time() < deadline:
                if msvcrt.kbhit():
                    msvcrt.getch()
                    return
                _time.sleep(0.2)
        except KeyboardInterrupt:
            return


def screen_users(cfg: dict):
    while True:
        console.rule("[bold cyan]USER MANAGEMENT")
        identities = list_identities(cfg)
        if not identities:
            console.print("[red]No users found yet.[/red]"); pause(); return
        t = Table(box=box.ROUNDED, header_style="bold white on dark_blue")
        t.add_column("#"); t.add_column("User name"); t.add_column("Laptop name")
        t.add_column("State"); t.add_column("Last activity")
        for i, ident in enumerate(identities, 1):
            st = read_status(cfg, ident)
            state = state_of(cfg, ident, st)
            color = {"ACTIVE": "green", "OFFLINE": "red", "DISABLED": "yellow", "NO REPORT": "grey"}[state]
            t.add_row(str(i), ident.split("@")[0], ident.split("@", 1)[1],
                      f"[{color}]{state}[/{color}]", st.get("last_seen", "-")[:19])
        console.print(t)
        console.print("  [b]S[/b] = show detail of one user    [b]D[/b] = disable/enable a user    [b]B[/b] = back")
        ch = console.input("  Choice: ").strip().lower()
        if ch == "b":
            return
        if ch == "s":
            n = console.input("  User number: ").strip()
            if not n.isdigit() or not (1 <= int(n) <= len(identities)):
                console.print("[red]Bad number.[/red]"); continue
            user_screen_detail(cfg, identities[int(n) - 1])
        if ch == "d":
            n = console.input("  User number: ").strip()
            if not n.isdigit() or not (1 <= int(n) <= len(identities)):
                console.print("[red]Bad number.[/red]"); continue
            flip_disable(cfg, identities[int(n) - 1])
            pause()


def user_screen_detail(cfg: dict, ident: str):
    st = read_status(cfg, ident)
    console.print(Panel(f"user      : {ident}\n"
                        f"state     : {state_of(cfg, ident, st)}\n"
                        f"current   : {st.get('current_app','-')}  tab: {st.get('current_tab','-')}\n"
                        f"location  : {st.get('location','-')}  IP: {st.get('ip','-')}\n"
                        f"last seen : {st.get('last_seen','-')}",
                        title=f"[bold]{ident}[/bold]", border_style="cyan"))
    for day in [str(dt.date.today()), str(dt.date.today() - dt.timedelta(days=1))]:
        recs = read_day_records(cfg, ident, day)
        if not recs:
            continue
        app_secs, tab_secs = aggregate(recs)
        apps = sorted(app_secs.items(), key=lambda x: -x[1])[:5]
        console.print(f"\n[bold cyan]{day}[/bold cyan] - top apps:")
        for a, s in apps:
            console.print(f"   {a:<26} {hms(s)}")
        if tab_secs:
            console.print("[bold cyan]Chrome tabs:[/bold cyan]")
        for tab, s in sorted(tab_secs.items(), key=lambda x: -x[1])[:5]:
            console.print(f"   {tab[:50]:<50} {hms(s)}")
        # latest 10 events
        console.print("[bold cyan]Latest events:[/bold cyan]")
        for r in recs[-10:]:
            tab = f" | tab: {r.get('tab','')}" if r.get("tab") else ""
            console.print(f"   {r.get('ts','')}  {r.get('app','')}  | {r.get('title','')[:55]}{tab}")


def flip_disable(cfg: dict, ident: str):
    st = read_status(cfg, ident)
    now_disabled = st.get("enabled") is False
    if ident in (st.get("identity") or ident) and cfg.get("cloud_url"):
        try:
            cloud_post(cfg, "/api/disable", {"identity": ident, "disabled": not now_disabled})
            console.print(f"[green]{'ENABLED' if now_disabled else 'DISABLED'} via cloud for {ident} (agent obeys within ~1 min).[/green]")
            return
        except Exception as e:
            console.print(f"[red]Cloud disable failed: {e} (also writing local flag)[/red]")
    cdir = os.path.join(cfg["data_path"], "control")
    os.makedirs(cdir, exist_ok=True)
    flag = os.path.join(cdir, f"{ident}.disabled")
    if os.path.exists(flag):
        os.remove(flag)
        console.print(f"[green]ENABLED monitoring for {ident} (agent resumes within ~1 min).[/green]")
    else:
        open(flag, "w").close()
        console.print(f"[yellow]DISABLED monitoring for {ident} (agent pauses within ~1 min).[/yellow]")


def find_mobile_apk(cfg: dict) -> str:
    """Where is the built phone APK? (settings value first, then auto-search)"""
    cand = [cfg.get("mobile_apk", "")]
    root = os.path.dirname(os.path.abspath(_app_dir()))     # project root (dist\..)
    cand += [
        os.path.join(root, "mobile", "app", "build", "outputs", "apk", "debug", "app-debug.apk"),
        os.path.join(root, "mobile", "app", "build", "outputs", "apk", "debug", "app-debug.apk".replace("app-debug", "app-release")),
        os.path.join(root, "mobile", "app", "build", "outputs", "apk", "release", "app-release.apk"),
    ]
    for p in cand:
        if p and os.path.isfile(p):
            return p
    return ""


def screen_export_agent(cfg: dict):
    console.rule("[bold cyan]EXPORT AGENT EXE (gives the employee-side EXE)")
    agent_exe = cfg.get("agent_exe", "")
    if not agent_exe or not os.path.isfile(agent_exe):
        console.print(f"Current agent EXE path: [red]not set[/red]")
        agent_exe = console.input("Enter full path to HRAgent.exe (e.g. C:\\code\\hr-monitor\\dist\\HRAgent.exe): ").strip().strip('"')
        if agent_exe and os.path.isfile(agent_exe):
            cfg["agent_exe"] = agent_exe
            save_cfg(cfg)
        else:
            console.print("[red]File does not exist. Build it first (see README -> build step).[/red]")
            pause(); return

    console.print(f"Agent EXE : [green]{agent_exe}[/green]")
    dest = console.input("Destination folder to copy the EXE to (Enter = USB folder C:\\\\HRAgentKit): ").strip().strip('"') or r"C:\HRAgentKit"
    try:
        os.makedirs(dest, exist_ok=True)
        shutil.copy2(agent_exe, os.path.join(dest, "HRAgent.exe"))
        # also copy a pre-filled config so the employee needs zero setup
        src_cfg = os.path.join(os.path.dirname(agent_exe), "config.json")
        agent_src_cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent", "config.json")
        if os.path.isfile(src_cfg):
            shutil.copy2(src_cfg, os.path.join(dest, "config.json"))
        elif os.path.isfile(agent_src_cfg):
            shutil.copy2(agent_src_cfg, os.path.join(dest, "config.json"))
        else:
            with open(os.path.join(dest, "config.json"), "w") as f:
                json.dump({"server_path": cfg["data_path"],
                           "cloud_url": cfg.get("cloud_url", ""),
                           "api_key": cfg.get("api_key", ""),
                           "interval_seconds": 10,
                           "heartbeat_seconds": 30, "location_enabled": True}, f, indent=2)
        readme = os.path.join(dest, "INSTALL-STEPS.txt")
        with open(readme, "w", encoding="utf-8") as f:
            f.write("HRAgent install steps (employee laptop)\n"
                    "=======================================\n"
                    "1. Copy HRAgent.exe and config.json into the same folder.\n"
                    "2. config.json already points to the HR server - do not edit it.\n"
                    "3. Double-click HRAgent.exe once. It hides immediately,\n"
                    "   registers itself to start on every boot, and begins recording.\n"
                    "That's all.\n")

        # ---------- phone side (APK) ----------
        apk_src = find_mobile_apk(cfg)
        if apk_src:
            shutil.copy2(apk_src, os.path.join(dest, "HRMonitor-Mobile.apk"))
            console.print(f"[green]Phone APK added to the kit: HRMonitor-Mobile.apk  ({os.path.basename(apk_src)})[/green]")
            console.print("  Send HRMonitor-Mobile.apk to workers on WhatsApp/Drive/USB;")
            console.print("  it installs via Android's package installer, then they enter the")
            console.print("  same server + API key and tap START TRACKING.")
        else:
            console.print("[yellow]No phone APK found yet -> the kit contains only the PC EXE for now.[/yellow]")
            console.print("[yellow]Build the APK once (see mobile\\README-MOBILE.md), then run Export again;")
            console.print("[yellow]it will be picked up automatically from the mobile\\ build output.[/yellow]")

        console.print(f"[green]Kit copied to {dest}.[/green]")
        console.print(f"[green]Kit copied to {dest} (HRAgent.exe + config.json + INSTALL-STEPS.txt).[/green]")
    except Exception as e:
        console.print(f"[red]Export failed: {e}[/red]")
    pause()


def screen_settings(cfg: dict):
    while True:
        console.rule("[bold cyan]SETTINGS")
        console.print(f"  1) Records folder (agents write here) : [green]{cfg['data_path']}[/green]")
        console.print(f"  2) Backup folder                      : [green]{cfg['backup_path']}[/green]")
        console.print(f"  3) Agent EXE path (for export)        : [green]{cfg.get('agent_exe') or 'not set'}[/green]")
        console.print("  4) Backup records now (copy all records + PDFs into backup folder)")
        console.print("  5) Open records folder in Explorer")
        console.print(f"  6) Cloud settings (Render server)     : [green]{cfg.get('cloud_url') or 'not set'}[/green]")
        console.print(f"  7) Mobile APK path (auto for Export)  : [green]{cfg.get('mobile_apk') or 'auto-detect'}[/green]")
        console.print("  B) Back")
        ch = console.input("  Choice: ").strip().lower()
        if ch == "1":
            p = console.input("  New folder path (UNC share like \\\\ADMIN-PC\\HRData or C:\\HRData): ").strip().strip('"')
            if p:
                try:
                    os.makedirs(p, exist_ok=True)
                    cfg["data_path"] = p; save_cfg(cfg)
                    console.print("[green]Saved. Remember: agent config.json must point to the same folder.[/green]")
                except Exception as e:
                    console.print(f"[red]Cannot create folder: {e}[/red]")
        elif ch == "2":
            p = console.input("  New backup folder path: ").strip().strip('"')
            if p:
                cfg["backup_path"] = p; save_cfg(cfg); console.print("[green]Saved.[/green]")
        elif ch == "3":
            p = console.input("  Full path to HRAgent.exe: ").strip().strip('"')
            if p:
                cfg["agent_exe"] = p; save_cfg(cfg)
        elif ch == "4":
            do_backup(cfg)
            pause()
        elif ch == "5":
            try:
                os.startfile(cfg["data_path"])          # noqa: S606
            except Exception as e:
                console.print(f"[red]Cannot open: {e}[/red]")
        elif ch == "6":
            u = console.input("  Cloud server URL (Enter = keep, e.g. https://hr-monitor-xxxx.onrender.com): ").strip().strip('"')
            if u:
                cfg["cloud_url"] = u; save_cfg(cfg)
            k = console.input("  Cloud API key (Enter = keep): ").strip().strip('"')
            if k:
                cfg["api_key"] = k; save_cfg(cfg)
            if cfg.get("cloud_url"):
                try:
                    d = cloud_get(cfg, "/ping")
                    console.print(f"[green]Cloud OK - server in '{d.get('mode')}' mode.[/green]")
                except Exception as e:
                    console.print(f"[red]Cloud test FAILED: {e}[/red]")
        elif ch == "7":
            p = console.input("  Full path to the built APK (app-debug.apk, Enter = auto-detect from project): ").strip().strip('"')
            cfg["mobile_apk"] = p; save_cfg(cfg)
            if p and not os.path.isfile(p):
                console.print("[yellow]File not found - Export will keep auto-detecting![/yellow]")
        elif ch == "b":
            return


def do_backup(cfg: dict):
    src, dst = cfg["data_path"], cfg["backup_path"]
    if not os.path.isdir(src):
        console.print(f"[red]Records folder not found: {src}[/red]"); return
    target = os.path.join(dst, f"backup_{dt.datetime.now().strftime('%Y-%m-%d_%H%M%S')}")
    try:
        console.print("Backing up records and PDFs (this can take a moment)...")
        shutil.copytree(src, target, dirs_exist_ok=True)
        console.print(f"[green]Backup complete -> {target}[/green]")
    except Exception as e:
        console.print(f"[red]Backup failed: {e}[/red]")


# ----------------------------------------------------------------------------
# Phone sync server - workers' phones (Android app) upload activity here
# ----------------------------------------------------------------------------
_SYNC = {"server": None, "data_path": None}
SYNC_PORT = 8595


def get_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class PhoneSyncHandler(http.server.BaseHTTPRequestHandler):
    """POST /upload  {status:{user, device, ...}, records:[{ts, app, secs,...}]}"""

    def log_message(self, *a):        # keep the console clean
        return

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/ping":
            self._send(200, {"ok": True, "who": "HRMonitor phone sync"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/upload":
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            st = body.get("status", {}) or {}
            recs = body.get("records", []) or []

            ident = f"{st.get('user', 'phone')}@{st.get('device', 'mobile')}"
            data_path = _SYNC["data_path"]

            # daily records -> same layout as laptop agents
            for r in recs:
                ts = str(r.get("ts", ""))
                day = ts[:10] or str(dt.date.today())
                path = os.path.join(data_path, "users", ident, f"activity_{day}.jsonl")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

            st.setdefault("last_seen", dt.datetime.now().isoformat(timespec="seconds"))
            st["identity"] = ident
            st.setdefault("active", True)
            st["kind"] = "phone"
            write_json(os.path.join(data_path, "status", f"{ident}.json"), st)
            self._send(200, {"received": len(recs)})
        except Exception as e:
            self._send(400, {"error": str(e)})


def write_json(path: str, obj: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def start_phone_server(cfg: dict):
    if _SYNC["server"] is not None:
        return
    _SYNC["data_path"] = cfg["data_path"]
    os.makedirs(cfg["data_path"], exist_ok=True)

    class Handler(PhoneSyncHandler):
        pass

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", SYNC_PORT), Handler)
    _SYNC["server"] = srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def stop_phone_server():
    if _SYNC["server"] is not None:
        try:
            _SYNC["server"].shutdown()
            _SYNC["server"].server_close()
        except Exception:
            pass
        _SYNC["server"] = None


def screen_phone_server(cfg: dict):
    while True:
        console.rule("[bold cyan]PHONE SYNC - mobile workers (Android app)")
        if _SYNC["server"] is None:
            console.print("  Phone sync server: [red]STOPPED[/red]   (phones upload to this PC over Wi-Fi)")
            console.print("  1) START  the phone sync server (port " + str(SYNC_PORT) + ")")
        else:
            console.print(f"  Phone sync server: [green]RUNNING[/green] on port {SYNC_PORT}  -> phones appear on Dashboard")
            console.print("  1) STOP the phone sync server")
        ip = get_lan_ip()
        console.print(f"  This PC's Wi-Fi IP: [green]{ip}[/green]  (type this into the phone app:  http://{ip}:{SYNC_PORT})")
        console.print("  2) Open Windows firewall for port " + str(SYNC_PORT) + " (run once; may ask admin rights)")
        console.print("  3) Help - how workers connect")
        console.print("  B) Back")
        ch = console.input("  Choice: ").strip().lower()
        if ch == "1":
            if _SYNC["server"] is None:
                try:
                    start_phone_server(cfg)
                    console.print("[green]Server started. Keep this window open while monitoring phones.[/green]")
                except Exception as e:
                    console.print(f"[red]Could not start (port busy?): {e}[/red]")
            else:
                stop_phone_server()
                console.print("[yellow]Server stopped.[/yellow]")
        elif ch == "2":
            os.system(f'netsh advfirewall firewall add rule name="HRPhoneSync{SYNC_PORT}" dir=in action=allow protocol=TCP localport={SYNC_PORT}')
            console.print("[green]Firewall rule added (allows phones to connect to this PC).[/green]")
        elif ch == "3":
            console.print(Panel(
                "1. On the ADMIN PC:  start this phone sync server (option 1 above).\n"
                "2. On the WORKER'S PHONE:  install the HRMonitor Mobile app (see mobile/README-MOBILE.md)\n"
                "   -> in the app enter:  worker name + this PC's IP (shown above) + port " + str(SYNC_PORT) + "\n"
                "   -> grant 'Usage access' + 'Location' permissions, then tap START TRACKING.\n"
                "3. The phone and this PC must be on the same Wi-Fi network.\n"
                "4. Phone activity then appears in Dashboard / Reports like any laptop.",
                title="Phone workers", border_style="cyan"))
        elif ch == "b":
            return
        pause()


def screen_reports(cfg: dict):
    console.rule("[bold cyan]REPORTS - build a PDF record for any user / date")
    identities = [i for i in list_identities(cfg)]
    if not identities:
        console.print("[red]No users found.[/red]"); pause(); return
    for i, ident in enumerate(identities, 1):
        console.print(f"  {i}) {ident}")
    n = console.input("  User number: ").strip()
    day = console.input(f"  Date (YYYY-MM-DD, Enter = today): ").strip() or str(dt.date.today())
    if not n.isdigit() or not (1 <= int(n) <= len(identities)):
        console.print("[red]Bad number.[/red]"); pause(); return
    ident = identities[int(n) - 1]
    build_pdf_for(cfg, ident, day)


def to_latin(t) -> str:
    return str(t).encode("latin-1", "replace").decode("latin-1")


def build_pdf_for(cfg: dict, ident: str, day: str):
    recs = read_day_records(cfg, ident, day)
    if not recs:
        console.print(f"[red]No records for {ident} on {day}.[/red]"); pause(); return
    app_secs, tab_secs = aggregate(recs)
    st = read_status(cfg, ident)
    out_dir = os.path.join(cfg["backup_path"], "reports")
    out = os.path.join(out_dir, f"{ident}_activity_{day}.pdf")

    pdf = FPDF("P", "mm", "A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 10, to_latin("Employee Activity Report"), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, to_latin(f"Employee  : {ident.split('@')[0]}     Machine: {ident.split('@',1)[1]}"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 7, to_latin(f"Date      : {day}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 7, to_latin(f"Location  : {st.get('location','-')}   IP: {st.get('ip','-')}"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 7, to_latin(f"Records   : {len(recs)}    First: {recs[0].get('ts','')}    Last: {recs[-1].get('ts','')}"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    def table(title, pairs, total):
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, to_latin(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(110, 7, "  Item", border=1)
        pdf.cell(35, 7, "  Time used", border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        for name, secs in pairs:
            pdf.cell(110, 7, to_latin(f"  {name[:55]}"), border=1)
            pdf.cell(35, 7, to_latin(f"  {hms(secs)}"), border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.cell(110, 7, "  TOTAL", border=1)
        pdf.cell(35, 7, to_latin(f"  {hms(total)}"), border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)

    table("1. Applications used", sorted(app_secs.items(), key=lambda x: -x[1]), sum(app_secs.values()))
    if tab_secs:
        table("2. Chrome tabs visited", sorted(tab_secs.items(), key=lambda x: -x[1]), sum(tab_secs.values()))

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, to_latin("3. Activity timeline (latest events)"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 8)
    for r in recs[-40:]:
        pdf.cell(0, 5, to_latin(f"{r.get('ts','')}  {r.get('app','')}  | {r.get('title','')[:70]}"),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    try:
        os.makedirs(out_dir, exist_ok=True)
        pdf.output(out)
        console.print(Panel(f"[green]PDF saved:[/green]\n{out}\n\nOpening it now...",
                            border_style="green"))
        os.startfile(out)  # noqa: S606
    except Exception as e:
        console.print(f"[red]PDF failed: {e}[/red]")
    pause()


# ----------------------------------------------------------------------------
# Header + main menu ("command GUI")
# ----------------------------------------------------------------------------
BANNER = """\
   __  __          _______  ____     __  __
  /\\ \\/\\ \\        / ____\\| \\/\\ \\     /\\ \\/\\ \\
  \\ \\ \\_\\ \\  ___  \\ \\___\\ \\ \\/\\ \\    \\ \\ \\_\\ \\
   \\ \\  _  \\/ __ \\  \\  __ \\ \\  _  \\    \\ \\  _  \\
    \\ \\ \\ \\ \\/ \\_\\ \\  \\ \\_\\ \\ \\ \\_\\ \\    \\ \\ \\ \\ \\
     \\ \\_\\ \\_\\ \\____/   \\_\\ \\_\\ \\_\\ \\_\\    \\ \\_\\ \\_\\
      \\/_/\\/_/ \\___/     \\/_/\\/ /\\/_/ \\/    \\/_/\\/_/
          Employee Monitoring  -  Command GUI (no database)
"""


def main():
    cfg = load_cfg()
    while True:
        console.clear()
        console.print(Panel(BANNER, border_style="cyan"))
        console.print(f"  Records folder : [green]{cfg['data_path']}[/green]    "
                      f"Backup folder : [green]{cfg['backup_path']}[/green]\n"
                      f"  Cloud          : [green]{cfg.get('cloud_url') or 'not set (local/share mode)'}[/green]\n")
        console.print("  [b]1)[/b] Dashboard        (who is using what - live)")
        console.print("  [b]2)[/b] User management  (user, laptop, active status)")
        console.print("  [b]3)[/b] Export agent EXE (downloadable employee EXE kit)")
        console.print("  [b]4)[/b] Settings         (records folder, backup folder, backup now)")
        console.print("  [b]5)[/b] Reports          (build PDF record for a user / date)")
        console.print("  [b]6)[/b] Phone sync        (start/stop server for worker PHONES - Android app)")
        console.print("  [b]7)[/b] [bold]Worker detail[/bold]  (ALL apps used + real links & searches - LIVE)")
        console.print("  [b]0)[/b] Exit")
        ch = console.input("\n  Choose: ").strip()
        if ch == "1":
            screen_dashboard(cfg)
        elif ch == "2":
            screen_users(cfg)
        elif ch == "3":
            screen_export_agent(cfg)
        elif ch == "4":
            screen_settings(cfg)
        elif ch == "5":
            screen_reports(cfg)
        elif ch == "6":
            screen_phone_server(cfg)
        elif ch == "7":
            screen_worker_detail(cfg)
        elif ch == "0":
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
