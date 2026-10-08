import { get, writable } from 'svelte/store';
import type { ToastT } from 'svelte-sonner-original';

export type MobileNotificationPriority = 'urgent' | 'high-level' | 'info';
export type MobileNotificationId = ToastT['id'];
export type MobileNotification = ToastT & {
	mobilePriority?: MobileNotificationPriority;
	dismiss?: boolean;
	revision: number;
};
export type MobileNotificationInput = Omit<MobileNotification, 'id' | 'revision'> & {
	id?: MobileNotificationId;
};

export const mobileNotifications = writable<MobileNotification[]>([]);

let notificationCounter = 0;
let notificationRevision = 0;

export function getMobileNotificationPriority(
	notification: Pick<MobileNotification, 'type' | 'important' | 'action' | 'mobilePriority'>
): MobileNotificationPriority {
	if (notification.type === 'error' || notification.type === 'warning') {
		return 'urgent';
	}
	if (notification.mobilePriority) {
		return notification.mobilePriority;
	}
	if (notification.important || notification.action) {
		return 'urgent';
	}
	if (notification.type === 'info') {
		return 'info';
	}
	return 'high-level';
}

export function getMobileNotificationDuration(notification: MobileNotification): number {
	const priority = getMobileNotificationPriority(notification);
	if (notification.type === 'loading' || priority === 'info') {
		return Number.POSITIVE_INFINITY;
	}
	if (notification.duration !== undefined) {
		return notification.duration;
	}
	if (priority === 'urgent') {
		return Number.POSITIVE_INFINITY;
	}
	return 4000;
}

export function createMobileNotification(data: MobileNotificationInput): MobileNotificationId {
	let id = data.id;
	if (id === undefined || id === '') {
		id = `buddy-mobile-${notificationCounter++}`;
	}
	const notificationId = id;
	const revision = ++notificationRevision;

	mobileNotifications.update((notifications) => {
		const existingNotification = notifications.find(
			(notification) => notification.id === notificationId
		);
		const notification: MobileNotification = {
			...existingNotification,
			...data,
			id: notificationId,
			dismissable: data.dismissable ?? true,
			dismiss: false,
			delete: false,
			updated: existingNotification !== undefined,
			revision: revision
		};
		if (existingNotification) {
			return notifications.map((existing) => {
				if (existing.id === notificationId) {
					return notification;
				}
				return existing;
			});
		}
		return [notification, ...notifications];
	});

	return notificationId;
}

export function removeMobileNotification(
	id: MobileNotificationId,
	reason: 'dismiss' | 'auto' = 'dismiss'
): void {
	const notification = get(mobileNotifications).find((item) => item.id === id);
	if (!notification) {
		return;
	}
	mobileNotifications.update((notifications) => notifications.filter((item) => item.id !== id));
	if (reason === 'auto') {
		notification.onAutoClose?.(notification);
	} else {
		notification.onDismiss?.(notification);
	}
}

export function clearMobileNotifications(): void {
	const notifications = get(mobileNotifications);
	mobileNotifications.set([]);
	for (const notification of notifications) {
		notification.onDismiss?.(notification);
	}
}
