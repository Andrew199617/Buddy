import { describe, expect, it } from 'vitest';

import {
	isAgentFile,
	nameFromFileName,
	parseAgentFile,
	sortAgents,
	splitFrontmatter
} from './agent-files';

describe('isAgentFile', () => {
	it('takes any Markdown file in the agent folders', () => {
		expect(isAgentFile('.github/agents', 'reviewer.agent.md')).toBe(true);
		expect(isAgentFile('.github/agents', 'Planner.MD')).toBe(true);
		expect(isAgentFile('.claude/agents', 'tester.md')).toBe(true);
		expect(isAgentFile('.github/agents', 'notes.txt')).toBe(false);
	});

	it('takes only chat mode files from the old chat modes folder', () => {
		expect(isAgentFile('.github/chatmodes', 'plan.chatmode.md')).toBe(true);
		expect(isAgentFile('.github/chatmodes', 'README.md')).toBe(false);
	});
});

describe('nameFromFileName', () => {
	it('drops the agent file endings', () => {
		expect(nameFromFileName('reviewer.agent.md')).toBe('reviewer');
		expect(nameFromFileName('Plan.ChatMode.md')).toBe('Plan');
		expect(nameFromFileName('tester.md')).toBe('tester');
	});

	it('keeps a name that is only an ending', () => {
		expect(nameFromFileName('.md')).toBe('.md');
	});
});

describe('splitFrontmatter', () => {
	it('splits the YAML header from the body, with CRLF and a byte order mark', () => {
		const text = '﻿---\r\nname: Reviewer\r\n---\r\nReview it.\r\n';
		expect(splitFrontmatter(text)).toEqual({ header: 'name: Reviewer', body: 'Review it.\n' });
	});

	it('reads an empty header', () => {
		expect(splitFrontmatter('---\n---\nBody')).toEqual({ header: '', body: 'Body' });
	});

	it('treats a file without a closing line as all body', () => {
		expect(splitFrontmatter('---\nname: x\nBody')).toEqual({
			header: '',
			body: '---\nname: x\nBody'
		});
	});

	it('treats a file without a header as all body', () => {
		expect(splitFrontmatter('# Agent\nDo things')).toEqual({
			header: '',
			body: '# Agent\nDo things'
		});
	});
});

describe('parseAgentFile', () => {
	it('reads a VS Code agent', () => {
		const text = [
			'---',
			"description: 'Plans a feature before coding'",
			'tools: [search, fetch]',
			'model: Claude Opus 5.5',
			'---',
			'',
			'# Planning',
			'Write a plan. Do not edit files.',
			''
		].join('\n');
		expect(parseAgentFile(text, '.github/agents/planner.agent.md')).toEqual({
			name: 'planner',
			description: 'Plans a feature before coding',
			path: '.github/agents/planner.agent.md',
			instructions: '# Planning\nWrite a plan. Do not edit files.'
		});
	});

	it('uses the name and folded description from the header', () => {
		const text = [
			'---',
			'name: Code reviewer',
			'description: >-',
			'  Reviews changes',
			'  for bugs',
			'---',
			'Review the diff.'
		].join('\n');
		const agent = parseAgentFile(text, '.claude/agents/reviewer.md');
		expect(agent?.name).toBe('Code reviewer');
		expect(agent?.description).toBe('Reviews changes for bugs');
	});

	it('leaves out agents hidden from the agent picker', () => {
		const text = '---\nuser-invocable: false\n---\nOnly a subagent.';
		expect(parseAgentFile(text, '.github/agents/helper.agent.md')).toBeNull();
	});

	it('still lists a file whose header is not valid YAML', () => {
		const text = '---\nname: [unclosed\n---\nBody';
		expect(parseAgentFile(text, '.github/agents/broken.agent.md')).toEqual({
			name: 'broken',
			description: '',
			path: '.github/agents/broken.agent.md',
			instructions: 'Body'
		});
	});

	it('ignores header fields that are not text', () => {
		const text = '---\nname: 42\ndescription: [a, b]\n---\nBody';
		const agent = parseAgentFile(text, '.github/agents/numbers.md');
		expect(agent?.name).toBe('numbers');
		expect(agent?.description).toBe('');
	});
});

describe('sortAgents', () => {
	it('sorts by name, ignoring case, then by path', () => {
		const agent = (name: string, path: string) => ({
			name,
			description: '',
			path,
			instructions: ''
		});
		const sorted = sortAgents([
			agent('tester', '.github/agents/tester.md'),
			agent('Planner', '.github/agents/planner.agent.md'),
			agent('tester', '.claude/agents/tester.md')
		]);
		expect(sorted.map((item) => item.path)).toEqual([
			'.github/agents/planner.agent.md',
			'.claude/agents/tester.md',
			'.github/agents/tester.md'
		]);
	});
});
