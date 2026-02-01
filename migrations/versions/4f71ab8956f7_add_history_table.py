"""add_history_table

Revision ID: 4f71ab8956f7
Revises: ab2f6ac2669a
Create Date: 2026-01-31 22:58:04.389054

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4f71ab8956f7'
down_revision: Union[str, Sequence[str], None] = 'ab2f6ac2669a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "music_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uri", sa.String, sa.ForeignKey("music_repository.uri"), nullable=False),
        sa.Column("duration_played", sa.Float),
        sa.Column("listened_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("music_history")
