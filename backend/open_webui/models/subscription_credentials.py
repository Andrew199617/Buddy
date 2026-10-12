"""Claude plan tokens and API keys that users let Buddy keep for their machines.

Secrets are encrypted at rest (see ``utils/subscriptions/vault.py``) and never
leave the server except to the owner's own machines. Everything shown in the
browser comes from ``StoredCredential``, which has no secret in it.
"""

import logging
import time
import uuid
from typing import Optional

from open_webui.env import BUDDY_SUBSCRIPTION_OLD_SECRET_KEYS, BUDDY_SUBSCRIPTION_SECRET_KEY
from open_webui.internal.db import Base, get_async_db_context
from open_webui.utils.subscriptions.vault import Vault, VaultError, fingerprint
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Index, Text, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# A one-year Claude token from ``claude setup-token``.
KIND_PLAN_TOKEN = 'plan_token'
# A Claude Console or OpenAI Platform API key, billed per use.
KIND_API_KEY = 'api_key'
CREDENTIAL_KINDS = (KIND_PLAN_TOKEN, KIND_API_KEY)


class SubscriptionCredential(Base):
    __tablename__ = 'subscription_credential'

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    provider = Column(Text, nullable=False)
    kind = Column(Text, nullable=False)
    secret = Column(Text, nullable=False)
    fingerprint = Column(Text, nullable=True)
    expires_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('idx_subscription_credential_owner', 'user_id', 'provider', 'kind', unique=True),
    )


class StoredCredential(BaseModel):
    """What may be shown about a stored secret: never the secret itself."""

    user_id: str
    provider: str
    kind: str
    fingerprint: Optional[str] = None
    expires_at: Optional[int] = None
    created_at: int
    updated_at: int

    model_config = ConfigDict(from_attributes=True)


class SubscriptionCredentialTable:
    def __init__(self, vault: Vault | None = None):
        self._vault = vault

    @property
    def vault(self) -> Vault:
        if self._vault is None:
            self._vault = Vault(BUDDY_SUBSCRIPTION_SECRET_KEY, BUDDY_SUBSCRIPTION_OLD_SECRET_KEYS)
        return self._vault

    async def save(
        self,
        user_id: str,
        provider: str,
        kind: str,
        secret: str,
        expires_at: int | None = None,
        db: Optional[AsyncSession] = None,
    ) -> StoredCredential:
        """Store or replace the user's secret of this kind for this provider."""
        if kind not in CREDENTIAL_KINDS:
            raise ValueError(f'Unknown credential kind: {kind}')
        now = int(time.time())
        encrypted = self.vault.encrypt(secret)
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(SubscriptionCredential).filter_by(user_id=user_id, provider=provider, kind=kind)
            )
            row = result.scalars().first()
            if row is None:
                row = SubscriptionCredential(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    provider=provider,
                    kind=kind,
                    created_at=now,
                )
                db.add(row)
            row.secret = encrypted
            row.fingerprint = fingerprint(secret)
            row.expires_at = expires_at
            row.updated_at = now
            await db.commit()
            return StoredCredential.model_validate(row)

    async def secret(self, user_id: str, provider: str, kind: str, db: Optional[AsyncSession] = None) -> str | None:
        """The decrypted secret, or None when there is none or it can no longer be decrypted."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(SubscriptionCredential.secret).filter_by(user_id=user_id, provider=provider, kind=kind)
            )
            encrypted = result.scalars().first()
        if encrypted is None:
            return None
        try:
            return self.vault.decrypt(encrypted)
        except VaultError:
            log.warning('A stored %s %s for user %s could not be decrypted; it counts as missing', provider, kind, user_id)
            return None

    async def describe(self, user_id: str, db: Optional[AsyncSession] = None) -> list[StoredCredential]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(SubscriptionCredential).filter_by(user_id=user_id))
            return [StoredCredential.model_validate(row) for row in result.scalars().all()]

    async def delete(self, user_id: str, provider: str, kind: str, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                delete(SubscriptionCredential).filter_by(user_id=user_id, provider=provider, kind=kind)
            )
            await db.commit()
            return result.rowcount > 0

    async def delete_user(self, user_id: str, db: Optional[AsyncSession] = None) -> None:
        async with get_async_db_context(db) as db:
            await db.execute(delete(SubscriptionCredential).filter_by(user_id=user_id))
            await db.commit()


SubscriptionCredentials = SubscriptionCredentialTable()
