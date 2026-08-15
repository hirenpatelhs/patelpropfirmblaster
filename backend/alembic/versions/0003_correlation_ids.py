"""Add lifecycle correlation IDs for end-to-end tracing."""
from alembic import op


revision = "0003_correlation_ids"
down_revision = "0002_shadow_lifecycle"
branch_labels = None
depends_on = None


TABLES = (
    "telegram_messages", "signals", "signal_account_decisions", "orders",
    "positions", "position_events", "trades", "notifications", "audit_logs",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS correlation_id VARCHAR(64)")
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_correlation_id ON {table}(correlation_id)")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_correlation_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS correlation_id")
