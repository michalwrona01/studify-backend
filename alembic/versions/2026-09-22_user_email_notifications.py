"""Add email addresses and opt-in notification preferences."""

from alembic import op
import sqlalchemy as sa

revision = "c13e49a76b02"
down_revision = "f29b6c80d143"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(254), nullable=True))
    op.add_column("users", sa.Column("email_notifications", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("users", "email_notifications")
    op.drop_column("users", "email")
