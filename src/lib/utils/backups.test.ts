import { describe, expect, it } from 'vitest';

import type { BackupOverview, BackupRun } from '$lib/apis/backups';
import { formatBackupSize, getBackupHealth } from './backups';

const run = (status: BackupRun['status']): BackupRun => ({
	id: `run-${status}`,
	status,
	trigger: 'scheduled',
	requested_by: null,
	created_at: '2026-10-10T10:00:00.000Z',
	started_at: '2026-10-10T10:00:00.000Z',
	finished_at: null,
	duration_seconds: null,
	changelist: null,
	db_bytes: null,
	data_bytes: null,
	env_bytes: null,
	db_revision: null,
	error: null
});

const overview = (changes: Partial<BackupOverview> = {}): BackupOverview => ({
	configured: true,
	available: true,
	reason: null,
	schedule: null,
	scheduler: { last_heartbeat_at: null, next_run_at: null, online: true },
	last_success: run('succeeded'),
	latest_run: run('succeeded'),
	active_run: null,
	failing: false,
	stale: false,
	runs: [],
	...changes
});

describe('getBackupHealth', () => {
	it('is quiet when the latest run succeeded recently and the service is online', () => {
		expect(getBackupHealth(overview())).toEqual({ alert: null, serviceOffline: false });
	});

	it('reports a failed latest run before staleness', () => {
		const health = getBackupHealth(overview({ failing: true, stale: true }));
		expect(health.alert).toBe('failed');
	});

	it('tells stale backups apart from never having succeeded', () => {
		expect(getBackupHealth(overview({ stale: true })).alert).toBe('stale');
		expect(getBackupHealth(overview({ stale: true, last_success: null })).alert).toBe('never');
	});

	it('waits for a run in progress before calling backups stale', () => {
		const firstRun = overview({ stale: true, last_success: null, active_run: run('running') });
		expect(getBackupHealth(firstRun).alert).toBeNull();
	});

	it('still alerts on staleness when the service running it is offline', () => {
		const stuck = overview({
			stale: true,
			active_run: run('running'),
			scheduler: { last_heartbeat_at: null, next_run_at: null, online: false }
		});
		expect(getBackupHealth(stuck)).toEqual({ alert: 'stale', serviceOffline: true });
	});

	it('has no alerts while backups are not set up', () => {
		const notStarted = overview({ available: false, stale: true, failing: true, scheduler: null });
		expect(getBackupHealth(notStarted)).toEqual({ alert: null, serviceOffline: false });
	});
});

describe('formatBackupSize', () => {
	it('formats bytes with binary units', () => {
		expect(formatBackupSize(256)).toBe('256 B');
		expect(formatBackupSize(5808411)).toBe('5.5 MB');
		expect(formatBackupSize(37888000)).toBe('36.1 MB');
		expect(formatBackupSize(null)).toBe('–');
	});
});
