"""Persist live MT5 equity snapshots in daily account statistics."""
import sqlalchemy as sa
from alembic import op


revision = "0005_live_equity_snapshots"
down_revision = "0004_demo_trading_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("account_daily_stats", sa.Column("latest_balance", sa.Numeric(18, 2), nullable=True))
    op.add_column("account_daily_stats", sa.Column("latest_equity", sa.Numeric(18, 2), nullable=True))
    op.add_column("account_daily_stats", sa.Column("margin", sa.Numeric(18, 2), nullable=True))
    op.add_column("account_daily_stats", sa.Column("margin_level", sa.Numeric(18, 4), nullable=True))
    op.add_column("account_daily_stats", sa.Column("daily_loss", sa.Numeric(18, 2), nullable=True))


def downgrade() -> None:
    for column in ("daily_loss", "margin_level", "margin", "latest_equity", "latest_balance"):
        op.drop_column("account_daily_stats", column)
