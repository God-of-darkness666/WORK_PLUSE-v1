# WorkPulse V3 (3.0.0) - multi-tenant build

This repository contains the production WorkPulse stack:

| Part | Path | What it does |
|---|---|---|
| Cloud API | `server/app.py` | Flask + gunicorn, JWT admin auth, tenants, devices, enrollment, audit, legacy compatibility |
| Windows agent | `agent/agent.py` | Silent activity monitor, consent screen, per-device token enrollment, anti-bypass watchdog |
| Admin panel | `admin/admin_cli.py` | WORKPLUSE command GUI: dashboard, worker detail, employees/devices, subscription, audit, reports |
| Owner panel | `holder/holder_cli.py` | SaaS control room: tenants, plans, seats, offline payments, invoices, Razorpay |
| Mobile agent | `mobile/` | Android APK (legacy shared-key flow until mobile tokens land) |
| Branding | `branding/` | Shield logo + Windows icon |

## Live service

- API: <https://work-pluse-v1.onrender.com> (`/ping` -> version 3.0.0)
- Storage: TiDB Cloud Serverless (MySQL protocol), database `hrmonitor`
- Hosting: Render free web service; first request after 15 idle minutes may take 25-90 s

## Admin onboarding

1. Run `HRAdmin.exe` (built from `admin/admin_cli.py`).
2. On an empty deployment the panel offers to create the first administrator
   (organisation, email, password 10+ chars). This creates the owner of tenant
   `workpulse`.
3. `E) Employees & devices` -> add employees and issue single-use
   8-character enrollment codes (15 minutes).
4. On each laptop: `HRAgent.exe --enroll <code>`, then start `HRAgent.exe`
   and accept the consent screen. Verify with `HRAgent.exe --status`.

The export kit (menu 3) contains **no shared secret**: only the EXE, a config
with the server URL, and enrollment instructions.

## API summary (tenant-scoped, Bearer JWT)

```
POST /api/v1/auth/login            email + password -> access + refresh token
POST /api/v1/auth/refresh          rotate refresh token
POST /api/v1/auth/logout
GET  /api/v1/auth/me
POST /api/v1/auth/change-password
GET  /api/v1/admin/dashboard
GET  /api/v1/admin/employees       POST /api/v1/admin/employees
PATCH/api/v1/admin/employees/<id>
GET  /api/v1/admin/devices         POST /api/v1/admin/devices/<id>/revoke
POST /api/v1/admin/enrollment-codes
GET  /api/v1/admin/identities      POST /api/v1/admin/identities/<id>/disable
GET  /api/v1/admin/status | /status_one
GET  /api/v1/admin/activity?identity=&day=
GET  /api/v1/admin/audit           POST /api/v1/admin/audit
GET  /api/v1/admin/license | /subscription
POST /api/v1/admin/subscription/request | /verify
POST /api/v1/admin/settings/screenshots
GET  /api/v1/plans                 public price list
```

Owner (holder) endpoints (`Authorization: Bearer`, role `holder`):

```
GET  /api/v1/holder/auth/status
POST /api/v1/holder/auth/bootstrap (X-Holder-Bootstrap-Key, once)
POST /api/v1/holder/auth/login | /refresh | /logout
GET  /api/v1/holder/summary
GET  /api/v1/holder/tenants        POST /api/v1/holder/tenants
GET  /api/v1/holder/tenants/<id>
PUT  /api/v1/holder/tenants/<id>/subscription
PATCH/api/v1/holder/tenants/<id>   status + config (screenshots)
GET  /api/v1/holder/invoices
POST /api/v1/billing/razorpay/webhook
```

Plans: Starter 5-20 seats INR 3,500/mo, Business 21-40 INR 6,400/mo,
Custom 41-100 INR 150/seat/mo, Enterprise 101+ INR 125/seat/mo (annual =
10 months for 12). Screenshots are an Enterprise entitlement and stay off
until the tenant owner enables them (`settings/screenshots`).

Agent endpoints (device token in `X-Device-Token`):

```
POST /api/v1/devices/enroll        one-time code -> wp_... device token
POST /api/v1/agent/activity
POST /api/v1/agent/heartbeat
GET  /api/v1/agent/state           disabled flag for the kill switch
GET  /api/v1/agent/config | /release
```

Legacy endpoints (`/upload`, `/api/*`) stay available with `X-API-Key` and map
to the pilot tenant for older agents and the current Android build.

## Build

```
python -m PyInstaller --onefile --console --icon branding\workpulse.ico --name HRAdmin admin\admin_cli.py
python -m PyInstaller --onefile --console --icon branding\workpulse.ico --name HRAgent agent\agent.py
python -m PyInstaller --onefile --console --icon branding\workpulse.ico --name WP_Holder holder\holder_cli.py
```

## Tests

`docs/` and the delivery notes record three live test runs against the production
TiDB: Phase 1 (12/12 checks: migration, auth, enrollment, isolation, revoke),
Phase 2 (16/16 checks: admin CLI routing, kill switch, audit, agent enrollment,
revocation) and Phase 3 (21/21 checks: plans, holder bootstrap/login, tenant
creation, seat limits, screenshots entitlement, Razorpay webhook, suspension,
invoices). Temporary rows were removed after each run.

## Time

Server timestamps use the tenant business clock (`TZ_OFFSET_MINUTES`, default
`330` = IST), matching the local timestamps agents send. Set it to your region's
UTC offset in minutes for other deployments.

## Next

Phase 3 is complete. Remaining: mobile device tokens, React holder panel,
Linux agent, and Razorpay keys on Render (offline payments work without them).
