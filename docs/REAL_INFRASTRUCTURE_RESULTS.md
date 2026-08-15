# Real infrastructure validation results

Status: **SHADOW VALIDATION COMPLETE; MT5 DEMO ARMED, FIRST REAL DEMO ORDER PENDING**

## Environment

- Run date/time: 2026-08-15 (Europe/Berlin)
- PPB revision/package: `c5ea7f5` plus the Windows compatibility fixes recorded in this working tree
- Project root: `C:\Users\Administrator\patelpropfirmblaster`
- Windows version: Windows Server 2025 Datacenter, build 26100, 64-bit
- PowerShell version: 5.1.26100.1591
- Python version: 3.12.10 (clean `backend\venv`)
- pip version: 26.2.1
- Node.js version: 24.19.0
- npm version: 11.17.0
- PostgreSQL version: 17.11
- Redis-compatible server and version: redis-windows 8.10.0
- Dedicated test database: `ppb_integration_test`, owned by `ppb_test`, local PostgreSQL port 5433
- Dedicated test Redis: `127.0.0.1:6380/15`, AOF enabled
- SHADOW runtime database: `ppb_shadow`, owned by `ppb_runtime`, local PostgreSQL port 5433
- SHADOW runtime Redis: `127.0.0.1:6379/0`, AOF enabled

No passwords, application secrets, encryption keys, Telegram tokens, broker credentials, or MT5 credentials are recorded here.

## Results

- Unit/torture: **PASS** — 96 passed, 0 failed; 11 explicitly gated infrastructure tests skipped
- Real-infrastructure integration: **PASS** — 10 passed, 0 failed, 0 skipped; 73.20 seconds on the final run
- Frontend lint: **PASS**
- Frontend production build: **PASS** — Next.js 16.3.1 production build compiled, type-checked, and generated all static pages
- Two-worker/single-leader: **PASS**
- Leader failover: **PASS**
- In-flight crash/reclaim: **PASS**
- PostgreSQL `SKIP LOCKED` duplicate-TP protection: **PASS**
- PostgreSQL outage/fail-closed recovery: **PASS**
- Redis restart/database-authority recovery: **PASS**
- API restart/state independence: **PASS**
- Full lifecycle state recovery (executed TP and break-even preservation): **PASS**
- Duplicate Telegram/listener delivery idempotency: **PASS**
- Scheduler safety (Redis lease and two-worker ownership): **PASS**
- Consistency checker: **PASS** — `consistent: true`, `issue_count: 0`
- Dashboard: **RUNNING** — HTTP 200 on `http://127.0.0.1:3000`
- API: **RUNNING** — root health `ok`; system checks report backend, database, and Redis `CONNECTED`
- Worker: **RUNNING** — scheduler leadership acquired on SHADOW runtime Redis
- Windows reboot recovery: **PASS** — PostgreSQL, runtime Redis, backend, worker, and frontend returned automatically; API health was fully connected and dashboard returned HTTP 200
- Telegram: **CONNECTED** — authorized user session runs as an Automatic Windows service; dialog re-verification identified the broadcast signal source as `Fredtrading - VIP - Main channel` (`-1001239815745`). The former `Fredtrading - VIP CHAT` mapping (`-1001622898322`) is disabled while its historical chat messages remain correctly attributed; the next real Main-channel signal remains the final live-delivery observation.
- Patel Scalper notifications: **PASS** — private `/start` recipient configured; durable test notification was `DELIVERED` to the dashboard and `SENT` through Telegram
- Execution mode: **SHADOW** — runtime contains no configured trading accounts
- MT5: **DEMO CONNECTED / ARMED** — the installed terminal is authenticated to a genuine `MetaQuotes-Demo` account, permits expert trading, exposes XAUUSD, and reconnects through the application adapter. The account is ACTIVE with a hard 0.05-lot cap, one-position limit, and live-equity-aware risk guards; no order had been sent at the final preflight.
- LIVE: **DISABLED / NOT CONFIGURED**

## MT5 DEMO activation state

- Parser now recognizes spelled-out `TAKE PROFIT` targets and does not treat a small unlabeled value such as `XAUUSD BUY 0.1` as an entry price.
- The MT5 adapter performs bounded reconnect attempts, reselects symbols, marks exhausted connections unhealthy, and suppresses repeated order keys by scanning MT5 positions/orders for a compact hashed PPB comment.
- `TradingMode.DEMO` is database-backed by migration `0004_demo_trading_mode`. DEMO account creation requires an MT5 platform, SIMULATION stage, encrypted password, terminal/server/magic configuration, and an exact automation acknowledgement.
- DEMO execution requires MT5 to report a genuine demo login and clamps submitted volume to at most 0.05 lot. LIVE remains unconditionally blocked in both the API and execution engine.
- MT5 balance, equity, margin, margin level, account-wide floating P&L, positions, and pending orders feed fail-closed daily-loss and drawdown guards on every route and monitoring tick.
- Broker-initiated closure reconciliation and PPB position recovery are implemented and covered by focused tests. The first real through-bot DEMO trade remains the final operational validation.

## Sanitized evidence

```text
pytest -q
96 passed, 11 skipped

pytest tests\integration -q
10 passed in 73.20s

python -m app.testing.check_consistency
consistent: true
issue_count: 0

GET /api/v1/system/health
backend: CONNECTED
database: CONNECTED
redis: CONNECTED

GET http://127.0.0.1:3000
HTTP 200
```

## Safety gates

- [x] Every seeded integration account was SHADOW.
- [x] The PostgreSQL URL named the dedicated `ppb_integration_test` database.
- [x] The Redis URL used dedicated database 15 on dedicated port 6380.
- [x] Disruptive restart tests controlled only the local TEST PostgreSQL cluster and TEST Redis process.
- [x] No MT5 terminal or prop-firm account was connected.
- [x] LIVE execution remained disabled.

## Remaining service and Telegram limitations

An administrator registered all four documented PPB NSSM definitions plus `PPBPostgreSQL` and `PPBRedisRuntime`. Backend and worker depend on both infrastructure services; frontend depends on backend. A controlled Windows reboot completed successfully: PostgreSQL, runtime Redis, backend, worker, and frontend were `Running`, the API reported backend/database/Redis `CONNECTED`, the dashboard returned HTTP 200, and the worker acquired the scheduler leadership lock. `PatelPropfirmTelegram` correctly remained stopped with Manual startup pending user-session authorization.

Telegram API credentials and the notification-bot credential are configured. The Telethon session is authorized as the Telegram user account and the listener runs automatically as a Windows service. Dialog inspection verified the source as `Fredtrading - VIP CHAT`, Telegram megagroup ID `-1001622898322`; the enabled database mapping now uses that exact ID. The mistakenly created bot-authorized session was moved aside as `backend\data\telegram\ppb.bot-session.backup`. A synthetic event using the source pipeline traversed the real Redis Stream and parser successfully, produced one four-target XAUUSD signal, and produced zero orders/positions/trades with no accounts configured. Those synthetic rows were removed afterward and the consistency checker returned zero issues. The next real Fred Trading post remains the final live-delivery observation. Patel Scalper private notifications are configured from the bot's `/start` update. The durable queue now emits dashboard and Telegram records for signal decisions, position opens, lifecycle transactions, manual SHADOW actions, reconciliation warnings, and idempotent prior-day summaries; a test delivery reached both channels successfully.
