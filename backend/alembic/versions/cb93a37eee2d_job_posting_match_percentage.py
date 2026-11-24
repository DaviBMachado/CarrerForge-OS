"""job_posting_match_percentage

Revision ID: cb93a37eee2d
Revises: 4ef264aa2bf2
Create Date: 2026-11-10 18:20:56.773399

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cb93a37eee2d"
down_revision: str | Sequence[str] | None = "4ef264aa2bf2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("job_postings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("match_percentage", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("job_postings", schema=None) as batch_op:
        batch_op.drop_column("match_percentage")
