import { WEBUI_API_BASE_URL } from '$lib/constants';

// A 60-second, single-use token the Buddy app trades for this computer's credential.
export type RunnerEnrollment = { token: string; expires_in: number };

export const createRunnerEnrollment = async (token: string): Promise<RunnerEnrollment> => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/runners/enrollments`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
	});
	const data = await response.json().catch(() => null);
	if (!response.ok) {
		throw data?.detail ?? `Request failed (${response.status})`;
	}
	return data;
};
