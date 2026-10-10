import { WEBUI_API_BASE_URL } from '$lib/constants';

export type SubscriptionProviderId = 'claude' | 'codex';
export type SubscriptionAccess = 'chat' | 'read' | 'full';

export type SubscriptionSettings = {
	enable: boolean;
	access: SubscriptionAccess;
	workspace: string;
	cli_path: string;
	// 'local' (this server) or the id of a Buddy Runner machine.
	machine_id: string;
};

export type SubscriptionMachine = {
	id: string;
	name: string;
	// Changes when this registry entry points at a different execution target.
	revision?: string;
	// Null for "This server".
	url: string | null;
	// Explicit browser address; the backend URL may not be reachable from a browser.
	browser_url?: string | null;
	host?: { id: string; name: string; platform: string; version: string };
	capabilities?: SubscriptionMachineCapabilities;
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
	api_billing?: boolean;
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
};

const subscriptionRequest = async (
	token: string,
	path: string,
	method: 'GET' | 'POST' | 'DELETE' = 'GET',
	body?: object
) => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/subscriptions${path}`, {
		method,
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: body ? JSON.stringify(body) : undefined
	});

	const data = await response.json().catch(() => null);
	if (!response.ok) {
		throw data?.detail ?? `Request failed (${response.status})`;
	}
	return data;
};

const withExpectedMachine = (path: string, machineId?: string, machineRevision?: string) => {
	const guards = new URLSearchParams();
	if (machineId !== undefined) guards.set('expected_machine_id', machineId);
	if (machineRevision !== undefined) guards.set('expected_machine_revision', machineRevision);
	if (!guards.size) return path;
	return `${path}${path.includes('?') ? '&' : '?'}${guards}`;
};

export const getSubscriptions = async (
	token: string
): Promise<{ providers: SubscriptionProvider[]; machines: SubscriptionMachine[] }> => {
	return subscriptionRequest(token, '/');
};

export const saveSubscriptionMachine = async (
	token: string,
	machine: { id?: string; name: string; url: string; key?: string; browser_url?: string | null }
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
	key: string,
	browserUrl?: string
): Promise<SubscriptionMachineInfo> => {
	return subscriptionRequest(token, '/machines/verify', 'POST', {
		url,
		key,
		browser_url: browserUrl
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
