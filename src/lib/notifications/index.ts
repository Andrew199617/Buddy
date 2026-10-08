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
	data?: MobileToastOptions
): MobileNotificationId {
	if (data?.id !== undefined) {
		originalToast.dismiss(data.id);
	}
	return createMobileNotification({
		...data,
		title: message,
		type: type
	});
}

function createToast(message: ToastMessage, data?: MobileToastOptions): MobileNotificationId {
	if (!isMobileViewport()) {
		return originalToast(message, data);
	}
	return createMobileToast(message, 'default', data);
}

function successToast(message: ToastMessage, data?: MobileToastOptions): MobileNotificationId {
	if (!isMobileViewport()) {
		return originalToast.success(message, data);
	}
	return createMobileToast(message, 'success', data);
}

function infoToast(message: ToastMessage, data?: MobileToastOptions): MobileNotificationId {
	if (!isMobileViewport()) {
		return originalToast.info(message, data);
	}
	return createMobileToast(message, 'info', data);
}

function warningToast(message: ToastMessage, data?: MobileToastOptions): MobileNotificationId {
	if (!isMobileViewport()) {
		return originalToast.warning(message, data);
	}
	return createMobileToast(message, 'warning', data);
}

function errorToast(message: ToastMessage, data?: MobileToastOptions): MobileNotificationId {
	if (!isMobileViewport()) {
		return originalToast.error(message, data);
	}
	return createMobileToast(message, 'error', data);
}

function messageToast(message: ToastMessage, data?: MobileToastOptions): MobileNotificationId {
	if (!isMobileViewport()) {
		return originalToast.message(message, data);
	}
	return createMobileToast(message, 'default', data);
}

function loadingToast(message: ToastMessage, data?: MobileToastOptions): MobileNotificationId {
	if (!isMobileViewport()) {
		return originalToast.loading(message, data);
	}
	return createMobileToast(message, 'loading', data);
}

function customToast<T extends ComponentType = ComponentType>(
	component: T,
	data?: MobileToastOptions<T>
): MobileNotificationId {
	if (!isMobileViewport()) {
		return originalToast.custom(component, data);
	}
	if (data?.id !== undefined) {
		originalToast.dismiss(data.id);
	}
	return createMobileNotification({
		...data,
		component: component,
		type: 'default'
	});
}

function dismissToast(id?: MobileNotificationId): MobileNotificationId | undefined {
	if (id === undefined) {
		clearMobileNotifications();
	} else {
		removeMobileNotification(id, 'dismiss');
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
		return originalToast.promise(promise, data);
	}
	if (!data) {
		return;
	}

	const { loading, success, error, finally: onFinally, ...toastOptions } = data;
	let id = toastOptions.id;
	if (loading !== undefined) {
		id = loadingToast(loading, toastOptions);
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
		if (notification.component) {
			originalToast.custom(notification.component, options);
		} else {
			originalToast(notification.title ?? '', options);
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
