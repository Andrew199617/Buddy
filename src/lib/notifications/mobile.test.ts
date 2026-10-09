import { get } from 'svelte/store';
import type { ComponentType } from 'svelte';
import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';

const { originalToast } = vi.hoisted(() => {
	const createToast = vi.fn(() => 'desktop-notification');
	const originalToast = Object.assign(createToast, {
		success: vi.fn(() => 'desktop-success'),
		info: vi.fn(() => 'desktop-info'),
		warning: vi.fn(() => 'desktop-warning'),
		error: vi.fn(() => 'desktop-error'),
		message: vi.fn(() => 'desktop-message'),
		custom: vi.fn(() => 'desktop-custom'),
		loading: vi.fn(() => 'desktop-loading'),
		promise: vi.fn(() => 'desktop-promise'),
		dismiss: vi.fn((id?: string | number) => id)
	});
	return { originalToast: originalToast };
});

vi.mock('svelte-sonner-original', () => ({
	toast: originalToast,
	Icon: {},
	Loader: {},
	Toaster: {}
}));

import { flushMobileNotificationsToDesktop, toast } from './index';
import {
	clearMobileNotifications,
	getMobileNotificationDuration,
	getMobileNotificationPriority,
	mobileNotifications,
	removeMobileNotification
} from './mobile';

const viewportListeners = new Set<(event: MediaQueryListEvent) => void>();
const viewportQuery = {
	matches: true,
	addEventListener: (_event: string, listener: (event: MediaQueryListEvent) => void) => {
		viewportListeners.add(listener);
	},
	removeEventListener: (_event: string, listener: (event: MediaQueryListEvent) => void) => {
		viewportListeners.delete(listener);
	}
};

function resizeToDesktop(): void {
	viewportQuery.matches = false;
	for (const listener of viewportListeners) {
		listener({ matches: false } as MediaQueryListEvent);
	}
}

beforeEach(() => {
	mobileNotifications.set([]);
	vi.clearAllMocks();
	viewportQuery.matches = true;
	vi.stubGlobal('window', {
		matchMedia: vi.fn(() => viewportQuery)
	});
});

afterAll(() => {
	vi.unstubAllGlobals();
});

describe('mobile notification routing', () => {
	it('routes urgent failures, high-level successes, and quiet information', () => {
		toast.error('Could not save');
		toast.warning('Connect your calendar');
		toast.success('Note saved');
		toast.info('A response is ready');

		const notifications = get(mobileNotifications);
		expect(notifications.map(getMobileNotificationPriority)).toEqual([
			'info',
			'high-level',
			'urgent',
			'urgent'
		]);
		expect(notifications.map(getMobileNotificationDuration)).toEqual([
			Infinity,
			4000,
			Infinity,
			Infinity
		]);
		expect(originalToast.error).not.toHaveBeenCalled();
	});

	it('keeps explicitly quiet action notifications in the sidebar', () => {
		toast('An update is available', {
			mobilePriority: 'info',
			action: { label: 'View release', onClick: vi.fn() }
		});
		toast.info('Needs a response', { important: true });
		toast.error('Save failed', { mobilePriority: 'info' });

		expect(get(mobileNotifications).map(getMobileNotificationPriority)).toEqual([
			'urgent',
			'urgent',
			'info'
		]);
	});

	it('updates the same notification ID and preserves its options', () => {
		const onDismiss = vi.fn();
		const id = toast.loading('Saving', { id: 0, description: 'Weekend plans', onDismiss });
		const loadingNotification = get(mobileNotifications)[0];
		toast.success('Saved', { id: id, duration: 8000 });

		const notifications = get(mobileNotifications);
		expect(id).toBe(0);
		expect(notifications).toHaveLength(1);
		expect(notifications[0]).toMatchObject({
			id: 0,
			title: 'Saved',
			type: 'success',
			description: 'Weekend plans',
			onDismiss: onDismiss
		});
		expect(notifications[0].revision).toBeGreaterThan(loadingNotification.revision);
		expect(getMobileNotificationDuration(notifications[0])).toBe(8000);
	});

	it('leaves queued notices alive until a visible card closes them', () => {
		vi.useFakeTimers();
		try {
			toast.success('First completed');
			toast.success('Second completed');
			toast.loading('Still working', { duration: 1 });
			vi.advanceTimersByTime(20000);
			expect(get(mobileNotifications)).toHaveLength(3);
			expect(getMobileNotificationDuration(get(mobileNotifications)[0])).toBe(Infinity);
		} finally {
			vi.useRealTimers();
		}
	});

	it('calls the right lifecycle callback once, including programmatic nondismissable removal', () => {
		const onDismiss = vi.fn();
		const onAutoClose = vi.fn();
		const protectedId = toast.warning('Attention needed', {
			dismissable: false,
			onDismiss: onDismiss,
			onAutoClose: onAutoClose
		});
		toast.dismiss(protectedId);
		toast.dismiss(protectedId);
		expect(onDismiss).toHaveBeenCalledTimes(1);
		expect(onAutoClose).not.toHaveBeenCalled();

		const timedId = toast.success('Done', { onDismiss: onDismiss, onAutoClose: onAutoClose });
		removeMobileNotification(timedId, 'auto');
		removeMobileNotification(timedId, 'auto');
		expect(onAutoClose).toHaveBeenCalledTimes(1);
		expect(onDismiss).toHaveBeenCalledTimes(1);
		expect(get(mobileNotifications)).toEqual([]);
	});

	it('dismisses all mobile and original notifications', () => {
		const firstDismiss = vi.fn();
		const secondDismiss = vi.fn();
		toast.info('First', { onDismiss: firstDismiss });
		toast.error('Second', { onDismiss: secondDismiss });
		toast.dismiss();
		clearMobileNotifications();

		expect(get(mobileNotifications)).toEqual([]);
		expect(firstDismiss).toHaveBeenCalledTimes(1);
		expect(secondDismiss).toHaveBeenCalledTimes(1);
		expect(originalToast.dismiss).toHaveBeenCalledWith(undefined);
	});
});

describe('mobile notification promises', () => {
	it('runs a promise factory once and updates its loading notice in place', async () => {
		const factory = vi.fn(() => Promise.resolve('Weekend plans'));
		const onFinally = vi.fn();
		const id = toast.promise(factory, {
			loading: 'Saving',
			success: (name) => `${name} saved`,
			error: 'Could not save',
			description: 'In your personal Notes',
			finally: onFinally
		});
		expect(get(mobileNotifications)[0].type).toBe('loading');

		await vi.waitFor(() => {
			expect(get(mobileNotifications)).toHaveLength(1);
			expect(get(mobileNotifications)[0]).toMatchObject({
				id: id,
				type: 'success',
				title: 'Weekend plans saved',
				description: 'In your personal Notes'
			});
			expect(onFinally).toHaveBeenCalledTimes(1);
		});
		expect(factory).toHaveBeenCalledTimes(1);
	});

	it('handles HTTP failure responses without reporting success', async () => {
		const success = vi.fn(() => 'Saved');
		const error = vi.fn((reason: unknown) => `Save failed: ${reason}`);
		const id = toast.promise(Promise.resolve({ ok: false, status: 403 }), {
			loading: 'Saving',
			success: success,
			error: error
		});

		await vi.waitFor(() => {
			expect(get(mobileNotifications)[0]).toMatchObject({
				id: id,
				type: 'error',
				title: 'Save failed: HTTP error! status: 403'
			});
		});
		expect(success).not.toHaveBeenCalled();
		expect(error).toHaveBeenCalledWith('HTTP error! status: 403');
	});

	it('turns rejected and synchronously throwing factories into urgent errors', async () => {
		const factory = vi.fn(() => {
			throw new Error('Offline');
		});
		const id = toast.promise(factory, {
			loading: 'Saving',
			error: (reason) => (reason as Error).message
		});

		await vi.waitFor(() => {
			expect(get(mobileNotifications)[0]).toMatchObject({
				id: id,
				type: 'error',
				title: 'Offline'
			});
		});
		expect(factory).toHaveBeenCalledTimes(1);
	});

	it('removes a loading notice when there is no completion message', async () => {
		const onFinally = vi.fn();
		toast.promise(Promise.resolve('Done'), { loading: 'Working', finally: onFinally });

		await vi.waitFor(() => {
			expect(get(mobileNotifications)).toEqual([]);
			expect(onFinally).toHaveBeenCalledTimes(1);
		});
	});
});

describe('desktop compatibility', () => {
	it('keeps a pending promise alive when its loading notice moves to desktop', async () => {
		let resolveOperation: (value: string) => void = () => {};
		const operation = new Promise<string>((resolve) => {
			resolveOperation = resolve;
		});
		const factory = vi.fn(() => operation);
		const id = toast.promise(factory, { loading: 'Saving', success: 'Saved' });
		resizeToDesktop();

		expect(originalToast).toHaveBeenCalledWith(
			'Saving',
			expect.objectContaining({ id: id, type: 'loading', promise: factory })
		);
		expect(factory).toHaveBeenCalledTimes(1);
		resolveOperation('Done');
		await vi.waitFor(() => {
			expect(originalToast.success).toHaveBeenCalledWith(
				'Saved',
				expect.objectContaining({ id: id })
			);
		});
		expect(get(mobileNotifications)).toEqual([]);
		expect(factory).toHaveBeenCalledTimes(1);
	});

	it('delegates desktop calls and promise factories to the original API', () => {
		viewportQuery.matches = false;
		const options = { id: 'existing', duration: 9000 };
		const factory = vi.fn(() => Promise.resolve('Done'));
		expect(toast.success('Saved', options)).toBe('desktop-success');
		expect(toast.promise(factory, { loading: 'Working' })).toBe('desktop-promise');
		expect(originalToast.success).toHaveBeenCalledWith('Saved', options);
		expect(originalToast.promise).toHaveBeenCalledWith(factory, { loading: 'Working' });
		expect(factory).not.toHaveBeenCalled();
		expect(get(mobileNotifications)).toEqual([]);
	});

	it('moves queued mobile notices to desktop with IDs, order, components and actions intact', () => {
		const onDismiss = vi.fn();
		const action = { label: 'Open note', onClick: vi.fn() };
		const CustomNotification = (() => {}) as unknown as ComponentType;
		toast.info('A response is ready', { id: 'quiet', onDismiss: onDismiss, action: action });
		toast.custom(CustomNotification, {
			id: 'custom',
			componentProps: { title: 'Weekend plans' },
			duration: 15000
		});
		resizeToDesktop();

		expect(get(mobileNotifications)).toEqual([]);
		expect(onDismiss).not.toHaveBeenCalled();
		expect(originalToast).toHaveBeenCalledWith(
			'A response is ready',
			expect.objectContaining({ id: 'quiet', type: 'info', action: action, onDismiss: onDismiss })
		);
		expect(originalToast.custom).toHaveBeenCalledWith(
			CustomNotification,
			expect.objectContaining({
				id: 'custom',
				componentProps: { title: 'Weekend plans' },
				duration: 15000
			})
		);
		expect(originalToast.mock.invocationCallOrder[0]).toBeLessThan(
			originalToast.custom.mock.invocationCallOrder[0]
		);
		flushMobileNotificationsToDesktop();
		expect(originalToast).toHaveBeenCalledTimes(1);
		expect(originalToast.custom).toHaveBeenCalledTimes(1);
	});
});
