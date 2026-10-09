# Buddy branding assets

Buddy uses a smiling conversation bubble: a mint fill, forest outline, two eyes,
and a simple smile. The tail makes the symbol recognizable as a conversation
at small sizes. The wordmark uses a bold, rounded system sans serif treatment.

| Color | Value | Role |
| --- | --- | --- |
| Forest | `#27634B` | Brand text, primary actions, icon background |
| Mint | `#DDF3E4` | Conversation mark, dark-theme brand text |
| Ivory | `#FAF8F5` | Light application canvas |
| Ink | `#202321` | Body text |
| Deep forest | `#111512` | Dark conversation canvas |

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

The conversation avatar extends the bubble into a friendly sprout character.
Its code-native SVG lives in `src/lib/components/buddy/BuddyAvatar.svelte` and
uses the same forest/mint palette. It breathes and blinks at rest, gently tilts
while thinking, and animates its mouth as answer text arrives. Reasoning and
tool progress keep it in the thinking state. Reduced-motion settings disable
animation while preserving the current expression.

The conversation layout centers Buddy above rounded response cards. A compact
message bar reveals the existing model, attachment, voice, reasoning and
context controls when used. The glass dock opens Chat, Notes, Knowledge,
Automations and Workspace according to the user's existing permissions.
History, settings and account controls remain in the overlay drawer. The dock
makes room for the phone keyboard, and legacy runtime layout patches yield to
these native Buddy components.

Buddy stays at the top of a fixed app frame while conversation content scrolls
independently. Browser safe-area insets protect the avatar, controls, drawer and
dock around a notch, Dynamic Island, rounded edges and home indicator. Phones
without those insets keep the compact spacing. Long drafts scroll inside the
composer so the keyboard and top safe area still leave room for the transcript.

This application is derived from Open WebUI. Its upstream copyright, license,
and attribution remain in `LICENSE` and the About screen. Use and distribution
of this fork remain subject to those terms, including the branding provisions.
