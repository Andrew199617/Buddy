import { writable } from 'svelte/store';

// Keep OAuth UI separate from navigation so the active chat stays mounted.
export const showOAuthConnect = writable(false);
