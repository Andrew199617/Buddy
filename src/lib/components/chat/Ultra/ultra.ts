// Ultra in the composer: whether a chat can use it, the choices for its workers,
// what a chat saves and sends, and the text its workers card shows. Plain helpers
// with no DOM or network; the server checks whatever the composer sends.

import { isChannelChatId, isTemporaryChatId } from '$lib/utils/chatId';

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
 * The request's `ultra` field, or undefined while Ultra is off so the field is
 * left out. Pass the choice through fitUltra first, and send it only while
 * availability says Ultra is available.
 */
export function requestUltra(state: UltraState): UltraRequest | undefined {
	if (!state.on) return undefined;
	return { worker_model: state.worker_model, worker_effort: state.worker_effort };
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
