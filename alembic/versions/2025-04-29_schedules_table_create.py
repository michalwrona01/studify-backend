"""Schedules table create

Revision ID: d53e288f1ec5
Revises:
Create Date: 2025-04-29 21:48:08.468555

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d53e288f1ec5"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "schedules",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("day_of_week", sa.String(length=31), nullable=False),
        sa.Column("group", sa.String(length=15), nullable=False),
        sa.Column("section", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=15), nullable=False),
        sa.Column("hours", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("modified_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("schedules")
    # ### end Alembic commands ###
