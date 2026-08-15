# Security

Passwords use Argon2, JWT signatures use a strict HS256 allowlist and short expiry, and all privileged routers depend on centralized authentication. Broker credentials use a dedicated Fernet key. CORS is an explicit allowlist; wildcard credentialed origins are not used. Trusted-host and security-header middleware are enabled. Production API docs can be disabled with `DOCS_ENABLED=false`.

Do not place tokens in browser storage in production; use the Authorization header and keep the access token in memory. Put the app behind TLS and a VPN/restricted reverse proxy. Rotate application, encryption, Telegram and broker credentials on suspected compromise. Never log passwords, ciphertext plaintext, access tokens or Telegram session data.

Live trading carries material financial risk. Use least-privilege Windows service accounts, OS/database backups, strict firewall rules, PostgreSQL/Redis authentication, patched dependencies, audit-log retention and periodic restore tests.
