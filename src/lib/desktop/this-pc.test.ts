import { get } from 'svelte/store';
import { describe, expect, it, vi } from 'vitest';

import type { BuddyDesktop, DesktopStatus } from './bridge';
import { thisPcStore } from './this-pc';

function status(machineId?: string): DesktopStatus {
	return {
		state: machineId ? 'connected' : 'not_connected',
		machineId,
		folders: [],
		allowEdits: false,
		allowCommands: false,
		startAtLogin: false,
		platform: 'win32',
		appVersion: '0.1.0'
	};
}

function fakeDesktop(initial: DesktopStatus) {
	let listener: ((status: DesktopStatus) => void) | null = null;
	const desktop = {
		getStatus: vi.fn(async () => initial),
		onStatusChanged: vi.fn((next: (status: DesktopStatus) => void) => {
			listener = next;
			return () => {
				listener = null;
			};
		})
	} as unknown as BuddyDesktop;
	return { desktop, emit: (next: DesktopStatus) => listener?.(next) };
}

describe('this PC', () => {
	it('is nobody in an ordinary browser', () => {
		expect(get(thisPcStore(null))).toBeNull();
	});

	it('is the app’s computer once it is connected, and follows changes', async () => {
		const { desktop, emit } = fakeDesktop(status('pc-1'));
		const store = thisPcStore(desktop);
		const seen: (string | null)[] = [];
		const stop = store.subscribe((value) => seen.push(value));
		await vi.waitFor(() => expect(seen).toEqual([null, 'pc-1']));
		emit(status());
		emit(status('pc-2'));
		stop();
		expect(seen).toEqual([null, 'pc-1', null, 'pc-2']);
	});

	it('ignores a first answer that arrives after a newer change', async () => {
		const { desktop, emit } = fakeDesktop(status('pc-old'));
		const store = thisPcStore(desktop);
		const seen: (string | null)[] = [];
		const stop = store.subscribe((value) => seen.push(value));
		emit(status('pc-new'));
		await new Promise((resolve) => setTimeout(resolve, 0));
		stop();
		expect(seen).toEqual([null, 'pc-new']);
	});
});
