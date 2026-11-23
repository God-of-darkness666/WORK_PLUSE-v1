# WORKPULSE V3
## Enhanced Master Blueprint — Built to Sell

Version 3.0 | 23 November 2026
Tagline: Secure Workforce. Trusted Devices. Smarter Management.

---

# 0. READ THIS FIRST — EXECUTIVE VERDICT

The V2 blueprint is strong on vision and weak on sequencing. It describes a finished
platform but not the order that makes it real. It also contains contradictions that
would block a developer on day one. This V3 keeps every good idea, fixes the
contradictions, and turns the document into an execution plan.

**Verdict in one line:** do not rewrite what already works. Finish it.

**The 8 contradictions found in V2, and the fix used here:**

| # | Contradiction in V2 | Fix in V3 |
|---|---|---|
| 1 | "Backend: FastAPI + PostgreSQL" but a working Flask + TiDB system already runs | Keep Flask + TiDB. Rewrite only when a real trigger is hit (Section 3) |
| 2 | Section 3 says screenshots are PROHIBITED, but the shipped agent captures them | Decide now: tenant-level opt-in "evidence capture" (Section 5.6), default OFF |
| 3 | "Browser monitoring must use a disclosed extension" but the agent reads history files | Move to an MV3 extension as primary; history reading becomes legacy fallback only |
| 4 | Starter is priced ₹1,850 in the table but ₹2,900 in the notes | One price sheet, one number (Section 10) |
| 5 | Custom plan "above 10 employees" overlaps Starter's 20 employees | Custom starts at 41 employees (Section 10) |
| 6 | Custom costs ₹112.50/employee but Business costs ₹87.50 — bigger customers pay MORE per seat | Corrected ladder: price per seat always falls as volume rises (Section 10) |
| 7 | "31 days" subscriptions vs monthly GST invoicing | Bill on calendar months; 31-day wording removed (Section 10.5) |
| 8 | "No refunds, absolutely" vs Indian consumer-protection law | No-refund policy + service credits, with legal review and pre-checkout disclosure (Section 10.7) |

**The single most important engineering change:** the EXE must stop carrying a global
secret. Today one API key burnt into every agent unlocks every customer. V3 replaces it
with enrollment codes and per-device tokens (Section 4).

---

# 1. PRODUCT DEFINITION

## 1.1 What WorkPulse is

A workforce activity and device management platform for organizations that issue
computers and phones to employees. It answers three questions for an employer:

1. Are company devices being used for work during work hours?
2. Which applications and websites consume the working day?
3. Are the devices healthy, updated and reporting?

It does not answer "what is this person typing" and should never try to.

## 1.2 Who buys it

Primary target for the first 12 months: Indian SMBs with 10–100 employees.

| Segment | Why they buy | Typical size |
|---|---|---|
| IT services and software agencies | Client billing proof, idle-time visibility | 15–80 |
| BPO / support centres | Adherence, app usage, shift coverage | 30–200 |
| Digital marketing agencies | Billable hours, tool usage | 10–40 |
| Coaching and training institutes | Lab computer usage, misuse | 20–100 |
| Clinics, retail back-office, logistics offices | Device accountability | 10–50 |

Buyer: owner, operations head, or HR head. Not IT — IT only approves the install.
Champion: the person who currently guesses at productivity from WhatsApp updates.

## 1.3 The promise

"Know what your work devices are doing — without spying on your people."

That sentence is the whole marketing strategy. It lets a buyer say yes without feeling
like they are installing spyware, and it is defensible in a legal dispute.

## 1.4 What we refuse to do (non-negotiable)

- No keystroke logging.
- No clipboard capture.
- No private messages, email contents, or personal files.
- No camera, microphone, or GPS.
- No incognito or personal browser profiles.
- No hidden operation: the employee can always see that the agent is running.
- No screenshots of personal apps (banking, health, personal email) even when evidence
  capture is enabled.

These refusals are a sales asset. Teramind and Veriato carry a "spyware" reputation that
makes HR teams nervous. WorkPulse sells the opposite.

## 1.5 The three interfaces

| Interface | User | Form | Purpose |
|---|---|---|---|
| Employee agent | Employee | Windows EXE, Android APK, Linux package, browser extension | Transparent, consented activity reporting |
| HR panel | Customer HR/ops | CMD GUI (Windows EXE) | Employees, devices, activity, reports, security, updates |
| Holder panel | You, the platform owner | CMD GUI first, web dashboard later | Organizations, admins, plans, payments, releases, alerts |

---

# 2. BASELINE — WHAT ALREADY EXISTS

This is not a greenfield project. Roughly 65% of Phase 1–5 of the V2 roadmap is already
built, tested and running live.

| Component | Status | Evidence |
|---|---|---|
| Cloud API (Flask) | LIVE | Render deployment, mysql mode, `/ping` 0 ms |
| Database (TiDB MySQL) | LIVE | Pooled connections, indexes, dedup, 0.5–0.7 s reads |
| Windows agent | WORKING | App tracking, browser URLs, screenshots, consent, watchdog, EXE-only |
| Android agent | WORKING | Usage stats, foreground service, battery exemption, branded APK |
| HR CMD panel | WORKING | Banner, 12 menus, instant cache, live feed, reports, settings, export |
| Anti-bypass | WORKING | Watchdog revives agent in 60 s; burnt-in cloud settings |
| Consent + audit | PARTIAL | Consent screen and audit endpoint exist; full audit UI not built |
| Tenancy | MISSING | One global API key — must be replaced before selling |
| HR authentication | MISSING | No login on the CMD panel |
| Holder panel | MISSING | Not started |
| Payments / subscriptions | MISSING | Not started |
| PDF / Excel reports | MISSING | Console reports only |
| Browser extension | MISSING | History-file reading only |
| Linux agent | MISSING | Not started |
| Signed releases / auto-update | MISSING | Manual EXE replacement only |

**What this means:** the remaining work is mostly server-side and commercial, not
agent-side. That is good news — it is cheaper to build and easier to test.

---

# 3. ARCHITECTURE

## 3.1 Recommended architecture (keep, extend, don't rewrite)

```
EMPLOYEE DEVICES                    CLOUD (Render)                 CUSTOMER OFFICE
┌──────────────────┐          ┌────────────────────────┐        ┌──────────────────┐
│ Windows agent    │          │ Flask API  /api/v1/*   │        │ HR CMD panel     │
│ Android agent    │  HTTPS   │ - auth (JWT)           │ HTTPS  │ - login          │
│ Browser ext (MV3)│ ───────► │ - tenant scoping       │ ◄───── │ - cache-first UI │
│ Linux (later)    │  device  │ - ingest + dedup       │  admin │ - reports        │
└──────────────────┘  token   │ - reports (PDF/XLSX)   │  JWT   │ - security scan  │
                              │ - billing webhooks     │        └──────────────────┘
                              │ - release manifest     │
                              └───────────┬────────────┘        ┌──────────────────┐
                                          │                     │ Holder panel     │
                                          ▼                     │ - orgs, plans    │
                              ┌────────────────────────┐        │ - payments       │
                              │ TiDB Cloud (MySQL)     │        │ - releases       │
                              │ 17 tables, tenant_id   │        │ - block/unblock  │
                              │ nightly backup → B2    │        └──────────────────┘
                              └────────────────────────┘
```

## 3.2 Why we keep Flask + TiDB

The V2 document specifies FastAPI + PostgreSQL + SQLAlchemy + Alembic. That stack is
good, but switching now costs 6–8 weeks and delivers nothing a customer can see.

| Factor | Keep Flask + TiDB | Rewrite to FastAPI + PostgreSQL |
|---|---|---|
| Time to first sale | 1–2 weeks | 8–10 weeks |
| Risk | Low — code runs today | High — regressions in a working agent |
| Cost | ₹0 extra (free tiers) | New DB, new migrations, new tests |
| Performance | Already 0.5–1 s | Similar after tuning |
| Talent | You already maintain it | Needs a second developer |

**Migration triggers (only then rewrite):**
- More than 2,000 active devices, or
- A client contractually requires PostgreSQL/India-only hosting, or
- Complex analytics that need JSONB/CTEs at scale.

If a trigger is hit, the API contract in Section 9 stays identical, so agents and the
panel keep working while the backend is swapped behind a versioned path.

## 3.3 Hybrid deployment, correctly scoped

The V2 "local HR gateway" is a good idea but a v1 trap. Most Indian SMBs have working
internet. Build it only when a paying client demands offline LAN operation.

When built, the gateway is:
- A small Windows service holding SQLite.
- Receives agent traffic on the LAN, buffers during outages.
- Forwards to cloud when online, tracks a sync cursor.
- Never the source of truth; cloud always wins on conflict.
- Licensed separately as an Enterprise add-on (₹25,000 one-time + ₹5,000/yr).

Until then, offline resilience lives on the agent: a local SQLite queue with 7-day
buffering and exponential backoff (Section 5.5).

## 3.4 Environments

| Environment | Purpose | Data |
|---|---|---|
| dev | Local Flask, local MySQL or TiDB branch | Fake tenants |
| staging | Render staging service + staging DB | Seeded demo org |
| prod | work-pluse-v1.onrender.com (until domain) | Real customer data |

Rule: no developer ever tests against prod customer data. Seed script creates two fake
tenants for every test run.

---

# 4. TENANCY AND SECURITY MODEL

This is the part that turns a demo into a product. Everything else is secondary.

## 4.1 Tenant isolation rules

1. Every business table carries `tenant_id` (organization id).
2. Every query is filtered by the tenant taken from the authenticated token — never
   from a request parameter.
3. The holder role can read counts and health across tenants, never activity rows.
4. Cross-tenant access is a release blocker; an automated test proves it on every deploy.
5. Identities (worker IDs) are stored with a tenant prefix to prevent collisions:
   `tenant_slug:employee_id`.

## 4.2 Identity and tokens

| Token | Holder | Lifetime | Storage |
|---|---|---|---|
| Admin access JWT | HR panel | 30 minutes | Memory |
| Admin refresh JWT | HR panel | 30 days, rotating | Windows credential store / DPAPI |
| Device token | Agent | Long-lived, revocable | `%ProgramData%\WorkPulse\device.token`, DPAPI-encrypted |
| Enrollment code | One-time | 15 minutes, single use | Printed/shown by HR |
| Holder access | Holder panel | 30 minutes + TOTP | Memory |

**Critical change from today:** the EXE must not contain a global API key. It contains
only the public enrollment endpoint. A secret inside a distributed binary is not a
secret — anyone can extract it with a text editor, and one leak exposes every customer.

## 4.3 Enrollment flow

1. HR creates an employee record and generates an enrollment code (8 characters, 15 min).
2. Employee runs `HRAgent.exe` and sees the consent screen.
3. On ACCEPT, the agent posts the code + device fingerprint to `/api/v1/devices/enroll`.
4. Server validates the code, creates the device row, returns a device token (once).
5. Agent stores the token encrypted and begins reporting.
6. HR sees the new device in Device Management and can revoke it at any time.

Revocation is instant server-side; the agent's next request returns 401 and it stops.

## 4.4 Passwords and authentication

- Argon2id (preferred) or bcrypt with cost ≥ 12.
- Minimum 10 characters, block the top 10,000 leaked passwords.
- Lockout: 5 failures in 15 minutes → 15-minute lock + security event.
- TOTP MFA required for holder and organization owner; optional for HR staff.
- Sessions invalidated on password change and on explicit "log out all devices".
- No shared hard-coded access codes anywhere (V2 explicitly forbids this — enforce it
  in code review, not policy).

## 4.5 Transport and data protection

- HTTPS/TLS only; HSTS enabled; HTTP redirects to HTTPS.
- Database connections use TLS (already configured for TiDB).
- Secrets live in environment variables on Render, never in the repo.
- Sensitive columns (email, phone) encrypted at rest if the DB plan supports it;
  otherwise rely on disk encryption and restrict access.
- Backups encrypted before upload.
- Logs never contain tokens, passwords, full URLs with query strings, or employee
  window titles at INFO level.

## 4.6 Rate limits and abuse controls

| Endpoint | Limit |
|---|---|
| `/api/v1/auth/login` | 10 per IP per 15 min, 5 per account per 15 min |
| `/api/v1/devices/enroll` | 20 per tenant per hour |
| `/api/v1/agent/activity` | 1 request per device per 15 s |
| `/api/v1/agent/heartbeat` | 1 per device per 30 s |
| `/api/v1/reports` | 10 per tenant per hour |
| `/api/v1/billing/webhooks/*` | provider-signed only, replay-protected |

## 4.7 Audit trail

Append-only table. Every row: `tenant_id, actor_type, actor_id, action, target,
result, ip, user_agent, ts, prev_hash`. Hash chaining makes silent edits detectable.

Must log: login, logout, failed login, employee create/update/deactivate, device
enroll/revoke, report export, settings change, plan change, payment event, update
install, holder block/unblock with reason.

## 4.8 Agent integrity

- SHA-256 of the running EXE compared to the signed release manifest.
- Self-check every 15 minutes; mismatch → security event + visible warning.
- Watchdog task restores the agent within 60 seconds (already proven).
- Server flags any device with no heartbeat for 5 minutes.
- Optional code signing certificate for SmartScreen trust (Azure Trusted Signing
  ~$10/month, or an OV certificate ~$200–400/year).

---

# 5. EMPLOYEE AGENT SPECIFICATION

## 5.1 Data collected (the complete list)

| Category | Fields | Notes |
|---|---|---|
| Session | start, end, duration, lock/unlock, idle | No content |
| Application | name, executable, foreground, duration, timeline | Visible to employee |
| Browser | domain, tab title, duration, active tab | Extension only; no query strings |
| Device | device id, name, OS, version, agent version, IP, last seen | IP is diagnostic, not location |
| Health | heartbeat, queue depth, sync errors, integrity status | Drives security alerts |
| Evidence (optional) | screenshot of work app on app/tab switch | Tenant toggle, default OFF, disclosed |

## 5.2 Data explicitly not collected

Passwords, keystrokes, clipboard, private messages, email bodies, personal files,
camera, microphone, GPS, incognito browsing, personal browser profiles, screen
recordings, and any content inside documents.

## 5.3 Windows agent changes required

1. Replace burnt-in API key with enrollment code + device token.
2. Add SQLite offline queue (7 days, exponential backoff).
3. Heartbeat every 60 seconds with queue depth and version.
4. Signed-release self-check every 15 minutes.
5. Visible tray indicator + "Why am I being monitored?" help link.
6. Per-tenant configuration pulled at startup (features, retention, schedule).
7. Update check on start and every 6 hours; staged rollout respected.
8. Work-hours scheduling: pause collection outside configured hours (optional per tenant).
9. Clean uninstall path for HR (revokes device, removes watchdog) — required for trust.

## 5.4 Browser extension (new, replaces history scraping)

- Chrome/Edge Manifest V3, published privately or loaded via enterprise policy.
- Reports: active domain, tab title, active duration, tab switches.
- Strips query strings and fragments before sending.
- Ignores incognito and non-work profiles.
- Shows a badge when active.
- History-file reading remains only as a disclosed fallback for clients who refuse the
  extension; it is off by default and marked "legacy" in the UI.

## 5.5 Offline behaviour

- Local SQLite queue, capped at 7 days or 200 MB.
- Upload every 30–60 seconds when online; backoff 5 s → 10 s → 30 s → 2 min → 5 min.
- Duplicate protection: each record has a client-generated UUID; server unique index
  makes retries idempotent (this replaces today's fragile content comparison).
- On queue overflow, drop the oldest non-critical records and log a security event.

## 5.6 Screenshots — the decision (must be made before coding)

The V2 document prohibits screenshots. The current agent captures them, and earlier
business discussions treated them as a selling point. Both cannot be true.

**Recommended policy:**

| Rule | Implementation |
|---|---|
| Default | OFF for every new tenant |
| Availability | Enterprise add-on only (+₹30/employee/month) |
| Disclosure | Named in the consent screen and the employee notice; visible tray icon |
| Scope | Work applications only; blocklist for banking, health, personal email, password managers |
| Trigger | App switch / tab switch, maximum 1 per 30 seconds, 200 per day |
| Storage | Customer-chosen folder on the HR PC or customer file server; never the cloud |
| Retention | 30 days, auto-delete |
| Access | HR admin only; every view is audited |
| Kill switch | One toggle disables capture across the tenant within 5 minutes |

If the team prefers a strictly privacy-first product, remove screenshots entirely and
sell the absence as the differentiator. Either choice is defensible; running both
silently is not.

## 5.7 Android agent changes

- Enrollment by QR code generated in the HR panel (contains code + server URL).
- Usage stats, foreground app, session duration, heartbeat.
- No screenshots, no location, no contacts, no messages.
- Battery-optimisation guidance and OEM-specific help (Xiaomi, Realme, Samsung).
- Work-profile support; personal apps excluded.
- Visible persistent notification (required by Android and good for trust).

## 5.8 Linux agent (later)

- Phase after 10 paying clients. X11 first (`xdotool`, `wmctrl`), Wayland documented as
  limited. Package as `.deb` + AppImage. Same API contract as Windows.

---

# 6. HR PANEL SPECIFICATION (CMD GUI)

## 6.1 Menu map (V2's 12 menus, made concrete)

| # | Menu | What it actually does |
|---|---|---|
| 1 | Dashboard | Employee/device counts, online/offline, current app, alerts, agent versions |
| 2 | Employee Management | Search, view, activate/deactivate, assign/revoke device, enrollment code |
| 3 | Device Management | Device list, OS/model/version, last seen, revoke credential, rename |
| 4 | Activity Monitoring | App and browser timelines, filters by employee/device/date |
| 5 | Cloud Management | Connection status, queue depth, sync errors, pending records |
| 6 | Reports & Downloads | Daily/weekly/monthly/custom; PDF and XLSX; destination folder |
| 7 | Security Scanner | Integrity, signature, version, missing heartbeat, config tampering |
| 8 | Installer Management | Download EXE/APK/ZIP, hashes, release notes, install guide |
| 9 | Cache Management | Refresh cache, clear temp cache (never records), confirmation |
| 10 | Organization Settings | Profile, work schedule, monitoring scope, retention, timezone |
| 11 | Audit History | Filterable log of every admin action and security event |
| 12 | Software & Version Updates | Installed vs latest, release notes, download, verify, install, rollback |
| 0 | Secure Logout | Ends session, clears memory tokens |

Login is required before the menu. The panel must refuse to render data without a
valid token — not merely hide the menu.

## 6.2 Instant UI rule (already implemented, keep it)

- Every screen renders from the local cache immediately.
- A background sweeper refreshes every 6 seconds, patiently, without blocking input.
- A cold cache uses a maximum 8-second foreground probe, then fills in.
- No screen may ever wait on the network synchronously. This is a hard rule.

## 6.3 Cache management safety

The V2 rule is correct: only temporary application cache may be cleared. Employee
records, activity history and server data are never touched by this function. The UI
must say so before asking for confirmation, and the confirmation must be typed, not a
single keypress.

## 6.4 Security scanner scope

This is integrity monitoring, not antivirus. Say so in the UI. Checks: agent hash,
version, signature presence, heartbeat recency, unexpected config changes, duplicate
device IDs, enrollment anomalies. Each finding produces a severity and an audit entry.

---

# 7. HOLDER PANEL SPECIFICATION

## 7.1 Build order decision

| Option | Time | Recommendation |
|---|---|---|
| CMD GUI v1 (same tech as HR panel) | 8–12 days | **Build this first** |
| React + TypeScript web dashboard | 3–4 weeks | Build after 10 paying clients |

Reason: a holder dashboard is used by one person — you. A CMD panel is enough to run
the business, and every day spent on React is a day not spent selling.

## 7.2 Holder capabilities (v1)

- Strong login: email + password + TOTP, separate from HR accounts.
- Organization list: name, plan, seats, expiry, status, last activity.
- Admin management: create, edit, assign plan, extend after verified payment, suspend,
  restore, view history. Every action requires a reason and is audited.
- Subscriptions: active, expiring in 7/30 days, expired, over-limit.
- Transactions: list, filter, search, export; status PAID / PENDING / FAILED only.
- Security: failed logins, integrity failures, revoked-token use, alerts.
- Releases: upload manifest, rollout percentage, minimum version, rollback.
- Exports: org list, license usage, payments, security events (PDF/XLSX).

## 7.3 Hard restriction

The holder panel never displays employee activity records, window titles, URLs, or
screenshots. It may show aggregate counts and health only. This must be enforced in the
API, not by hiding buttons — the holder token simply lacks the scope.

---

# 8. DATA MODEL

## 8.1 Tables (18)

| # | Table | Key columns | Notes |
|---|---|---|---|
| 1 | organizations | id, name, slug, status, created_at | Tenant root |
| 2 | admins | id, tenant_id, email, password_hash, role, totp_secret, status | HR users |
| 3 | employees | id, tenant_id, name, email, department, status | Monitored people |
| 4 | devices | id, tenant_id, employee_id, device_uid, os, model, version, last_seen | Enrolled devices |
| 5 | device_tokens | id, device_id, token_hash, prefix, created_at, revoked_at | Hashed only |
| 6 | enrollment_codes | id, tenant_id, code_hash, employee_id, expires_at, used_at | Single use |
| 7 | activity_events | id, tenant_id, device_id, employee_id, app, exe, title, started_at, duration, day | Main volume |
| 8 | browser_events | id, tenant_id, device_id, domain, title, started_at, duration | No query strings |
| 9 | agent_heartbeats | id, tenant_id, device_id, queue_depth, version, integrity, ts | 1/min |
| 10 | security_events | id, tenant_id, device_id, type, severity, details, ts | Alerts |
| 11 | plans | id, code, name, min_seats, max_seats, price_per_seat, flat_price | Pricing |
| 12 | subscriptions | id, tenant_id, plan_id, seats, started_at, expires_at, status | 31-day removed |
| 13 | transactions | id, tenant_id, order_id, amount, currency, provider, provider_ref, status, paid_at | Audit-grade |
| 14 | payment_webhooks | id, provider, event_id, signature_valid, payload, processed_at | Idempotent |
| 15 | audit_logs | id, tenant_id, actor_type, actor_id, action, target, result, ip, ts, prev_hash | Chained |
| 16 | software_releases | id, version, channel, sha256, notes, min_version, rollout_pct, published_at | Signed |
| 17 | report_jobs | id, tenant_id, type, range_start, range_end, format, status, file_path, attempts | Scheduler |
| 18 | settings | tenant_id, key, value | Per-tenant config |

## 8.2 Indexing and retention

- Index `(tenant_id, day)` and `(tenant_id, device_id, ts)` on activity_events.
- Index `(tenant_id, ts)` on browser_events and audit_logs.
- Retention job daily: delete activity older than 90 days (configurable 30–365),
  keep audit 12 months, keep transactions forever (legal).
- Hard cap per tenant per day to prevent runaway storage (e.g., 50,000 events).

## 8.3 Migration from today's tables

Today: `records_tbl`, `status_tbl`, `controls_tbl`, `audit_log` with a single identity.
Migration steps:

1. Create `organizations` row for the pilot tenant.
2. Add `tenant_id` columns with a default of that pilot tenant.
3. Backfill `device_id` by mapping identity → device.
4. Rename old tables to `legacy_*` and keep read-only for 30 days.
5. New writes go to the new tables only.
6. Rollback = point reads back at `legacy_*`.

This is a one-evening migration, not a rewrite.

---

# 9. API DESIGN

## 9.1 Conventions

- Base path `/api/v1`. Old paths kept as aliases for 6 months.
- JSON only. Errors always: `{"error": {"code": "...", "message": "..."}}`.
- Status codes: 200 ok, 201 created, 400 validation, 401 auth, 403 scope, 404 missing,
  409 conflict, 413 too large, 429 rate limit, 500 server.
- Every write accepts `Idempotency-Key`; duplicates return the original result.
- Pagination: `?limit=50&cursor=...`, max 200.
- Timestamps ISO-8601 UTC; the panel converts to the tenant timezone.

## 9.2 Auth

```
POST /api/v1/auth/login          {email, password, totp?}  -> access + refresh
POST /api/v1/auth/refresh        {refresh}                 -> new pair (rotate)
POST /api/v1/auth/logout         {}                        -> revoke refresh
GET  /api/v1/auth/me                                       -> profile + scopes
POST /api/v1/auth/mfa/enable     {}                        -> secret + QR
POST /api/v1/auth/mfa/verify     {code}
```

## 9.3 Agent endpoints (device token auth)

```
POST /api/v1/devices/enroll      {code, device_uid, os, model, version} -> token
POST /api/v1/agent/activity      {events:[...], heartbeat:{...}}
POST /api/v1/agent/browser       {events:[...]}
POST /api/v1/agent/heartbeat     {queue_depth, version, integrity}
GET  /api/v1/agent/config                                  -> tenant config
GET  /api/v1/agent/release                                 -> latest version + hash
POST /api/v1/agent/update-result  {from, to, ok}
```

## 9.4 HR endpoints (admin JWT, tenant-scoped)

```
GET  /api/v1/dashboard
GET  /api/v1/employees            ?q=&status=&limit=&cursor=
POST /api/v1/employees
PATCH /api/v1/employees/{id}
POST /api/v1/employees/{id}/enrollment-code
GET  /api/v1/devices              ?status=
POST /api/v1/devices/{id}/revoke
GET  /api/v1/activity             ?employee=&device=&from=&to=
GET  /api/v1/security/events
POST /api/v1/reports              {type, range, format}
GET  /api/v1/reports/{id}
GET  /api/v1/reports/{id}/download
GET  /api/v1/audit                ?from=&to=&action=
GET  /api/v1/license/status
```

## 9.5 Billing

```
POST /api/v1/billing/orders       {plan, seats, cycle} -> order id + checkout
POST /api/v1/billing/webhooks/razorpay   (signature verified, idempotent)
GET  /api/v1/billing/transactions ?status=&from=&to=
GET  /api/v1/billing/subscription
```

## 9.6 Holder (separate scope)

```
GET  /api/v1/holder/dashboard
GET  /api/v1/holder/organizations
GET  /api/v1/holder/admins
POST /api/v1/holder/admins
PATCH /api/v1/holder/admins/{id}/status   {status, reason}
GET  /api/v1/holder/transactions
GET  /api/v1/holder/security-events
POST /api/v1/holder/releases
```

## 9.7 Backwards compatibility

Existing agent builds call `/upload`, `/api/records`, `/api/status_one`,
`/api/identity_list`, `/api/audit`. Keep these as thin adapters that map the global key
to the pilot tenant until every deployed agent is updated. Set a hard removal date and
track upgrade status in the holder panel.

---

# 10. SUBSCRIPTIONS, LICENSING AND PAYMENTS

## 10.1 Pricing problems in V2 (fixed here)

The V2 ladder charges more per seat as the customer grows:

- Starter: ₹1,850 / 20 = ₹92.50 per seat
- Business: ₹3,500 / 40 = ₹87.50 per seat
- Custom: ₹112.50 per seat ← more expensive than Business

That is backwards. Volume must always be rewarded, otherwise the best customers have a
reason to split into small accounts.

## 10.2 Recommended price sheet (Option B — sustainable)

| Plan | Seats | Price / month | ₹ per seat | vs list |
|---|---|---|---|---|
| Starter | 5–20 | ₹3,500 | ₹175 | −12% |
| Business | 21–40 | ₹6,400 | ₹160 | −20% |
| Custom | 41–100 | ₹150 × seats | ₹150 | −25% |
| Enterprise | 101+ | ₹125 × seats, annual | ₹125 | −37% |

List price: ₹199 per employee per month. Annual prepay: pay 10 months, get 12.

Why not the V2 ₹125 base: at ₹87.50 per seat, a 40-employee client pays ₹3,500/month.
After support (2 hours at ₹500), payment fees (2%) and infrastructure (~₹100 per
client), gross margin is under 60% — and one demanding client destroys it. At ₹160 per
seat, the same client pays ₹6,400 and margin is ~80%, while still being 80% cheaper
than Insightful (₹8/seat ≈ ₹680; 40 seats ≈ ₹27,000/month) and 55% cheaper than
Teramind.

Keep Option A (₹125 base, V2 math) only as a launch offer for the first three pilot
clients, with a written expiry date.

## 10.3 Add-ons

| Add-on | Price |
|---|---|
| Evidence screenshots | +₹30 / employee / month |
| Extra retention (12 months) | +₹10 / employee / month |
| Local HR gateway (on-prem buffer) | ₹25,000 one-time + ₹5,000/year |
| White-label (customer logo/name) | ₹50,000/year |
| Setup and onboarding | ₹15,000 (waived on annual 40+ seats) |
| Data migration from another tool | ₹10,000+ |
| Custom integration (payroll/Tally/biometric) | ₹25,000–150,000 per project |
| Priority support (4-hour response) | +15% of contract |

## 10.4 GST and invoicing

- GST 18% on SaaS; SAC 998439 (confirm with a CA).
- Invoice fields: legal name, GSTIN, place of supply, HSN/SAC, tax split (CGST/SGST
  or IGST), invoice number series, payment reference.
- Invoices generated server-side on PAID; emailed as PDF; downloadable from the panel.
- Keep a sequential invoice counter per financial year; never edit a issued invoice —
  issue a credit note instead.

## 10.5 Billing cycle

Replace "31 days" with calendar months. Reasons: customers compare monthly prices,
accountants reconcile monthly, and GST returns are monthly. Annual plans are billed on
the purchase anniversary.

Expiry handling: 7-day grace with a visible banner, then read-only mode (data preserved),
then suspension of new enrollments. Never delete customer data on expiry.

## 10.6 Payment implementation

- Provider: Razorpay for India (UPI, cards, netbanking, auto-debit), Stripe later for
  international.
- Flow: HR selects plan → server creates order → hosted checkout → provider webhook →
  signature verified → transaction PAID → subscription activated → invoice issued.
- Never activate on a screenshot, a manual UTR, or an admin clicking "mark paid".
  If a manual override is ever needed, it requires holder role + reason + audit entry.
- Webhooks are idempotent by `event_id`; replay is detected and ignored.
- Reconciliation: daily job compares provider settlements to transactions and flags
  mismatches.
- No card data ever touches our servers (hosted checkout only). This keeps PCI scope
  minimal.

## 10.7 Refund policy

"No refunds" is enforceable for business customers only if it is disclosed before
purchase and does not conflict with non-waivable consumer law. Implement:

1. Pre-checkout checkbox: "I have read the no-refund policy."
2. Policy page linked in the invoice and the panel footer.
3. No refund button in the product (as V2 requires).
4. Service credits for verified outages against the SLA, issued as credit notes —
   not cash refunds.
5. Legal review before the first sale; keep a signed B2B terms document per client.

## 10.8 License enforcement

- Server is the source of truth. The panel checks `/license/status` at login and every
  6 hours.
- Over-limit: block new enrollments, keep existing devices reporting and data readable.
- Expired: read-only, with a clear renewal path.
- Offline tolerance for the HR panel: 7 days cached license, then read-only.
- Tampering with the clock must not extend a license — always compare server time.

---

# 11. UPDATES AND RELEASE MANAGEMENT

## 11.1 Release manifest

Each release publishes a signed JSON manifest:

```
{
  "version": "3.1.0",
  "channel": "stable",
  "released_at": "2026-12-01T10:00:00Z",
  "min_supported": "3.0.0",
  "rollout_pct": 20,
  "artifacts": [
    {"name": "HRAgent.exe", "sha256": "...", "size": 12345678,
     "url": "https://.../HRAgent-3.1.0.exe"},
    {"name": "HRAdmin.exe", "sha256": "...", "size": 12345678,
     "url": "https://.../HRAdmin-3.1.0.exe"},
    {"name": "WorkPulse.apk", "sha256": "...", "size": 12345678,
     "url": "https://.../WorkPulse-3.1.0.apk"}
  ],
  "notes": {"features": [...], "fixes": [...], "security": [...]},
  "signature": "ed25519:..."
}
```

The private signing key never leaves the release machine. The public key ships inside
the admin and agent binaries.

## 11.2 Update flow (data preservation is mandatory)

1. Admin panel shows "Update available" (checked every 6 hours; Telegram notification).
2. Download to a temp folder.
3. Verify SHA-256 and signature. Abort on mismatch.
4. Backup current config and (for gateway installs) the local database.
5. Replace binaries atomically (write new, rename old, swap).
6. Run migrations if any, with a pre-migration snapshot.
7. Restart the panel; verify version and health.
8. On failure: restore the previous binaries and config automatically.
9. Record the result in update history and the audit log.

Employee records, activity history, credentials, subscriptions and reports are never
touched by an update. This rule is tested before every release.

## 11.3 Rollout strategy

Staged: 10% → 50% → 100%, with a 24-hour observation window at each step. A single
integrity-alert spike stops the rollout automatically.

---

# 12. PRIVACY, LEGAL AND COMPLIANCE (INDIA)

## 12.1 DPDP Act 2023 obligations

| Obligation | Implementation |
|---|---|
| Notice | Plain-language notice in the consent screen and a printable notice for the notice board |
| Consent | Recorded with timestamp, version, employee id, device; withdrawable; withdrawal stops collection |
| Purpose limitation | Only productivity/device management; stated explicitly |
| Data minimisation | The field list in Section 5.1 is the ceiling |
| Retention | 90 days activity default; configurable; deletion job |
| Accuracy | Employee can request a copy of their own activity summary |
| Grievance | Named grievance officer + email in the notice |
| Breach notification | Documented 72-hour internal playbook; notify clients and the Data Protection Board |
| Processor agreement | DPA with every client, stating we process on their instructions |
| Cross-border | TiDB Singapore region today; disclose in the DPA; move to an India region when revenue justifies it |

## 12.2 Documents required before the first sale

1. Privacy policy (public website).
2. Terms of service (B2B, includes no-refund clause and SLA).
3. Data processing agreement (DPA) template.
4. Employee monitoring notice (English + Hindi + one regional language).
5. Consent form (paper twin of the ACCEPT screen).
6. Data retention and deletion policy.
7. Incident response plan.
8. Sub-processor list (Render, TiDB, Razorpay, email provider).
9. Security overview one-pager for client IT teams.

## 12.3 What we tell employees

"WorkPulse records which applications and websites you use on company devices during
work hours, and how long you use them. It does not record what you type, your messages,
your personal files, your camera, your microphone, or your location. You can see it
running at any time. Ask HR for your own activity summary."

That paragraph, printed and signed, prevents most disputes.

---

# 13. RELIABILITY, BACKUP AND OBSERVABILITY

## 13.1 Targets

| Metric | Target |
|---|---|
| Availability | 99.5% monthly (Render Starter) |
| API latency (p95) | < 1.5 s |
| Upload success | > 99% with retries |
| RPO (max data loss) | 24 hours |
| RTO (restore time) | 4 hours |
| Support first response | 8 business hours (4 for priority) |

## 13.2 Backups

- Nightly logical dump of TiDB, encrypted, uploaded to Backblaze B2 or S3
  (~₹50–100/month at current size).
- 7 daily, 4 weekly, 12 monthly copies.
- Restore drill on the first Saturday of every month; record the result.
- Backups are useless until a restore has been proven — treat the drill as a release
  requirement, not an optional task.

## 13.3 Monitoring

- Uptime monitor on `/ping` every minute (UptimeRobot free) with email/Telegram alerts.
- Render alerts on instance restarts and error-rate spikes.
- Sentry (free tier) for server exceptions; a daily digest, not per-error spam.
- Agent-side: queue depth and sync failures reported in the heartbeat, shown in the
  Security Scanner.
- A status page (static) for clients during incidents.

## 13.4 Capacity plan

| Devices | Events/day | DB growth/month | Instance |
|---|---|---|---|
| 100 | ~30k | ~150 MB | Render Starter $7 |
| 500 | ~150k | ~750 MB | Render Standard $25 |
| 1,000 | ~300k | ~1.5 GB | Standard → Pro, or Hetzner 4 vCPU |
| 5,000 | ~1.5M | ~7.5 GB | Dedicated + read replica |

TiDB free tier covers the first ~80–100 devices for a year. Plan the upgrade path
before signing a 300-device client, not after.

---

# 14. DEVELOPMENT ROADMAP (SEQUENCED)

The V2 roadmap lists 12 phases but does not say what blocks what. This order does.

| Phase | Scope | Duration | Depends on | Exit test |
|---|---|---|---|---|
| 0 | Baseline (done) | — | — | Agent reports, panel shows data |
| 1 | Tenancy + auth + enrollment + token rotation | 8–10 days | — | Two tenants cannot see each other; revoked device stops in <60 s |
| 2 | HR panel login, menus, tenant scoping, audit UI | 6–8 days | 1 | HR logs in, sees only their data, every action audited |
| 3 | Reports: PDF + XLSX, daily/weekly/monthly, server-side | 5–7 days | 1, 2 | Report downloads with correct totals |
| 4 | Holder panel v1 (CMD) + org/admin/plan management | 8–12 days | 1 | You can create, suspend and restore an org |
| 5 | Subscriptions + Razorpay + license enforcement | 8–10 days | 4 | Test payment activates; expired license goes read-only |
| 6 | Scheduled reports (6 PM / 12 AM) + Telegram notifications | 4–5 days | 3 | Report appears without anyone opening the panel |
| 7 | Release manifest + update flow + rollback | 6–8 days | 5 | Update preserves data; failed update rolls back |
| 8 | Browser extension (MV3) | 8–10 days | 1 | Domains + durations appear, query strings stripped |
| 9 | Signed builds + integrity checks | 4–5 days | 7 | Tampered binary raises a security event |
| 10 | Hardening: rate limits, pen-test checklist, docs, packaging | 8–10 days | all | Acceptance checklist in Section 15 passes |
| 11 | Linux agent | 10–15 days | 10 | Same API, X11 supported, Wayland documented |
| 12 | Web holder dashboard, analytics, integrations | later | 11 | — |

**Critical path:** 1 → 2 → 5. Everything else can run in parallel or wait.

Realistic calendar with one developer: **8–10 weeks to a sellable V3**, with the first
paying pilot possible after Phase 5 (about 5–6 weeks).

---

# 15. TESTING AND ACCEPTANCE

## 15.1 Automated tests that must exist

1. Tenant isolation: every endpoint tested with tenant A's token against tenant B's ids.
2. Token revocation: revoked device gets 401 within one request.
3. Enrollment code: single use, expires in 15 minutes, wrong tenant rejected.
4. Webhook: bad signature rejected; replayed event ignored; duplicate order not double-credited.
5. License: expired → read-only; over-limit → new enrollment blocked, data intact.
6. Idempotency: same upload twice stores once.
7. Offline queue: 7 days buffered, flushes in order, no duplicates.
8. Reports: totals match raw events for a seeded day.
9. Retention: 91-day-old rows deleted, 89-day-old rows kept, audit kept 12 months.
10. Update: data preserved; forced failure rolls back.
11. Backup restore: fresh database restored from last night's dump.
12. Permission matrix: holder token cannot read activity rows.

## 15.2 Manual acceptance before first sale

- Install on a clean Windows 11 machine as a non-admin user.
- Consent screen appears; decline exits cleanly; accept enrolls.
- Kill the agent; it returns within 60 seconds.
- Unplug the network for 30 minutes; data appears after reconnect.
- Two organizations on one server never see each other.
- Pay in Razorpay test mode; license activates; invoice PDF is correct.
- Expire a subscription manually; panel goes read-only, data still visible.
- Run an update; config and history survive.

## 15.3 Demo script for sales

1. Open the panel → dashboard fills instantly.
2. Show one employee's app timeline for today.
3. Show a live "opened now" event while the client watches.
4. Show the security scanner catching a killed agent.
5. Show the consent screen and the prohibited-collection list.
6. Show a PDF report and the invoice.
7. Close with price per employee versus one competitor.

Ten minutes, no slides.

---

# 16. BUSINESS PLAN

## 16.1 Unit economics at recommended pricing

Per 40-employee Business client (₹6,400/month):

| Line | Amount |
|---|---|
| Revenue | ₹6,400 |
| Payment fee (2%) | −₹128 |
| Infrastructure share | −₹100 |
| Support (2 h × ₹500) | −₹1,000 |
| Gross profit | ₹5,172 (81%) |

Break-even: one client covers the entire monthly infrastructure. Ten clients ≈ ₹64,000
MRR ≈ ₹7.7 lakh/year.

## 16.2 Go-to-market for the first 90 days

1. **Pilot:** three companies, 14 days free, in exchange for a written case study and a
   testimonial. Cap at 20 devices each.
2. **Channel:** local IT resellers and system integrators get 20–25% recurring
   commission. They already sell antivirus and AMC; this is an easy add.
3. **Direct:** 20 visits to SMBs within driving distance. Demo on the laptop, not slides.
4. **Content:** one LinkedIn post per week showing a real dashboard insight (no client
   names), plus a one-page PDF.
5. **Offer:** annual prepay (10 months for 12) with free setup. Improves cash flow and
   reduces churn.
6. **Referral:** one free month per referred client that pays.

## 16.3 Sales assets to produce

- One-page product sheet (what it does, what it refuses to do, price).
- Consent and notice pack (Section 12.2).
- Demo script (Section 15.3).
- Security overview for client IT.
- Case study template.
- Invoice and terms templates.

## 16.4 KPIs to watch

| KPI | Target |
|---|---|
| Trial → paid | > 30% |
| Activation (first device reporting) | < 15 minutes |
| Monthly churn | < 3% |
| Support tickets per client per month | < 2 |
| CAC payback | < 3 months |
| Uptime | > 99.5% |
| ARPU | > ₹5,000/month |

---

# 17. RISKS AND MITIGATIONS

| Risk | Impact | Mitigation |
|---|---|---|
| One leaked global key exposes all data | Fatal | Per-device tokens; key never ships in the EXE |
| Employee resistance / legal complaint | High | Consent, notice, prohibited list, grievance officer |
| Screenshot feature misused by a client | High | Default OFF, blocklist, audit, tenant toggle |
| Payment activated without verification | High | Webhook signature + idempotency; no manual mark-paid |
| Server outage loses trust | High | Agent queue, uptime monitor, status page, backups |
| Scope creep (Linux, AI, React) delays revenue | High | Roadmap gate: no new platform until 10 paying clients |
| Competitor undercuts price | Medium | Compete on local support and transparency, not price |
| Database bill spike | Medium | Spend caps, retention policy, capacity plan |
| Support burden per client | Medium | Setup fee, onboarding, self-serve docs, priority tier |
| Founder bandwidth | High | Sequence strictly; ship Phase 1–5 before anything else |

---

# 18. OPEN DECISIONS (with recommended answers)

| # | Decision | Recommendation |
|---|---|---|
| 1 | Keep Flask/TiDB or rewrite to FastAPI/PostgreSQL | Keep. Revisit at 2,000 devices |
| 2 | Screenshots: prohibited, opt-in, or standard | Opt-in Enterprise add-on, default OFF |
| 3 | Pricing: V2 math or Option B | Option B (₹199 list, ₹160 at 40 seats) |
| 4 | Custom plan minimum | 41 employees |
| 5 | Payment provider | Razorpay first, Stripe later |
| 6 | Holder panel v1 | CMD GUI, web dashboard later |
| 7 | Data region | Singapore now, India when revenue justifies |
| 8 | Retention | 90 days activity, 12 months audit |
| 9 | Refund policy | No refunds + SLA credits, legal review, pre-checkout disclosure |
| 10 | Linux agent | After 10 paying clients |
| 11 | Billing cycle | Calendar month; annual = 10 months for 12 |
| 12 | Browser monitoring | MV3 extension primary; history fallback off by default |

---

# 19. NEXT 7 DAYS — THE BUILD I START NOW

| Day | Work | Deliverable |
|---|---|---|
| 1 | Tenant schema, migration plan, `organizations` + `admins` + `devices` tables | Migration script + seeded demo tenant |
| 2 | Auth: login, refresh, logout, password hashing, lockout, audit | Working `/auth/*` endpoints + tests |
| 3 | Enrollment codes + device tokens + revoke | Agent enrolls, token stored encrypted, revoke works |
| 4 | Tenant scoping on every query + isolation test suite | Two tenants provably isolated |
| 5 | Agent update: enrollment flow, device token, offline queue | New EXE build that enrolls cleanly |
| 6 | HR panel login + tenant-scoped screens | Panel works with real accounts |
| 7 | Deploy to staging, run full test suite, document API | Staging URL + API docs |

After day 7, the product can safely hold two customers. That is the line between a demo
and a business.

---

# 20. FINAL DEFINITION

WorkPulse V3 is a hybrid, multi-tenant workforce activity and device management platform
with three interfaces, transparent monitoring, per-device security, verified payments,
automated reporting and data-preserving updates.

Core principles, in priority order:

1. Customer data is never lost — not on update, not on expiry, not on migration.
2. One tenant can never see another tenant's data.
3. Monitoring is transparent, consented and minimal.
4. Money activates only after provider verification.
5. Every administrative action is audited.
6. Ship the smallest sellable version, then earn the right to build the rest.

WORKPULSE V3
Secure Workforce. Trusted Devices. Smarter Management.
