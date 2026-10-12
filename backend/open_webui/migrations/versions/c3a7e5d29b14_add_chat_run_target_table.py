"""add chat run target table

Revision ID: c3a7e5d29b14
Revises: b6d1f0c3a9e4
Create Date: 2026-10-12
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c3a7e5d29b14'
down_revision: Union[str, None] = 'b6d1f0c3a9e4'
branch_labels = None
depends_on = None

TABLE = 'chat_run_target'
USER_INDEX = 'idx_chat_run_target_user_created'


def _index_exists(inspector, index_name, table_name):
    return any(index['name'] == index_name for index in inspector.get_indexes(table_name))


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        op.create_table(
            TABLE,
            sa.Column('chat_id', sa.Text(), sa.ForeignKey('chat.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('user_id', sa.Text(), nullable=False),
            sa.Column('kind', sa.Text(), nullable=False),
            sa.Column('machine_id', sa.Text(), nullable=True),
            sa.Column('machine_revision', sa.Text(), nullable=True),
            sa.Column('folder_mode', sa.Text(), nullable=False),
            sa.Column('path', sa.Text(), nullable=True),
            sa.Column('access', sa.Text(), nullable=False),
            sa.Column('acknowledged_instructions_folder_id', sa.Text(), nullable=True),
            sa.Column('source', sa.Text(), nullable=False),
            sa.Column('note', sa.Text(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
        )
    inspector.clear_cache()
    if not _index_exists(inspector, USER_INDEX, TABLE):
        op.create_index(USER_INDEX, TABLE, ['user_id', 'created_at'])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        if _index_exists(inspector, USER_INDEX, TABLE):
            op.drop_index(USER_INDEX, table_name=TABLE)
        op.drop_table(TABLE)
