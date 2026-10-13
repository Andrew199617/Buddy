import { writable } from 'svelte/store';

import type { ChatRunTargetDetails } from '$lib/apis/subscriptions';

// "New chat here": where a started chat runs, for the next new chat to start from.
// Its fields stay editable there, and it is used once.
export const pendingRunTarget = writable<ChatRunTargetDetails | null>(null);
