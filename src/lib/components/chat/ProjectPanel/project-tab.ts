import { writable } from 'svelte/store';

// Whether the open chat has a Project tab, so the side panel's button shows
// even where an administrator turned off Chat Controls.
export const projectTabAvailable = writable(false);
