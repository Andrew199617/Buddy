# Mobile Buddy component previews

These are design previews for choosing the next Context info and notification treatments. They do not change the running Buddy application. The preview sources are HTML fragments for the Codex visualization runtime, with local interactions and no application API calls.

## Current UI captures

The before images use the production frontend on port 8082 with an isolated synthetic session. No real chats, notes, provider calls, or user settings were changed. The mobile fixture uses the iPhone 12 Pro Max CSS viewport of 428 by 926, a 47 px top safe area, and a 34 px bottom safe area. The keyboard capture reduces the visual viewport to 566 px. These are browser captures of the current app, not photographs from a physical iPhone.

- `before-notifications-light.png`: native success notification in light mode.
- `before-notifications-dark.png`: native success notification in dark mode.
- `before-notifications-keyboard.png`: native notification with the keyboard viewport open.
- `before-context-keyboard.png`: current Context info row with the keyboard viewport open.

### Measured problems

The global toaster is configured at the top right, but its mobile stylesheet places notifications 16 px from the left and 20 px from the top. This overrides the safe-area placement. In the 428 px capture, a success or error notification covers 52.5 px of Buddy's header; a two-line info notification covers 73.5 px. The native dismiss target is only 20 by 20 px.

Context info has no background image. Its transparent rail reserves approximately 51 px, including the form gap, above the composer. With the keyboard open, that allocation clips the welcome suggestions and reads as a rectangular backdrop.

## Context alternatives

| Design            | Treatment                                                                                       |
| ----------------- | ----------------------------------------------------------------------------------------------- |
| Floating label    | Transparent text and info icon above the composer, with a floating details card.                |
| Usage ring        | A compact percentage ring above the composer; when usage is unavailable it shows the info icon. |
| Composer shortcut | An info button inside the composer opens the same details card and leaves more room above it.   |

The previews include usage available/unavailable and keyboard visible/hidden controls. The usage sample is illustrative. The details card can be dismissed with its close button or Escape, returning focus to the trigger.

## Notification alternatives

| Design        | Treatment                                                                                    |
| ------------- | -------------------------------------------------------------------------------------------- |
| Header ribbon | A readable notification below Buddy's pinned header, with a Buddy avatar and dismiss button. |
| Composer card | A notification above the composer, near the action that generated it, with a details action. |
| Quiet capsule | A compact centered status that expands into details when tapped.                             |

The previews include saved, connection interrupted, and working states, plus a keyboard toggle. Notification dismissal is local to the preview. Real errors should remain readable and actionable; routine confirmations can use a quieter treatment. Multiple events should be grouped rather than cover the chat with an unlimited stack.

## Implementation considerations after selection

Use Buddy's existing safe-area and visual-viewport measurements for final placement. Keep the avatar and header controls unobstructed, keep the composer usable, and retain the current first-tap editor focus behavior. Context triggers should stay inside the native composer form so the existing disclosure observer continues to work. Anchor floating controls to the actual composer rather than reserving another full-width row.

Use at least 44 px dismiss targets on mobile. Pair status icons with readable text; success, working, and error states should remain distinguishable without relying on color. Preserve the notification library's accessibility behavior and honor reduced motion when adding transitions.

## Preview validation

Context previews were checked across 36 configurations: three designs at 320, 428, and 736 px conversation widths, in light and dark themes, with the keyboard viewport open and closed. The affected usage-ring configurations were rechecked after centering the unavailable-usage icon and correcting voice-icon contrast. The 428 px keyboard view leaves 38.67 px between welcome suggestions and the floating context trigger.

Checks cover no horizontal overflow or control overlap, accessible trigger and dismiss targets, details fitting within the phone, dismissal and Escape focus restoration, usage availability, and design-control updates. These measurements describe the previews; the selected treatment still needs to be implemented and verified in the production application.
Notification previews passed 108 layout states across the three designs, three notification kinds, light/dark themes, keyboard open/closed, and 320/428/736 px conversation widths. All dismiss targets measure 44 by 44 px. Details and dismiss actions work, both Composer card details triggers stay synchronized, and the host visibility control restores a dismissed notification. No horizontal overflow, header overlap, composer overlap, or runtime errors were observed.
