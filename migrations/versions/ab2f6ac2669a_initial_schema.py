"""initial schema

Revision ID: ab2f6ac2669a
Revises: 
Create Date: 2026-01-30 23:47:04.790728

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ab2f6ac2669a'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "music_repository",
        sa.Column("uri", sa.String, primary_key=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("artists", postgresql.JSONB, nullable=False),
        sa.Column("filepath", sa.String, nullable=False),
        sa.Column("hash", sa.LargeBinary, nullable=False),
        sa.Column("mime", sa.String, nullable=False),
        sa.Column("metadata_json", postgresql.JSONB, nullable=False),
        sa.Column("created", sa.DateTime, nullable=False),
    )

def downgrade() -> None:
    op.drop_table("music_repository")