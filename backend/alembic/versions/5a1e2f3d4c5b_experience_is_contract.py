"""experience_is_contract

Revision ID: 5a1e2f3d4c5b
Revises: 3fbbdf32e317
Create Date: 2026-11-11 13:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5a1e2f3d4c5b"
down_revision: str | Sequence[str] | None = "3fbbdf32e317"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("experiences", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_contract", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("experiences", schema=None) as batch_op:
        batch_op.drop_column("is_contract")
