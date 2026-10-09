import { WEBUI_API_BASE_URL } from '$lib/constants';

export type SubscriptionProviderId = 'claude' | 'codex';
export type SubscriptionAccess = 'chat' | 'read' | 'full';

export type SubscriptionSettings = {
	enable: boolean;
	access: SubscriptionAccess;
	workspace: string;
	cli_path: string;
};

export type SubscriptionUsageWindow = {
	label: string;
	used_percent: number | null;
	resets_at: number | null;
};

export type SubscriptionStatus = {
	installed?: boolean;
	signed_in?: boolean;
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

export const getSubscriptions = async (
	token: string
): Promise<{ providers: SubscriptionProvider[] }> => {
	return subscriptionRequest(token, '/');
};

export const getSubscription = async (
	token: string,
	providerId: SubscriptionProviderId,
	refresh = false
): Promise<SubscriptionProvider> => {
	return subscriptionRequest(token, `/${providerId}?refresh=${refresh ? 'true' : 'false'}`);
};

export const updateSubscriptionConfig = async (
	token: string,
	providerId: SubscriptionProviderId,
	settings: Partial<SubscriptionSettings>
): Promise<SubscriptionProvider> => {
	return subscriptionRequest(token, `/${providerId}/config`, 'POST', settings);
};

export const startSubscriptionLogin = async (
	token: string,
	providerId: SubscriptionProviderId,
	method: 'browser' | 'device' = 'browser'
): Promise<SubscriptionLogin> => {
	return subscriptionRequest(token, `/${providerId}/login`, 'POST', { method });
};

export const getSubscriptionLogin = async (
	token: string,
	providerId: SubscriptionProviderId
): Promise<SubscriptionLogin> => {
	return subscriptionRequest(token, `/${providerId}/login`);
};

export const submitSubscriptionLoginCode = async (
	token: string,
	providerId: SubscriptionProviderId,
	code: string
): Promise<SubscriptionLogin> => {
	return subscriptionRequest(token, `/${providerId}/login/code`, 'POST', { code });
};

export const cancelSubscriptionLogin = async (
	token: string,
	providerId: SubscriptionProviderId
): Promise<SubscriptionLogin> => {
	return subscriptionRequest(token, `/${providerId}/login`, 'DELETE');
};

export const logoutSubscription = async (
	token: string,
	providerId: SubscriptionProviderId
): Promise<SubscriptionProvider> => {
	return subscriptionRequest(token, `/${providerId}/logout`, 'POST');
};
