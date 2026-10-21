// The custom agent a chat uses: what is saved with the chat, and how its
// instructions join the system prompt. No requests here.

export type ChatAgent = {
	name: string;
	description: string;
	// Where the agent file is, relative to the project folder, with '/' separators.
	path: string;
	// The file's Markdown body, read when the agent was chosen.
	instructions: string;
};

function isText(value: unknown): value is string {
	return typeof value === 'string';
}

/** The agent saved with a chat, or null when there is none or it is not usable. */
export function savedAgent(value: unknown): ChatAgent | null {
	if (!value || typeof value !== 'object') return null;
	const saved = value as Record<string, unknown>;
	if (!isText(saved.name) || !isText(saved.path) || !isText(saved.instructions)) return null;
	if (saved.name.trim() === '' || saved.path.trim() === '') return null;
	let description = '';
	if (isText(saved.description)) description = saved.description;
	return {
		name: saved.name,
		description,
		path: saved.path,
		instructions: saved.instructions
	};
}

/**
 * A chat's contents without its agent, for anything that leaves this server, such
 * as the community share. The instructions come from the owner's private files.
 */
export function withoutAgent<T extends Record<string, unknown>>(contents: T): Omit<T, 'agent'> {
	const rest: Record<string, unknown> = { ...contents };
	delete rest.agent;
	return rest as Omit<T, 'agent'>;
}

type LoadedChat = { user_id?: string; chat?: { agent?: unknown } } | null;

/**
 * The loaded chat's agent, only for the person who chose it. Someone opening a
 * chat in a folder shared with them never sends its owner's instructions.
 */
export function ownAgent(chat: LoadedChat, userId: string | undefined): ChatAgent | null {
	if (!chat || !userId || chat.user_id !== userId) return null;
	return savedAgent(chat.chat?.agent);
}

/**
 * Runs each save after the one before it, so when choices come quickly the last
 * choice is also the last one saved. A failed save doesn't stop later ones.
 */
export function createSaveQueue(): (save: () => Promise<void>) => Promise<void> {
	let last: Promise<void> = Promise.resolve();
	return (save) => {
		const next = last.catch(() => {}).then(save);
		last = next;
		return next;
	};
}

/** The chat's system prompt with the agent's instructions after it. */
export function systemWithAgent(system: string, agent: ChatAgent | null): string {
	const instructions = agent?.instructions.trim() ?? '';
	if (!agent || instructions === '') return system;
	const agentPart =
		`The user chose the "${agent.name}" custom agent, from ${agent.path} in the project folder. ` +
		`Follow its instructions:\n\n${instructions}`;
	if (system.trim() === '') return agentPart;
	return `${system}\n\n${agentPart}`;
}
