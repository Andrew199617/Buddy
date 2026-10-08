"""Run `open-webui` with the patches in owui_local_patches.py.

Drop-in replacement for `.venv\\Scripts\\open-webui.exe`, with the same arguments:

    .venv\\Scripts\\python.exe local\\serve.py serve --host 0.0.0.0 --port 8080

Patches are applied just before uvicorn starts, after `open-webui serve` has
loaded WEBUI_SECRET_KEY and imported the app. Importing Open WebUI any earlier
would freeze its settings before the secret key is set.
"""

import uvicorn

import owui_local_patches

_uvicorn_run = uvicorn.run


def _run_with_patches(*args, **kwargs):
    owui_local_patches.apply()
    return _uvicorn_run(*args, **kwargs)


uvicorn.run = _run_with_patches

if __name__ == '__main__':
    from open_webui import app

    app()
