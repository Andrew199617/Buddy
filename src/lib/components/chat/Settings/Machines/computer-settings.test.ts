import { describe, expect, it } from 'vitest';

import type { ComputerSettings } from '$lib/apis/subscriptions';
import { draftFromSettings } from './computer-settings';

function settings(overrides: Partial<ComputerSettings> = {}): ComputerSettings {
	return {
		machine_id: 'pc-1',
		revision: 'rev-1',
		saved: false,
		start_folder: '',
		cli_paths: { claude: '', codex: '' },
		from_plans: {},
		...overrides
	};
}

describe('the Advanced form for a computer', () => {
	it('shows the computer’s own settings once saved', () => {
		const draft = draftFromSettings(
			settings({
				saved: true,
				start_folder: '/home/sam/work',
				cli_paths: { claude: '/opt/claude', codex: '' },
				from_plans: { claude: { start_folder: '/old', cli_path: '/old/claude' } }
			})
		);
		expect(draft).toEqual({
			startFolder: '/home/sam/work',
			cliPaths: { claude: '/opt/claude', codex: '' }
		});
	});

	it('starts from what the plans saved, so saving keeps their CLI paths', () => {
		const draft = draftFromSettings(
			settings({
				from_plans: {
					claude: { start_folder: '/home/sam/work', cli_path: '/opt/claude' },
					codex: { start_folder: '/home/sam/work', cli_path: '' }
				}
			})
		);
		expect(draft).toEqual({
			startFolder: '/home/sam/work',
			cliPaths: { claude: '/opt/claude', codex: '' }
		});
	});

	it('leaves the start folder empty when the plans saved different ones', () => {
		const draft = draftFromSettings(
			settings({
				from_plans: {
					claude: { start_folder: '/home/sam/a', cli_path: '' },
					codex: { start_folder: '/home/sam/b', cli_path: '/opt/codex' }
				}
			})
		);
		expect(draft.startFolder).toBe('');
		expect(draft.cliPaths.codex).toBe('/opt/codex');
	});
});
