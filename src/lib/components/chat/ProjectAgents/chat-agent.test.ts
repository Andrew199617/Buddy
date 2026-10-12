import { describe, expect, it } from 'vitest';

import { ownAgent, savedAgent, systemWithAgent, type ChatAgent } from './chat-agent';

const reviewer: ChatAgent = {
	name: 'Reviewer',
	description: 'Reviews changes',
	path: '.github/agents/reviewer.agent.md',
	instructions: 'Review the diff.\n\nList risks first.'
};

describe('savedAgent', () => {
	it('keeps a complete saved agent', () => {
		expect(savedAgent({ ...reviewer })).toEqual(reviewer);
	});

	it('fills in a missing description', () => {
		const { description, ...withoutDescription } = reviewer;
		expect(description).not.toBe('');
		expect(savedAgent(withoutDescription)).toEqual({ ...reviewer, description: '' });
	});

	it('drops anything that is not a usable agent', () => {
		expect(savedAgent(null)).toBeNull();
		expect(savedAgent('Reviewer')).toBeNull();
		expect(savedAgent({ ...reviewer, name: '  ' })).toBeNull();
		expect(savedAgent({ ...reviewer, path: 7 })).toBeNull();
		expect(savedAgent({ ...reviewer, instructions: undefined })).toBeNull();
	});
});

describe('ownAgent', () => {
	const loaded = { user_id: 'owner', chat: { agent: { ...reviewer } } };

	it('gives the owner their saved agent', () => {
		expect(ownAgent(loaded, 'owner')).toEqual(reviewer);
	});

	it('gives anyone else no agent', () => {
		expect(ownAgent(loaded, 'folder-member')).toBeNull();
		expect(ownAgent(loaded, undefined)).toBeNull();
		expect(ownAgent({ chat: loaded.chat }, 'owner')).toBeNull();
		expect(ownAgent(null, 'owner')).toBeNull();
	});
});

describe('systemWithAgent', () => {
	it('leaves the system prompt alone without an agent', () => {
		expect(systemWithAgent('Be brief.', null)).toBe('Be brief.');
	});

	it('adds the agent after the system prompt, naming its file', () => {
		expect(systemWithAgent('Be brief.', reviewer)).toBe(
			'Be brief.\n\nThe user chose the "Reviewer" custom agent, from ' +
				'.github/agents/reviewer.agent.md in the project folder. Follow its instructions:\n\n' +
				'Review the diff.\n\nList risks first.'
		);
	});

	it('uses the agent alone when there is no system prompt', () => {
		expect(systemWithAgent('', reviewer)).toMatch(/^The user chose the "Reviewer" custom agent/);
	});

	it('adds nothing for an agent without instructions', () => {
		expect(systemWithAgent('Be brief.', { ...reviewer, instructions: ' \n' })).toBe('Be brief.');
	});
});
