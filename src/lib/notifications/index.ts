/// <reference types="vite/client" />
import { get } from 'svelte/store';
import type { ComponentType } from 'svelte';
import { toast as originalToast } from 'svelte-sonner-original';
import type { ExternalToast, ToastT } from 'svelte-sonner-original';
import {
	clearMobileNotifications,
	createMobileNotification,
	mobileNotifications,
	removeMobileNotification,
	type MobileNotificationId,
	type MobileNotificationInput,
	type MobileNotificationPriority
} from './mobile';

export { Icon, Loader, Toaster } from 'svelte-sonner-original';
export type { ExternalToast, ToastT, ToasterProps, ToastOptions } from 'svelte-sonner-original';
export * from './mobile';

export type MobileToastOptions<T extends ComponentType = ComponentType> = ExternalToast<T> & {
	mobilePriority?: MobileNotificationPriority;
};
type ToastMessage = string | ComponentType;
type ToastPromise<T> = Promise<T> | (() => Promise<T>);
type ToastPromiseOptions<T> = MobileToastOptions & {
	loading?: ToastMessage;
	success?: ToastMessage | ((data: T) => ToastMessage);
	error?: ToastMessage | ((error: unknown) => ToastMessage);
	finally?: () => void | Promise<void>;
};

type DesktopMigration = {
	displayId: MobileNotificationId;
	notification: MobileNotificationInput;
	retired: boolean;
	closed: boolean;
};

const desktopMigrations = new Map<MobileNotificationId, DesktopMigration>();
let desktopDisplayCounter = 0;

function completeDesktopMigration(
	logicalId: MobileNotificationId,
	migration: DesktopMigration,
	reason: 'dismiss' | 'auto',
	notification: ToastT
): void {
	if (migration.retired || migration.closed) {
		return;
	}
	migration.closed = true;
	if (desktopMigrations.get(logicalId) === migration) {
		desktopMigrations.delete(logicalId);
	}
	const logicalNotification = { ...notification, id: logicalId };
	if (reason === 'auto') {
		migration.notification.onAutoClose?.(logicalNotification);
	} else {
		migration.notification.onDismiss?.(logicalNotification);
	}
}

function mappedDesktopOptions<T extends ComponentType>(
	data: MobileToastOptions<T>,
	logicalId: MobileNotificationId,
	migration: DesktopMigration
): MobileToastOptions<T> {
	migration.notification = { ...migration.notification, ...data, id: logicalId };
	function onMappedDismiss(notification: ToastT): void {
		completeDesktopMigration(logicalId, migration, 'dismiss', notification);
	}
	function onMappedAutoClose(notification: ToastT): void {
		completeDesktopMigration(logicalId, migration, 'auto', notification);
	}
	return {
		...data,
		id: migration.displayId,
		onDismiss: onMappedDismiss,
		onAutoClose: onMappedAutoClose
	};
}

function desktopToastOptions<T extends ComponentType>(
	data?: MobileToastOptions<T>
): MobileToastOptions<T> | undefined {
	if (data?.id === undefined) {
		return data;
	}
	const migration = desktopMigrations.get(data.id);
	if (!migration) {
		return data;
	}
	return mappedDesktopOptions(data, data.id, migration);
}

function logicalToastId(
	displayId: MobileNotificationId | undefined,
	data?: { id?: MobileNotificationId }
): MobileNotificationId | undefined {
	if (data?.id !== undefined && data.id !== '') {
		return data.id;
	}
	return displayId;
}

function retireDesktopMigration(
	logicalId: MobileNotificationId,
	migration: DesktopMigration
): void {
	migration.retired = true;
	if (desktopMigrations.get(logicalId) === migration) {
		desktopMigrations.delete(logicalId);
	}
	originalToast.dismiss(migration.displayId);
}

function mobileToastOptions(data?: MobileToastOptions): MobileToastOptions | undefined {
	if (data?.id === undefined) {
		return data;
	}
	const migration = desktopMigrations.get(data.id);
	if (!migration) {
		originalToast.dismiss(data.id);
		return data;
	}
	const options = { ...migration.notification, ...data, id: data.id };
	retireDesktopMigration(data.id, migration);
	return options;
}
let viewportQuery: MediaQueryList | undefined;

function handleViewportChange(event: MediaQueryListEvent): void {
	if (!event.matches) {
		flushMobileNotificationsToDesktop();
	}
}

function isMobileViewport(): boolean {
	if (typeof window === 'undefined' || !window.matchMedia) {
		return false;
	}
	if (!viewportQuery) {
		viewportQuery = window.matchMedia('(max-width: 767px)');
		if (viewportQuery.addEventListener) {
			viewportQuery.addEventListener('change', handleViewportChange);
		} else {
			viewportQuery.addListener(handleViewportChange);
		}
	}
	return viewportQuery.matches;
}

function createMobileToast(
	message: ToastMessage,
	type: ToastT['type'],
	data?: MobileToastOptions,
	operation?: ToastPromise<unknown>
): MobileNotificationId {
	const options = mobileToastOptions(data);
	return createMobileNotification({
		...options,
		title: message,
		type: type,
		promise: operation
	});
}

function createToast(message: ToastMessage, data?: MobileToastOptions): MobileNotificationId {
	if (!isMobileViewport()) {
		const displayId = originalToast(message, desktopToastOptions(data));
		return logicalToastId(displayId, data) as MobileNotificationId;
	}
	return createMobileToast(message, 'default', data);
}

function successToast(message: ToastMessage, data?: MobileToastOptions): MobileNotificationId {
	if (!isMobileViewport()) {
		const displayId = originalToast.success(message, desktopToastOptions(data));
		return logicalToastId(displayId, data) as MobileNotificationId;
	}
	return createMobileToast(message, 'success', data);
}

function infoToast(message: ToastMessage, data?: MobileToastOptions): MobileNotificationId {
	if (!isMobileViewport()) {
		const displayId = originalToast.info(message, desktopToastOptions(data));
		return logicalToastId(displayId, data) as MobileNotificationId;
	}
	return createMobileToast(message, 'info', data);
}

function warningToast(message: ToastMessage, data?: MobileToastOptions): MobileNotificationId {
	if (!isMobileViewport()) {
		const displayId = originalToast.warning(message, desktopToastOptions(data));
		return logicalToastId(displayId, data) as MobileNotificationId;
	}
	return createMobileToast(message, 'warning', data);
}

function errorToast(message: ToastMessage, data?: MobileToastOptions): MobileNotificationId {
	if (!isMobileViewport()) {
		const displayId = originalToast.error(message, desktopToastOptions(data));
		return logicalToastId(displayId, data) as MobileNotificationId;
	}
	return createMobileToast(message, 'error', data);
}

function messageToast(message: ToastMessage, data?: MobileToastOptions): MobileNotificationId {
	if (!isMobileViewport()) {
		const displayId = originalToast.message(message, desktopToastOptions(data));
		return logicalToastId(displayId, data) as MobileNotificationId;
	}
	return createMobileToast(message, 'default', data);
}

function loadingToast(message: ToastMessage, data?: MobileToastOptions): MobileNotificationId {
	if (!isMobileViewport()) {
		const displayId = originalToast.loading(message, desktopToastOptions(data));
		return logicalToastId(displayId, data) as MobileNotificationId;
	}
	return createMobileToast(message, 'loading', data);
}

function customToast<T extends ComponentType = ComponentType>(
	component: T,
	data?: MobileToastOptions<T>
): MobileNotificationId {
	if (!isMobileViewport()) {
		const displayId = originalToast.custom(component, desktopToastOptions(data));
		return logicalToastId(displayId, data) as MobileNotificationId;
	}
	const options = mobileToastOptions(data);
	return createMobileNotification({
		...options,
		component: component,
		type: 'default'
	});
}

function dismissToast(id?: MobileNotificationId): MobileNotificationId | undefined {
	if (id === undefined) {
		clearMobileNotifications();
		desktopMigrations.clear();
		return originalToast.dismiss();
	}
	removeMobileNotification(id, 'dismiss');
	const migration = desktopMigrations.get(id);
	if (migration) {
		desktopMigrations.delete(id);
		originalToast.dismiss(migration.displayId);
		return id;
	}
	return originalToast.dismiss(id);
}

function isFailedHttpResponse(value: unknown): value is { ok: false; status?: number } {
	if (!value || typeof value !== 'object' || !('ok' in value)) {
		return false;
	}
	return value.ok === false;
}

function resolvePromiseMessage<T>(
	message: ToastMessage | ((value: T) => ToastMessage),
	value: T
): ToastMessage {
	if (typeof message === 'function') {
		const messageCallback = message as (value: T) => ToastMessage;
		return messageCallback(value);
	}
	return message;
}
function promiseToast<T>(
	promise: ToastPromise<T>,
	data?: ToastPromiseOptions<T>
): MobileNotificationId | undefined {
	if (!isMobileViewport()) {
		if (!data) {
			return originalToast.promise(promise, data);
		}
		const desktopOptions = { ...data, ...desktopToastOptions(data) };
		const displayId = originalToast.promise(promise, desktopOptions);
		return logicalToastId(displayId, data);
	}
	if (!data) {
		return;
	}

	const { loading, success, error, finally: onFinally, ...toastOptions } = data;
	let id = toastOptions.id;
	if (loading !== undefined) {
		id = createMobileToast(loading, 'loading', toastOptions, promise);
	}
	const loadingId = id;
	let shouldDismissLoading = loading !== undefined;

	function showPromiseError(reason: unknown): void {
		if (error === undefined) {
			return;
		}
		const message = resolvePromiseMessage(error, reason);
		id = errorToast(message, { ...toastOptions, id: id });
		shouldDismissLoading = false;
	}

	function handlePromiseSuccess(response: T): void {
		if (isFailedHttpResponse(response)) {
			const message = `HTTP error! status: ${response.status}`;
			if (error === undefined) {
				id = errorToast(message, { ...toastOptions, id: id });
				shouldDismissLoading = false;
			} else {
				showPromiseError(message);
			}
			return;
		}
		if (success !== undefined) {
			const message = resolvePromiseMessage(success, response);
			id = successToast(message, { ...toastOptions, id: id });
			shouldDismissLoading = false;
		}
	}

	function finishPromise(): void | Promise<void> {
		if (shouldDismissLoading && loadingId !== undefined) {
			dismissToast(loadingId);
		}
		return onFinally?.();
	}

	let observedPromise: Promise<T>;
	try {
		if (typeof promise === 'function') {
			observedPromise = promise();
		} else {
			observedPromise = promise;
		}
	} catch (reason) {
		observedPromise = Promise.reject(reason);
	}
	void Promise.resolve(observedPromise)
		.then(handlePromiseSuccess)
		.catch(showPromiseError)
		.finally(finishPromise);

	return id;
}

export function flushMobileNotificationsToDesktop(): void {
	const notifications = get(mobileNotifications);
	mobileNotifications.set([]);
	// Replay oldest first so Sonner preserves the mobile queue's newest-first order.
	for (const notification of [...notifications].reverse()) {
		const {
			revision,
			mobilePriority,
			dismiss,
			delete: deleted,
			updated,
			...options
		} = notification;
		const previousMigration = desktopMigrations.get(notification.id);
		if (previousMigration) {
			retireDesktopMigration(notification.id, previousMigration);
		}
		const migration: DesktopMigration = {
			displayId: `buddy-desktop-${desktopDisplayCounter++}`,
			notification: options,
			retired: false,
			closed: false
		};
		desktopMigrations.set(notification.id, migration);
		const desktopOptions = mappedDesktopOptions(options, notification.id, migration);
		if (notification.component) {
			originalToast.custom(notification.component, desktopOptions);
		} else {
			originalToast(notification.title ?? '', desktopOptions);
		}
	}
}

function disposeViewportListener(): void {
	if (!viewportQuery) {
		return;
	}
	if (viewportQuery.removeEventListener) {
		viewportQuery.removeEventListener('change', handleViewportChange);
	} else {
		viewportQuery.removeListener(handleViewportChange);
	}
	viewportQuery = undefined;
}

if (import.meta.hot) {
	import.meta.hot.dispose(disposeViewportListener);
}

export const toast = Object.assign(createToast, {
	success: successToast,
	info: infoToast,
	warning: warningToast,
	error: errorToast,
	message: messageToast,
	custom: customToast,
	loading: loadingToast,
	promise: promiseToast,
	dismiss: dismissToast
});
