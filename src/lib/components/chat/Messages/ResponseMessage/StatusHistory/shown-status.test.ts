import { describe, expect, it } from 'vitest';

import { shownStatus } from './shown-status';

const ultraOff = {
	description: "Ultra is off for this reply: Claude on your computer can't run workers with Haiku.",
	done: false,
	keep: true
};
const running = { description: 'Running `ls`', done: false };
const ended = { description: 'Done', done: true, hidden: true };

describe('shownStatus', () => {
	it('shows the latest line while the reply runs', () => {
		expect(shownStatus([ultraOff])).toBe(ultraOff);
		expect(shownStatus([ultraOff, running])).toBe(running);
	});

	it('keeps showing a kept line once the reply ends', () => {
		expect(shownStatus([ultraOff, running, ended])).toBe(ultraOff);
	});

	it('shows the latest of several kept lines', () => {
		const second = { ...ultraOff, description: 'Ultra is off for this reply: Codex is too old.' };
		expect(shownStatus([ultraOff, second, ended])).toBe(second);
	});

	it('leaves the history hidden when no line is kept, as before', () => {
		expect(shownStatus([running, ended])).toBe(ended);
		expect(shownStatus([ended])).toBe(ended);
	});

	it('shows nothing without lines', () => {
		expect(shownStatus([])).toBeNull();
		expect(shownStatus(null)).toBeNull();
		expect(shownStatus(undefined)).toBeNull();
	});
});
