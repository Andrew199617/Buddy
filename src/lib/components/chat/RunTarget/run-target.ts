// Where a new chat runs, as the composer's bar edits it: pure helpers with no
// DOM or network. The server checks whatever the bar sends.

import type {
	ChatRunTargetDetails,
	RunTargetComputer,
	RunTargetFolder,
	RunTargetOptions,
	RunTargetPlanState,
	RunTargetRequest,
	SubscriptionAccess,
	SubscriptionProviderId
} from '$lib/apis/subscriptions';

const ACCESS_ORDER: SubscriptionAccess[] = ['chat', 'read', 'full'];

export type RunTargetDraft = {
	kind: 'computer' | 'detached';
	machineId: string | null;
	expectedRevision: string | null;
	folder: RunTargetFolder | null;
	access: SubscriptionAccess;
	// The person ticked "Use files in this chat anyway" for a chat folder's shared instructions.
	acknowledgedFolderId: string | null;
	// Fields the person changed; defaults never overwrite them.
	touched: { computer: boolean; folder: boolean; access: boolean };
};

type ModelLike = { id: string; owned_by?: string; subscription?: { provider?: string } };

/** The plans behind the selected models: only Claude and ChatGPT models run on computers. */
export function planProvidersFor(
	modelIds: string[],
	models: ModelLike[]
): SubscriptionProviderId[] {
	const providers: SubscriptionProviderId[] = [];
	for (const modelId of modelIds) {
		const model = models.find((candidate) => candidate.id === modelId);
		if (model?.owned_by !== 'subscription') continue;
		const provider = model.subscription?.provider;
		if (provider !== 'claude' && provider !== 'codex') continue;
		if (!providers.includes(provider)) providers.push(provider);
	}
	return providers;
}

export function lowestAccess(
	first: SubscriptionAccess,
	second: SubscriptionAccess
): SubscriptionAccess {
	if (ACCESS_ORDER.indexOf(first) < ACCESS_ORDER.indexOf(second)) return first;
	return second;
}

/** The plans' own access for new chats: the lowest of the selected plans, or of every plan that is on. */
export function defaultAccess(
	options: RunTargetOptions,
	providers: SubscriptionProviderId[]
): SubscriptionAccess {
	const planAccess = options.plan_access ?? {};
	let considered = providers.filter((provider) => planAccess[provider] !== undefined);
	if (considered.length === 0) {
		considered = Object.keys(planAccess) as SubscriptionProviderId[];
	}
	let access: SubscriptionAccess | null = null;
	for (const provider of considered) {
		const planDefault = planAccess[provider];
		if (!planDefault) continue;
		if (access === null) {
			access = planDefault;
		} else {
			access = lowestAccess(access, planDefault);
		}
	}
	return access ?? 'chat';
}

function computerById(
	options: RunTargetOptions,
	machineId: string | null
): RunTargetComputer | null {
	if (!machineId) return null;
	return options.computers?.find((computer) => computer.machine_id === machineId) ?? null;
}

function detachedDraft(touched: RunTargetDraft['touched']): RunTargetDraft {
	return {
		kind: 'detached',
		machineId: null,
		expectedRevision: null,
		folder: null,
		access: 'chat',
		acknowledgedFolderId: null,
		touched
	};
}

/** Where a new chat runs when the person changes nothing, as the server worked it out. */
export function draftFromDefault(
	options: RunTargetOptions,
	providers: SubscriptionProviderId[]
): RunTargetDraft {
	const untouched = { computer: false, folder: false, access: false };
	const chosen = options.default;
	const computer = computerById(options, chosen?.machine_id ?? null);
	if (!chosen || chosen.kind === 'detached' || !computer) return detachedDraft(untouched);
	let access = chosen.access;
	if (access === null) access = defaultAccess(options, providers);
	if (chosen.access_locked) access = 'chat';
	return {
		kind: 'computer',
		machineId: computer.machine_id,
		expectedRevision: computer.revision,
		folder: chosen.folder ?? { mode: 'auto' },
		access,
		acknowledgedFolderId: null,
		touched: untouched
	};
}

/** Defaults again for the fields the person hasn't changed, after the chat folder or model changed. */
export function refreshUntouched(
	draft: RunTargetDraft,
	options: RunTargetOptions,
	providers: SubscriptionProviderId[]
): RunTargetDraft {
	const fresh = draftFromDefault(options, providers);
	if (!draft.touched.computer) {
		return { ...fresh, touched: draft.touched };
	}
	const next = { ...draft };
	if (!draft.touched.folder && next.kind === 'computer' && fresh.machineId === next.machineId) {
		next.folder = fresh.folder;
	}
	if (!draft.touched.access && next.kind === 'computer') {
		next.access = defaultAccess(options, providers);
	}
	return next;
}

/** The draft after picking a computer: its folder resets to that computer's default, or to AI chooses. */
export function chooseComputer(
	draft: RunTargetDraft,
	options: RunTargetOptions,
	providers: SubscriptionProviderId[],
	machineId: string | null
): RunTargetDraft {
	const touched = { ...draft.touched, computer: true };
	const computer = computerById(options, machineId);
	if (!computer) return detachedDraft(touched);
	let folder: RunTargetFolder = { mode: 'auto' };
	const folderDefault = options.chat_folder?.paths?.[computer.machine_id];
	if (folderDefault) folder = { mode: 'path', path: folderDefault };
	if (draft.folder?.mode === 'path' && draft.machineId === computer.machine_id) {
		folder = draft.folder;
	}
	let access = draft.access;
	if (draft.kind === 'detached' && !draft.touched.access) {
		access = defaultAccess(options, providers);
	}
	if (options.default?.access_locked && draft.acknowledgedFolderId === null) access = 'chat';
	return {
		...draft,
		kind: 'computer',
		machineId: computer.machine_id,
		expectedRevision: computer.revision,
		folder,
		access,
		touched
	};
}

/** What the first send carries. The acknowledgement goes only when the box was ticked. */
export function requestFromDraft(draft: RunTargetDraft): RunTargetRequest {
	if (draft.kind === 'detached' || !draft.machineId) return { kind: 'detached', access: 'chat' };
	return {
		kind: 'computer',
		machine_id: draft.machineId,
		expected_revision: draft.expectedRevision,
		folder: draft.folder ?? { mode: 'auto' },
		access: draft.access,
		acknowledged_instructions_folder_id: draft.acknowledgedFolderId
	};
}

/** A draft from a started chat's target, for "New chat here". */
export function draftFromTarget(
	details: ChatRunTargetDetails,
	options: RunTargetOptions
): RunTargetDraft {
	const touched = { computer: true, folder: true, access: true };
	const computer = computerById(options, details.machine_id);
	if (details.kind === 'detached' || !computer) return detachedDraft(touched);
	let folder: RunTargetFolder = { mode: 'auto' };
	if (details.folder_mode === 'path' && details.path) folder = { mode: 'path', path: details.path };
	return {
		kind: 'computer',
		machineId: computer.machine_id,
		expectedRevision: computer.revision,
		folder,
		access: details.access,
		acknowledgedFolderId: null,
		touched
	};
}

export type ComputerPlace = 'local' | 'remote' | 'server' | 'detached';

/** "Local" only ever means the computer showing this page in the Buddy app. */
export function computerPlace(
	computer: Pick<RunTargetComputer, 'machine_id' | 'kind'> | null,
	thisPcMachineId: string | null
): ComputerPlace {
	if (!computer) return 'detached';
	if (computer.kind === 'server') return 'server';
	if (thisPcMachineId !== null && computer.machine_id === thisPcMachineId) return 'local';
	return 'remote';
}

const STATE_ORDER: RunTargetPlanState[] = [
	'ready',
	'unknown',
	'checking',
	'api_billing',
	'not_installed',
	'sign_in',
	'not_allowed',
	'offline'
];

/** The least ready state among the selected plans on a computer; every plan that is on when none is selected. */
export function computerReadiness(
	computer: RunTargetComputer,
	providers: SubscriptionProviderId[]
): RunTargetPlanState {
	let considered = providers.filter((provider) => computer.providers[provider] !== undefined);
	if (considered.length === 0) {
		considered = Object.keys(computer.providers) as SubscriptionProviderId[];
	}
	let worst: RunTargetPlanState = 'ready';
	for (const provider of considered) {
		const state = computer.providers[provider]?.state ?? 'unknown';
		if (STATE_ORDER.indexOf(state) > STATE_ORDER.indexOf(worst)) worst = state;
	}
	return worst;
}

/** The plan whose state is shown first: the first selected plan that is not ready there. */
export function providerNeedingAttention(
	computer: RunTargetComputer,
	providers: SubscriptionProviderId[]
): SubscriptionProviderId | null {
	for (const provider of providers) {
		const state = computer.providers[provider]?.state;
		if (state && state !== 'ready') return provider;
	}
	return null;
}

export type FolderLabel =
	| { kind: 'path'; name: string; path: string }
	| { kind: 'auto' }
	| { kind: 'none' };

export function folderLabel(draft: RunTargetDraft): FolderLabel {
	if (draft.kind === 'detached' || !draft.folder) return { kind: 'none' };
	if (draft.folder.mode === 'auto') return { kind: 'auto' };
	const parts = draft.folder.path.split(/[\\/]/).filter(Boolean);
	const name = parts[parts.length - 1] ?? draft.folder.path;
	return { kind: 'path', name, path: draft.folder.path };
}
