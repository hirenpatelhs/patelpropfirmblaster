"""Initial Patel Propfirm Blaster schema."""
from alembic import op

from app.database.base import Base
from app.models import entities  # noqa: F401


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
