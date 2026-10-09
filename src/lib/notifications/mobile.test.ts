import { get } from 'svelte/store';
import type { ComponentType } from 'svelte';
import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';

const { originalToast, statefulOriginal } = vi.hoisted(() => {
	const statefulOriginal = {
		enabled: false,
		notifications: new Map<string | number, Record<string, any>>(),
		nextId: 0
	};

	function createOriginalNotification(
		message: unknown,
		data: Record<string, any> | undefined,
		type: string,
		fallbackId: string
	): string | number {
		if (!statefulOriginal.enabled) {
			return data?.id ?? fallbackId;
		}
		const id = data?.id ?? statefulOriginal.nextId++;
		const existing = statefulOriginal.notifications.get(id);
		statefulOriginal.notifications.set(id, {
			...existing,
			...data,
			id: id,
			title: message,
			type: type
		});
		return id;
	}

	function markOriginalDismissed(id?: string | number): string | number | undefined {
		if (statefulOriginal.enabled) {
			for (const [displayId, notification] of statefulOriginal.notifications) {
				if (id === undefined || displayId === id) {
					statefulOriginal.notifications.set(displayId, { ...notification, dismiss: true });
				}
			}
		}
		return id;
	}

	const createToast = vi.fn((message: unknown, data?: Record<string, any>) =>
		createOriginalNotification(message, data, data?.type ?? 'default', 'desktop-notification')
	);
	const originalToast = Object.assign(createToast, {
		success: vi.fn((message: unknown, data?: Record<string, any>) =>
			createOriginalNotification(message, data, 'success', 'desktop-success')
		),
		info: vi.fn((message: unknown, data?: Record<string, any>) =>
			createOriginalNotification(message, data, 'info', 'desktop-info')
		),
		warning: vi.fn((message: unknown, data?: Record<string, any>) =>
			createOriginalNotification(message, data, 'warning', 'desktop-warning')
		),
		error: vi.fn((message: unknown, data?: Record<string, any>) =>
			createOriginalNotification(message, data, 'error', 'desktop-error')
		),
		message: vi.fn((message: unknown, data?: Record<string, any>) =>
			createOriginalNotification(message, data, 'default', 'desktop-message')
		),
		custom: vi.fn((component: unknown, data?: Record<string, any>) =>
			createOriginalNotification(
				undefined,
				{ ...data, component: component },
				'default',
				'desktop-custom'
			)
		),
		loading: vi.fn((message: unknown, data?: Record<string, any>) =>
			createOriginalNotification(message, data, 'loading', 'desktop-loading')
		),
		promise: vi.fn(() => 'desktop-promise'),
		dismiss: vi.fn(markOriginalDismissed)
	});
	return { originalToast: originalToast, statefulOriginal: statefulOriginal };
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
	toast.dismiss();
	statefulOriginal.enabled = false;
	statefulOriginal.notifications.clear();
	statefulOriginal.nextId = 0;
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
		expect(originalToast.dismiss).toHaveBeenCalledWith();
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
	it('preserves a native desktop operation through rapid mobile and desktop ID updates', () => {
		statefulOriginal.enabled = true;
		viewportQuery.matches = false;
		const logicalId = toast.loading('Starting on desktop');
		viewportQuery.matches = true;
		const onDismiss = vi.fn();
		const onAutoClose = vi.fn();
		expect(
			toast.loading('Continuing on mobile', {
				id: logicalId,
				onDismiss: onDismiss,
				onAutoClose: onAutoClose
			})
		).toBe(logicalId);
		expect(statefulOriginal.notifications.get(logicalId)?.dismiss).toBe(true);
		resizeToDesktop();

		const displayId = originalToast.mock.calls[0][1]?.id;
		expect(displayId).not.toBe(logicalId);
		expect(statefulOriginal.notifications.get(displayId)?.dismiss).not.toBe(true);
		expect(toast.success('Completed', { id: logicalId, duration: 3000 })).toBe(logicalId);
		const notification = statefulOriginal.notifications.get(displayId);
		expect(notification).toMatchObject({ title: 'Completed', type: 'success', duration: 3000 });
		expect(notification?.dismiss).not.toBe(true);

		expect(toast.dismiss(logicalId)).toBe(logicalId);
		expect(originalToast.dismiss).toHaveBeenLastCalledWith(displayId);
		notification?.onDismiss(notification);
		notification?.onDismiss(notification);
		notification?.onAutoClose(notification);
		expect(onDismiss).toHaveBeenCalledTimes(1);
		expect(onDismiss).toHaveBeenCalledWith(expect.objectContaining({ id: logicalId }));
		expect(onAutoClose).not.toHaveBeenCalled();
	});

	it('ignores retired display cleanup while preserving the replacement mapping and callbacks', () => {
		statefulOriginal.enabled = true;
		const onDismiss = vi.fn();
		const onAutoClose = vi.fn();
		const logicalId = toast.loading('First mobile notice', {
			onDismiss: onDismiss,
			onAutoClose: onAutoClose
		});
		resizeToDesktop();
		const firstDisplayId = originalToast.mock.calls[0][1]?.id;
		const firstDisplay = statefulOriginal.notifications.get(firstDisplayId);

		viewportQuery.matches = true;
		toast.loading('Updated on mobile', { id: logicalId });
		resizeToDesktop();
		const secondDisplayId = originalToast.mock.calls[1][1]?.id;
		expect(secondDisplayId).not.toBe(firstDisplayId);
		firstDisplay?.onDismiss(firstDisplay);
		firstDisplay?.onAutoClose(firstDisplay);
		expect(onDismiss).not.toHaveBeenCalled();
		expect(onAutoClose).not.toHaveBeenCalled();

		expect(toast.success('Latest completion', { id: logicalId })).toBe(logicalId);
		expect(originalToast.success).toHaveBeenLastCalledWith(
			'Latest completion',
			expect.objectContaining({ id: secondDisplayId })
		);
		const secondDisplay = statefulOriginal.notifications.get(secondDisplayId);
		secondDisplay?.onAutoClose(secondDisplay);
		secondDisplay?.onAutoClose(secondDisplay);
		secondDisplay?.onDismiss(secondDisplay);
		expect(onAutoClose).toHaveBeenCalledTimes(1);
		expect(onAutoClose).toHaveBeenCalledWith(expect.objectContaining({ id: logicalId }));
		expect(onDismiss).not.toHaveBeenCalled();
	});

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
			expect.objectContaining({
				id: expect.stringMatching(/^buddy-desktop-/),
				type: 'loading',
				promise: factory
			})
		);
		expect(factory).toHaveBeenCalledTimes(1);
		resolveOperation('Done');
		await vi.waitFor(() => {
			expect(originalToast.success).toHaveBeenCalledWith(
				'Saved',
				expect.objectContaining({ id: originalToast.mock.calls[0][1]?.id })
			);
		});
		expect(get(mobileNotifications)).toEqual([]);
		expect(factory).toHaveBeenCalledTimes(1);
	});

	it('delegates desktop calls and promise factories to the original API', () => {
		viewportQuery.matches = false;
		const options = { id: 'existing', duration: 9000 };
		const factory = vi.fn(() => Promise.resolve('Done'));
		expect(toast.success('Saved', options)).toBe('existing');
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
			expect.objectContaining({
				id: expect.stringMatching(/^buddy-desktop-/),
				type: 'info',
				action: action,
				onDismiss: expect.any(Function)
			})
		);
		expect(originalToast.custom).toHaveBeenCalledWith(
			CustomNotification,
			expect.objectContaining({
				id: expect.stringMatching(/^buddy-desktop-/),
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
