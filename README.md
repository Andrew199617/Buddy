# Buddy

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="static/static/buddy-wordmark-dark.svg" />
  <img src="static/static/buddy-wordmark.svg" alt="Buddy" width="420" />
</picture>

**Your AI companion for ideas, answers, and getting things done.**

Buddy is a self-hosted workspace for conversations with local and cloud AI
models. Connect Ollama or an OpenAI-compatible provider, bring your documents
and tools, and work from desktop or phone.

Buddy's identity uses a smiling conversation mark, forest green, mint, and warm
ivory surfaces. The interface, browser titles, notifications, welcome screen,
and installed-app icons share the same name and assets.

## Get started on Windows

Use Python 3.11 and Node.js 22. Install the pinned runtime dependencies, then
build the frontend from this checkout:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install open-webui==0.11.4
npm ci
$env:NODE_OPTIONS = '--max-old-space-size=8192'
npm run build
```

Open **Start Buddy.bat** or run `start-buddy.cmd`, then visit
`http://localhost:8080`. The launcher serves this checkout's backend and built
frontend with the local extensions enabled. Rebuild after changing frontend
code or branding assets.

Existing installations continue using the same data directory and accounts.
The older launcher filenames remain compatibility aliases. Stop a running
older server before starting the new build.

See [Buddy setup and customizations](BUDDY.md) for reasoning-filter installation,
research tools, configuration, and validation commands.

## What you can do

- Talk to local Ollama models and OpenAI-compatible cloud providers.
- Work with multiple models, documents, knowledge, notes, and tools.
- Choose reasoning effort and inspect response timing and token usage.
- Use a responsive mobile chat layout with keyboard-aware scrolling and settings.
- Connect research tools for Perforce, Slack, Outlook, and unified communications.
- Install Buddy as a browser app with its own icon and launch experience.

## Development

The application frontend lives in `src/`, the backend in `backend/open_webui/`,
and the local runtime extensions in `local/`. The Python package directory and
API identifiers keep their established names for compatibility.

```powershell
npm run dev
npm run check
npm run build
.venv\Scripts\python.exe -m unittest discover -s local\tests -v
node --test local\tests\*.test.mjs
```

The development frontend proxies backend requests to `http://localhost:8080`.
Use `WEBUI_BACKEND_URL` to target another local backend.

[Local extension details](local/README.md) ·
[Browser regression guide](local/tests/browser/README.md) ·
[Brand asset guide](static/BRANDING.md)

## Credits and license

Buddy is derived from Open WebUI. Upstream copyright, license, and contribution
notices remain in this repository. Use and distribution are governed by
[LICENSE](LICENSE), including its branding provisions, and [LICENSE_HISTORY](LICENSE_HISTORY).
