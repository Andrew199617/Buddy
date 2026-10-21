import { describe, expect, it } from 'vitest';

import {
	availability,
	effortLabel,
	fitUltra,
	formatElapsed,
	requestUltra,
	runningWorkers,
	savedUltra,
	savedWorkers,
	shortModelName,
	startingUltra,
	workerEffortOptions,
	workerModelOptions,
	workerPill,
	workersElapsed,
	workersHeader,
	workersModel,
	workersStillRunning,
	workersUsed,
	type UltraModel,
	type UltraState,
	type WorkerItem
} from './ultra';

// Fills in {{name}} the way i18next does when a string has no translation.
const i18n = {
	t: (key: string, options: Record<string, unknown> = {}) =>
		key.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(options[name]))
};

function planModel(
	id: string,
	name: string,
	provider: string,
	efforts: unknown,
	ultra: unknown = true
): UltraModel {
	return { id, name, owned_by: 'subscription', subscription: { provider, efforts, ultra } };
}

const opus = planModel('claude-code.opus', 'Claude Opus 4.1', 'claude', [
	'low',
	'medium',
	'high',
	'xhigh',
	'max'
]);
const sonnet = planModel('claude-code.sonnet', 'Claude Sonnet 4.5', 'claude', [
	'low',
	'medium',
	'high'
]);
const haiku = planModel('claude-code.haiku', 'Claude Haiku 4.5', 'claude', [], false);
const codex = planModel('codex.gpt-5.1-codex', 'GPT-5.1-Codex', 'codex', [
	'none',
	'low',
	'medium',
	'high',
	'xhigh'
]);
const oldCodex = planModel('codex.gpt-5', 'GPT-5', 'codex', ['low'], false);
const llama: UltraModel = { id: 'llama3', name: 'Llama 3', owned_by: 'ollama' };
const models = [opus, sonnet, haiku, codex, oldCodex, llama];

const savedChat = { id: 'chat-1', temporary: false };

function ultra(changes: Partial<UltraState> = {}): UltraState {
	return { on: true, worker_model: null, worker_effort: null, ...changes };
}

function worker(changes: Partial<WorkerItem> = {}): WorkerItem {
	return {
		id: 'task-1',
		parent_id: null,
		depth: 1,
		label: 'Read the parser',
		state: 'done',
		model: null,
		effort: null,
		activity: null,
		summary: null,
		tokens: null,
		started_at: null,
		ended_at: null,
		...changes
	};
}

describe('availability', () => {
	it('offers Ultra for one plan model that runs workers', () => {
		expect(availability(i18n, [opus.id], models, savedChat)).toEqual({
			available: true,
			reason: null
		});
		expect(availability(i18n, [codex.id], models, savedChat).available).toBe(true);
	});

	it('offers Ultra in a new chat before it has an id', () => {
		expect(availability(i18n, [opus.id], models, { id: '', temporary: null }).available).toBe(true);
		expect(availability(i18n, [opus.id], models, { id: null, temporary: false }).available).toBe(
			true
		);
	});

	it('says why a temporary chat or a channel has no Ultra', () => {
		const temporary = "Ultra isn't available in temporary chats.";
		expect(availability(i18n, [opus.id], models, { id: '', temporary: true })).toEqual({
			available: false,
			reason: temporary
		});
		expect(
			availability(i18n, [opus.id], models, { id: 'temporary:session', temporary: false }).reason
		).toBe(temporary);
		expect(
			availability(i18n, [opus.id], models, { id: 'local:session', temporary: false }).reason
		).toBe(temporary);
		expect(
			availability(i18n, [opus.id], models, { id: 'channel:general', temporary: false }).reason
		).toBe("Ultra isn't available in channels.");
	});

	it('asks for exactly one model', () => {
		const none = 'Choose a Claude or ChatGPT plan model to use Ultra.';
		expect(availability(i18n, [], models, savedChat).reason).toBe(none);
		expect(availability(i18n, [''], models, savedChat).reason).toBe(none);

		const several = 'Ultra works with one model at a time.';
		expect(availability(i18n, [opus.id, sonnet.id], models, savedChat).reason).toBe(several);
		// The same model twice still asks for two replies.
		expect(availability(i18n, [opus.id, opus.id], models, savedChat).reason).toBe(several);
	});

	it('needs a Claude or ChatGPT plan model', () => {
		const reason = 'Ultra works with Claude and ChatGPT plan models.';
		expect(availability(i18n, [llama.id], models, savedChat).reason).toBe(reason);
		expect(availability(i18n, ['gone'], models, savedChat).reason).toBe(reason);
		const otherPlan = planModel('gemini.pro', 'Gemini', 'gemini', []);
		expect(availability(i18n, [otherPlan.id], [otherPlan], savedChat).reason).toBe(reason);
		const notOwned = { ...opus, owned_by: 'openai' };
		expect(availability(i18n, [opus.id], [notOwned], savedChat).reason).toBe(reason);
	});

	it('names the plan and model that cannot run workers', () => {
		expect(availability(i18n, [haiku.id], models, savedChat)).toEqual({
			available: false,
			reason: "Claude on your computer can't run workers with Claude Haiku 4.5."
		});
		expect(availability(i18n, [oldCodex.id], models, savedChat).reason).toBe(
			"ChatGPT on your computer can't run workers with GPT-5."
		);
		const unsure = planModel('claude-code.opus', '', 'claude', [], 'true');
		expect(availability(i18n, [unsure.id], [unsure], savedChat).reason).toBe(
			"Claude on your computer can't run workers with claude-code.opus."
		);
	});
});

describe('workerModelOptions', () => {
	it('lists "Same as chat" first, then the chat provider’s models that run workers', () => {
		expect(workerModelOptions(i18n, opus, models)).toEqual([
			{ value: null, label: 'Same as chat (Claude Opus 4.1)' },
			{ value: opus.id, label: 'Claude Opus 4.1' },
			{ value: sonnet.id, label: 'Claude Sonnet 4.5' }
		]);
		expect(workerModelOptions(i18n, codex, models)).toEqual([
			{ value: null, label: 'Same as chat (GPT-5.1-Codex)' },
			{ value: codex.id, label: 'GPT-5.1-Codex' }
		]);
	});

	it('lists nothing for a chat model that is not a plan model', () => {
		expect(workerModelOptions(i18n, llama, models)).toEqual([]);
		expect(workerModelOptions(i18n, null, models)).toEqual([]);
	});
});

describe('workersModel', () => {
	it('is the chosen model while the chat’s provider offers it', () => {
		expect(workersModel(ultra({ worker_model: sonnet.id }), opus, models)).toBe(sonnet);
	});

	it('is the chat’s model otherwise', () => {
		expect(workersModel(ultra(), opus, models)).toBe(opus);
		expect(workersModel(ultra({ worker_model: codex.id }), opus, models)).toBe(opus);
		expect(workersModel(ultra({ worker_model: haiku.id }), opus, models)).toBe(opus);
		expect(workersModel(ultra({ worker_model: 'gone' }), opus, models)).toBe(opus);
		expect(workersModel(ultra({ worker_model: sonnet.id }), null, models)).toBeNull();
	});
});

describe('workerEffortOptions', () => {
	it('lists "Same as chat" first, then the model’s levels', () => {
		expect(workerEffortOptions(i18n, sonnet)).toEqual([
			{ value: null, label: 'Same as chat' },
			{ value: 'low', label: 'Low' },
			{ value: 'medium', label: 'Medium' },
			{ value: 'high', label: 'High' }
		]);
		expect(workerEffortOptions(i18n, codex).map((option) => option.label)).toEqual([
			'Same as chat',
			'Off',
			'Low',
			'Medium',
			'High',
			'Extra high'
		]);
	});

	it('offers only "Same as chat" without levels', () => {
		expect(workerEffortOptions(i18n, null)).toEqual([{ value: null, label: 'Same as chat' }]);
		expect(workerEffortOptions(i18n, haiku)).toEqual([{ value: null, label: 'Same as chat' }]);
		const odd = planModel('claude-code.odd', 'Odd', 'claude', ['high', '', 3, null]);
		expect(workerEffortOptions(i18n, odd).map((option) => option.value)).toEqual([null, 'high']);
		const notAList = planModel('claude-code.odd', 'Odd', 'claude', 'high');
		expect(workerEffortOptions(i18n, notAList)).toHaveLength(1);
	});
});

describe('effortLabel', () => {
	it('words every level the way the Thinking menu does', () => {
		expect(effortLabel(i18n, 'none')).toBe('Off');
		expect(effortLabel(i18n, 'minimal')).toBe('Minimal');
		expect(effortLabel(i18n, 'low')).toBe('Low');
		expect(effortLabel(i18n, 'medium')).toBe('Medium');
		expect(effortLabel(i18n, 'high')).toBe('High');
		expect(effortLabel(i18n, 'xhigh')).toBe('Extra high');
		expect(effortLabel(i18n, 'max')).toBe('Max');
	});

	it('shows a level it does not know as it is', () => {
		expect(effortLabel(i18n, 'ultra-deep')).toBe('ultra-deep');
	});
});

describe('fitUltra', () => {
	it('keeps choices the chat’s model can use', () => {
		const choice = ultra({ worker_model: sonnet.id, worker_effort: 'high' });
		expect(fitUltra(choice, opus, models)).toEqual(choice);
		// Choosing the chat's own model by name stays a choice of that model.
		const ownModel = ultra({ worker_model: opus.id, worker_effort: 'max' });
		expect(fitUltra(ownModel, opus, models)).toEqual(ownModel);
	});

	it('sets another provider’s model back to the chat’s and checks the level against it', () => {
		const fromCodex = ultra({ worker_model: codex.id, worker_effort: 'none' });
		expect(fitUltra(fromCodex, opus, models)).toEqual(ultra());
		const levelOpusHas = ultra({ worker_model: codex.id, worker_effort: 'high' });
		expect(fitUltra(levelOpusHas, opus, models)).toEqual(ultra({ worker_effort: 'high' }));
	});

	it('drops a level the workers’ model does not offer', () => {
		const choice = ultra({ worker_model: sonnet.id, worker_effort: 'max' });
		expect(fitUltra(choice, opus, models)).toEqual(ultra({ worker_model: sonnet.id }));
	});

	it('keeps Ultra on or off as it was', () => {
		const off = ultra({ on: false, worker_model: sonnet.id });
		expect(fitUltra(off, opus, models)).toEqual(off);
	});
});

describe('savedUltra', () => {
	it('reads a saved choice', () => {
		expect(
			savedUltra({ on: true, worker_model: sonnet.id, worker_effort: 'high', extra: 1 })
		).toEqual(ultra({ worker_model: sonnet.id, worker_effort: 'high' }));
	});

	it('turns anything else into Ultra off with the chat’s own choices', () => {
		const off = { on: false, worker_model: null, worker_effort: null };
		expect(savedUltra(undefined)).toEqual(off);
		expect(savedUltra(null)).toEqual(off);
		expect(savedUltra('on')).toEqual(off);
		expect(savedUltra([true])).toEqual(off);
		expect(savedUltra({ on: 'true', worker_model: '', worker_effort: 5 })).toEqual(off);
		expect(savedUltra({ on: 1, worker_model: '  ', worker_effort: ['high'] })).toEqual(off);
	});
});

describe('startingUltra', () => {
	it('starts a new chat with Ultra off and the account’s last worker choices', () => {
		expect(startingUltra({ on: true, worker_model: sonnet.id, worker_effort: 'low' })).toEqual(
			ultra({ on: false, worker_model: sonnet.id, worker_effort: 'low' })
		);
		expect(startingUltra(undefined)).toEqual(ultra({ on: false }));
	});
});

describe('requestUltra', () => {
	it('leaves the field out while Ultra is off', () => {
		expect(requestUltra(ultra({ on: false, worker_model: sonnet.id }))).toBeUndefined();
	});

	it('sends exactly the two keys the server accepts', () => {
		const request = requestUltra(ultra({ worker_model: sonnet.id, worker_effort: 'high' }));
		expect(request).toEqual({ worker_model: sonnet.id, worker_effort: 'high' });
		expect(Object.keys(request ?? {}).sort()).toEqual(['worker_effort', 'worker_model']);
		expect(requestUltra(ultra())).toEqual({ worker_model: null, worker_effort: null });
	});
});

describe('savedWorkers', () => {
	it('reads the workers of a snapshot the server sent', () => {
		const snapshot = {
			items: [
				{
					id: 'task-1',
					parent_id: null,
					depth: 1,
					label: 'Read the parser',
					state: 'running',
					model: 'Claude Sonnet 4.5',
					effort: 'high',
					activity: 'Reading claude_code.py',
					summary: null,
					tokens: 1200,
					started_at: 1000,
					ended_at: null
				},
				{
					id: 'task-2',
					parent_id: 'task-1',
					depth: 2,
					label: 'Check the tests',
					state: 'done',
					model: null,
					effort: null,
					activity: null,
					summary: 'All 12 pass.',
					tokens: null,
					started_at: 1010,
					ended_at: 1050
				}
			],
			running: 1
		};
		expect(savedWorkers(snapshot)).toEqual(snapshot.items);
	});

	it('leaves out workers it cannot show and fills in the rest', () => {
		const workers = savedWorkers({
			items: [
				null,
				'task-0',
				{ label: 'No id', state: 'done' },
				{ id: 'task-1', label: 'Unknown state', state: 'paused' },
				{ id: 'task-2', state: 'failed', depth: 3, tokens: -5, started_at: '1000', model: '' }
			]
		});
		expect(workers).toEqual([
			worker({ id: 'task-2', label: 'Worker', state: 'failed', depth: 1, tokens: null })
		]);
	});

	it('reads nothing from a value that is not a snapshot', () => {
		expect(savedWorkers(undefined)).toEqual([]);
		expect(savedWorkers([worker()])).toEqual([]);
		expect(savedWorkers({ items: 'task-1' })).toEqual([]);
	});
});

describe('workerPill', () => {
	it('shows the workers’ model and thinking level', () => {
		expect(workerPill(i18n, worker({ model: 'Claude Sonnet', effort: 'high' }))).toBe(
			'Sonnet · high'
		);
		expect(workerPill(i18n, worker({ model: 'GPT-5.1-Codex', effort: 'xhigh' }))).toBe(
			'GPT-5.1-Codex · extra high'
		);
	});

	it('names the chat’s model for workers that run on it', () => {
		expect(workerPill(i18n, worker({ effort: 'low' }), 'Claude Opus 4.1')).toBe('Opus 4.1 · low');
		expect(workerPill(i18n, worker(), 'Claude Opus 4.1')).toBe('Opus 4.1');
	});

	it('is empty with nothing to show', () => {
		expect(workerPill(i18n, worker())).toBe('');
		expect(workerPill(i18n, worker({ effort: 'medium' }))).toBe('medium');
	});
});

describe('shortModelName', () => {
	it('drops the provider’s word only', () => {
		expect(shortModelName('Claude Sonnet 4.5')).toBe('Sonnet 4.5');
		expect(shortModelName('GPT-5.1-Codex')).toBe('GPT-5.1-Codex');
		expect(shortModelName('Claudette')).toBe('Claudette');
	});
});

describe('workers card text', () => {
	const team = [
		worker({ id: 'task-1', state: 'running', started_at: 1000 }),
		worker({ id: 'task-2', state: 'done', started_at: 1010, ended_at: 1100 }),
		worker({ id: 'task-3', parent_id: 'task-1', depth: 2, state: 'running', started_at: 1020 })
	];

	it('counts the running workers, sub-workers included', () => {
		expect(runningWorkers(team)).toBe(2);
		expect(runningWorkers([])).toBe(0);
	});

	it('heads the open card with the workers and how many still run', () => {
		expect(workersHeader(i18n, team)).toBe('Ultra · 3 workers · 2 running');
		expect(workersHeader(i18n, [worker({ state: 'running' })])).toBe(
			'Ultra · 1 worker · 1 running'
		);
		expect(workersHeader(i18n, [worker(), worker({ id: 'task-2', state: 'stopped' })])).toBe(
			'Ultra · 2 workers'
		);
	});

	it('says how many still run after the reply ended', () => {
		expect(workersStillRunning(i18n, team)).toBe('2 still running');
		expect(workersStillRunning(i18n, [worker({ state: 'running' })])).toBe('1 still running');
	});

	it('times the workers from the first start to the last end', () => {
		const done = [
			worker({ id: 'task-1', started_at: 1000, ended_at: 1150 }),
			worker({ id: 'task-2', state: 'failed', started_at: 1010, ended_at: 1192 }),
			worker({ id: 'task-3', state: 'stopped', started_at: 1100, ended_at: 1120 })
		];
		expect(workersElapsed(done, 5000)).toBe(192);
		// A worker that still runs counts up to now.
		expect(workersElapsed(team, 1300)).toBe(300);
		expect(workersElapsed([worker()], 1300)).toBeNull();
		expect(workersElapsed([], 1300)).toBeNull();
	});

	it('words a duration', () => {
		expect(formatElapsed(0)).toBe('0s');
		expect(formatElapsed(45)).toBe('45s');
		expect(formatElapsed(59.9)).toBe('59s');
		expect(formatElapsed(192)).toBe('3m 12s');
		expect(formatElapsed(3600 + 240 + 7)).toBe('1h 4m');
		expect(formatElapsed(-3)).toBe('0s');
	});

	it('sums up the collapsed card', () => {
		const done = [
			worker({ id: 'task-1', started_at: 1000, ended_at: 1150 }),
			worker({ id: 'task-2', started_at: 1010, ended_at: 1192 })
		];
		expect(workersUsed(i18n, done, 5000)).toBe('Used 2 workers · 3m 12s');
		expect(workersUsed(i18n, [worker({ started_at: 1000, ended_at: 1045 })], 5000)).toBe(
			'Used 1 worker · 45s'
		);
		expect(workersUsed(i18n, [worker(), worker({ id: 'task-2' })], 5000)).toBe('Used 2 workers');
		expect(workersUsed(i18n, [], 5000)).toBe('Answered without workers');
	});
});
