import { writable } from 'svelte/store';

import type { SubscriptionMachine } from '$lib/apis/subscriptions';

// The machine list after an accepted add, edit or removal. A write can finish
// after its dialog closed or another settings tab opened, so every view that
// shows machines or the providers on them listens here.
export const machineRegistryChanges = writable<SubscriptionMachine[] | null>(null);
