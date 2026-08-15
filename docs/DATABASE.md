# Database

PostgreSQL stores users, firms and versioned profiles, accounts/settings/daily state, Telegram sources/messages, normalized signals/updates/targets, per-account decisions, broker connections, orders/positions/events/trades, virtual execution, immutable rule/risk snapshots, notifications, performance aggregates, system events/settings and audit logs.

All timestamps are timezone-aware UTC. Profile reset timezones are IANA names and applied only at calculation boundaries. Critical routing/execution persistence uses transactions and row locks. Public identifiers are UUIDs. Run `alembic upgrade head` for schema upgrades; always run `scripts\backup-database.ps1` before an update.
