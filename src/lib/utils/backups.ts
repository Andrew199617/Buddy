import type { BackupOverview } from '$lib/apis/backups';

export type BackupAlert = 'failed' | 'stale' | 'never';

export type BackupHealth = {
	// Red alert: the newest finished run failed ('failed'), or nothing succeeded in 26 hours
	// ('stale' after an older success, 'never' without one).
	alert: BackupAlert | null;
	// Amber warning: the backup service has not checked in for 5 minutes.
	serviceOffline: boolean;
};

export const getBackupHealth = (overview: BackupOverview): BackupHealth => {
	if (!overview.available) {
		return { alert: null, serviceOffline: false };
	}

	const serviceOffline = !overview.scheduler?.online;
	let alert: BackupAlert | null = null;
	if (overview.failing) {
		alert = 'failed';
	} else if (overview.stale) {
		// A run in progress on a live service is about to answer the question; don't alarm before it does.
		const runInProgress = overview.active_run !== null && !serviceOffline;
		if (!runInProgress) {
			if (overview.last_success) {
				alert = 'stale';
			} else {
				alert = 'never';
			}
		}
	}

	return { alert, serviceOffline };
};

const SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];

export const formatBackupSize = (bytes: number | null | undefined): string => {
	if (bytes === null || bytes === undefined) {
		return '–';
	}
	let value = bytes;
	let unit = 0;
	while (value >= 1024 && unit < SIZE_UNITS.length - 1) {
		value /= 1024;
		unit += 1;
	}
	if (unit === 0) {
		return `${value} B`;
	}
	return `${value.toFixed(1)} ${SIZE_UNITS[unit]}`;
};
