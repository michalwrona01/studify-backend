"""Keep the previous schedule and stage the source file until its data arrives."""

from alembic import op
import sqlalchemy as sa

revision = "e85f0d312b64"
down_revision = "d74e9c201a53"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "schedules_previous",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("day_of_week", sa.String(31), nullable=False),
        sa.Column("group", sa.String(15), nullable=False),
        sa.Column("section", sa.String(15), nullable=False),
        sa.Column("mode", sa.String(15), nullable=False),
        sa.Column("hours", sa.JSON(), nullable=False),
    )
    op.add_column("schedules_files", sa.Column("content", sa.LargeBinary(), nullable=True))
    op.add_column("schedules_files", sa.Column("filename", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("schedules_files", "filename")
    op.drop_column("schedules_files", "content")
    op.drop_table("schedules_previous")
