"""add_user_summaries_table

Revision ID: eaf50dd86b5b
Revises: 
Create Date: 2026-08-30 19:12:21.242519

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = 'eaf50dd86b5b'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_summaries',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('project_id', sa.VARCHAR(length=64), server_default='__root__', nullable=False),
        sa.Column('summary_date', sa.Date(), nullable=False),
        sa.Column('kind', sa.VARCHAR(length=8), server_default='daily', nullable=False),
        sa.Column('content', sa.TEXT(), nullable=False),
        sa.Column('event_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('embedding', Vector(dim=1536), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['user_id', 'project_id'],
            ['users.id', 'users.project_id'],
            onupdate='CASCADE',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', 'project_id'),
        sa.UniqueConstraint(
            'user_id',
            'project_id',
            'summary_date',
            'kind',
            name='uq_user_summaries_user_project_date_kind',
        ),
    )
    op.create_index(
        'idx_user_summaries_user_id_project_id',
        'user_summaries',
        ['user_id', 'project_id'],
        unique=False,
    )
    op.create_index(
        'idx_user_summaries_user_id_project_id_date',
        'user_summaries',
        ['user_id', 'project_id', 'summary_date'],
        unique=False,
    )
    op.create_index(
        'idx_user_summaries_user_id_project_id_kind',
        'user_summaries',
        ['user_id', 'project_id', 'kind'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('idx_user_summaries_user_id_project_id_kind', table_name='user_summaries')
    op.drop_index('idx_user_summaries_user_id_project_id_date', table_name='user_summaries')
    op.drop_index('idx_user_summaries_user_id_project_id', table_name='user_summaries')
    op.drop_table('user_summaries')

