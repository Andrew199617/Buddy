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

type LoadedChat = { user_id?: string; chat?: { agent?: unknown } } | null;

/**
 * The loaded chat's agent, only for the person who chose it. Someone opening a
 * chat in a folder shared with them never sends its owner's instructions.
 */
export function ownAgent(chat: LoadedChat, userId: string | undefined): ChatAgent | null {
	if (!chat || !userId || chat.user_id !== userId) return null;
	return savedAgent(chat.chat?.agent);
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
