import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function loadPureTypeScript(sourceUrl, dependencies = {}) {
	const source = readFileSync(sourceUrl, 'utf8');
	const compiled = ts.transpileModule(source, {
		compilerOptions: {
			module: ts.ModuleKind.CommonJS,
			target: ts.ScriptTarget.ES2022
		},
		fileName: sourceUrl.pathname
	});
	const module = { exports: {} };
	const sandbox = {
		module,
		exports: module.exports,
		require(specifier) {
			if (!Object.hasOwn(dependencies, specifier)) {
				throw new Error('Unexpected dependency in pure Buddy activity test: ' + specifier);
			}
			return dependencies[specifier];
		}
	};
	vm.runInNewContext(compiled.outputText, sandbox, { filename: sourceUrl.pathname });
	return module.exports;
}

const structuredOutput = loadPureTypeScript(
	new URL('../../src/lib/components/chat/Messages/structuredOutput.ts', import.meta.url)
);
const { getBuddyActivity } = loadPureTypeScript(
	new URL('../../src/lib/components/buddy/chatActivity.ts', import.meta.url),
	{ '../chat/Messages/structuredOutput': structuredOutput }
);

function responseHistory(response = {}) {
	return {
		currentId: 'response',
		messages: {
			user: { role: 'user', childrenIds: ['response'] },
			response: { role: 'assistant', parentId: 'user', content: '', done: false, ...response }
		}
	};
}

test('no active generation or task keeps the avatar idle even with an unfinished draft response', () => {
	assert.equal(
		getBuddyActivity(responseHistory({ content: 'Earlier answer' }), false, null),
		'idle'
	);
	assert.equal(getBuddyActivity(responseHistory(), false, []), 'idle');
});

test('generation before a response exists shows thinking', () => {
	const history = { currentId: null, messages: {} };
	assert.equal(getBuddyActivity(history, true, null), 'thinking');
	assert.equal(getBuddyActivity(history, false, ['task']), 'thinking');
});

test('a task moves from thinking to responding only when visible answer text arrives', () => {
	const history = responseHistory();
	assert.equal(getBuddyActivity(history, false, ['task']), 'thinking');
	history.messages.response.content = 'Here is a gentle first step.';
	assert.equal(getBuddyActivity(history, false, ['task']), 'responding');
	history.messages.response.done = true;
	assert.equal(getBuddyActivity(history, false, ['task']), 'idle');
});

test('closed legacy reasoning and tool details are not visible answer progress', () => {
	const reasoning =
		'<details type="reasoning"><summary>Thinking</summary>Internal thought</details>';
	const tool =
		'<details type="tool_calls"><summary>Tool executed</summary>Search results</details>';
	assert.equal(getBuddyActivity(responseHistory({ content: reasoning }), true, null), 'thinking');
	assert.equal(getBuddyActivity(responseHistory({ content: tool }), false, ['task']), 'thinking');
});

test('unfinished legacy reasoning remains thinking until answer text precedes it', () => {
	const reasoning = '<details type="reasoning"><summary>Thinking</summary>Still working';
	assert.equal(getBuddyActivity(responseHistory({ content: reasoning }), true, null), 'thinking');
	assert.equal(
		getBuddyActivity(responseHistory({ content: 'A useful first step. ' + reasoning }), true, null),
		'responding'
	);
});

test('legacy answer after closed reasoning shows responding', () => {
	const content = '<details type="reasoning">Internal thought</details>\nThe answer is ready.';
	assert.equal(getBuddyActivity(responseHistory({ content }), false, ['task']), 'responding');
});

test('structured reasoning and tool results do not count as the assistant answer', () => {
	const output = [
		{ type: 'reasoning', summary: [{ type: 'summary_text', text: 'Working out the answer' }] },
		{ type: 'function_call', call_id: 'search', name: 'search', arguments: '{}' },
		{
			type: 'function_call_output',
			call_id: 'search',
			output: [{ type: 'text', text: 'Found a result' }]
		}
	];
	assert.equal(
		getBuddyActivity(responseHistory({ content: 'Stale legacy text', output }), true, null),
		'thinking'
	);
});

test('structured message text shows responding without relying on legacy content', () => {
	const output = [
		{ type: 'reasoning', summary: [{ type: 'summary_text', text: 'Considering options' }] },
		{ type: 'message', content: [{ type: 'output_text', text: 'Choose one small next step.' }] }
	];
	assert.equal(getBuddyActivity(responseHistory({ output }), false, ['task']), 'responding');
});

test('whitespace-only structured messages keep the avatar thinking', () => {
	const output = [{ type: 'message', content: [{ type: 'output_text', text: ' \n\t ' }] }];
	assert.equal(getBuddyActivity(responseHistory({ output }), true, null), 'thinking');
});

test('an active sibling answer drives responding even when the selected sibling finished', () => {
	const history = responseHistory({ done: true, content: 'Finished selected response' });
	history.messages.user.childrenIds.push('sibling');
	history.messages.sibling = {
		role: 'assistant',
		parentId: 'user',
		done: false,
		content: 'Streaming sibling response'
	};
	assert.equal(getBuddyActivity(history, false, ['task']), 'responding');
	history.messages.sibling.content = '';
	assert.equal(getBuddyActivity(history, false, ['task']), 'thinking');
	history.messages.sibling.done = true;
	assert.equal(getBuddyActivity(history, false, ['task']), 'idle');
});

test('a user message detects its active assistant children', () => {
	const history = responseHistory({ content: 'The child response is arriving.' });
	history.currentId = 'user';
	assert.equal(getBuddyActivity(history, false, ['task']), 'responding');
});

test('a pending response without a parent still follows its own visible text', () => {
	const history = responseHistory();
	delete history.messages.response.parentId;
	assert.equal(getBuddyActivity(history, true, null), 'thinking');
	history.messages.response.content = 'An answer.';
	assert.equal(getBuddyActivity(history, true, null), 'responding');
});

test('merged generation uses merged answer progress instead of finished source answers', () => {
	const history = responseHistory({
		done: true,
		content: 'Finished source answer',
		merged: { status: true, content: '<details type="reasoning">Comparing</details>' }
	});
	assert.equal(getBuddyActivity(history, true, ['task']), 'thinking');
	history.messages.response.merged.content += 'The combined answer.';
	assert.equal(getBuddyActivity(history, true, ['task']), 'responding');
	assert.equal(getBuddyActivity(history, false, ['task']), 'idle');
});

test('unfinished merged reasoning does not animate responding', () => {
	const history = responseHistory({
		done: true,
		merged: { status: true, content: '<details type="reasoning">Still comparing' }
	});
	assert.equal(getBuddyActivity(history, true, null), 'thinking');
});
