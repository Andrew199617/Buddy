"""Install or update the Open WebUI Functions kept in local/functions/.

Functions live in Open WebUI's database, so they survive `git pull` and
`uv pip install -U open-webui`. Re-run this only after editing a file in
local/functions/ (or after wiping open-webui-data). It is idempotent and only
touches the functions it finds in that folder.

    .venv\\Scripts\\python.exe local\\install_functions.py

It does the same thing as Admin Panel > Functions > Import, then turns each
function on and makes it global (offered for every model).
"""

import asyncio
import os
import sys
from pathlib import Path

LOCAL_DIR = Path(__file__).resolve().parent
ROOT = LOCAL_DIR.parent
FUNCTIONS_DIR = LOCAL_DIR / 'functions'


def prepare_environment() -> None:
    # Match start-open-webui.ps1 so this talks to the same database and secret.
    os.environ.setdefault('DATA_DIR', str(ROOT / 'open-webui-data'))
    key_file = ROOT / '.webui_secret_key'
    if 'WEBUI_SECRET_KEY' not in os.environ and key_file.exists():
        os.environ['WEBUI_SECRET_KEY'] = key_file.read_text()
    # The server owns schema migrations; never race it from here.
    os.environ['ENABLE_DB_MIGRATIONS'] = 'false'


async def install() -> int:
    from open_webui.models.functions import FunctionForm, FunctionMeta, Functions
    from open_webui.models.users import Users
    from open_webui.utils.plugin import load_function_module_by_id, replace_imports

    owner = await Users.get_super_admin_user()
    if owner is None:
        print('No admin user found; sign in to Open WebUI once first.')
        return 1
    owner_id = owner.id

    paths = sorted(FUNCTIONS_DIR.glob('*.py'))
    if not paths:
        print(f'No functions found in {FUNCTIONS_DIR}')
        return 1

    for path in paths:
        function_id = path.stem.lower()
        content = replace_imports(path.read_text(encoding='utf-8'))
        module, function_type, frontmatter = await load_function_module_by_id(function_id, content=content)
        form = FunctionForm(
            id=function_id,
            name=frontmatter.get('title', function_id),
            content=content,
            meta=FunctionMeta(description=frontmatter.get('description'), manifest=frontmatter),
        )

        existing = await Functions.get_function_by_id(function_id)
        if existing is None:
            await Functions.insert_new_function(owner_id, function_type, form)
            # First install only: later runs keep whatever you set in Admin Panel > Functions.
            await Functions.update_function_by_id(function_id, {'is_active': True, 'is_global': True})
            action = 'installed (active, global)'
        elif existing.content == content:
            action = 'unchanged'
        else:
            await Functions.update_function_by_id(
                function_id, {**form.model_dump(exclude={'id'}), 'type': function_type}
            )
            action = 'updated'

        if function_type == 'filter':
            await Functions.update_function_metadata_by_id(
                function_id, {'toggle': bool(getattr(module, 'toggle', False))}
            )
        print(f'{function_id}: {action}')

    return 0


if __name__ == '__main__':
    prepare_environment()
    sys.exit(asyncio.run(install()))
