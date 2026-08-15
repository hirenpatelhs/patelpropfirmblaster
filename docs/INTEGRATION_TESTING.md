# Real PostgreSQL and Redis integration testing

Run this workflow only on the Windows VPS (or a deliberately prepared development host). It does not use Docker and never installs or configures services automatically.

Record the observed versions and pass/fail results in `docs/REAL_INFRASTRUCTURE_RESULTS.md`. Leave its status pending until the VPS run actually completes.

## Isolation requirements

- Create a PostgreSQL database whose name contains `test`, for example `ppb_integration_test`.
- Use a dedicated PostgreSQL test user with access only to that database.
- Use a nonzero dedicated Redis database, for example database 15. For Redis restart testing, use a completely separate TEST Redis service/port because restarting a shared process affects every logical database.
- Never reuse production connection strings, Redis database 0, Telegram sessions, or broker credentials.
- Keep every seeded account in SHADOW mode. The integration suite contains no MT5 connection path.

Example PostgreSQL setup, executed by an administrator:

```sql
CREATE ROLE ppb_test LOGIN PASSWORD 'replace-with-test-only-password';
CREATE DATABASE ppb_integration_test OWNER ppb_test;
```

## Windows VPS command

After installing backend requirements into `backend\venv` and starting the existing PostgreSQL and Redis services:

```powershell
.\scripts\run-integration-tests.ps1 `
  -DatabaseUrl "postgresql+asyncpg://ppb_test:TEST_PASSWORD@127.0.0.1:5432/ppb_integration_test" `
  -RedisUrl "redis://127.0.0.1:6379/15"
```

The runner refuses database URLs without `test` and refuses Redis database 0. It migrates only the test database and runs only `backend\tests\integration`.

To include the destructive restart scenarios, supply the Windows service names of separately installed TEST instances from an elevated PowerShell terminal:

```powershell
.\scripts\run-integration-tests.ps1 `
  -DatabaseUrl "postgresql+asyncpg://ppb_test:TEST_PASSWORD@127.0.0.1:5433/ppb_integration_test" `
  -RedisUrl "redis://127.0.0.1:6380/15" `
  -PostgresTestServiceName "postgresql-ppb-test" `
  -RedisTestServiceName "redis-ppb-test"
```

Omitting either service name safely skips that service's stop/start test. Never pass production service names.

## Service and failure tests

The core suite launches two worker processes, verifies one Redis scheduler lease, terminates the leader, checks failover, kills an in-flight consumer before acknowledgement, and verifies reclaimed processing produces one durable execution. PostgreSQL row-lock tests run two monitors concurrently and verify one TP side effect.

Redis and PostgreSQL service-stop tests must be run during an isolated maintenance window with service-control commands appropriate to the VPS installation. Database state is authoritative; Redis scheduler leases and unpersisted stream events are non-durable unless Redis AOF/RDB persistence is configured. Never perform disruptive service tests on the production PPB database or Redis instance.

After the suite, run the read-only durable-state check against the same test database:

```powershell
cd backend
.\venv\Scripts\python.exe -m app.testing.check_consistency
```

Historical replay can produce a machine-readable report without enqueueing anything:

```powershell
cd backend
.\venv\Scripts\python.exe -m app.testing.replay_telegram tests\fixtures\guru\contextual_examples.jsonl --report artifacts\guru-replay.json
```

## Windows NSSM services

The registered names are exactly:

- `PatelPropfirmBackend`
- `PatelPropfirmWorker`
- `PatelPropfirmTelegram`
- `PatelPropfirmFrontend`

Each uses NSSM restart-on-failure with a five-second restart delay and ten-second throttle. Registration refuses an existing service name. The worker additionally holds the host-wide `Global\PatelPropfirmBlasterWorker` mutex and a renewable Redis scheduler lease.
