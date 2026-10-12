"""Where each chat runs: the computer, folder and access chosen when it started.

Kept apart from the chat itself. Clients can patch a chat, and import, clone
and fork copy its fields, so a target stored there could be forged or could
follow a chat to another person. A row here is written once, by the server,
for the chat's owner, and never changes.
"""

import time
from typing import Optional

from open_webui.internal.db import Base, get_async_db_context
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, ForeignKey, Index, Text, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

KIND_COMPUTER = 'computer'
KIND_DETACHED = 'detached'

FOLDER_PATH = 'path'
FOLDER_AUTO = 'auto'
FOLDER_NONE = 'none'

# Chosen in a signed-in session.
SOURCE_BAR = 'bar'
# Worked out by the server for a chat started without a choice.
SOURCE_DEFAULT = 'default'
# A fork or clone of the person's own chat.
SOURCE_COPY = 'copy'
# A choice that could not be used; the chat runs Detached, chat only.
SOURCE_REFUSED = 'refused'

RECENT_SCAN_LIMIT = 200


class ChatRunTarget(Base):
    __tablename__ = 'chat_run_target'

    chat_id = Column(Text, ForeignKey('chat.id', ondelete='CASCADE'), primary_key=True)
    user_id = Column(Text, nullable=False)
    kind = Column(Text, nullable=False)
    machine_id = Column(Text, nullable=True)
    machine_revision = Column(Text, nullable=True)
    folder_mode = Column(Text, nullable=False)
    path = Column(Text, nullable=True)
    access = Column(Text, nullable=False)
    acknowledged_instructions_folder_id = Column(Text, nullable=True)
    source = Column(Text, nullable=False)
    # Why it runs there: a refusal code for refused targets, or where a default came from.
    note = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (Index('idx_chat_run_target_user_created', 'user_id', 'created_at'),)


class ChatRunTargetModel(BaseModel):
    chat_id: str
    user_id: str
    kind: str
    machine_id: Optional[str] = None
    machine_revision: Optional[str] = None
    folder_mode: str
    path: Optional[str] = None
    access: str
    acknowledged_instructions_folder_id: Optional[str] = None
    source: str
    note: Optional[str] = None
    created_at: int

    model_config = ConfigDict(from_attributes=True)


class ChatRunTargetTable:
    async def get(self, chat_id: str, db: Optional[AsyncSession] = None) -> Optional[ChatRunTargetModel]:
        async with get_async_db_context(db) as session:
            row = await session.get(ChatRunTarget, chat_id)
            if row is None:
                return None
            return ChatRunTargetModel.model_validate(row)

    async def insert_if_absent(
        self, target: ChatRunTargetModel, db: Optional[AsyncSession] = None
    ) -> ChatRunTargetModel:
        """Save a chat's target unless it already has one; either way, return the one it has."""
        async with get_async_db_context(db) as session:
            existing = await session.get(ChatRunTarget, target.chat_id)
            if existing is not None:
                return ChatRunTargetModel.model_validate(existing)
            session.add(ChatRunTarget(**target.model_dump()))
            try:
                await session.commit()
            except IntegrityError:
                # Another request saved one first.
                await session.rollback()
                existing = await session.get(ChatRunTarget, target.chat_id)
                return ChatRunTargetModel.model_validate(existing)
            return target

    async def last_for_user(self, user_id: str, db: Optional[AsyncSession] = None) -> Optional[ChatRunTargetModel]:
        async with get_async_db_context(db) as session:
            result = await session.execute(
                select(ChatRunTarget)
                .filter_by(user_id=user_id)
                .order_by(ChatRunTarget.created_at.desc())
                .limit(1)
            )
            row = result.scalars().first()
            if row is None:
                return None
            return ChatRunTargetModel.model_validate(row)

    async def recent_paths(
        self, user_id: str, limit: int = 12, db: Optional[AsyncSession] = None
    ) -> list[ChatRunTargetModel]:
        """The person's most recent chats in an exact folder, one per computer and folder."""
        async with get_async_db_context(db) as session:
            result = await session.execute(
                select(ChatRunTarget)
                .filter_by(user_id=user_id, folder_mode=FOLDER_PATH)
                .order_by(ChatRunTarget.created_at.desc())
                .limit(RECENT_SCAN_LIMIT)
            )
            rows = result.scalars().all()
        recent = []
        seen = set()
        for row in rows:
            key = (row.machine_id, row.path)
            if key in seen:
                continue
            seen.add(key)
            recent.append(ChatRunTargetModel.model_validate(row))
            if len(recent) >= limit:
                break
        return recent

    async def copy_for_owner(
        self, source_chat_id: str, new_chat_id: str, user_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[ChatRunTargetModel]:
        """Give a fork or clone the same target, only when both chats are the same person's."""
        source = await self.get(source_chat_id, db=db)
        if source is None or source.user_id != user_id:
            return None
        copied = source.model_copy(update={'chat_id': new_chat_id, 'source': SOURCE_COPY, 'created_at': int(time.time())})
        return await self.insert_if_absent(copied, db=db)


ChatRunTargets = ChatRunTargetTable()
