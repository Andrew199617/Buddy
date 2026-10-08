r"""Run Buddy's tracked backend with the local runtime patches.

The pinned .venv supplies dependencies. The application and frontend come from
this checkout, so branding and app changes do not require edits in .venv.

    .venv\Scripts\python.exe local\serve.py serve --host 0.0.0.0 --port 8080

Patches run after the CLI has loaded WEBUI_SECRET_KEY and imported the app.
"""

import os
import sys
from pathlib import Path

import uvicorn

import owui_local_patches

ROOT_DIR = Path(__file__).resolve().parents[1]
_uvicorn_run = uvicorn.run


def configure_buddy_runtime() -> None:
    frontend_dir = ROOT_DIR / 'build'
    if not (frontend_dir / 'index.html').is_file():
        raise SystemExit(
            'Buddy frontend is missing. Run npm ci and npm run build in this folder, '
            'then start Buddy again.'
        )

    os.environ['FRONTEND_BUILD_DIR'] = str(frontend_dir)
    os.environ.setdefault('DATA_DIR', str(ROOT_DIR / 'open-webui-data'))
    os.environ.setdefault('STATIC_DIR', str(Path(os.environ['DATA_DIR']) / 'static'))
    os.environ.setdefault('WEBUI_NAME', 'Buddy')
    os.environ.setdefault('WEBUI_FAVICON_URL', '/static/favicon.png')
    sys.path.insert(0, str(ROOT_DIR / 'backend'))


def _run_with_patches(*args, **kwargs):
    owui_local_patches.apply()
    return _uvicorn_run(*args, **kwargs)


uvicorn.run = _run_with_patches

if __name__ == '__main__':
    configure_buddy_runtime()
    from open_webui import app

    app()
