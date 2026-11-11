"""experience_is_academic

Revision ID: 3fbbdf32e317
Revises: cb93a37eee2d
Create Date: 2026-11-10 18:28:07.182335

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3fbbdf32e317"
down_revision: str | Sequence[str] | None = "cb93a37eee2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("experiences", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_academic", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("experiences", schema=None) as batch_op:
        batch_op.drop_column("is_academic")
