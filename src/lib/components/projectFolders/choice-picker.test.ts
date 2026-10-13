import { describe, expect, it } from 'vitest';

import { focusableIndex, nextEnabled, type ChoiceOption } from './choice-picker';

function option(value: string, disabled = false): ChoiceOption {
	return { value, name: value, description: '', icon: 'chat', disabled };
}

describe('moving between choices', () => {
	const options = [option('a'), option('b', true), option('c'), option('d')];

	it('skips choices that are off and wraps around', () => {
		expect(nextEnabled(options, 0, 1)).toBe(2);
		expect(nextEnabled(options, 2, -1)).toBe(0);
		expect(nextEnabled(options, 3, 1)).toBe(0);
		expect(nextEnabled(options, 0, -1)).toBe(3);
	});

	it('stays put when nothing else can be chosen', () => {
		expect(nextEnabled([option('a'), option('b', true)], 0, 1)).toBe(0);
	});

	it('lets Tab reach the chosen one, or else the first that can be chosen', () => {
		expect(focusableIndex(options, 'c')).toBe(2);
		expect(focusableIndex(options, null)).toBe(0);
		expect(focusableIndex(options, 'b')).toBe(0);
	});
});
