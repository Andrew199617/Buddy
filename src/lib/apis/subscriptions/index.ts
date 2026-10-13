import { WEBUI_API_BASE_URL } from '$lib/constants';

export type SubscriptionProviderId = 'claude' | 'codex';
export type SubscriptionAccess = 'chat' | 'read' | 'full';

export type SubscriptionSettings = {
	enable: boolean;
	access: SubscriptionAccess;
	workspace: string;
	cli_path: string;
	// One of the user's computers, 'local' (this server, for the instance owner), or '' before one is chosen.
	machine_id: string;
};

// How a provider signs in: the computer's own CLI sign-in, a one-year Claude token, or an API key.
export type SubscriptionCredentialMode = 'machine' | 'plan_token' | 'api_key';

// What Buddy shows about a stored token or key; never the secret itself.
export type StoredSubscriptionCredential = {
	fingerprint?: string | null;
	expires_at?: number | null;
	expires_soon?: boolean;
};

export type SubscriptionCredential = {
	mode: SubscriptionCredentialMode;
	stored: { plan_token?: StoredSubscriptionCredential; api_key?: StoredSubscriptionCredential };
};

export type SubscriptionPolicy = {
	user_machines: 'all' | 'groups' | 'admins';
	machine_group_ids: string[];
	plan_sign_in: boolean;
	api_keys: boolean;
	server_machine: boolean;
	// Where people get the Buddy app for Windows; '' when not set.
	desktop_download_url: string;
	owner_id?: string | null;
};

// The part of the policy every user sees.
export type SubscriptionPublicPolicy = {
	plan_sign_in: boolean;
	api_keys: boolean;
	desktop_download_url: string;
};

export type SubscriptionMachine = {
	id: string;
	name: string;
	// Changes when this registry entry points at a different execution target.
	revision?: string;
	// Null for "This server".
	url: string | null;
	// False when Buddy has no Runner key for this machine, as after a config import.
	has_key?: boolean;
	// Explicit browser address; the backend URL may not be reachable from a browser.
	host?: { id: string; name: string; platform: string; version: string };
	capabilities?: SubscriptionMachineCapabilities;
	// 'runner' for a computer connected through the Buddy app.
	kind?: 'runner';
	online?: boolean;
	created_at?: number;
	last_seen_at?: number;
	// Only in the administrators' list of every user's machines.
	owner_id?: string | null;
	owner_name?: string;
};

export type SubscriptionMachineCapabilities = {
	directories: boolean;
	files: boolean;
	workspaces: boolean;
	terminals: boolean;
	pty: boolean;
	providerExecution?: boolean;
};

export type SubscriptionMachineInfo = {
	ok: boolean;
	version: number;
	platform: string;
	hostname: string;
	chat_dir: string;
	default_workspace: string;
	host?: SubscriptionMachine['host'];
	capabilities?: SubscriptionMachineCapabilities;
};

export type SubscriptionUsageWindow = {
	label: string;
	used_percent: number | null;
	resets_at: number | null;
};

export type SubscriptionStatus = {
	// True while Buddy is still waiting for the CLI to answer the first check.
	checking?: boolean;
	installed?: boolean;
	signed_in?: boolean;
	// The sign-in does not match the one chosen for this provider, so turns are refused.
	api_billing?: boolean;
	billing?: 'plan' | 'api_key';
	cli_path?: string;
	version?: string;
	message?: string;
	account?: {
		email?: string | null;
		organization?: string | null;
		plan?: string | null;
		auth_method?: string | null;
	};
	usage?: {
		plan?: string | null;
		windows?: SubscriptionUsageWindow[];
		limit_reached?: boolean;
	} | null;
};

export type SubscriptionLogin = {
	state: 'idle' | 'waiting' | 'success' | 'error' | 'cancelled';
	method?: 'browser' | 'device' | 'code';
	url?: string | null;
	user_code?: string | null;
	needs_code?: boolean;
	message?: string | null;
};

// Whether a plan is signed in on one of the person's computers.
export type SubscriptionComputer = {
	machine_id: string;
	name: string;
	online: boolean;
	state: 'ready' | 'api_billing' | 'signed_out' | 'not_installed' | 'not_checked';
	message?: string;
};

export type SubscriptionProvider = {
	id: SubscriptionProviderId;
	name: string;
	settings: SubscriptionSettings;
	status: SubscriptionStatus;
	login: SubscriptionLogin;
	// The computer the sign-in in progress runs on.
	login_machine_id?: string | null;
	computers?: SubscriptionComputer[];
	models: { id: string; name: string }[];
	machine: { id: string; name: string; revision?: string };
	default_workspace: string;
	credential?: SubscriptionCredential;
};

type SubscriptionMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

const sendSubscriptionRequest = (
	token: string,
	path: string,
	method: SubscriptionMethod,
	body?: object
) => {
	return fetch(`${WEBUI_API_BASE_URL}/subscriptions${path}`, {
		method,
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: body ? JSON.stringify(body) : undefined
	});
};

const readSubscriptionResponse = async (response: Response) => {
	const data = await response.json().catch(() => null);
	if (!response.ok) {
		throw data?.detail ?? `Request failed (${response.status})`;
	}
	return data;
};

const subscriptionRequest = async (
	token: string,
	path: string,
	method: SubscriptionMethod = 'GET',
	body?: object
) => {
	return readSubscriptionResponse(await sendSubscriptionRequest(token, path, method, body));
};

// chosenMachineId names the computer a sign-in action is for; without it, the plan's own computer.
const withExpectedMachine = (
	path: string,
	machineId?: string,
	machineRevision?: string,
	chosenMachineId?: string
) => {
	const guards = new URLSearchParams();
	if (machineId !== undefined) guards.set('expected_machine_id', machineId);
	if (machineRevision !== undefined) guards.set('expected_machine_revision', machineRevision);
	if (chosenMachineId !== undefined) guards.set('machine_id', chosenMachineId);
	// URLSearchParams.size is missing before Safari 17 and Chrome 113; the string is not.
	const query = guards.toString();
	if (!query) return path;
	return `${path}${path.includes('?') ? '&' : '?'}${query}`;
};

export const getSubscriptions = async (
	token: string
): Promise<{
	allowed: boolean;
	providers: SubscriptionProvider[];
	machines: SubscriptionMachine[];
	policy: SubscriptionPublicPolicy;
}> => {
	return subscriptionRequest(token, '/');
};

export const saveSubscriptionMachine = async (
	token: string,
	machine: { id?: string; name: string; url: string; key?: string }
): Promise<{ machine: SubscriptionMachine; machines: SubscriptionMachine[] }> => {
	return subscriptionRequest(token, '/machines', 'POST', machine);
};

export const getSubscriptionMachines = async (
	token: string
): Promise<{ machines: SubscriptionMachine[] }> => {
	return subscriptionRequest(token, '/machines');
};

export const verifySubscriptionMachine = async (
	token: string,
	url: string,
	key: string
): Promise<SubscriptionMachineInfo> => {
	return subscriptionRequest(token, '/machines/verify', 'POST', { url, key });
};

export const renameSubscriptionMachine = async (
	token: string,
	machineId: string,
	name: string
): Promise<{ machines: SubscriptionMachine[] }> => {
	return subscriptionRequest(token, `/machines/${encodeURIComponent(machineId)}`, 'PATCH', {
		name
	});
};

export const deleteSubscriptionMachine = async (
	token: string,
	machineId: string
): Promise<{ machines: SubscriptionMachine[] }> => {
	return subscriptionRequest(token, `/machines/${encodeURIComponent(machineId)}`, 'DELETE');
};

export const getSubscription = async (
	token: string,
	providerId: SubscriptionProviderId,
	refresh = false,
	expectedMachineId?: string,
	expectedMachineRevision?: string
): Promise<SubscriptionProvider> => {
	return subscriptionRequest(
		token,
		withExpectedMachine(
			`/${providerId}?refresh=${refresh ? 'true' : 'false'}`,
			expectedMachineId,
			expectedMachineRevision
		)
	);
};

export const updateSubscriptionConfig = async (
	token: string,
	providerId: SubscriptionProviderId,
	settings: Partial<SubscriptionSettings>,
	expectedMachineId?: string,
	expectedMachineRevision?: string
): Promise<SubscriptionProvider> => {
	return subscriptionRequest(token, `/${providerId}/config`, 'POST', {
		...settings,
		expected_machine_id: expectedMachineId,
		expected_machine_revision: expectedMachineRevision
	});
};

export const startSubscriptionLogin = async (
	token: string,
	providerId: SubscriptionProviderId,
	method: 'browser' | 'device' = 'browser',
	expectedMachineId?: string,
	expectedMachineRevision?: string,
	machineId?: string
): Promise<SubscriptionLogin> => {
	return subscriptionRequest(token, `/${providerId}/login`, 'POST', {
		method,
		expected_machine_id: expectedMachineId,
		expected_machine_revision: expectedMachineRevision,
		machine_id: machineId
	});
};

export const getSubscriptionLogin = async (
	token: string,
	providerId: SubscriptionProviderId,
	expectedMachineId?: string,
	expectedMachineRevision?: string,
	machineId?: string
): Promise<SubscriptionLogin> => {
	return subscriptionRequest(
		token,
		withExpectedMachine(
			`/${providerId}/login`,
			expectedMachineId,
			expectedMachineRevision,
			machineId
		)
	);
};

export const submitSubscriptionLoginCode = async (
	token: string,
	providerId: SubscriptionProviderId,
	code: string,
	expectedMachineId?: string,
	expectedMachineRevision?: string,
	machineId?: string
): Promise<SubscriptionLogin> => {
	return subscriptionRequest(token, `/${providerId}/login/code`, 'POST', {
		code,
		expected_machine_id: expectedMachineId,
		expected_machine_revision: expectedMachineRevision,
		machine_id: machineId
	});
};

export const cancelSubscriptionLogin = async (
	token: string,
	providerId: SubscriptionProviderId,
	expectedMachineId?: string,
	expectedMachineRevision?: string,
	machineId?: string
): Promise<SubscriptionLogin> => {
	return subscriptionRequest(
		token,
		withExpectedMachine(
			`/${providerId}/login`,
			expectedMachineId,
			expectedMachineRevision,
			machineId
		),
		'DELETE'
	);
};

export const logoutSubscription = async (
	token: string,
	providerId: SubscriptionProviderId,
	expectedMachineId?: string,
	expectedMachineRevision?: string,
	machineId?: string
): Promise<SubscriptionProvider> => {
	return subscriptionRequest(
		token,
		withExpectedMachine(
			`/${providerId}/logout`,
			expectedMachineId,
			expectedMachineRevision,
			machineId
		),
		'POST'
	);
};

export const saveSubscriptionApiKey = async (
	token: string,
	providerId: SubscriptionProviderId,
	key: string
): Promise<SubscriptionProvider> => {
	return subscriptionRequest(token, `/${providerId}/credentials/api_key`, 'PUT', { key });
};

export const saveClaudePlanToken = async (
	token: string,
	planToken: string
): Promise<SubscriptionProvider> => {
	return subscriptionRequest(token, '/claude/credentials/plan_token', 'PUT', { token: planToken });
};

export const deleteSubscriptionCredential = async (
	token: string,
	providerId: SubscriptionProviderId,
	kind: 'plan_token' | 'api_key'
): Promise<SubscriptionProvider> => {
	return subscriptionRequest(token, `/${providerId}/credentials/${kind}`, 'DELETE');
};

export const useMachineSignIn = async (
	token: string,
	providerId: SubscriptionProviderId
): Promise<SubscriptionProvider> => {
	return subscriptionRequest(token, `/${providerId}/credentials/machine`, 'POST');
};

export const useStoredSubscriptionCredential = async (
	token: string,
	providerId: SubscriptionProviderId,
	kind: 'plan_token' | 'api_key'
): Promise<SubscriptionProvider> => {
	return subscriptionRequest(token, `/${providerId}/credentials/${kind}/use`, 'POST');
};

export const getAllSubscriptionMachines = async (
	token: string
): Promise<{ machines: SubscriptionMachine[] }> => {
	return subscriptionRequest(token, '/admin/machines');
};

export const deleteAnySubscriptionMachine = async (
	token: string,
	machineId: string
): Promise<{ machines: SubscriptionMachine[] }> => {
	return subscriptionRequest(token, `/admin/machines/${encodeURIComponent(machineId)}`, 'DELETE');
};

export const getSubscriptionPolicy = async (token: string): Promise<SubscriptionPolicy> => {
	return subscriptionRequest(token, '/admin/policy');
};

export const saveSubscriptionPolicy = async (
	token: string,
	policy: Partial<SubscriptionPolicy>
): Promise<SubscriptionPolicy> => {
	return subscriptionRequest(token, '/admin/policy', 'POST', policy);
};

// --- Project folders for chat folders ---------------------------------------
//
// Each person chooses, per chat folder, where Claude and ChatGPT start on their
// computers. The choice is theirs alone, even in a shared folder.

export type ProjectFolderMode = 'auto' | 'folder' | 'chat';

export type ProjectFolderPath = {
	path: string;
	// Set when the computer was set up again after the path was chosen.
	problem: 'computer_changed' | null;
};

export type ProjectFolderChoice = {
	folder_id: string;
	folder_name: string;
	mode: ProjectFolderMode;
	// Machine ID to path; only for mode 'folder'.
	paths: Record<string, ProjectFolderPath>;
	// What new chats in the folder start with; '' follows the usual default.
	machine_id: string;
	access: SubscriptionAccess | '';
	// More folders new chats here can pick from, per machine ID.
	offered: Record<string, ProjectFolderPath[]>;
};

export type FolderProjectChoice = {
	mode: ProjectFolderMode;
	paths: Record<string, string>;
	machine_id?: string;
	access?: SubscriptionAccess | '';
	offered?: Record<string, string[]>;
};

export type ProjectFolderComputer = {
	machine_id: string;
	name: string;
	// 'app': connected through the Buddy app; 'address': an older Runner added by address; 'server': This server.
	kind: 'app' | 'address' | 'server' | 'cloud';
	online: boolean | null;
	platform: string | null;
	can_browse: boolean;
	// Whether its folders can be browsed and suggested: only with the Buddy app, when allowed there.
	folders: 'on' | 'off' | 'offline' | 'unsupported';
	providers: { id: SubscriptionProviderId; name: string; access: SubscriptionAccess }[];
	recent_paths: string[];
};

// One of the person's plans, as their chat folder settings see it.
export type FolderPlan = {
	id: SubscriptionProviderId;
	name: string;
	// Off plans answer in no chat.
	enable: boolean;
	access: SubscriptionAccess;
	// The computer it runs on, with the revision an access change must confirm,
	// or null when it has none it can use.
	machine: { id: string; name: string; revision: string } | null;
};

export type FolderProject = {
	folder_id: string;
	folder_name: string;
	// The person's own choice for this folder, if any.
	own: ProjectFolderChoice | null;
	// The nearest parent folder's choice that applies otherwise.
	inherited: ProjectFolderChoice | null;
	// The folder adds instructions others can change, so its chats stay chat-only
	// until this person makes their own choice for it.
	shared_instructions: boolean;
	has_plans: boolean;
	plans: FolderPlan[];
	computers: ProjectFolderComputer[];
};

/**
 * The person's project folder for a chat folder, or null when they may not use
 * computers (403) or can't read the folder (404). Other failures throw.
 */
export const getFolderProject = async (
	token: string,
	folderId: string
): Promise<FolderProject | null> => {
	const path = `/folders/${encodeURIComponent(folderId)}/project`;
	const response = await sendSubscriptionRequest(token, path, 'GET');
	if (response.status === 403 || response.status === 404) return null;
	return readSubscriptionResponse(response);
};

export const saveFolderProject = async (
	token: string,
	folderId: string,
	choice: FolderProjectChoice
): Promise<FolderProject> => {
	return subscriptionRequest(
		token,
		`/folders/${encodeURIComponent(folderId)}/project`,
		'PUT',
		choice
	);
};

export const clearFolderProject = async (
	token: string,
	folderId: string
): Promise<FolderProject> => {
	return subscriptionRequest(token, `/folders/${encodeURIComponent(folderId)}/project`, 'DELETE');
};

// Checks the folder exists on that computer; returns its tidy form.
export const checkMachineFolder = async (
	token: string,
	machineId: string,
	path: string
): Promise<{ path: string }> => {
	return subscriptionRequest(
		token,
		`/machines/${encodeURIComponent(machineId)}/folders/check`,
		'POST',
		{
			path
		}
	);
};

// Browsing a computer's folders to choose where a chat works. Folder names
// only; the server checks every name and builds each path itself.

export type FolderEntry = {
	name: string;
	path: string;
	hidden: boolean;
	// A symlink or junction to another folder.
	link: boolean;
	// False for a folder that can't be a project folder, such as a whole drive.
	selectable: boolean;
};

export type FolderRoot = {
	name: string;
	path: string;
	kind: 'drive' | 'remote_drive' | 'removable' | 'place';
};

export type FolderListing = {
	// '' for the drives and places to start from.
	path: string;
	parent: string | null;
	roots: FolderRoot[];
	entries: FolderEntry[];
	truncated: boolean;
};

export type FolderSuggestion = {
	path: string;
	sources: ('claude' | 'codex')[];
	// Seconds since 1970, rounded down to the day.
	last_used: number;
};

export type FolderSuggestions = {
	// 'off': not allowed in the Buddy app there; 'unsupported': no Buddy app, or an older one.
	state: 'ok' | 'off' | 'offline' | 'unsupported';
	folders: FolderSuggestion[];
};

export const listMachineFolders = async (
	token: string,
	machineId: string,
	options: { path?: string; hidden?: boolean; prefix?: string } = {}
): Promise<FolderListing> => {
	const query = new URLSearchParams({
		path: options.path ?? '',
		hidden: String(options.hidden === true),
		prefix: options.prefix ?? ''
	});
	return subscriptionRequest(token, `/machines/${encodeURIComponent(machineId)}/folders?${query}`);
};

export const getSuggestedFolders = async (
	token: string,
	machineId: string
): Promise<FolderSuggestions> => {
	return subscriptionRequest(token, `/machines/${encodeURIComponent(machineId)}/folders/suggested`);
};

// --- Where chats run ---------------------------------------------------------------

export type RunTargetFolder = { mode: 'path'; path: string } | { mode: 'auto' };

// What the composer's bar sends when a chat starts.
export type RunTargetRequest =
	| { kind: 'detached'; access: 'chat' }
	| {
			kind: 'computer';
			machine_id: string;
			// The computer's revision the bar showed; the server compares it.
			expected_revision: string | null;
			folder: RunTargetFolder;
			access: SubscriptionAccess;
			acknowledged_instructions_folder_id: string | null;
	  };

export type RunTargetPlanState =
	| 'ready'
	| 'unknown'
	| 'checking'
	| 'sign_in'
	| 'not_installed'
	| 'api_billing'
	| 'offline'
	| 'not_allowed';

export type RunTargetComputer = Omit<ProjectFolderComputer, 'providers'> & {
	revision: string | null;
	providers: Partial<
		Record<SubscriptionProviderId, { state: RunTargetPlanState; message: string | null }>
	>;
};

export type RunTargetDefault = {
	kind: 'computer' | 'detached';
	machine_id: string | null;
	folder: RunTargetFolder | null;
	// null means each plan's own access.
	access: SubscriptionAccess | null;
	from: 'chat_folder' | 'this_pc' | 'last_used' | 'plan' | 'first_ready' | 'none';
	// The chat folder's shared instructions keep it chat only until the person acknowledges them.
	access_locked: boolean;
};

export type RunTargetOptions = {
	allowed: boolean;
	has_plans: boolean;
	plan_access?: Partial<Record<SubscriptionProviderId, SubscriptionAccess>>;
	computers?: RunTargetComputer[];
	recent?: { machine_id: string; path: string; last_used: number }[];
	chat_folder?: {
		id: string;
		name: string;
		mode: ProjectFolderMode | null;
		paths: Record<string, string>;
		offered: Record<string, string[]>;
		shared_instructions: boolean;
	} | null;
	default?: RunTargetDefault;
};

export type ChatRunTargetDetails = {
	kind: 'computer' | 'detached';
	machine_id: string | null;
	computer: { name: string; kind: ProjectFolderComputer['kind']; online: boolean | null } | null;
	folder_mode: 'path' | 'auto' | 'none';
	path: string | null;
	access: SubscriptionAccess;
	source: 'bar' | 'default' | 'copy' | 'refused';
	note: string | null;
	note_message?: string | null;
	problem: 'removed' | 'reset' | 'server_off' | 'offline' | null;
};

export type ChatRunTarget =
	| ({ bound: true } & ChatRunTargetDetails)
	| { bound: false; would_bind: ChatRunTargetDetails };

export const getRunTargetOptions = async (
	token: string,
	options: { folderId?: string | null; thisMachineId?: string | null } = {}
): Promise<RunTargetOptions> => {
	const query = new URLSearchParams();
	if (options.folderId) query.set('folder_id', options.folderId);
	if (options.thisMachineId) query.set('this_machine_id', options.thisMachineId);
	const search = query.toString();
	let path = '/run-target/options';
	if (search) path = `${path}?${search}`;
	return subscriptionRequest(token, path);
};

export const getRunTargetReadiness = async (
	token: string,
	providerId: SubscriptionProviderId,
	machineId: string
): Promise<{ state: RunTargetPlanState; message: string | null }> => {
	const query = new URLSearchParams({ provider: providerId, machine_id: machineId });
	return subscriptionRequest(token, `/run-target/readiness?${query}`);
};

/** Where the person's chat runs, or null when it isn't theirs (404). */
export const getChatRunTarget = async (
	token: string,
	chatId: string
): Promise<ChatRunTarget | null> => {
	const path = `/chats/${encodeURIComponent(chatId)}/run-target`;
	const response = await sendSubscriptionRequest(token, path, 'GET');
	if (response.status === 404) return null;
	return readSubscriptionResponse(response);
};

// --- Each computer's own start folder and CLI paths -------------------------------

export type ComputerSettings = {
	machine_id: string;
	revision: string;
	// False while the computer still uses what a plan saved for it.
	saved: boolean;
	start_folder: string;
	cli_paths: Record<SubscriptionProviderId, string>;
	// What each plan saved for this computer before it had its own; turns still use these.
	from_plans: Partial<Record<SubscriptionProviderId, { start_folder: string; cli_path: string }>>;
};

export const getComputerSettings = async (
	token: string,
	machineId: string
): Promise<ComputerSettings> => {
	return subscriptionRequest(token, `/machines/${encodeURIComponent(machineId)}/settings`);
};

export const saveComputerSettings = async (
	token: string,
	machineId: string,
	settings: {
		expected_revision: string;
		start_folder: string;
		cli_paths: Partial<Record<SubscriptionProviderId, string>>;
	}
): Promise<ComputerSettings> => {
	return subscriptionRequest(
		token,
		`/machines/${encodeURIComponent(machineId)}/settings`,
		'PUT',
		settings
	);
};

export type AgentFileText = { path: string; content: string };

/**
 * The agent files in a folder on This server, the chat's own folder. Agents on
 * Buddy app computers are read through the relay instead. Throws the server's
 * reason when they can't be read.
 */
export const getServerAgentFiles = async (
	token: string,
	machineId: string,
	path: string
): Promise<{ files: AgentFileText[]; left_out: number }> => {
	const query = new URLSearchParams({ path });
	return subscriptionRequest(token, `/machines/${encodeURIComponent(machineId)}/agents?${query}`);
};
