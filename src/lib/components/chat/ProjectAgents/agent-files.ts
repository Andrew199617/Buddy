// Where VS Code and Claude Code keep a project's custom agents, and what one
// agent file says. No requests here.

import { parse as parseYaml } from 'yaml';

import type { ChatAgent } from './chat-agent';

// The folders inside a project folder that hold agents. .github/agents holds VS Code and GitHub Copilot agents (*.agent.md, or any
// .md), .claude/agents holds Claude Code subagents, and .github/chatmodes holds
// the custom chat modes VS Code used before agents.
export const AGENT_FOLDERS = ['.github/agents', '.claude/agents', '.github/chatmodes'];

const LEGACY_CHAT_MODES = '.github/chatmodes';

export function isAgentFile(folder: string, fileName: string): boolean {
	const lower = fileName.toLowerCase();
	if (folder === LEGACY_CHAT_MODES) return lower.endsWith('.chatmode.md');
	return lower.endsWith('.md');
}

/** The name VS Code shows for an agent file without a name of its own. */
export function nameFromFileName(fileName: string): string {
	const lower = fileName.toLowerCase();
	for (const ending of ['.agent.md', '.chatmode.md', '.md']) {
		if (lower.endsWith(ending) && lower.length > ending.length) {
			return fileName.slice(0, fileName.length - ending.length);
		}
	}
	return fileName;
}

/** The YAML header between the opening and closing '---' lines, and the Markdown after it. */
export function splitFrontmatter(text: string): { header: string; body: string } {
	const lines = text.replace(/^﻿/, '').split(/\r?\n/);
	if (lines[0]?.trim() !== '---') return { header: '', body: lines.join('\n') };
	let end = -1;
	for (let index = 1; index < lines.length; index++) {
		if (lines[index].trim() === '---') {
			end = index;
			break;
		}
	}
	if (end === -1) return { header: '', body: lines.join('\n') };
	return {
		header: lines.slice(1, end).join('\n'),
		body: lines.slice(end + 1).join('\n')
	};
}

function readHeader(header: string): Record<string, unknown> {
	if (header.trim() === '') return {};
	try {
		const parsed = parseYaml(header);
		if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
			return parsed as Record<string, unknown>;
		}
	} catch {
		// A header VS Code can't read either; the file name and body still work.
	}
	return {};
}

function textField(header: Record<string, unknown>, key: string): string {
	const value = header[key];
	if (typeof value !== 'string') return '';
	return value.trim();
}

/**
 * The agent one file describes, or null when it asks to stay out of the agent
 * picker (`user-invocable: false`). `path` is relative to the project folder.
 */
export function parseAgentFile(text: string, path: string): ChatAgent | null {
	const { header, body } = splitFrontmatter(text);
	const fields = readHeader(header);
	if (fields['user-invocable'] === false) return null;

	const fileName = path.split('/').pop() ?? path;
	let name = textField(fields, 'name');
	if (name === '') name = nameFromFileName(fileName);

	return {
		name,
		description: textField(fields, 'description'),
		path,
		instructions: body.trim()
	};
}

/** Agents by name, then by where they are, so each list reads the same every time. */
export function sortAgents(agents: ChatAgent[]): ChatAgent[] {
	return [...agents].sort((first, second) => {
		const byName = first.name.localeCompare(second.name, undefined, { sensitivity: 'base' });
		if (byName !== 0) return byName;
		return first.path.localeCompare(second.path);
	});
}
