"""Add fail-closed MT5 demo execution mode."""
from alembic import op


revision = "0004_demo_trading_mode"
down_revision = "0003_correlation_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE tradingmode ADD VALUE IF NOT EXISTS 'DEMO'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely while rows may reference
    # them. Keeping DEMO is the non-destructive downgrade behavior.
    pass
