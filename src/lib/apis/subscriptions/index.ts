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

export type SubscriptionProvider = {
	id: SubscriptionProviderId;
	name: string;
	settings: SubscriptionSettings;
	status: SubscriptionStatus;
	login: SubscriptionLogin;
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

const withExpectedMachine = (path: string, machineId?: string, machineRevision?: string) => {
	const guards = new URLSearchParams();
	if (machineId !== undefined) guards.set('expected_machine_id', machineId);
	if (machineRevision !== undefined) guards.set('expected_machine_revision', machineRevision);
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
	expectedMachineRevision?: string
): Promise<SubscriptionLogin> => {
	return subscriptionRequest(token, `/${providerId}/login`, 'POST', {
		method,
		expected_machine_id: expectedMachineId,
		expected_machine_revision: expectedMachineRevision
	});
};

export const getSubscriptionLogin = async (
	token: string,
	providerId: SubscriptionProviderId,
	expectedMachineId?: string,
	expectedMachineRevision?: string
): Promise<SubscriptionLogin> => {
	return subscriptionRequest(
		token,
		withExpectedMachine(`/${providerId}/login`, expectedMachineId, expectedMachineRevision)
	);
};

export const submitSubscriptionLoginCode = async (
	token: string,
	providerId: SubscriptionProviderId,
	code: string,
	expectedMachineId?: string,
	expectedMachineRevision?: string
): Promise<SubscriptionLogin> => {
	return subscriptionRequest(token, `/${providerId}/login/code`, 'POST', {
		code,
		expected_machine_id: expectedMachineId,
		expected_machine_revision: expectedMachineRevision
	});
};

export const cancelSubscriptionLogin = async (
	token: string,
	providerId: SubscriptionProviderId,
	expectedMachineId?: string,
	expectedMachineRevision?: string
): Promise<SubscriptionLogin> => {
	return subscriptionRequest(
		token,
		withExpectedMachine(`/${providerId}/login`, expectedMachineId, expectedMachineRevision),
		'DELETE'
	);
};

export const logoutSubscription = async (
	token: string,
	providerId: SubscriptionProviderId,
	expectedMachineId?: string,
	expectedMachineRevision?: string
): Promise<SubscriptionProvider> => {
	return subscriptionRequest(
		token,
		withExpectedMachine(`/${providerId}/logout`, expectedMachineId, expectedMachineRevision),
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
};

export type ProjectFolderComputer = {
	machine_id: string;
	name: string;
	// 'app': connected through the Buddy app; 'address': an older Runner added by address; 'server': This server.
	kind: 'app' | 'address' | 'server' | 'cloud';
	online: boolean | null;
	platform: string | null;
	can_browse: boolean;
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
	choice: { mode: ProjectFolderMode; paths: Record<string, string> }
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

export type AgentFileText = { path: string; content: string };

/**
 * The agent files in this person's project folder on This server for a chat
 * folder. Agents on Buddy app computers are read through the relay instead.
 * Throws the server's reason when there are none to read.
 */
export const getFolderAgentFiles = async (
	token: string,
	folderId: string
): Promise<{ files: AgentFileText[]; left_out: number }> => {
	return subscriptionRequest(token, `/folders/${encodeURIComponent(folderId)}/agents`);
};
