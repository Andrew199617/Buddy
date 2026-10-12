"""add subscription credential table

Revision ID: b6d1f0c3a9e4
Revises: d4c1a8e37b62
Create Date: 2026-10-11
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b6d1f0c3a9e4'
down_revision: Union[str, None] = 'd4c1a8e37b62'
branch_labels = None
depends_on = None

TABLE = 'subscription_credential'
OWNER_INDEX = 'idx_subscription_credential_owner'


def _index_exists(inspector, index_name, table_name):
    return any(index['name'] == index_name for index in inspector.get_indexes(table_name))


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE not in inspector.get_table_names():
        op.create_table(
            TABLE,
            sa.Column('id', sa.Text(), primary_key=True),
            sa.Column('user_id', sa.Text(), nullable=False),
            sa.Column('provider', sa.Text(), nullable=False),
            sa.Column('kind', sa.Text(), nullable=False),
            sa.Column('secret', sa.Text(), nullable=False),
            sa.Column('fingerprint', sa.Text(), nullable=True),
            sa.Column('expires_at', sa.BigInteger(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
        )
    inspector.clear_cache()
    if not _index_exists(inspector, OWNER_INDEX, TABLE):
        op.create_index(OWNER_INDEX, TABLE, ['user_id', 'provider', 'kind'], unique=True)


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        if _index_exists(inspector, OWNER_INDEX, TABLE):
            op.drop_index(OWNER_INDEX, table_name=TABLE)
        op.drop_table(TABLE)
