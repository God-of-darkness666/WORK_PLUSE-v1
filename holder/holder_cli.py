"""
WorkPulse Holder - the business owner's command panel.

This is the panel YOU (the WorkPulse owner) use to run the SaaS:

    * see every tenant (company), its plan, seats, status and revenue
    * create a new tenant + its owner login in one step
    * move a tenant between plans / seats, renew, suspend or reactivate
    * record an offline payment (bank transfer / UPI) and mark it paid
    * turn the Enterprise screenshot add-on on/off per tenant
    * read the invoice ledger

It talks to the same Flask server as HRAdmin, but with holder credentials
(separate table: holder_admins). Nothing here is visible to tenants.
"""

import json
import os
import sys
import time as _time
import urllib.error
import urllib.parse
import urllib.request

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
os.system("")          # enable ANSI colours on Windows


def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(_app_dir(), "holder_config.json")
SESSION_PATH = os.path.join(_app_dir(), "holder_session.json")
DEFAULT_CONFIG = {
    "cloud_url": "",   # e.g. https://work-pluse-v1.onrender.com
    "api_key": "",     # holder bootstrap key (not needed after first setup)
}
BANNER = r"""
 __        ___     _   _                     ____         _
 \ \      / (_) __| | | |__   __ _  ___ _ __ |  _ \ _   _| | _____
  \ \ /\ / /| |/ _` | | '_ \ / _` |/ _ \ '__| | |_) | | | | |/ / __|
   \ V  V / | | (_| | | |_) | (_| |  __/ |    |  __/| |_| |   <|\__ \
    \_/\_/  |_|\__,_| |_.__/ \__,_|\___|_|    |_|    \__,_|_|\_\___/
      WORKPLUSE HOLDER  -  tenants  ·  plans  ·  revenue
"""


# ----------------------------------------------------------------------------
# config + session
# ----------------------------------------------------------------------------
def load_cfg() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def load_session() -> dict:
    try:
        if os.path.exists(SESSION_PATH):
            with open(SESSION_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_session(sess: dict) -> None:
    try:
        with open(SESSION_PATH, "w", encoding="utf-8") as f:
            json.dump(sess, f, indent=2)
        try:
            os.chmod(SESSION_PATH, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def clear_session() -> None:
    try:
        if os.path.exists(SESSION_PATH):
            os.remove(SESSION_PATH)
    except Exception:
        pass


def pause():
    try:
        import msvcrt
        console.print("\n  [dim]Press any key to continue...[/dim]", end="")
        msvcrt.getwch()
    except Exception:
        try:
            input()
        except Exception:
            pass


def set_title():
    try:
        os.system("title WORKPLUSE HOLDER - owner console")
    except Exception:
        pass


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
def _raw(cfg: dict, method: str, path: str, obj=None, headers=None,
         timeout: int = 90):
    url = cfg["cloud_url"].rstrip("/") + path
    h = {"X-API-Key": cfg.get("api_key", "")}
    h.update(headers or {})
    data = None
    if obj is not None:
        data = json.dumps(obj).encode("utf-8")
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        raise RuntimeError(f"server unreachable: {e}")


def _err(body) -> str:
    if isinstance(body, dict):
        e = body.get("error")
        if isinstance(e, dict):
            return str(e.get("message") or e.get("code") or e)
        if e:
            return str(e)
        return str(body.get("message") or body)
    return str(body)


def _store_login(body: dict) -> dict:
    holder = body.get("holder") or {}
    sess = {
        "access_token": body.get("access_token", ""),
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": _time.time() + int(body.get("expires_in") or 1800),
        "email": holder.get("email", ""),
        "name": holder.get("name", ""),
    }
    save_session(sess)
    return sess


def ensure_access(cfg: dict, force: bool = False) -> bool:
    if not cfg.get("cloud_url"):
        return False
    sess = load_session()
    if not sess.get("refresh_token"):
        return False
    if (not force and sess.get("access_token")
            and float(sess.get("expires_at") or 0) > _time.time() + 60):
        return True
    try:
        status, body = _raw(cfg, "POST", "/api/v1/holder/auth/refresh",
                            {"refresh_token": sess["refresh_token"]})
    except Exception:
        return False
    if status == 200 and body.get("access_token"):
        _store_login(body)
        return True
    clear_session()
    return False


def signed_in(cfg: dict) -> bool:
    return bool(cfg.get("cloud_url") and load_session().get("refresh_token"))


def api(cfg: dict, method: str, path: str, obj=None, timeout: int = 90,
        retry: bool = True):
    ensure_access(cfg)
    sess = load_session()
    headers = {}
    if sess.get("access_token"):
        headers["Authorization"] = "Bearer " + sess["access_token"]
    status, body = _raw(cfg, method, path, obj, headers, timeout)
    if status == 401 and retry and sess.get("refresh_token"):
        if ensure_access(cfg, force=True):
            return api(cfg, method, path, obj, timeout, retry=False)
    return status, body


# ----------------------------------------------------------------------------
# auth screens
# ----------------------------------------------------------------------------
def screen_login(cfg: dict) -> bool:
    while True:
        console.clear()
        console.print(Panel("[bold cyan]WORKPLUSE HOLDER[/bold cyan]  -  sign in",
                            border_style="cyan"))
        if not cfg.get("cloud_url"):
            console.print("  [red]No cloud_url configured.[/red]\n"
                          f"  Put it in: {CONFIG_PATH}")
            pause()
            return False
        try:
            st, body = _raw(cfg, "GET", "/api/v1/holder/auth/status", timeout=90)
            initialized = bool(body.get("initialized"))
        except Exception as e:
            console.print(f"  [red]Server unreachable:[/red] {e}")
            pause()
            return False

        console.print(f"  Server      : {cfg['cloud_url']}")
        console.print(f"  Holder setup: {'ready' if initialized else 'NOT DONE YET'}\n")
        console.print("  [b]1)[/b] Sign in")
        if not initialized:
            console.print("  [b]2)[/b] Create the holder account (first run, needs key)")
        console.print("  [b]0)[/b] Exit\n")
        ch = console.input("  Choose: ").strip()
        if ch == "0":
            return False
        if ch == "2":
            if initialized:
                console.print("  [yellow]A holder account already exists.[/yellow]")
                pause()
                continue
            return screen_bootstrap(cfg)
        if ch != "1":
            continue
        if not cfg.get("api_key"):
            console.print("  [yellow]Note: api_key is empty in holder_config.json "
                          "(only needed for the first-run bootstrap).[/yellow]")
        email = console.input("  Email    : ").strip().lower()
        import getpass
        pw = getpass.getpass("  Password : ")
        try:
            st, body = _raw(cfg, "POST", "/api/v1/holder/auth/login",
                            {"email": email, "password": pw})
        except Exception as e:
            console.print(f"  [red]Server unreachable:[/red] {e}")
            pause()
            continue
        if st == 200:
            sess = _store_login(body)
            console.print(f"  [green]Welcome, {sess.get('name') or sess.get('email')}.[/green]")
            pause()
            return True
        console.print(f"  [red]{_err(body)}[/red]")
        if "rate" in _err(body).lower():
            return False
        pause()


def screen_bootstrap(cfg: dict) -> bool:
    console.clear()
    console.print(Panel("[bold cyan]WORKPLUSE HOLDER[/bold cyan]  -  first-run setup",
                        border_style="cyan"))
    console.print("  This creates the ONE owner account for the whole service.")
    console.print("  Use a strong password and keep it safe.\n")
    name = console.input("  Your name : ").strip() or "WorkPulse Holder"
    email = console.input("  Email     : ").strip().lower()
    import getpass
    pw = getpass.getpass("  Password (min 10): ")
    pw2 = getpass.getpass("  Repeat password  : ")
    if pw != pw2 or len(pw) < 10:
        console.print("  [red]Passwords must match and be 10+ characters.[/red]")
        pause()
        return False
    try:
        st, body = _raw(cfg, "POST", "/api/v1/holder/auth/bootstrap",
                        {"name": name, "email": email, "password": pw},
                        {"X-Holder-Bootstrap-Key": cfg.get("api_key", "")})
    except Exception as e:
        console.print(f"  [red]Server unreachable:[/red] {e}")
        pause()
        return False
    if st in (200, 201):
        console.print("  [green]Holder account created.[/green] Sign in now.")
        pause()
        return screen_login(cfg)
    console.print(f"  [red]{_err(body)}[/red]")
    pause()
    return False


# ----------------------------------------------------------------------------
# screens
# ----------------------------------------------------------------------------
def screen_summary(cfg: dict) -> None:
    console.clear()
    console.print(Panel("[bold cyan]WORKPLUSE HOLDER[/bold cyan]  -  business summary",
                        border_style="cyan"))
    try:
        st, b = api(cfg, "GET", "/api/v1/holder/summary")
    except Exception as e:
        console.print(f"  [red]Server unreachable:[/red] {e}")
        pause()
        return
    if st != 200:
        console.print(f"  [red]{_err(b)}[/red]")
        pause()
        return
    console.print(f"  Tenants        : [bold]{b.get('tenants')}[/bold] "
                  f"(active {b.get('active_tenants')}, paying {b.get('paying_tenants')})")
    console.print(f"  MRR            : INR [bold]{b.get('mrr_inr')}[/bold]"
                  f"     ARR: INR {b.get('arr_inr')}")
    console.print(f"  Collected      : INR {b.get('collected_inr')}")
    console.print(f"  Seats used     : {b.get('seats_used')}    "
                  f"Active devices: {b.get('devices')}")
    console.print(f"  Server time    : {b.get('server_time')}")
    pause()


def screen_tenants(cfg: dict) -> None:
    while True:
        console.clear()
        console.print(Panel("[bold cyan]WORKPLUSE HOLDER[/bold cyan]  -  tenants",
                            border_style="cyan"))
        try:
            st, b = api(cfg, "GET", "/api/v1/holder/tenants")
        except Exception as e:
            console.print(f"  [red]Server unreachable:[/red] {e}")
            pause()
            return
        if st != 200:
            console.print(f"  [red]{_err(b)}[/red]")
            pause()
            return
        rows = b.get("tenants", [])
        t = Table(box=box.SIMPLE, header_style="bold cyan")
        t.add_column("#"); t.add_column("Company"); t.add_column("Plan")
        t.add_column("Seats"); t.add_column("Sub"); t.add_column("INR")
        t.add_column("People"); t.add_column("Devices"); t.add_column("Tenant")
        for r in rows:
            t.add_row(str(r["id"]), r["name"], r["plan"],
                      str(r.get("seats") or "-"),
                      str(r.get("subscription_status") or "-"),
                      str(r.get("amount_inr") or 0),
                      str(r.get("employees")),
                      str(r.get("devices")), str(r.get("status")))
        console.print(t)
        console.print("  [b]N)[/b] New tenant    [b]0)[/b] Back")
        ch = console.input("\n  Tenant # to manage (or N / 0): ").strip()
        if ch == "0":
            return
        if ch.lower() == "n":
            screen_new_tenant(cfg)
            continue
        if ch.isdigit():
            screen_tenant(cfg, int(ch))
            continue


def screen_new_tenant(cfg: dict) -> None:
    console.clear()
    console.print(Panel("[bold cyan]WORKPLUSE HOLDER[/bold cyan]  -  new tenant",
                        border_style="cyan"))
    name = console.input("  Company name : ").strip()
    slug = console.input("  Slug (a-z0-9-) : ").strip().lower()
    plan = console.input("  Plan [starter/business/custom/enterprise] : ").strip().lower()
    seats = console.input("  Seats : ").strip()
    email = console.input("  Owner email : ").strip().lower()
    import getpass
    pw = getpass.getpass("  Owner password (min 10): ")
    payload = {"name": name, "slug": slug, "plan": plan,
               "owner_email": email, "owner_password": pw}
    if seats.isdigit():
        payload["seats"] = int(seats)
    try:
        st, b = api(cfg, "POST", "/api/v1/holder/tenants", payload)
    except Exception as e:
        console.print(f"  [red]Server unreachable:[/red] {e}")
        pause()
        return
    if st in (200, 201):
        console.print(f"\n  [green]Created tenant {b.get('tenant_id')} "
                      f"({b.get('slug')})[/green]")
        console.print(f"  Plan {b.get('plan')} / {b.get('seats')} seats - "
                      f"INR {b.get('amount_inr')}, status pending_payment.")
        console.print("  Send the owner the email/password and collect payment, "
                      "then activate it here.")
    else:
        console.print(f"  [red]{_err(b)}[/red]")
    pause()


def screen_tenant(cfg: dict, tid: int) -> None:
    while True:
        console.clear()
        console.print(Panel("[bold cyan]WORKPLUSE HOLDER[/bold cyan]  -  tenant detail",
                            border_style="cyan"))
        try:
            st, b = api(cfg, "GET", f"/api/v1/holder/tenants/{tid}")
        except Exception as e:
            console.print(f"  [red]Server unreachable:[/red] {e}")
            pause()
            return
        if st != 200:
            console.print(f"  [red]{_err(b)}[/red]")
            pause()
            return
        ten = b.get("tenant") or {}
        lic = b.get("license") or {}
        feats = lic.get("features") or {}
        seats = lic.get("seats")
        console.print(f"  Company     : [bold]{ten.get('name')}[/bold] "
                      f"(id {ten.get('id')}, {ten.get('slug')})  status={ten.get('status')}")
        console.print(f"  Plan        : {lic.get('plan_name')} ({lic.get('plan')}) "
                      f"sub={lic.get('status')}  cycle={lic.get('billing_cycle')}")
        console.print(f"  Seats       : {lic.get('seats_used')}/"
                      f"{'unlimited' if seats is None else seats} used   "
                      f"employees={lic.get('employees')}  "
                      f"unassigned devices={lic.get('unassigned_devices')}")
        console.print(f"  Amount      : INR {lic.get('amount_inr')}   "
                      f"expires={lic.get('expires_at') or '-'}"
                      + (f"  ({lic.get('days_left')} days)" if lic.get("days_left") is not None else ""))
        console.print(f"  Screenshots : {'ON' if feats.get('screenshots_enabled') else 'off'} "
                      f"(entitled={bool(feats.get('screenshots_entitled'))})")

        inv = b.get("invoices") or []
        if inv:
            t = Table(title="Recent invoices", box=box.SIMPLE, header_style="bold cyan")
            for c in ("When", "Plan", "Seats", "INR", "Status", "Reference"):
                t.add_column(c)
            for r in inv[:8]:
                t.add_row(str(r.get("ts"))[:19], str(r.get("plan")),
                          str(r.get("seats")), str(r.get("amount_inr")),
                          str(r.get("status")), str(r.get("reference") or "-"))
            console.print(t)

        console.print("\n  [b]1)[/b] Set plan / seats / renew   [b]2)[/b] Record offline payment")
        console.print("  [b]3)[/b] Screenshots on/off          [b]4)[/b] Suspend / activate")
        console.print("  [b]5)[/b] Admins                    [b]0)[/b] Back")
        ch = console.input("\n  Choose: ").strip()
        if ch == "1":
            plan = console.input("  Plan [starter/business/custom/enterprise/pilot] "
                                 "(Enter = keep): ").strip().lower()
            seats = console.input("  Seats (Enter = keep): ").strip()
            cycle = console.input("  Cycle [monthly/annual] (Enter = keep): ").strip().lower()
            status = console.input("  Status [active/pending_payment/past_due/"
                                   "suspended] (Enter = active): ").strip().lower()
            payload = {}
            if plan:
                payload["plan"] = plan
            if seats.isdigit():
                payload["seats"] = int(seats)
            if cycle:
                payload["billing_cycle"] = cycle
            if status:
                payload["status"] = status
            try:
                st2, b2 = api(cfg, "PUT",
                              f"/api/v1/holder/tenants/{tid}/subscription", payload)
            except Exception as e:
                console.print(f"  [red]{e}[/red]")
                pause()
                continue
            if st2 == 200:
                l2 = b2.get("license") or {}
                console.print(f"  [green]Updated:[/green] {l2.get('plan')} / "
                              f"{l2.get('seats')} seats / {l2.get('status')} - "
                              f"expires {l2.get('expires_at')}")
            else:
                console.print(f"  [red]{_err(b2)}[/red]")
            pause()
        elif ch == "2":
            plan = console.input(f"  Plan [{lic.get('plan')}] (Enter = keep): ").strip().lower()
            seats = console.input(f"  Seats [{lic.get('seats')}] (Enter = keep): ").strip()
            cycle = console.input(f"  Cycle [{lic.get('billing_cycle')}] "
                                  "(Enter = keep): ").strip().lower()
            ref = console.input("  Payment reference (UPI/bank/UTR): ").strip()
            amount = console.input(f"  Amount INR [{lic.get('amount_inr')}]: ").strip()
            payload = {"status": "active", "record_invoice": True,
                       "payment_reference": ref or "offline/manual"}
            if plan:
                payload["plan"] = plan
            if seats.isdigit():
                payload["seats"] = int(seats)
            if cycle:
                payload["billing_cycle"] = cycle
            try:
                st2, b2 = api(cfg, "PUT",
                              f"/api/v1/holder/tenants/{tid}/subscription", payload)
            except Exception as e:
                console.print(f"  [red]{e}[/red]")
                pause()
                continue
            if st2 == 200:
                console.print("  [green]Payment recorded, plan active for a new period.[/green]")
            else:
                console.print(f"  [red]{_err(b2)}[/red]")
            pause()
        elif ch == "3":
            if not feats.get("screenshots_entitled"):
                console.print("  [yellow]Screenshots need an active Enterprise plan.[/yellow]")
                pause()
                continue
            on = console.input("  Enable screenshots? [y/N]: ").strip().lower() in ("y", "yes")
            try:
                st2, b2 = api(cfg, "PATCH", f"/api/v1/holder/tenants/{tid}",
                              {"config": {"screenshots_enabled": on}})
            except Exception as e:
                console.print(f"  [red]{e}[/red]")
                pause()
                continue
            console.print("  [green]Saved.[/green]" if st2 == 200 else f"  [red]{_err(b2)}[/red]")
            pause()
        elif ch == "4":
            new = console.input("  Tenant status [active/suspended/closed]: ").strip().lower()
            if not new:
                continue
            try:
                st2, b2 = api(cfg, "PATCH", f"/api/v1/holder/tenants/{tid}",
                              {"status": new})
            except Exception as e:
                console.print(f"  [red]{e}[/red]")
                pause()
                continue
            console.print("  [green]Saved.[/green]" if st2 == 200 else f"  [red]{_err(b2)}[/red]")
            pause()
        elif ch == "5":
            admins = b.get("admins") or []
            t = Table(title="Admin logins", box=box.SIMPLE, header_style="bold cyan")
            for c in ("Email", "Role", "Status", "Created", "Last login"):
                t.add_column(c)
            for a in admins:
                t.add_row(str(a.get("email")), str(a.get("role")), str(a.get("status")),
                          str(a.get("created_at"))[:19], str(a.get("last_login") or "-")[:19])
            console.print(t)
            pause()
        else:
            return


def screen_invoices(cfg: dict) -> None:
    console.clear()
    console.print(Panel("[bold cyan]WORKPLUSE HOLDER[/bold cyan]  -  invoices",
                        border_style="cyan"))
    try:
        st, b = api(cfg, "GET", "/api/v1/holder/invoices")
    except Exception as e:
        console.print(f"  [red]Server unreachable:[/red] {e}")
        pause()
        return
    if st != 200:
        console.print(f"  [red]{_err(b)}[/red]")
        pause()
        return
    rows = b.get("invoices", [])
    t = Table(box=box.SIMPLE, header_style="bold cyan")
    for c in ("#", "Tenant", "When", "Plan", "Seats", "INR", "Status", "Reference"):
        t.add_column(c)
    for r in rows[:40]:
        t.add_row(str(r.get("id")), str(r.get("tenant_id") or "-"),
                  str(r.get("ts"))[:19], str(r.get("plan")), str(r.get("seats")),
                  str(r.get("amount_inr")), str(r.get("status")),
                  str(r.get("reference") or "-"))
    console.print(t)
    pause()


def screen_account(cfg: dict) -> None:
    while True:
        console.clear()
        console.print(Panel("[bold cyan]WORKPLUSE HOLDER[/bold cyan]  -  account",
                            border_style="cyan"))
        sess = load_session()
        console.print(f"  Signed in : {sess.get('email')}  ({sess.get('name')})")
        console.print(f"  Server    : {cfg.get('cloud_url')}")
        console.print("\n  [b]1)[/b] Sign out   [b]0)[/b] Back")
        ch = console.input("\n  Choose: ").strip()
        if ch == "1":
            try:
                _raw(cfg, "POST", "/api/v1/holder/auth/logout",
                     {"refresh_token": sess.get("refresh_token", "")})
            except Exception:
                pass
            clear_session()
            console.print("  [green]Signed out.[/green]")
            pause()
            return
        return


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    cfg = load_cfg()
    set_title()
    console.clear()
    console.print(Panel(BANNER, border_style="cyan"))
    if not cfg.get("cloud_url"):
        console.print("  [red]holder_config.json has no cloud_url.[/red]")
        console.print(f"  Edit: {CONFIG_PATH}")
        pause()
        return
    if "--check" in sys.argv:
        console.print(f"  Config      : {CONFIG_PATH}")
        console.print(f"  Cloud       : {cfg['cloud_url']}")
        console.print(f"  Bootstrap key set: {'yes' if cfg.get('api_key') else 'no'}")
        console.print(f"  Signed in   : {'yes' if signed_in(cfg) else 'no'}")
        try:
            st, body = _raw(cfg, "GET", "/api/v1/holder/auth/status", timeout=120)
            console.print(f"  Server      : HTTP {st}  {body}")
        except Exception as e:
            console.print(f"  Server      : [red]{e}[/red]")
        return
    if signed_in(cfg) and ensure_access(cfg):
        sess = load_session()
        console.print(f"  [green]Signed in[/green] as {sess.get('email')}")
    else:
        clear_session()
        if not screen_login(cfg):
            return
    while True:
        console.clear()
        console.print(Panel(BANNER, border_style="cyan"))
        sess = load_session()
        console.print(f"  [green]{sess.get('email')}[/green]   "
                      f"{cfg.get('cloud_url')}\n")
        console.print("  [b]1)[/b] Business summary   (MRR, tenants, seats, devices)")
        console.print("  [b]2)[/b] Tenants            (manage plans, payment, suspend)")
        console.print("  [b]3)[/b] Invoices           (payment ledger)")
        console.print("  [b]4)[/b] Account            (sign out)")
        console.print("  [b]0)[/b] Exit")
        ch = console.input("\n  Choose: ").strip()
        if ch == "1":
            screen_summary(cfg)
        elif ch == "2":
            screen_tenants(cfg)
        elif ch == "3":
            screen_invoices(cfg)
        elif ch == "4":
            if not signed_in(cfg):
                screen_login(cfg)
            else:
                screen_account(cfg)
                if not signed_in(cfg):
                    return
        elif ch == "0":
            return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
