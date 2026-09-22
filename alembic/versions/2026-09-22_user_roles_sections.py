"""Add administrator role and a single section per user."""

from alembic import op
import sqlalchemy as sa

revision = "f29b6c80d143"
down_revision = "a7c9d21e8f40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
    # Existing users wait for an explicit assignment; never grant an arbitrary section.
    op.add_column("users", sa.Column("section", sa.String(15), nullable=True))
    op.add_column("users", sa.Column("session_version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    op.drop_column("users", "session_version")
    op.drop_column("users", "section")
    op.drop_column("users", "is_admin")
