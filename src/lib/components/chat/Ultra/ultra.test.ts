import { describe, expect, it } from 'vitest';

import type {
	ChatRunTargetDetails,
	RunTargetComputer,
	RunTargetOptions
} from '$lib/apis/subscriptions';
import type { RunTargetDraft } from '../RunTarget/run-target';
import {
	availability,
	canStopWorkers,
	effortLabel,
	firstSelectedModel,
	fitUltra,
	formatElapsed,
	loadedUltra,
	newlyEnded,
	nextReplyAccess,
	requestUltra,
	runningWorkers,
	savedReplyWorkers,
	savedUltra,
	savedWorkers,
	shortModelName,
	startingUltra,
	stopControl,
	stopFailedMessage,
	ultraButtonLabel,
	ultraForReply,
	workerDetails,
	workerEffortOptions,
	workerLimit,
	workerLimitLine,
	workerLine,
	workerModelOptions,
	workerPill,
	workerRows,
	workersAccessLine,
	workersAccessNote,
	workersCardOpens,
	workersCardPhase,
	workersCardTitle,
	workersElapsed,
	workersEndedAnnouncement,
	workersHeader,
	workersModel,
	workersSnapshot,
	workersStillRunning,
	workersUsed,
	workerStateLabel,
	withCurrentWorkers,
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

describe('firstSelectedModel', () => {
	it('finds the first selected model in the list', () => {
		expect(firstSelectedModel([sonnet.id], models)).toBe(sonnet);
		expect(firstSelectedModel(['', codex.id, opus.id], models)).toBe(codex);
	});

	it('is null with no model selected, or one the list lacks', () => {
		expect(firstSelectedModel([], models)).toBeNull();
		expect(firstSelectedModel([''], models)).toBeNull();
		expect(firstSelectedModel(['claude-code.gone'], models)).toBeNull();
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

describe('loadedUltra', () => {
	const accountDefault = { worker_model: sonnet.id, worker_effort: 'low' };

	it('reads the choice saved with the chat', () => {
		const saved = { on: true, worker_model: opus.id, worker_effort: 'max' };
		expect(loadedUltra(saved, accountDefault)).toEqual(
			ultra({ worker_model: opus.id, worker_effort: 'max' })
		);
		// A chat's own "Same as chat" choices stay, whatever the account's are now.
		const sameAsChat = { on: false, worker_model: null, worker_effort: null };
		expect(loadedUltra(sameAsChat, accountDefault)).toEqual(ultra({ on: false }));
	});

	it('starts a chat saved without a choice like a new chat', () => {
		const starting = ultra({ on: false, worker_model: sonnet.id, worker_effort: 'low' });
		expect(loadedUltra(undefined, accountDefault)).toEqual(starting);
		expect(loadedUltra(null, accountDefault)).toEqual(starting);
		expect(loadedUltra('on', accountDefault)).toEqual(starting);
		expect(loadedUltra([true], accountDefault)).toEqual(starting);
		expect(loadedUltra(undefined, undefined)).toEqual(ultra({ on: false }));
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

describe('ultraForReply', () => {
	// A new message runs the models the composer has selected.
	function newMessage(modelIds: string[]) {
		return { selected: modelIds, reply: modelIds };
	}

	it('sends the choice while Ultra is on and the reply can use it', () => {
		const choice = ultra({ worker_model: sonnet.id, worker_effort: 'high' });
		expect(ultraForReply(choice, newMessage([opus.id]), models, savedChat)).toEqual({
			worker_model: sonnet.id,
			worker_effort: 'high'
		});
		expect(ultraForReply(ultra(), newMessage([codex.id]), models, savedChat)).toEqual({
			worker_model: null,
			worker_effort: null
		});
	});

	it('leaves the field out while Ultra is off', () => {
		const off = ultra({ on: false, worker_model: sonnet.id });
		expect(ultraForReply(off, newMessage([opus.id]), models, savedChat)).toBeUndefined();
	});

	it('leaves the field out for a reply that cannot use Ultra', () => {
		const on = ultra({ worker_model: sonnet.id });
		expect(ultraForReply(on, newMessage([opus.id, sonnet.id]), models, savedChat)).toBeUndefined();
		expect(ultraForReply(on, newMessage([haiku.id]), models, savedChat)).toBeUndefined();
		expect(ultraForReply(on, newMessage([llama.id]), models, savedChat)).toBeUndefined();
		expect(ultraForReply(on, newMessage([]), models, savedChat)).toBeUndefined();
		const temporary = { id: '', temporary: true };
		expect(ultraForReply(on, newMessage([opus.id]), models, temporary)).toBeUndefined();
		const channel = { id: 'channel:general', temporary: false };
		expect(ultraForReply(on, newMessage([opus.id]), models, channel)).toBeUndefined();
	});

	it('leaves the field out while the composer shows Ultra off', () => {
		const on = ultra();
		// Regenerating the Opus column after a second model joined the chat.
		const oneColumn = { selected: [opus.id, codex.id], reply: [opus.id] };
		expect(ultraForReply(on, oneColumn, models, savedChat)).toBeUndefined();
		// Continuing an Opus reply after the composer moved to models without Ultra.
		const afterLlama = { selected: [llama.id], reply: [opus.id] };
		expect(ultraForReply(on, afterLlama, models, savedChat)).toBeUndefined();
		const afterHaiku = { selected: [haiku.id], reply: [opus.id] };
		expect(ultraForReply(on, afterHaiku, models, savedChat)).toBeUndefined();
	});

	it('leaves the field out for a single reply whose model cannot use Ultra', () => {
		const onHaiku = { selected: [opus.id], reply: [haiku.id] };
		expect(ultraForReply(ultra(), onHaiku, models, savedChat)).toBeUndefined();
	});

	it('sends a regenerated or continued reply’s choice while the composer shows Ultra on', () => {
		const choice = ultra({ worker_model: sonnet.id, worker_effort: 'max' });
		const opusReply = { selected: [sonnet.id], reply: [opus.id] };
		expect(ultraForReply(choice, opusReply, models, savedChat)).toEqual({
			worker_model: sonnet.id,
			worker_effort: null
		});
	});

	it('fits the choice to the reply’s model', () => {
		const levelCodexLacks = ultra({ worker_model: sonnet.id, worker_effort: 'max' });
		expect(ultraForReply(levelCodexLacks, newMessage([codex.id]), models, savedChat)).toEqual({
			worker_model: null,
			worker_effort: null
		});
		const levelCodexHas = ultra({ worker_model: sonnet.id, worker_effort: 'high' });
		expect(ultraForReply(levelCodexHas, newMessage(['', codex.id]), models, savedChat)).toEqual({
			worker_model: null,
			worker_effort: 'high'
		});
	});
});

describe('ultraButtonLabel', () => {
	const available = { available: true, reason: null };

	it('says Ultra is off while it is off or not available', () => {
		expect(ultraButtonLabel(i18n, ultra({ on: false }), available, opus, models)).toBe(
			'Ultra: off'
		);
		const unavailable = { available: false, reason: 'Ultra works with one model at a time.' };
		expect(ultraButtonLabel(i18n, ultra(), unavailable, opus, models)).toBe('Ultra: off');
	});

	it('names the workers’ model and thinking level while Ultra is on', () => {
		expect(ultraButtonLabel(i18n, ultra(), available, opus, models)).toBe(
			'Ultra: on. Workers use Claude Opus 4.1. Thinking: same as chat.'
		);
		const chosen = ultra({ worker_model: sonnet.id, worker_effort: 'high' });
		expect(ultraButtonLabel(i18n, chosen, available, opus, models)).toBe(
			'Ultra: on. Workers use Claude Sonnet 4.5. Thinking: High.'
		);
		const off = ultra({ worker_effort: 'none' });
		expect(ultraButtonLabel(i18n, off, available, codex, models)).toBe(
			'Ultra: on. Workers use GPT-5.1-Codex. Thinking: Off.'
		);
	});

	it('names the chat’s model for a worker model the chat’s provider does not offer', () => {
		const fromCodex = ultra({ worker_model: codex.id });
		expect(ultraButtonLabel(i18n, fromCodex, available, opus, models)).toBe(
			'Ultra: on. Workers use Claude Opus 4.1. Thinking: same as chat.'
		);
		expect(ultraButtonLabel(i18n, ultra(), available, null, models)).toBe(
			"Ultra: on. Workers use the chat's model. Thinking: same as chat."
		);
	});
});

describe('nextReplyAccess', () => {
	function details(changes: Partial<ChatRunTargetDetails> = {}): ChatRunTargetDetails {
		return {
			kind: 'computer',
			machine_id: 'pc-1',
			computer: { name: 'Work PC', kind: 'app', online: true },
			folder_mode: 'auto',
			path: null,
			access: 'full',
			source: 'bar',
			note: null,
			problem: null,
			...changes
		};
	}

	const workPc: RunTargetComputer = {
		machine_id: 'pc-1',
		name: 'Work PC',
		kind: 'app',
		online: true,
		platform: 'win32',
		can_browse: true,
		folders: 'on',
		recent_paths: [],
		revision: 'rev-1',
		providers: { claude: { state: 'ready', message: null } }
	};
	const options: RunTargetOptions = { allowed: true, has_plans: true, computers: [workPc] };

	function draft(changes: Partial<RunTargetDraft> = {}): RunTargetDraft {
		return {
			kind: 'computer',
			machineId: 'pc-1',
			expectedRevision: 'rev-1',
			folder: { mode: 'auto' },
			access: 'read',
			acknowledgedFolderId: null,
			touched: { computer: false, folder: false, access: false },
			...changes
		};
	}

	it('uses the chat’s own target once it has one', () => {
		const bound = { bound: true as const, ...details({ access: 'full' }) };
		expect(nextReplyAccess(bound, null, null)).toBe('full');
		expect(nextReplyAccess(bound, draft(), options)).toBe('full');
	});

	it('uses where an older chat’s next message would bind it', () => {
		const unbound = { bound: false as const, would_bind: details({ access: 'read' }) };
		expect(nextReplyAccess(unbound, null, null)).toBe('read');
	});

	it('uses a new chat’s draft in the composer’s bar', () => {
		expect(nextReplyAccess(null, draft(), options)).toBe('read');
		expect(nextReplyAccess(null, draft({ access: 'full' }), options)).toBe('full');
		// A chat that runs on no computer, or on one Buddy no longer lists, only chats.
		expect(nextReplyAccess(null, draft({ kind: 'detached', machineId: null }), options)).toBe(
			'chat'
		);
		expect(nextReplyAccess(null, draft({ machineId: 'gone' }), options)).toBe('chat');
	});

	it('is chat only until where the chat runs is known', () => {
		expect(nextReplyAccess(null, null, null)).toBe('chat');
		expect(nextReplyAccess(null, draft(), null)).toBe('chat');
	});
});

describe('menu footer', () => {
	it('says what workers may do at each access level', () => {
		expect(workersAccessLine(i18n, 'chat')).toBe('Workers can only think.');
		expect(workersAccessLine(i18n, 'read')).toBe('Workers can read the project.');
		expect(workersAccessLine(i18n, 'full')).toBe('Workers can edit the project.');
	});

	it('notes that Codex workers always share the chat’s access', () => {
		expect(workersAccessNote(i18n, codex)).toBe("Codex workers share the chat's access.");
		expect(workersAccessNote(i18n, opus)).toBeNull();
		expect(workersAccessNote(i18n, llama)).toBeNull();
		expect(workersAccessNote(i18n, null)).toBeNull();
	});

	it('says there is no worker limit unless an admin set one', () => {
		const noLimit = 'No worker limit — Ultra uses your plan faster';
		expect(workerLimitLine(i18n, null)).toBe(noLimit);
		expect(workerLimitLine(i18n, 0)).toBe(noLimit);
		expect(workerLimitLine(i18n, 1)).toBe('Up to 1 worker at once');
		expect(workerLimitLine(i18n, 4)).toBe('Up to 4 workers at once');
	});

	it('reads the admin’s worker limit from the chat’s model', () => {
		const capped: UltraModel = {
			...opus,
			subscription: { ...opus.subscription, ultra_max_workers: 3 }
		};
		expect(workerLimit(capped)).toBe(3);
		expect(workerLimitLine(i18n, workerLimit(capped))).toBe('Up to 3 workers at once');
	});

	it('reads no worker limit when the model lists none', () => {
		function withLimit(limit: unknown): UltraModel {
			return { ...opus, subscription: { ...opus.subscription, ultra_max_workers: limit } };
		}
		expect(workerLimit(opus)).toBeNull();
		expect(workerLimit(null)).toBeNull();
		expect(workerLimit(llama)).toBeNull();
		expect(workerLimit(withLimit(null))).toBeNull();
		expect(workerLimit(withLimit(0))).toBeNull();
		expect(workerLimit(withLimit(2.5))).toBeNull();
		expect(workerLimit(withLimit('3'))).toBeNull();
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

describe('workersSnapshot', () => {
	it('keeps a snapshot as the server sent it', () => {
		const snapshot = { items: [worker({ state: 'running' })], running: 1 };
		expect(workersSnapshot(snapshot)).toBe(snapshot);
		const none = { items: [], running: 0 };
		expect(workersSnapshot(none)).toBe(none);
	});

	it('refuses anything the server would not save as a snapshot', () => {
		expect(workersSnapshot(null)).toBeNull();
		expect(workersSnapshot(undefined)).toBeNull();
		expect(workersSnapshot('running')).toBeNull();
		expect(workersSnapshot([worker()])).toBeNull();
		expect(workersSnapshot({ running: 1 })).toBeNull();
		expect(workersSnapshot({ items: { 'task-1': worker() } })).toBeNull();
	});
});

describe('withCurrentWorkers', () => {
	const running = { items: [worker({ state: 'running' })], running: 1 };
	const finished = { items: [worker({ state: 'done' })], running: 0 };

	it('saves a copy taken before the last snapshot with the newer workers', () => {
		const current = { id: 'reply-1', content: 'Done.', workers: finished };
		const rated = { id: 'reply-1', content: 'Done.', workers: running, annotation: { rating: 1 } };
		expect(withCurrentWorkers(rated, current)).toEqual({ ...rated, workers: finished });
		// The copy itself is left as it was.
		expect(rated.workers).toBe(running);
	});

	it('saves a copy taken before the first snapshot with the workers', () => {
		const current = { id: 'reply-1', workers: running };
		const rated = { id: 'reply-1', annotation: { rating: 1 } };
		expect(withCurrentWorkers(rated, current)).toEqual({ ...rated, workers: running });
	});

	it('changes nothing when there are no newer workers to carry over', () => {
		const message = { id: 'reply-1', workers: finished };
		expect(withCurrentWorkers(message, message)).toBe(message);

		const copy = { ...message, annotation: { rating: 1 } };
		expect(withCurrentWorkers(copy, message)).toBe(copy);

		const withoutWorkers = { id: 'reply-1', content: 'Hi' };
		expect(withCurrentWorkers(copy, withoutWorkers)).toBe(copy);
		expect(withCurrentWorkers(copy, undefined)).toBe(copy);
		expect(withCurrentWorkers(copy, null)).toBe(copy);
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

describe('workerRows', () => {
	it('puts sub-workers under the worker that started them, in the order they started', () => {
		const reader = worker({ id: 'task-1', label: 'Read the parser' });
		const fixer = worker({ id: 'task-2', label: 'Fix the parser' });
		const readTests = worker({ id: 'task-3', parent_id: 'task-1', depth: 2 });
		const runTests = worker({ id: 'task-4', parent_id: 'task-2', depth: 2 });
		const readDocs = worker({ id: 'task-5', parent_id: 'task-1', depth: 2 });
		expect(workerRows([reader, fixer, readTests, runTests, readDocs])).toEqual([
			{ worker: reader, subWorkers: [readTests, readDocs] },
			{ worker: fixer, subWorkers: [runTests] }
		]);
	});

	it('keeps every row in place as workers end', () => {
		const running = [
			worker({ id: 'task-1', state: 'running' }),
			worker({ id: 'task-2', state: 'running' }),
			worker({ id: 'task-3', state: 'running' })
		];
		const ended = [
			worker({ id: 'task-1', state: 'running' }),
			worker({ id: 'task-2', state: 'done' }),
			worker({ id: 'task-3', state: 'failed' })
		];
		const ids = (items: WorkerItem[]) => workerRows(items).map((row) => row.worker.id);
		expect(ids(running)).toEqual(['task-1', 'task-2', 'task-3']);
		expect(ids(ended)).toEqual(['task-1', 'task-2', 'task-3']);
	});

	it('gives a worker whose parent is not listed a row of its own, in its place', () => {
		const first = worker({ id: 'task-1' });
		const orphan = worker({ id: 'task-2', parent_id: 'task-gone', depth: 2 });
		const last = worker({ id: 'task-3' });
		expect(workerRows([first, orphan, last])).toEqual([
			{ worker: first, subWorkers: [] },
			{ worker: orphan, subWorkers: [] },
			{ worker: last, subWorkers: [] }
		]);
	});

	it('puts a worker that a sub-worker started under the same row', () => {
		// Codex can start a third level, which Buddy stops and still lists.
		const top = worker({ id: 'thread-1' });
		const sub = worker({ id: 'thread-2', parent_id: 'thread-1', depth: 2 });
		const tooDeep = worker({ id: 'thread-3', parent_id: 'thread-2', state: 'stopped' });
		expect(workerRows([top, sub, tooDeep])).toEqual([{ worker: top, subWorkers: [sub, tooDeep] }]);
	});

	it('shows a worker listed twice once, and ends a parent chain that loops', () => {
		const first = worker({ id: 'task-1', state: 'running' });
		const again = worker({ id: 'task-1', state: 'done' });
		expect(workerRows([first, again])).toEqual([{ worker: first, subWorkers: [] }]);

		const one = worker({ id: 'task-1', parent_id: 'task-2' });
		const two = worker({ id: 'task-2', parent_id: 'task-1' });
		expect(workerRows([one, two]).map((row) => row.worker.id)).toEqual(['task-1', 'task-2']);
		expect(workerRows([])).toEqual([]);
	});
});

describe('workers card phase', () => {
	const running = worker({ state: 'running' });
	const done = worker({ id: 'task-2', state: 'done' });

	it('is working, and open, while the reply is written', () => {
		expect(workersCardPhase([running, done], false)).toBe('working');
		expect(workersCardPhase([done], false)).toBe('working');
		expect(workersCardPhase([], false)).toBe('working');
		expect(workersCardOpens('working')).toBe(true);
	});

	it('stays open when the reply ended with workers still running', () => {
		expect(workersCardPhase([running, done], true)).toBe('still-running');
		expect(workersCardOpens('still-running')).toBe(true);
	});

	it('collapses once every worker ended, or when the reply started none', () => {
		expect(workersCardPhase([done], true)).toBe('finished');
		expect(workersCardPhase([], true)).toBe('none');
		expect(workersCardOpens('finished')).toBe(false);
		expect(workersCardOpens('none')).toBe(false);
	});
});

describe('workersCardTitle', () => {
	const team = [
		worker({ id: 'task-1', state: 'running', started_at: 1000 }),
		worker({ id: 'task-2', state: 'done', started_at: 1010, ended_at: 1100 }),
		worker({ id: 'task-3', parent_id: 'task-1', depth: 2, state: 'running', started_at: 1020 })
	];
	const done = [
		worker({ id: 'task-1', started_at: 1000, ended_at: 1150 }),
		worker({ id: 'task-2', started_at: 1010, ended_at: 1192 })
	];

	it('heads the card for each phase', () => {
		expect(workersCardTitle(i18n, team, 'working', 1300)).toBe('Ultra · 3 workers · 2 running');
		expect(workersCardTitle(i18n, team, 'still-running', 1300)).toBe(
			'Ultra · 3 workers · 2 still running'
		);
		expect(workersCardTitle(i18n, done, 'finished', 5000)).toBe('Used 2 workers · 3m 12s');
		expect(workersCardTitle(i18n, [], 'none', 5000)).toBe('Answered without workers');
	});
});

describe('worker rows text', () => {
	it('words each state', () => {
		expect(workerStateLabel(i18n, 'running')).toBe('Running');
		expect(workerStateLabel(i18n, 'done')).toBe('Done');
		expect(workerStateLabel(i18n, 'failed')).toBe('Failed');
		expect(workerStateLabel(i18n, 'stopped')).toBe('Stopped');
	});

	it('says what a running worker is doing', () => {
		const reading = worker({ state: 'running', activity: 'Reading claude_code.py' });
		expect(workerLine(i18n, reading)).toBe('Reading claude_code.py');
		expect(workerLine(i18n, worker({ state: 'running' }))).toBe('Working…');
		expect(workerLine(i18n, reading, 'sent')).toBe('Reading claude_code.py');
		expect(workerLine(i18n, reading, 'not-running')).toBe('Not running any more');
	});

	it('says how an ended worker ended, from its report', () => {
		const reported = worker({
			activity: 'Running the tests',
			summary: '\n  All 12 tests pass.  \nNothing else changed.'
		});
		expect(workerLine(i18n, reported)).toBe('All 12 tests pass.');
		expect(workerLine(i18n, worker({ state: 'failed', activity: 'Running the tests' }))).toBe(
			'Failed'
		);
		expect(workerLine(i18n, worker({ state: 'stopped', summary: '   ' }))).toBe('Stopped');
	});

	it('opens a row to what a worker is doing or its whole report', () => {
		const reading = worker({ state: 'running', activity: 'Reading claude_code.py' });
		expect(workerDetails(reading)).toBe('Reading claude_code.py');
		expect(workerDetails(reading, 'not-running')).toBeNull();
		expect(workerDetails(worker({ state: 'running' }))).toBeNull();

		const report = 'All 12 tests pass.\nNothing else changed.';
		expect(workerDetails(worker({ summary: report, activity: 'Running the tests' }))).toBe(report);
		expect(workerDetails(worker())).toBeNull();
	});
});

describe('stopping one worker', () => {
	const running = worker({ state: 'running' });

	it('lets only the owner of a saved chat stop its workers', () => {
		const savedChatId = '7d5e8a54-3b1f-4c2e-9a7d-0f6b2c1e4d3a';
		expect(canStopWorkers(true, savedChatId)).toBe(true);
		expect(canStopWorkers(false, savedChatId)).toBe(false);
		expect(canStopWorkers(true, '')).toBe(false);
		expect(canStopWorkers(true, null)).toBe(false);
		expect(canStopWorkers(true, 'temporary:session-1')).toBe(false);
		expect(canStopWorkers(true, 'channel:general')).toBe(false);
	});

	it('offers Stop on running workers only', () => {
		expect(stopControl(running, true, undefined)).toBe('ready');
		expect(stopControl(running, false, undefined)).toBe('none');
		expect(stopControl(worker({ state: 'done' }), true, undefined)).toBe('none');
		expect(stopControl(worker({ state: 'stopped' }), true, 'sent')).toBe('none');
	});

	it('waits once Stop is pressed, and drops it for a worker that already ended', () => {
		expect(stopControl(running, true, 'sending')).toBe('stopping');
		expect(stopControl(running, true, 'sent')).toBe('stopping');
		expect(stopControl(running, true, 'not-running')).toBe('none');
	});

	it('gives the server’s reason when Stop failed', () => {
		expect(stopFailedMessage(i18n, 'Fix the parser', 'Your computer is offline.')).toBe(
			"Couldn't stop Fix the parser: Your computer is offline."
		);
		expect(stopFailedMessage(i18n, 'Fix the parser', new TypeError('Failed to fetch'))).toBe(
			"Couldn't stop Fix the parser: Failed to fetch"
		);
		expect(stopFailedMessage(i18n, 'Fix the parser', '')).toBe(
			"Couldn't stop Fix the parser: Something went wrong."
		);
		expect(stopFailedMessage(i18n, 'Fix the parser', { detail: [] })).toBe(
			"Couldn't stop Fix the parser: Something went wrong."
		);
	});

	it('reads one reply’s saved workers from the chat', () => {
		const snapshot = { items: [worker()], running: 0 };
		const messages = { 'reply-1': { id: 'reply-1', workers: snapshot } };
		const chat = { id: 'chat-1', chat: { history: { messages } } };
		expect(savedReplyWorkers(chat, 'reply-1')).toBe(snapshot);
		expect(savedReplyWorkers(chat, 'reply-2')).toBeNull();

		const withoutWorkers = { chat: { history: { messages: { 'reply-1': { id: 'reply-1' } } } } };
		expect(savedReplyWorkers(withoutWorkers, 'reply-1')).toBeNull();
		expect(savedReplyWorkers({ chat: {} }, 'reply-1')).toBeNull();
		expect(savedReplyWorkers(null, 'reply-1')).toBeNull();
	});
});

describe('announcing ended workers', () => {
	it('finds the workers that ended since the last snapshot', () => {
		const earlier = [
			worker({ id: 'task-1', state: 'running' }),
			worker({ id: 'task-2', state: 'done' }),
			worker({ id: 'task-3', state: 'running' })
		];
		const finished = worker({ id: 'task-1', state: 'done' });
		const quick = worker({ id: 'task-4', state: 'failed' });
		const current = [
			finished,
			worker({ id: 'task-2', state: 'done' }),
			worker({ id: 'task-3', state: 'running' }),
			quick,
			worker({ id: 'task-5', state: 'running' })
		];
		expect(newlyEnded(earlier, current)).toEqual([finished, quick]);
		expect(newlyEnded(current, current)).toEqual([]);
	});

	it('names one worker that ended', () => {
		expect(workersEndedAnnouncement(i18n, [])).toBe('');
		const finished = worker({ label: 'Read the parser' });
		expect(workersEndedAnnouncement(i18n, [finished])).toBe('Worker finished: Read the parser');
		const failed = worker({ label: 'Run the tests', state: 'failed' });
		expect(workersEndedAnnouncement(i18n, [failed])).toBe('Worker failed: Run the tests');
		const stopped = worker({ label: 'Review the fix', state: 'stopped' });
		expect(workersEndedAnnouncement(i18n, [stopped])).toBe('Worker stopped: Review the fix');
	});

	it('counts several by how they ended', () => {
		const several = [
			worker({ id: 'task-1', state: 'done' }),
			worker({ id: 'task-2', state: 'failed' }),
			worker({ id: 'task-3', state: 'done' })
		];
		expect(workersEndedAnnouncement(i18n, several)).toBe('2 workers finished, 1 worker failed');
		const stopped = [
			worker({ id: 'task-1', state: 'stopped' }),
			worker({ id: 'task-2', state: 'stopped' })
		];
		expect(workersEndedAnnouncement(i18n, stopped)).toBe('2 workers stopped');
	});
});
