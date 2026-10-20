"""Add single-use password recovery tokens."""

from alembic import op
import sqlalchemy as sa

revision = "d74e9c201a53"
down_revision = "c13e49a76b02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_reset_hash", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("password_reset_expires", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("password_reset_sent_at", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "password_reset_sent_at")
    op.drop_column("users", "password_reset_expires")
    op.drop_column("users", "password_reset_hash")
