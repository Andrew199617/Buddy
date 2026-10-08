# Buddy branding assets

Buddy uses a smiling conversation bubble: a mint fill, forest outline, two eyes,
and a simple smile. The tail makes the symbol recognizable as a conversation
at small sizes. The wordmark uses a bold, rounded system sans serif treatment.

| Color | Value | Role |
| --- | --- | --- |
| Forest | `#27634B` | Brand text, primary actions, icon background |
| Mint | `#DDF3E4` | Conversation mark, dark-theme brand text |
| Ivory | `#FAF8F5` | Light application canvas |
| Ink | `#202321` | Body text and dark application canvas |

`buddy-mark.svg` is the transparent conversation symbol. `buddy-icon.svg` is the
square app icon. `buddy-wordmark.svg` and `buddy-wordmark-dark.svg` are the light
and dark lockups. Keep the mark's proportions intact, leave breathing room
around it, and use the dark lockup on dark surfaces.

The PNG, ICO, touch, splash and install icons are raster exports of these SVGs.
Both `static/static/` and `backend/open_webui/static/` carry the same assets so
frontend builds, API notifications, and standalone backend installs agree.
Existing favicon and splash filenames stay stable for integrations.

Buddy's interface uses warm neutral surfaces and forest/mint primary actions.
Content colors, model images, user avatars, and accessibility settings keep
their existing meanings.

This application is derived from Open WebUI. Its upstream copyright, license,
and attribution remain in `LICENSE` and the About screen. Use and distribution
of this fork remain subject to those terms, including the branding provisions.
