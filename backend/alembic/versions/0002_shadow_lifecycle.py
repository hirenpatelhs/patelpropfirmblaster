"""Add deterministic shadow lifecycle and configuration fields.

The initial historical migration imports current metadata, so every operation is
guarded for both an existing installation and a clean database.
"""
from alembic import op


revision = "0002_shadow_lifecycle"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          CREATE TYPE riskclassification AS ENUM ('NORMAL','MEDIUM_RISK','HIGH_RISK','VERY_HIGH_RISK');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        DO $$ BEGIN
          CREATE TYPE targetstatus AS ENUM ('WAITING','TRIGGERED','EXECUTED','SKIPPED','MERGED','FAILED');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;

        ALTER TABLE account_settings ADD COLUMN IF NOT EXISTS maximum_positions_per_symbol INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE account_settings ADD COLUMN IF NOT EXISTS maximum_positions_per_direction INTEGER NOT NULL DEFAULT 2;
        ALTER TABLE account_settings ADD COLUMN IF NOT EXISTS maximum_pending_orders INTEGER NOT NULL DEFAULT 2;
        ALTER TABLE account_settings ADD COLUMN IF NOT EXISTS concurrent_limit_action VARCHAR(20) NOT NULL DEFAULT 'REJECT';
        ALTER TABLE account_settings ADD COLUMN IF NOT EXISTS risk_multipliers JSONB NOT NULL DEFAULT '{"NORMAL":"1","MEDIUM_RISK":"0.75","HIGH_RISK":"0.5","VERY_HIGH_RISK":"0"}'::jsonb;
        ALTER TABLE account_settings ADD COLUMN IF NOT EXISTS tp_allocation_preset VARCHAR(20) NOT NULL DEFAULT 'PROTECT';
        ALTER TABLE account_settings ADD COLUMN IF NOT EXISTS tp_custom_allocations JSONB NOT NULL DEFAULT '[]'::jsonb;
        ALTER TABLE account_settings ADD COLUMN IF NOT EXISTS break_even_offset_points INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE account_settings ADD COLUMN IF NOT EXISTS symbol_mappings JSONB NOT NULL DEFAULT '{}'::jsonb;

        ALTER TABLE telegram_messages ADD COLUMN IF NOT EXISTS original_content TEXT;
        ALTER TABLE telegram_messages ADD COLUMN IF NOT EXISTS latest_content TEXT;
        ALTER TABLE telegram_messages ADD COLUMN IF NOT EXISTS event_type VARCHAR(20) NOT NULL DEFAULT 'NEW';
        UPDATE telegram_messages SET original_content = body WHERE original_content IS NULL;
        UPDATE telegram_messages SET latest_content = body WHERE latest_content IS NULL;
        ALTER TABLE signals ADD COLUMN IF NOT EXISTS risk_classification riskclassification NOT NULL DEFAULT 'NORMAL';

        ALTER TABLE positions ADD COLUMN IF NOT EXISTS broker_symbol VARCHAR(40);
        ALTER TABLE positions ADD COLUMN IF NOT EXISTS original_volume NUMERIC(12,3);
        ALTER TABLE positions ADD COLUMN IF NOT EXISTS remaining_volume NUMERIC(12,3);
        UPDATE positions SET broker_symbol = symbol WHERE broker_symbol IS NULL;
        UPDATE positions SET original_volume = size WHERE original_volume IS NULL;
        UPDATE positions SET remaining_volume = size WHERE remaining_volume IS NULL;
        ALTER TABLE positions ALTER COLUMN broker_symbol SET NOT NULL;
        ALTER TABLE positions ALTER COLUMN original_volume SET NOT NULL;
        ALTER TABLE positions ALTER COLUMN remaining_volume SET NOT NULL;

        CREATE TABLE IF NOT EXISTS position_targets (
          id UUID PRIMARY KEY,
          position_id UUID NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
          sequence INTEGER NOT NULL,
          price NUMERIC(18,8) NOT NULL,
          requested_percentage NUMERIC(8,6) NOT NULL,
          allocated_volume NUMERIC(12,3) NOT NULL,
          status targetstatus NOT NULL DEFAULT 'WAITING',
          merged_into_sequence INTEGER,
          executed_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL,
          CONSTRAINT uq_position_target_sequence UNIQUE(position_id, sequence)
        );
        CREATE INDEX IF NOT EXISTS ix_position_targets_position_id ON position_targets(position_id);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS position_targets")
    # Columns are intentionally retained: dropping lifecycle state is destructive.
