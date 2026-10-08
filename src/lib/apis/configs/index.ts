import { goto } from '$app/navigation';
import { showOAuthConnect } from '$lib/stores/oauth-connect';
import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';
import type { Banner } from '$lib/types';

export const importConfig = async (token: string, config: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/import`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			config: config
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const exportConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/export`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getConnectionsConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/connections`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const setConnectionsConfig = async (token: string, config: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/connections`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...config
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getToolServerConnections = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/tool_servers`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const setToolServerConnections = async (token: string, connections: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/tool_servers`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...connections
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getTerminalServerConnections = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/terminal_servers`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const setTerminalServerConnections = async (token: string, connections: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/terminal_servers`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...connections
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

/**
 * Detect whether a terminal server URL points to an Orchestrator or a direct
 * Open Terminal instance.
 *
 * - GET {url}/api/v1/policies → 200 → "orchestrator"
 * - GET {url}/api/config      → 200 → "terminal"
 * - Neither                         → null
 */
export const detectTerminalServerType = async (
	url: string,
	key: string
): Promise<'orchestrator' | 'terminal' | null> => {
	const baseUrl = url.replace(/\/$/, '');
	const headers: Record<string, string> = {};
	if (key) {
		headers['Authorization'] = `Bearer ${key}`;
	}

	// Orchestrators expose a policies API; plain terminals don't.
	try {
		const res = await fetch(`${baseUrl}/api/v1/policies`, { headers });
		if (res.ok) return 'orchestrator';
	} catch {
		// ignore
	}

	// Fall back to open-terminal config endpoint.
	try {
		const res = await fetch(`${baseUrl}/api/config`, { headers });
		if (res.ok) return 'terminal';
	} catch {
		// ignore
	}

	return null;
};

/**
 * Create or update a policy on the orchestrator.
 * Proxied through the Open WebUI backend to keep API keys server-side.
 */
export const putOrchestratorPolicy = async (
	token: string,
	url: string,
	key: string,
	policyId: string,
	policyData: object,
	authType: string = 'bearer'
): Promise<object | null> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/terminal_servers/policy`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			url: url.replace(/\/$/, ''),
			key,
			auth_type: authType,
			policy_id: policyId,
			policy_data: policyData
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getOrchestratorPolicy = async (
	token: string,
	url: string,
	key: string,
	policyId: string,
	authType: string = 'bearer'
): Promise<any> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/terminal_servers/policy`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			url: url.replace(/\/$/, ''),
			key,
			auth_type: authType,
			policy_id: policyId
		})
	});
	if (!res.ok) {
		const body = await res.json();
		throw Object.assign(new Error(body.detail || 'Failed to read policy'), { status: res.status });
	}
	return res.json();
};

export const putOrchestratorLifecycle = async (
	token: string,
	url: string,
	key: string,
	policyId: string,
	lifecycleData: object,
	authType: string = 'bearer'
): Promise<object | null> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/terminal_servers/lifecycle`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			url: url.replace(/\/$/, ''),
			key,
			auth_type: authType,
			policy_id: policyId,
			lifecycle_data: lifecycleData
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getOrchestratorLifecycle = async (
	token: string,
	url: string,
	key: string,
	policyId: string,
	authType: string = 'bearer'
): Promise<any> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/terminal_servers/lifecycle`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			url: url.replace(/\/$/, ''),
			key,
			auth_type: authType,
			policy_id: policyId
		})
	});
	if (!res.ok) {
		const body = await res.json();
		throw Object.assign(new Error(body.detail || 'Failed to read lifecycle'), {
			status: res.status
		});
	}
	return res.json();
};

export const refreshOrchestratorTerminals = async (
	token: string,
	url: string,
	key: string,
	body: {
		user_id?: string;
		policy_id?: string;
		only_idle?: boolean;
		reset?: boolean;
	},
	authType: string = 'bearer'
): Promise<object | null> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/terminal_servers/refresh`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			url: url.replace(/\/$/, ''),
			key,
			auth_type: authType,
			...body
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

/**
 * Verify a terminal server connection via the backend proxy.
 * Used for system/admin connections to avoid CORS issues and API key exposure.
 */
export const verifyTerminalServerConnection = async (token: string, connection: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/terminal_servers/verify`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...connection
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const verifyToolServerConnection = async (token: string, connection: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/tool_servers/verify`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...connection
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

type RegisterOAuthClientForm = {
	url: string;
	client_id: string;
	client_name?: string;
	client_secret?: string;
	oauth_server_url?: string;
	oauth_scope?: string;
};

export const registerOAuthClient = async (
	token: string,
	formData: RegisterOAuthClientForm,
	type: null | string = null
) => {
	let error = null;

	const searchParams = type ? `?type=${type}` : '';
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/oauth/clients/register${searchParams}`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...formData
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getOAuthClientAuthorizationUrl = (clientId: string, type: null | string = null) => {
	const oauthClientId = type ? `${type}:${clientId}` : clientId;
	return `${WEBUI_BASE_URL}/oauth/clients/${oauthClientId}/authorize`;
};

export type OAuthConnectSession = {
	toolId: string;
	toolName: string;
	authorizePath: string;
	returnPath: string;
	started: boolean;
	createdAt: number;
};

const OAUTH_CONNECT_SESSION_KEY = 'buddyOAuthConnectSession';
const OAUTH_CANCELLATION_KEY = 'buddyOAuthCancellation';
const OAUTH_CONNECT_MAX_AGE = 30 * 60 * 1000;

function safeOAuthReturnPath(path: unknown): string {
	if (typeof path !== 'string') {
		return '/';
	}

	try {
		const url = new URL(path, window.location.origin);
		if (url.origin !== window.location.origin || url.pathname.startsWith('/auth/connect')) {
			return '/';
		}
		return `${url.pathname}${url.search}${url.hash}`;
	} catch {
		return '/';
	}
}

function safeOAuthAuthorizePath(path: unknown): string | null {
	if (typeof path !== 'string') {
		return null;
	}

	try {
		const url = new URL(path, window.location.origin);
		if (
			url.origin !== window.location.origin ||
			!url.pathname.startsWith('/oauth/clients/') ||
			!url.pathname.endsWith('/authorize') ||
			url.search ||
			url.hash
		) {
			return null;
		}
		return url.pathname;
	} catch {
		return null;
	}
}

function recentOAuthSession(createdAt: unknown): boolean {
	if (typeof createdAt !== 'number' || !Number.isFinite(createdAt)) {
		return false;
	}
	const age = Date.now() - createdAt;
	return age >= 0 && age < OAUTH_CONNECT_MAX_AGE;
}

export function getOAuthConnectSession(): OAuthConnectSession | null {
	try {
		const stored = sessionStorage.getItem(OAUTH_CONNECT_SESSION_KEY);
		if (!stored) {
			return null;
		}
		const session = JSON.parse(stored);
		const authorizePath = safeOAuthAuthorizePath(session.authorizePath);
		if (
			!session.toolId ||
			typeof session.toolId !== 'string' ||
			!authorizePath ||
			!recentOAuthSession(session.createdAt)
		) {
			sessionStorage.removeItem(OAUTH_CONNECT_SESSION_KEY);
			return null;
		}

		let toolName = 'your tool';
		if (typeof session.toolName === 'string' && session.toolName.trim()) {
			toolName = session.toolName;
		}
		return {
			toolId: session.toolId,
			toolName: toolName,
			authorizePath: authorizePath,
			returnPath: safeOAuthReturnPath(session.returnPath),
			started: session.started === true,
			createdAt: session.createdAt
		};
	} catch {
		sessionStorage.removeItem(OAUTH_CONNECT_SESSION_KEY);
		return null;
	}
}

export function restoreOAuthConnectOverlay(): void {
	const session = getOAuthConnectSession();
	const isLegacyGate = window.location.pathname === '/auth/connect';
	showOAuthConnect.set(Boolean(session) && !isLegacyGate);
}

function currentOAuthReturnPath(): string {
	return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

function markOAuthCancelled(toolId: string) {
	const cancellation = { toolId: toolId, createdAt: Date.now() };
	sessionStorage.setItem(OAUTH_CANCELLATION_KEY, JSON.stringify(cancellation));
}

// Suppress one automatic defaults check after Exit, then permit an explicit retry.
export function consumeOAuthCancellation(): string | null {
	const stored = sessionStorage.getItem(OAUTH_CANCELLATION_KEY);
	if (!stored) {
		return null;
	}
	sessionStorage.removeItem(OAUTH_CANCELLATION_KEY);
	try {
		const cancellation = JSON.parse(stored);
		if (typeof cancellation.toolId === 'string' && recentOAuthSession(cancellation.createdAt)) {
			return cancellation.toolId;
		}
	} catch {
		return null;
	}
	return null;
}

export const initiateOAuthRedirect = (tool: {
	id: string;
	name?: string;
	serverId: string;
	authType?: string | null;
}) => {
	const authorizePath = safeOAuthAuthorizePath(
		getOAuthClientAuthorizationUrl(tool.serverId, tool.authType ?? 'mcp')
	);
	if (!authorizePath) {
		throw new Error('The connection sign-in address is invalid.');
	}

	const returnPath = safeOAuthReturnPath(
		`${window.location.pathname}${window.location.search}${window.location.hash}`
	);
	const session: OAuthConnectSession = {
		toolId: tool.id,
		toolName: tool.name || 'your tool',
		authorizePath: authorizePath,
		returnPath: returnPath,
		started: false,
		createdAt: Date.now()
	};
	sessionStorage.setItem(OAUTH_CONNECT_SESSION_KEY, JSON.stringify(session));
	sessionStorage.removeItem(OAUTH_CANCELLATION_KEY);
	showOAuthConnect.set(true);
};

export function continueOAuthConnect(): boolean {
	const session = getOAuthConnectSession();
	if (!session) {
		return false;
	}

	session.started = true;
	session.createdAt = Date.now();
	sessionStorage.setItem(OAUTH_CONNECT_SESSION_KEY, JSON.stringify(session));
	sessionStorage.setItem('pendingOAuthToolId', session.toolId);
	sessionStorage.setItem('oauthRedirectInProgressToolId', session.toolId);
	// Keep this synchronous: Continue supplies a fresh user gesture on iOS.
	window.open(session.authorizePath, '_self', 'noopener');
	return true;
}

async function cancelStartedOAuthConnect(session: OAuthConnectSession): Promise<void> {
	if (!session.started) {
		return;
	}

	const token = localStorage.token;
	if (!token) {
		return;
	}

	const cancelPath = `${session.authorizePath.slice(0, -'/authorize'.length)}/cancel`;
	const controller = new AbortController();
	const timeout = setTimeout(() => controller.abort(), 1500);
	try {
		await fetch(cancelPath, {
			method: 'POST',
			headers: { Authorization: `Bearer ${token}` },
			signal: controller.signal
		});
	} catch {
		// A canceled or unavailable request must not prevent returning to the chat.
	} finally {
		clearTimeout(timeout);
	}
}

export async function cancelOAuthConnect(): Promise<void> {
	const session = getOAuthConnectSession();
	sessionStorage.removeItem(OAUTH_CONNECT_SESSION_KEY);
	sessionStorage.removeItem('pendingOAuthToolId');
	sessionStorage.removeItem('oauthRedirectInProgressToolId');
	if (session) {
		markOAuthCancelled(session.toolId);
		const stayOnCurrentPage = currentOAuthReturnPath() === session.returnPath;
		showOAuthConnect.set(false);
		await cancelStartedOAuthConnect(session);
		if (!stayOnCurrentPage) {
			await goto(session.returnPath, { replaceState: true });
		}
		return;
	}

	showOAuthConnect.set(false);
	if (window.location.pathname === '/auth/connect') {
		await goto('/', { replaceState: true });
	}
}

export async function completeOAuthConnectRedirect(): Promise<'success' | 'error' | null> {
	const session = getOAuthConnectSession();
	if (!session?.started || window.location.pathname !== '/') {
		return null;
	}

	const failed = new URLSearchParams(window.location.search).has('error');
	showOAuthConnect.set(false);
	sessionStorage.removeItem(OAUTH_CONNECT_SESSION_KEY);
	if (failed) {
		sessionStorage.removeItem('pendingOAuthToolId');
		sessionStorage.removeItem('oauthRedirectInProgressToolId');
		markOAuthCancelled(session.toolId);
	}

	await goto(session.returnPath, { replaceState: true });
	if (failed) {
		return 'error';
	}
	return 'success';
}

export const getCodeExecutionConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/code_execution`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const setCodeExecutionConfig = async (token: string, config: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/code_execution`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...config
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getModelsDefaults = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/models/defaults`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getModelsConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/models`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const setModelsConfig = async (token: string, config: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/models`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...config
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getSubagentsConfig = async (token: string) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/subagents`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	});
	if (!res.ok) throw await res.json();
	return res.json();
};

export const setSubagentsConfig = async (token: string, config: object) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/subagents`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(config)
	});
	if (!res.ok) throw await res.json();
	return res.json();
};

export const setDefaultPromptSuggestions = async (
	token: string,
	promptSuggestions: any[] | null,
	promptSuggestionsI18n: Record<string, any> = {}
) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/suggestions`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			suggestions: promptSuggestions,
			i18n: promptSuggestionsI18n
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getBanners = async (token: string): Promise<Banner[]> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/banners`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const setBanners = async (token: string, banners: Banner[]) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/banners`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			banners: banners
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
