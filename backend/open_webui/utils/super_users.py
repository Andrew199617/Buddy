"""Super users: the server owner, and the administrators the server owner names.

Only they may edit other people's chat folders. A folder's instructions and
knowledge reach every chat in it, and a plan with file access acts on them, so
other administrators can read other people's folders but not change them.

Only the server owner may name or remove super users. The owner always is one,
and someone who stops being an administrator stops being one too. Only the owner,
or the person themselves, may change a super user's password, email or role, so
another administrator cannot sign in as one.

This guards against everyday administration, not a hostile administrator: any
administrator can still install Functions and Tools, which run code on the server.
"""

SUPER_USERS_CONFIG_KEY = 'users.super_user_ids'
# The same key as subscriptions.service.SERVER_OWNER_CONFIG_KEY.
SERVER_OWNER_CONFIG_KEY = 'subscriptions.owner'

# Who owns this server and who its super users are. Config import and export leave them out:
# only the owner names super users, and a file from another server would name its own people.
SERVER_IDENTITY_KEYS = frozenset({SUPER_USERS_CONFIG_KEY, SERVER_OWNER_CONFIG_KEY})


def without_server_identity(config: dict) -> dict:
    return {key: value for key, value in config.items() if key not in SERVER_IDENTITY_KEYS}


class SuperUserError(Exception):
    """A refused change, with a message for the person who asked."""


async def _config():
    from open_webui.models.config import Config

    return Config


async def server_owner_id() -> str | None:
    from open_webui.utils.subscriptions.service import server_owner_id as owner_id

    return await owner_id()


async def named_super_user_ids() -> list[str]:
    """The administrators the server owner has named, without the owner."""
    stored = await (await _config()).get(SUPER_USERS_CONFIG_KEY, [])
    if not isinstance(stored, list):
        return []
    return [user_id for user_id in stored if isinstance(user_id, str) and user_id]


async def is_super_user(user) -> bool:
    if user is None or getattr(user, 'role', None) != 'admin':
        return False
    if user.id == await server_owner_id():
        return True
    return user.id in await named_super_user_ids()


async def may_change_account(actor, target) -> bool:
    """Whether actor may change target's password, email or role, or delete them."""
    if actor.id == target.id:
        return True
    owner_id = await server_owner_id()
    if actor.id == owner_id:
        return True
    return target.id != owner_id and not await is_super_user(target)


async def forget_super_user(user_id: str) -> None:
    """Drops someone from the super users when they stop being an administrator.

    Otherwise being made an administrator again later would quietly bring it back.
    """
    named = await named_super_user_ids()
    if user_id not in named:
        return
    remaining = [named_id for named_id in named if named_id != user_id]
    await (await _config()).upsert({SUPER_USERS_CONFIG_KEY: remaining})


async def describe_super_users() -> dict:
    return {'owner_id': await server_owner_id(), 'user_ids': await named_super_user_ids()}


async def set_super_user(actor, target, enabled: bool) -> dict:
    """The server owner names or removes a super user. target is the user record, or None."""
    owner_id = await server_owner_id()
    if actor is None or owner_id is None or actor.id != owner_id:
        raise SuperUserError('Only the server owner can choose super users.')
    if target is None:
        raise SuperUserError('That user no longer exists.')
    if target.id == owner_id:
        raise SuperUserError('The server owner is always a super user.')
    named = await named_super_user_ids()
    if enabled:
        if target.role != 'admin':
            raise SuperUserError('Make this person an administrator first.')
        if target.id not in named:
            named.append(target.id)
    else:
        named = [user_id for user_id in named if user_id != target.id]
    await (await _config()).upsert({SUPER_USERS_CONFIG_KEY: named})
    return await describe_super_users()
