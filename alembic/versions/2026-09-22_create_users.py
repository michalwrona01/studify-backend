"""Create users with individual calendar subscription tokens."""

from alembic import op
import sqlalchemy as sa

revision = "a7c9d21e8f40"
down_revision = "4ab7d33f8e15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(150), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("calendar_token", sa.String(64), nullable=False),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.UniqueConstraint("calendar_token", name="uq_users_calendar_token"),
    )


def downgrade() -> None:
    op.drop_table("users")
