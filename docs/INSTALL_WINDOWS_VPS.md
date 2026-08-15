# Windows VPS installation

Use a supported 64-bit Windows Server VPS. Install Python 3.12+, current Node.js LTS, PostgreSQL, a Redis-compatible Windows service and NSSM. Create a dedicated non-administrator service account and restrict the installation and backup folders to it and administrators.

Run `scripts\install-windows.ps1` elevated. Create the PostgreSQL database/user, edit `backend\.env`, verify strict `CORS_ORIGINS` and `ALLOWED_HOSTS`, generate strong application/encryption keys, then run `alembic upgrade head` and `python -m app.database.seed`. Run `scripts\health-check.ps1` before `scripts\register-services.ps1`.

Use a reverse proxy with TLS for remote access. Keep ports 5432 and 6379 private. Permit dashboard/API access only from trusted IPs or a VPN. Install each MT5 terminal in an isolated directory, log into exactly one account, disable terminal auto-trading until Shadow verification succeeds, and assign a unique magic number.
