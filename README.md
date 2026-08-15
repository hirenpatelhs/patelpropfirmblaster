# Patel Propfirm Blaster

Patel Propfirm Blaster (PPB) is a Windows-native, fail-closed prop-firm signal execution and risk-control platform. Telegram provides trade ideas; deterministic application code owns validation, sizing, prop-rule compliance, daily loss controls, execution, and audit history.

## Architecture

- `backend/`: FastAPI, SQLAlchemy, Alembic, PostgreSQL, Redis workers, Telethon, Telegram notifications, mock and MT5 broker adapters.
- `frontend/`: Next.js, TypeScript, Tailwind, shadcn-style primitives, Recharts and a responsive dark operations dashboard.
- `scripts/`: PowerShell installation, services, updates, health checks and PostgreSQL backups.
- `docs/`: design and operating guides.

The pipeline is: receive → parse → normalize → validate freshness → route → rules → daily/exposure/drawdown guards → account-specific sizing → final preflight → queue → execute → acknowledge → persist → notify. Any missing or uncertain safety input rejects the trade.

## Local development

Prerequisites: Python 3.12+, Node.js LTS, PostgreSQL, and Redis. No Docker is used.

```powershell
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
alembic upgrade head
python -m app.database.seed
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In another terminal:

```powershell
cd frontend
npm install
copy .env.example .env.local
npm run dev
```

Open `http://localhost:3000`; API docs are at `http://localhost:8000/docs` in development. Bootstrap the first administrator with `POST /api/v1/auth/bootstrap` using a password of at least 12 characters.

Run the SHADOW services in separate terminals from `backend` after PostgreSQL and Redis are available:

```powershell
python -m app.workers.main
python -m app.telegram.main
```

Keep every account in `SHADOW` mode during this phase. The worker rejects non-SHADOW account execution; MT5 close behavior is covered at the adapter boundary only.

The worker uses a Redis Stream (`ppb:jobs:stream`) for crash-recoverable, at-least-once delivery. A renewable Redis scheduler lease and a Windows global mutex prevent multiple trade-management schedulers. Run one registered `PatelPropfirmWorker` service; reclaimed events remain protected by database and broker idempotency keys.

Historical guru messages can be validated and then replayed through the same Telegram pipeline. The command defaults to a read-only dry run and refuses enqueueing if any eligible account is not in SHADOW mode:

```powershell
python -m app.testing.replay_telegram tests\fixtures\messy_guru_messages.jsonl --chat-id YOUR_CONFIGURED_REPLAY_CHAT_ID
python -m app.testing.replay_telegram tests\fixtures\messy_guru_messages.jsonl --chat-id YOUR_CONFIGURED_REPLAY_CHAT_ID --enqueue
python -m app.testing.replay_telegram tests\fixtures\guru\contextual_examples.jsonl --report artifacts\guru-replay.json
```

Real PostgreSQL/Redis crash, failover, restart, and two-worker tests are VPS-only. See `docs\INTEGRATION_TESTING.md`; the runner requires isolated TEST connection strings and never enables LIVE execution. Durable database consistency can be checked read-only with `python -m app.testing.check_consistency`.

## Windows VPS

Install Python, Node.js LTS, PostgreSQL, a Redis-compatible Windows service, and NSSM. Run `scripts\install-windows.ps1` from an elevated PowerShell terminal, configure `backend\.env`, create the database, run the printed migration command, then register services with `scripts\register-services.ps1`.

Generate `ENCRYPTION_KEY` with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Generate a separate random `APP_SECRET`. Never commit `.env`.

## Telegram and MT5

Create Telegram API credentials at `my.telegram.org`; configure the API ID/hash for Telethon and a BotFather bot token for notifications. The first Telethon login creates a persistent session under `backend/data/telegram`.

Each MT5 account should use its own terminal installation/data directory. Store its terminal path, login, server, encrypted password and unique magic number. The Windows installer uses `requirements-windows.txt` to add the official `MetaTrader5` package; it is intentionally not required for mock tests on other operating systems.

## Safe onboarding

Create a prop firm, then an account/stage-specific rule profile. Verify the firm’s current terms and explicitly configure whether EA, signal copying and third-party signals are allowed. Add an account in **SHADOW** mode, configure internal safety buffers, map a Telegram source, and test the full lifecycle.

Live mode must remain unavailable until broker health, a reviewed rule profile, valid risk settings, the emergency stop and the automation-permission acknowledgement are all present. A signal source never controls lot size or account risk.

## Emergency controls

“Stop new trades” locks routing. “Close all positions” is separate and requires the exact confirmation `CLOSE ALL POSITIONS`. Neither action silently implies the other. Account pause and the global emergency stop are audited.

## Operations

- Health: `scripts\health-check.ps1`
- Backup: set `PPB_DATABASE_URL`, then run `scripts\backup-database.ps1`
- Update: stage the release and run `scripts\update.ps1`; it backs up before migration/build.
- Logs: `C:\PatelPropfirmBlaster\logs`; NSSM rotates service output.

If execution stops, check `/api/v1/system/health`, Redis, the account status, broker heartbeat, current rule acknowledgement, daily guard, signal freshness and the audit log—in that order. Do not bypass a rejection to restore trading.

## Testing

```powershell
cd backend
pytest -q
cd ..\frontend
npm run lint
npm run build
```

Tests use `MockBrokerAdapter`; live broker credentials are never required.
