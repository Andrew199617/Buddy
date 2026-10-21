// Ultra in the composer: whether a chat can use it, the choices for its workers,
// what a chat saves and sends, and how its workers card lays out, words and
// announces its workers. Plain helpers with no DOM or network; the server checks
// whatever the composer sends.

import type { ChatRunTarget, RunTargetOptions, SubscriptionAccess } from '$lib/apis/subscriptions';
import { isChannelChatId, isSavedChatId, isTemporaryChatId } from '$lib/utils/chatId';

import { detailsFromDraft, type RunTargetDraft } from '../RunTarget/run-target';

type Translator = { t: (key: string, options?: Record<string, unknown>) => string };

type PlanProvider = 'claude' | 'codex';

/** The parts of a model list entry Ultra reads. Plan models carry `subscription`. */
export type UltraModel = {
	id: string;
	name?: string;
	owned_by?: string;
	subscription?: { provider?: string; efforts?: unknown; ultra?: unknown };
};

/** The chat the composer is in. */
export type UltraChat = {
	// '' or null for a chat that isn't created yet.
	id: string | null | undefined;
	// The temporary chat switch, which is on before a temporary chat has an id.
	temporary: boolean | null | undefined;
};

export type UltraAvailability = { available: boolean; reason: string | null };

/** What a chat saves as `chat.ultra`, and what the composer edits. */
export type UltraState = {
	on: boolean;
	// A plan model's ID, such as 'claude-code.sonnet'; null runs the workers on the chat's model.
	worker_model: string | null;
	// One of the workers' model's thinking levels; null uses the chat's.
	worker_effort: string | null;
};

/**
 * One edit in the Ultra menu. The composer merges it into the chat's saved
 * choice, so a choice the chat's current model can't use stays saved.
 */
export type UltraChange =
	| { on: boolean }
	| { worker_model: string | null }
	| { worker_effort: string | null };

/** The chat request's top-level `ultra` field, sent only while Ultra is on. */
export type UltraRequest = { worker_model: string | null; worker_effort: string | null };

/** A row in the Worker model or Worker thinking list; null is "Same as chat". */
export type UltraOption = { value: string | null; label: string };

export type WorkerState = 'running' | 'done' | 'failed' | 'stopped';

/** One worker in a reply's `message.workers` snapshot, as the server sends and saves it. */
export type WorkerItem = {
	id: string;
	// The worker that started this one; null for the orchestrator's own workers.
	parent_id: string | null;
	// 1 for the orchestrator's workers, 2 for the sub-workers they start.
	depth: 1 | 2;
	label: string;
	state: WorkerState;
	// The workers' model's name, or null when they run on the chat's model.
	model: string | null;
	effort: string | null;
	// What the worker is doing now, in one short line.
	activity: string | null;
	// The worker's report once it ends.
	summary: string | null;
	tokens: number | null;
	// Seconds since 1970.
	started_at: number | null;
	ended_at: number | null;
};

const WORKER_STATES: WorkerState[] = ['running', 'done', 'failed', 'stopped'];

// For checks whose reason nobody reads, such as whether a request may carry Ultra.
const UNTRANSLATED: Translator = { t: (key) => key };

function planProvider(model: UltraModel | null | undefined): PlanProvider | null {
	if (!model || model.owned_by !== 'subscription') return null;
	const provider = model.subscription?.provider;
	if (provider === 'claude' || provider === 'codex') return provider;
	return null;
}

function runsUltra(model: UltraModel): boolean {
	return planProvider(model) !== null && model.subscription?.ultra === true;
}

function providerLabel(provider: PlanProvider): string {
	if (provider === 'codex') return 'ChatGPT';
	return 'Claude';
}

function modelName(model: UltraModel): string {
	if (model.name) return model.name;
	return model.id;
}

/** A model's thinking levels, leaving out anything that is not a level's name. */
function effortNames(model: UltraModel | null): string[] {
	const efforts = model?.subscription?.efforts;
	if (!Array.isArray(efforts)) return [];
	return efforts.filter((effort): effort is string => typeof effort === 'string' && effort !== '');
}

function textOrNull(value: unknown): string | null {
	if (typeof value !== 'string' || value.trim() === '') return null;
	return value;
}

function numberOrNull(value: unknown): number | null {
	if (typeof value !== 'number' || !Number.isFinite(value)) return null;
	return value;
}

function unavailable(reason: string): UltraAvailability {
	return { available: false, reason };
}

/**
 * Whether the composer can turn Ultra on: exactly one model is selected, it is a
 * Claude or ChatGPT plan model that can run workers, and the chat is neither
 * temporary nor a channel. Otherwise the reason, to show in place of the switch.
 */
export function availability(
	i18n: Translator,
	selectedModels: string[],
	models: UltraModel[],
	chat: UltraChat
): UltraAvailability {
	if (chat.temporary === true || isTemporaryChatId(chat.id)) {
		return unavailable(i18n.t("Ultra isn't available in temporary chats."));
	}
	if (isChannelChatId(chat.id)) {
		return unavailable(i18n.t("Ultra isn't available in channels."));
	}

	const chosen = selectedModels.filter((modelId) => !!modelId);
	if (chosen.length === 0) {
		return unavailable(i18n.t('Choose a Claude or ChatGPT plan model to use Ultra.'));
	}
	if (chosen.length > 1) {
		return unavailable(i18n.t('Ultra works with one model at a time.'));
	}

	const model = models.find((candidate) => candidate.id === chosen[0]);
	const provider = planProvider(model);
	if (!model || provider === null) {
		return unavailable(i18n.t('Ultra works with Claude and ChatGPT plan models.'));
	}
	if (!runsUltra(model)) {
		return unavailable(
			i18n.t("{{provider}} on your computer can't run workers with {{model}}.", {
				provider: providerLabel(provider),
				model: modelName(model)
			})
		);
	}
	return { available: true, reason: null };
}

/**
 * The chat's model as Ultra sees it: the first selected model, or null. Ultra is
 * available only while it is the one selected model (see availability).
 */
export function firstSelectedModel(
	selectedModels: string[],
	models: UltraModel[]
): UltraModel | null {
	const modelId = selectedModels.find((selected) => !!selected);
	if (!modelId) return null;
	return models.find((model) => model.id === modelId) ?? null;
}

/** The models the workers can run on: the chat's provider's plan models that run Ultra. */
function workerModels(chatModel: UltraModel | null, models: UltraModel[]): UltraModel[] {
	const provider = planProvider(chatModel);
	if (provider === null) return [];
	return models.filter((model) => planProvider(model) === provider && runsUltra(model));
}

/** The chosen worker model while the chat's provider offers it, else null. */
function chosenWorkerModel(
	state: UltraState,
	chatModel: UltraModel | null,
	models: UltraModel[]
): UltraModel | null {
	if (state.worker_model === null) return null;
	const offered = workerModels(chatModel, models);
	return offered.find((model) => model.id === state.worker_model) ?? null;
}

/**
 * The Worker model list: "Same as chat (<name>)" first, then the chat's
 * provider's plan models that run Ultra. Empty when the chat's model isn't a
 * plan model.
 */
export function workerModelOptions(
	i18n: Translator,
	chatModel: UltraModel | null,
	models: UltraModel[]
): UltraOption[] {
	if (!chatModel || planProvider(chatModel) === null) return [];
	const sameAsChat = i18n.t('Same as chat ({{model}})', { model: modelName(chatModel) });
	const options: UltraOption[] = [{ value: null, label: sameAsChat }];
	for (const model of workerModels(chatModel, models)) {
		options.push({ value: model.id, label: modelName(model) });
	}
	return options;
}

/** The model the workers run on: the chosen one while the chat's provider offers it, else the chat's. */
export function workersModel(
	state: UltraState,
	chatModel: UltraModel | null,
	models: UltraModel[]
): UltraModel | null {
	return chosenWorkerModel(state, chatModel, models) ?? chatModel;
}

/** A thinking level's name, as the composer's Thinking menu words it. */
export function effortLabel(i18n: Translator, effort: string): string {
	switch (effort) {
		case 'none':
			return i18n.t('Off');
		case 'minimal':
			return i18n.t('Minimal');
		case 'low':
			return i18n.t('Low');
		case 'medium':
			return i18n.t('Medium');
		case 'high':
			return i18n.t('High');
		case 'xhigh':
			return i18n.t('Extra high');
		case 'max':
			return i18n.t('Max');
		default:
			return effort;
	}
}

/**
 * The Worker thinking list for the workers' model (see workersModel): "Same as
 * chat" first, then the levels that model offers.
 */
export function workerEffortOptions(i18n: Translator, model: UltraModel | null): UltraOption[] {
	const options: UltraOption[] = [{ value: null, label: i18n.t('Same as chat') }];
	for (const effort of effortNames(model)) {
		options.push({ value: effort, label: effortLabel(i18n, effort) });
	}
	return options;
}

/**
 * The choice as the chat's current model can use it. A worker model the chat's
 * provider doesn't offer, such as the other provider's after the chat's model
 * changed, and a thinking level the workers' model doesn't offer both go back to
 * "Same as chat". The composer shows and sends this; the saved choice stays as it
 * was, for when the chat's model changes back.
 */
export function fitUltra(
	state: UltraState,
	chatModel: UltraModel | null,
	models: UltraModel[]
): UltraState {
	const chosen = chosenWorkerModel(state, chatModel, models);
	let workerModel: string | null = null;
	let efforts = effortNames(chatModel);
	if (chosen) {
		workerModel = chosen.id;
		efforts = effortNames(chosen);
	}

	let workerEffort: string | null = null;
	if (state.worker_effort !== null && efforts.includes(state.worker_effort)) {
		workerEffort = state.worker_effort;
	}
	return { on: state.on, worker_model: workerModel, worker_effort: workerEffort };
}

/** A chat's saved `chat.ultra`, or Ultra off with the chat's own choices when there is none. */
export function savedUltra(value: unknown): UltraState {
	if (!value || typeof value !== 'object' || Array.isArray(value)) {
		return { on: false, worker_model: null, worker_effort: null };
	}
	const saved = value as Record<string, unknown>;
	return {
		on: saved.on === true,
		worker_model: textOrNull(saved.worker_model),
		worker_effort: textOrNull(saved.worker_effort)
	};
}

/** What a new chat starts with: Ultra off, with the account's last worker choices. */
export function startingUltra(accountDefault: unknown): UltraState {
	const choices = savedUltra(accountDefault);
	return { on: false, worker_model: choices.worker_model, worker_effort: choices.worker_effort };
}

/**
 * A loaded chat's Ultra choice: the one saved with it, or what a new chat starts
 * with when it has none, like a chat from before Ultra.
 */
export function loadedUltra(saved: unknown, accountDefault: unknown): UltraState {
	if (!saved || typeof saved !== 'object' || Array.isArray(saved)) {
		return startingUltra(accountDefault);
	}
	return savedUltra(saved);
}

/**
 * The request's `ultra` field, or undefined while Ultra is off so the field is
 * left out. Pass the choice through fitUltra first, and send it only while
 * availability says Ultra is available.
 */
export function requestUltra(state: UltraState): UltraRequest | undefined {
	if (!state.on) return undefined;
	return { worker_model: state.worker_model, worker_effort: state.worker_effort };
}

/**
 * The models a chat request runs, and the models the composer has selected. They
 * differ when a reply is regenerated in one column of a side-by-side chat, which
 * runs only that column's model, and when a reply is continued, which runs the
 * reply's own model.
 */
export type UltraReplyModels = {
	// The composer's selection, which decides whether its Ultra button shows Ultra on.
	selected: string[];
	// The models this request runs.
	reply: string[];
};

/**
 * The `ultra` field for a reply, or undefined to leave it out: Ultra is off, the
 * composer shows it off because its own selection can't use it, or the reply's
 * models can't use it (see availability). The choice is fitted to the reply's
 * model first, so it never names a model the server would refuse.
 */
export function ultraForReply(
	state: UltraState,
	replyModels: UltraReplyModels,
	models: UltraModel[],
	chat: UltraChat
): UltraRequest | undefined {
	// A reply never starts workers while the composer says Ultra is off, even a
	// regenerated or continued reply that runs a single model.
	const composerShowsUltra = availability(UNTRANSLATED, replyModels.selected, models, chat);
	if (!composerShowsUltra.available) return undefined;
	const replyCanUseUltra = availability(UNTRANSLATED, replyModels.reply, models, chat);
	if (!replyCanUseUltra.available) return undefined;

	const chatModel = firstSelectedModel(replyModels.reply, models);
	return requestUltra(fitUltra(state, chatModel, models));
}

/**
 * The Ultra button's name and tooltip: whether the next message uses Ultra, and
 * what its workers run on. Ultra counts as off while it isn't available.
 */
export function ultraButtonLabel(
	i18n: Translator,
	state: UltraState,
	ultraAvailability: UltraAvailability,
	chatModel: UltraModel | null,
	models: UltraModel[]
): string {
	if (!state.on || !ultraAvailability.available) return i18n.t('Ultra: off');

	const model = workersModel(state, chatModel, models);
	let name = i18n.t("the chat's model");
	if (model) name = modelName(model);
	if (state.worker_effort === null) {
		return i18n.t('Ultra: on. Workers use {{model}}. Thinking: same as chat.', { model: name });
	}
	return i18n.t('Ultra: on. Workers use {{model}}. Thinking: {{effort}}.', {
		model: name,
		effort: effortLabel(i18n, state.worker_effort)
	});
}

/**
 * The access the chat's next reply runs with, which its workers share: the chat's
 * own target once it has one, else where its next message would bind it, else
 * the new chat's draft in the composer's bar. Chat only until one is known.
 */
export function nextReplyAccess(
	target: ChatRunTarget | null,
	draft: RunTargetDraft | null,
	options: RunTargetOptions | null
): SubscriptionAccess {
	if (target && target.bound) return target.access;
	if (target) return target.would_bind.access;
	if (draft && options) return detailsFromDraft(draft, options).access;
	return 'chat';
}

/** What the workers may do: the same as the access the chat's reply runs with. */
export function workersAccessLine(i18n: Translator, access: SubscriptionAccess): string {
	if (access === 'full') return i18n.t('Workers can edit the project.');
	if (access === 'read') return i18n.t('Workers can read the project.');
	return i18n.t('Workers can only think.');
}

/**
 * Codex starts every worker with the chat's access, so a worker that only needs
 * to read can't be given less. Null for Claude, whose read-only workers can.
 */
export function workersAccessNote(i18n: Translator, chatModel: UltraModel | null): string | null {
	if (planProvider(chatModel) !== 'codex') return null;
	return i18n.t("Codex workers share the chat's access.");
}

/** How many workers run at once: no limit unless an admin set one. */
export function workerLimitLine(i18n: Translator, maxWorkers: number | null): string {
	if (maxWorkers === null || maxWorkers < 1) {
		return i18n.t('No worker limit — Ultra uses your plan faster');
	}
	if (maxWorkers === 1) return i18n.t('Up to 1 worker at once');
	return i18n.t('Up to {{count}} workers at once', { count: maxWorkers });
}

function workerItem(value: unknown): WorkerItem | null {
	if (!value || typeof value !== 'object') return null;
	const saved = value as Record<string, unknown>;
	const id = textOrNull(saved.id);
	const state = WORKER_STATES.find((known) => known === saved.state);
	if (id === null || state === undefined) return null;

	let depth: 1 | 2 = 1;
	if (saved.depth === 2) depth = 2;
	let tokens = numberOrNull(saved.tokens);
	if (tokens !== null && tokens < 0) tokens = null;
	return {
		id,
		parent_id: textOrNull(saved.parent_id),
		depth,
		// The server names a worker without a name 'Worker' too.
		label: textOrNull(saved.label) ?? 'Worker',
		state,
		model: textOrNull(saved.model),
		effort: textOrNull(saved.effort),
		activity: textOrNull(saved.activity),
		summary: textOrNull(saved.summary),
		tokens,
		started_at: numberOrNull(saved.started_at),
		ended_at: numberOrNull(saved.ended_at)
	};
}

/** The workers in a reply's saved `message.workers` snapshot, leaving out any that can't be shown. */
export function savedWorkers(value: unknown): WorkerItem[] {
	if (!value || typeof value !== 'object') return [];
	const items = (value as Record<string, unknown>).items;
	if (!Array.isArray(items)) return [];
	const workers: WorkerItem[] = [];
	for (const item of items) {
		const worker = workerItem(item);
		if (worker) workers.push(worker);
	}
	return workers;
}

/** A reply's `message.workers`: every worker so far, in the order they started. */
export type WorkersSnapshot = { items: unknown[]; running?: unknown };

/** A chat message, as far as its workers go; its other fields are carried as they are. */
type MessageWithWorkers = { workers?: unknown; [field: string]: unknown };

/**
 * A chat:message:workers event's snapshot, or null when it isn't shaped like one.
 * Each snapshot lists every worker in the reply, so it replaces the message's
 * copy. The server saves only snapshots, and the page keeps only those too, so a
 * stray event can't replace the saved workers when the page saves the chat.
 */
export function workersSnapshot(data: unknown): WorkersSnapshot | null {
	if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
	const items = (data as Record<string, unknown>).items;
	if (!Array.isArray(items)) return null;
	return data as WorkersSnapshot;
}

/**
 * The message the page is about to save, with the workers of the message the
 * chat holds now. Worker snapshots update the message in the chat's history, so
 * a copy taken earlier, as a rating takes one and then waits on the network, can
 * hold an older snapshot or none. A chat save replaces each message it sends, so
 * saving that copy would replace the workers the server saved. Returns `saving`
 * itself when there is nothing to carry over.
 */
export function withCurrentWorkers<T extends MessageWithWorkers>(
	saving: T,
	current: MessageWithWorkers | null | undefined
): T {
	if (!current || current === saving) return saving;
	if (!current.workers || current.workers === saving.workers) return saving;
	return { ...saving, workers: current.workers };
}

/** A plan model's name without its provider's word: 'Claude Sonnet 4.5' becomes 'Sonnet 4.5'. */
export function shortModelName(name: string): string {
	if (name.startsWith('Claude ')) return name.slice('Claude '.length);
	return name;
}

/**
 * A worker's "model · thinking" pill, such as "Sonnet · high". A worker without
 * its own model runs on the chat's, named by chatModelName. Empty when there is
 * nothing to show.
 */
export function workerPill(
	i18n: Translator,
	item: WorkerItem,
	chatModelName: string | null = null
): string {
	const parts: string[] = [];
	const model = item.model ?? chatModelName;
	if (model) {
		parts.push(shortModelName(model));
	}
	if (item.effort) {
		parts.push(effortLabel(i18n, item.effort).toLocaleLowerCase());
	}
	return parts.join(' · ');
}

export function runningWorkers(items: WorkerItem[]): number {
	return items.filter((item) => item.state === 'running').length;
}

function workerCount(i18n: Translator, count: number): string {
	if (count === 1) return i18n.t('1 worker');
	return i18n.t('{{count}} workers', { count });
}

/** The card's header while it is open: "Ultra · 3 workers · 2 running". */
export function workersHeader(i18n: Translator, items: WorkerItem[]): string {
	const parts = ['Ultra', workerCount(i18n, items.length)];
	const running = runningWorkers(items);
	if (running > 0) {
		parts.push(i18n.t('{{count}} running', { count: running }));
	}
	return parts.join(' · ');
}

/** The note when the reply ended with workers still running: "2 still running". */
export function workersStillRunning(i18n: Translator, items: WorkerItem[]): string {
	return i18n.t('{{count}} still running', { count: runningWorkers(items) });
}

/**
 * Seconds from the first worker's start to the last one's end, counting a worker
 * that still runs up to now (in seconds, like the snapshot's times). Null when
 * no worker has a start time.
 */
export function workersElapsed(items: WorkerItem[], now: number): number | null {
	let first: number | null = null;
	let last: number | null = null;
	for (const item of items) {
		if (item.started_at !== null) {
			if (first === null || item.started_at < first) first = item.started_at;
		}
		let end = item.ended_at;
		if (item.state === 'running') end = now;
		if (end !== null) {
			if (last === null || end > last) last = end;
		}
	}
	if (first === null || last === null) return null;
	return Math.max(0, Math.round(last - first));
}

/** A duration such as "45s", "3m 12s" or "1h 4m". */
export function formatElapsed(seconds: number): string {
	const whole = Math.max(0, Math.floor(seconds));
	const hours = Math.floor(whole / 3600);
	const minutes = Math.floor((whole % 3600) / 60);
	const rest = whole % 60;
	if (hours > 0) return `${hours}h ${minutes}m`;
	if (minutes > 0) return `${minutes}m ${rest}s`;
	return `${rest}s`;
}

/**
 * The card collapsed once every worker is done: "Used 3 workers · 3m 12s", or
 * "Answered without workers" for an Ultra reply that started none.
 */
export function workersUsed(i18n: Translator, items: WorkerItem[], now: number): string {
	if (items.length === 0) return i18n.t('Answered without workers');
	let used: string;
	if (items.length === 1) {
		used = i18n.t('Used 1 worker');
	} else {
		used = i18n.t('Used {{count}} workers', { count: items.length });
	}
	const seconds = workersElapsed(items, now);
	if (seconds === null) return used;
	return `${used} · ${formatElapsed(seconds)}`;
}

// --- The workers card -----------------------------------------------------------

/** One row of the workers card: a worker and the sub-workers it started. */
export type WorkerRow = { worker: WorkerItem; subWorkers: WorkerItem[] };

/**
 * How the workers card shows:
 * - 'working' while the reply is being written, open;
 * - 'still-running' when the reply has ended and workers still run, open;
 * - 'finished' when every worker has ended, collapsed to what the reply used;
 * - 'none' for an Ultra reply that ended without starting any worker.
 */
export type WorkersCardPhase = 'working' | 'still-running' | 'finished' | 'none';

/** Where a Stop the person pressed stands, by worker id. */
export type StopRequest =
	// On its way to the server.
	| 'sending'
	// Accepted; the worker shows as running until the reply reports it stopped.
	| 'sent'
	// The server answered that the worker isn't running any more.
	| 'not-running';

/** A row's Stop button: none, ready to press, or waiting for the worker to stop. */
export type StopControl = 'none' | 'ready' | 'stopping';

/**
 * The row a worker shows in: the id of the top worker of its parent chain. A
 * sub-worker shows under the orchestrator's worker that started it, and so does
 * a worker it started in turn. A worker whose parent isn't listed gets its own row.
 */
function rowIdFor(worker: WorkerItem, byId: Map<string, WorkerItem>): string {
	let current = worker;
	// The workers already passed, so a parent chain that loops back still ends.
	const passed = new Set<string>();
	while (current.parent_id !== null && !passed.has(current.id)) {
		const parent = byId.get(current.parent_id);
		if (!parent) break;
		passed.add(current.id);
		current = parent;
	}
	return current.id;
}

/**
 * The card's rows: each of the orchestrator's workers with its sub-workers under
 * it, all in the order they started, which is the snapshot's order. Rows keep
 * their place as workers end, so a Stop button never moves under a finger. A
 * worker listed twice shows once.
 */
export function workerRows(items: WorkerItem[]): WorkerRow[] {
	const byId = new Map<string, WorkerItem>();
	for (const item of items) {
		if (!byId.has(item.id)) byId.set(item.id, item);
	}

	const rows: WorkerRow[] = [];
	const rowsById = new Map<string, WorkerRow>();
	for (const worker of byId.values()) {
		if (rowIdFor(worker, byId) !== worker.id) continue;
		const row: WorkerRow = { worker, subWorkers: [] };
		rows.push(row);
		rowsById.set(worker.id, row);
	}
	for (const worker of byId.values()) {
		const rowId = rowIdFor(worker, byId);
		if (rowId === worker.id) continue;
		rowsById.get(rowId)?.subWorkers.push(worker);
	}
	return rows;
}

/** The card's phase, from the reply's workers and whether its own text has ended. */
export function workersCardPhase(items: WorkerItem[], replyDone: boolean): WorkersCardPhase {
	if (!replyDone) return 'working';
	if (items.length === 0) return 'none';
	if (runningWorkers(items) > 0) return 'still-running';
	return 'finished';
}

/** Whether the card shows its rows in this phase, until the person opens or closes it. */
export function workersCardOpens(phase: WorkersCardPhase): boolean {
	return phase === 'working' || phase === 'still-running';
}

/**
 * The card's header: "Ultra · 3 workers · 2 running" while the reply is written,
 * "Ultra · 3 workers · 2 still running" after it, and what the reply used once
 * every worker has ended (see workersUsed).
 */
export function workersCardTitle(
	i18n: Translator,
	items: WorkerItem[],
	phase: WorkersCardPhase,
	now: number
): string {
	if (phase === 'working') return workersHeader(i18n, items);
	if (phase === 'still-running') {
		const parts = ['Ultra', workerCount(i18n, items.length), workersStillRunning(i18n, items)];
		return parts.join(' · ');
	}
	return workersUsed(i18n, items, now);
}

/** A worker's state in words, for its icon and its sheet. */
export function workerStateLabel(i18n: Translator, state: WorkerState): string {
	switch (state) {
		case 'running':
			return i18n.t('Running');
		case 'done':
			return i18n.t('Done');
		case 'failed':
			return i18n.t('Failed');
		case 'stopped':
			return i18n.t('Stopped');
	}
}

function firstLine(text: string | null): string | null {
	if (text === null) return null;
	for (const line of text.split('\n')) {
		if (line.trim() !== '') return line.trim();
	}
	return null;
}

/**
 * A row's one line under the worker's label: what a running worker is doing, or
 * how an ended one ended, from the first line of its report. A worker the server
 * said isn't running any more, while the chat still lists it running, says so.
 */
export function workerLine(
	i18n: Translator,
	worker: WorkerItem,
	request: StopRequest | undefined = undefined
): string {
	if (worker.state === 'running') {
		if (request === 'not-running') return i18n.t('Not running any more');
		if (worker.activity) return worker.activity;
		return i18n.t('Working…');
	}
	const reported = firstLine(worker.summary);
	if (reported) return reported;
	return workerStateLabel(i18n, worker.state);
}

/**
 * What a desktop row opens to show: the whole of what a running worker is doing,
 * or an ended worker's report. Null when there is nothing more: someone else's
 * chat never carries either, and a worker the server said isn't running any more
 * is no longer doing what it last reported.
 */
export function workerDetails(
	worker: WorkerItem,
	request: StopRequest | undefined = undefined
): string | null {
	if (worker.state !== 'running') return worker.summary;
	if (request === 'not-running') return null;
	return worker.activity;
}

/**
 * Whether the person may stop this chat's workers one by one: only the chat's
 * owner, and only in a saved chat, since the stop route finds workers by the
 * chat's id.
 */
export function canStopWorkers(ownChat: boolean, chatId: string | null | undefined): boolean {
	return ownChat && isSavedChatId(chatId);
}

/** A row's Stop button for a worker and the Stop the person pressed for it, if any. */
export function stopControl(
	worker: WorkerItem,
	canStop: boolean,
	request: StopRequest | undefined
): StopControl {
	if (!canStop || worker.state !== 'running') return 'none';
	if (request === 'not-running') return 'none';
	if (request === 'sending' || request === 'sent') return 'stopping';
	return 'ready';
}

/** The toast when Stop failed: the server's reason when it gave one. */
export function stopFailedMessage(i18n: Translator, label: string, error: unknown): string {
	let reason = i18n.t('Something went wrong.');
	if (typeof error === 'string' && error.trim() !== '') {
		reason = error;
	} else if (error instanceof Error && error.message) {
		reason = error.message;
	}
	return i18n.t("Couldn't stop {{worker}}: {{reason}}", { worker: label, reason });
}

/**
 * The workers snapshot saved with one reply of a chat read from the server, or
 * null when it has none.
 */
export function savedReplyWorkers(chat: unknown, messageId: string): WorkersSnapshot | null {
	if (!chat || typeof chat !== 'object') return null;
	const content = (chat as { chat?: { history?: { messages?: Record<string, unknown> } } }).chat;
	const message = content?.history?.messages?.[messageId];
	if (!message || typeof message !== 'object') return null;
	return workersSnapshot((message as MessageWithWorkers).workers);
}

/** The workers that ended since the earlier snapshot: running then, or not listed yet. */
export function newlyEnded(earlier: WorkerItem[], current: WorkerItem[]): WorkerItem[] {
	const earlierStates = new Map<string, WorkerState>();
	for (const worker of earlier) {
		earlierStates.set(worker.id, worker.state);
	}
	const ended: WorkerItem[] = [];
	for (const worker of current) {
		if (worker.state === 'running') continue;
		const earlierState = earlierStates.get(worker.id);
		if (earlierState === undefined || earlierState === 'running') ended.push(worker);
	}
	return ended;
}

function endedLine(i18n: Translator, worker: WorkerItem): string {
	if (worker.state === 'done')
		return i18n.t('Worker finished: {{worker}}', { worker: worker.label });
	if (worker.state === 'failed')
		return i18n.t('Worker failed: {{worker}}', { worker: worker.label });
	return i18n.t('Worker stopped: {{worker}}', { worker: worker.label });
}

function endedCount(i18n: Translator, state: WorkerState, count: number): string {
	if (state === 'done') {
		if (count === 1) return i18n.t('1 worker finished');
		return i18n.t('{{count}} workers finished', { count });
	}
	if (state === 'failed') {
		if (count === 1) return i18n.t('1 worker failed');
		return i18n.t('{{count}} workers failed', { count });
	}
	if (count === 1) return i18n.t('1 worker stopped');
	return i18n.t('{{count}} workers stopped', { count });
}

/**
 * What the card's live region says about workers that ended since it last spoke:
 * "Worker finished: Read the parser" for one, or "2 workers finished, 1 worker
 * failed" for several. Empty for none.
 */
export function workersEndedAnnouncement(i18n: Translator, ended: WorkerItem[]): string {
	if (ended.length === 0) return '';
	if (ended.length === 1) return endedLine(i18n, ended[0]);
	const parts: string[] = [];
	for (const state of ['done', 'failed', 'stopped'] as WorkerState[]) {
		const count = ended.filter((worker) => worker.state === state).length;
		if (count > 0) parts.push(endedCount(i18n, state, count));
	}
	return parts.join(', ');
}
