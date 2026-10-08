// Run from the open-webui folder:  node --test local/tests/loader.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// The script is a classic browser script; evaluate it without `window` to get its exports.
const sandbox = { module: { exports: {} } };
vm.runInNewContext(readFileSync(new URL('../web/reasoning-chip.js', import.meta.url), 'utf8'), sandbox);
const { withReasoningEffort, LEVELS, COMPLETIONS_URL } = sandbox.module.exports;

test('adds the level to the native params', () => {
	const body = JSON.stringify({ model: 'QwenMOE', params: { temperature: 0.2, reasoning_effort: 'low' } });
	assert.deepEqual(JSON.parse(withReasoningEffort(body, 'max')), {
		model: 'QwenMOE',
		params: { temperature: 0.2, reasoning_effort: 'max' }
	});
});

test('creates params when the request has none', () => {
	assert.deepEqual(JSON.parse(withReasoningEffort('{"model":"m"}', 'none')).params, { reasoning_effort: 'none' });
});

test('Default leaves the body byte-for-byte unchanged', () => {
	const body = '{"model":"m","params":{"reasoning_effort":"low"}}';
	assert.equal(withReasoningEffort(body, ''), body);
});

test('only chat completion URLs are rewritten', () => {
	assert.ok(COMPLETIONS_URL.test('/api/chat/completions'));
	assert.ok(COMPLETIONS_URL.test('http://localhost:8080/api/chat/completions'));
	assert.ok(!COMPLETIONS_URL.test('/api/chat/completions/abc'));
	assert.ok(!COMPLETIONS_URL.test('/api/v1/tasks/title/completions'));
});

test('offers Default plus the effort levels llama.cpp and the gateway accept', () => {
	assert.deepEqual(
		Array.from(LEVELS, (l) => l.value),
		['', 'none', 'low', 'medium', 'high', 'xhigh', 'max']
	);
});
