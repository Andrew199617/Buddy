import { writable } from 'svelte/store';

// Kept in a leaf module so unread aggregation and the store barrel share one selection.
export const chatId = writable('');
