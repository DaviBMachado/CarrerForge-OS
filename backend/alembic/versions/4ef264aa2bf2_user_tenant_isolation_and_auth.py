"""user_tenant_isolation_and_auth

Revision ID: 4ef264aa2bf2
Revises: 7ab68f08130c
Create Date: 2026-11-06 17:47:13.751574

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4ef264aa2bf2"
down_revision: str | Sequence[str] | None = "7ab68f08130c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("experiences", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=False, server_default="1"))
        batch_op.create_index(batch_op.f("ix_experiences_user_id"), ["user_id"], unique=False)
        batch_op.create_index("ix_experiences_user_id_id", ["user_id", "id"], unique=False)
        batch_op.create_foreign_key(
            "fk_experiences_user_id_users", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )

    with op.batch_alter_table("job_postings", schema=None) as batch_op:
        batch_op.create_index("ix_job_postings_user_id_id", ["user_id", "id"], unique=False)

    with op.batch_alter_table("master_profiles", schema=None) as batch_op:
        batch_op.create_index("ix_master_profiles_user_id_id", ["user_id", "id"], unique=False)

    with op.batch_alter_table("system_settings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=False, server_default="1"))
        batch_op.create_index(batch_op.f("ix_system_settings_user_id"), ["user_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_system_settings_user_id_users", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("email", existing_type=sa.VARCHAR(), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("email", existing_type=sa.VARCHAR(), nullable=False)

    with op.batch_alter_table("system_settings", schema=None) as batch_op:
        batch_op.drop_constraint("fk_system_settings_user_id_users", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_system_settings_user_id"))
        batch_op.drop_column("user_id")

    with op.batch_alter_table("master_profiles", schema=None) as batch_op:
        batch_op.drop_index("ix_master_profiles_user_id_id")

    with op.batch_alter_table("job_postings", schema=None) as batch_op:
        batch_op.drop_index("ix_job_postings_user_id_id")

    with op.batch_alter_table("experiences", schema=None) as batch_op:
        batch_op.drop_constraint("fk_experiences_user_id_users", type_="foreignkey")
        batch_op.drop_index("ix_experiences_user_id_id")
        batch_op.drop_index(batch_op.f("ix_experiences_user_id"))
        batch_op.drop_column("user_id")
