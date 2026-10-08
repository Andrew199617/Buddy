import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const module = { exports: {} };
vm.runInNewContext(readFileSync(new URL('../web/chat-usage-info.js', import.meta.url), 'utf8'), { module });
const { tokenCount, runUsage, activeMessages, latestResponse, advertisedCapacity, contextInfo, compactCount, durationText } = module.exports;
function chatWith(response, extra = {}) {
 const user = { id: 'user', role: 'user', parentId: null, childrenIds: [response.id] };
 return { id: 'chat', chat: { history: { currentId: response.id, messages: { user, [response.id]: { parentId: user.id, ...response } } } }, ...extra };
}
test('run totals use canonical accumulated usage while context uses latest request aliases', () => {
 const response = { id: 'reply', role: 'assistant', usage: { input_tokens: 13000, output_tokens: 1200, total_tokens: 14200, prompt_tokens: 5000, completion_tokens: 200 } };
 const usage = runUsage(response);
 assert.equal(usage.input, 13000);
 assert.equal(usage.output, 1200);
 assert.equal(usage.total, 14200);
 assert.equal(contextInfo(chatWith(response)).tokens, 5200);
 assert.equal(contextInfo(chatWith(response)).source, 'Last request and response');
});
test('server run metadata preserves explicit unavailable values and partial status', () => {
 const response = { usage: { input_tokens: 12, output_tokens: 24 }, meta: { local_run: { version: 1, input_tokens: null, output_tokens: null, total_tokens: null, usage_complete: false, status: 'cancelled', duration_ms: 1002 } } };
 const usage = runUsage(response);
 assert.equal(usage.total, null);
 assert.equal(usage.input, null);
 assert.equal(usage.output, null);
 assert.equal(usage.complete, false);
 assert.equal(usage.status, 'cancelled');
 assert.equal(usage.durationMs, 1002);
});
test('reported zero counts remain zero and total derives only from a full split', () => {
 assert.equal(runUsage({ usage: { input_tokens: 0, prompt_tokens: 99, output_tokens: 0 } }).input, 0);
 assert.equal(runUsage({ usage: { input_tokens: 0, output_tokens: 0 } }).total, 0);
 assert.equal(runUsage({ usage: { input_tokens: 90 } }).total, null);
 assert.equal(tokenCount(undefined), null);
 assert.equal(tokenCount(-1), null);
 assert.equal(tokenCount('bad'), null);
 assert.equal(tokenCount(true), null);
 assert.equal(tokenCount(1.4), null);
});
test('total-only legacy usage does not fabricate an input and output split', () => {
 const usage = runUsage({ usage: { input_tokens: 0, output_tokens: 0, total_tokens: 900 } });
 assert.equal(usage.total, 900);
 assert.equal(usage.input, null);
 assert.equal(usage.output, null);
 assert.equal(runUsage({ usage: { total_tokens: 100 } }).input, null);
});
test('old OpenAI and Ollama histories remain readable', () => {
 assert.equal(runUsage({ usage: { prompt_tokens: 20, completion_tokens: 30 } }).total, 50);
 assert.equal(runUsage({ info: { prompt_eval_count: 40, eval_count: 50 } }).total, 90);
 assert.equal(runUsage({ usage: { prompt_n: 20, cache_n: 80, predicted_n: 5 } }).total, 105);
});
test('context does not use accumulated run counts or present compaction as capacity', () => {
 const response = { id: 'reply', role: 'assistant', usage: { input_tokens: 200000, output_tokens: 5000, total_tokens: 205000 } };
 assert.equal(contextInfo(chatWith(response)).tokens, null);
 const context = contextInfo(chatWith(response, { context_usage: { estimated_tokens: 3000, threshold: 12000 } }));
 assert.equal(context.tokens, 3000);
 assert.equal(context.threshold, 12000);
 assert.equal(context.capacity, null);
 assert.equal(context.percent, null);
});
test('explicit metadata and advertised capacity produce context occupancy', () => {
 const response = { id: 'reply', role: 'assistant', meta: { local_run: { version: 1, model_id: 'model', context_tokens: 8000, context_capacity: { tokens: 32000, source: 'configured' } } } };
 const context = contextInfo(chatWith(response));
 assert.equal(context.tokens, 8000);
 assert.equal(context.percent, 25);
 assert.equal(context.source, 'Last request and response');
 assert.equal(advertisedCapacity({ id: 'model', info: { meta: { context_length: 128000 } } }).tokens, 128000);
 assert.equal(advertisedCapacity({ id: 'model', info: { params: { compact_token_threshold: 16000 } } }), null);
 assert.equal(contextInfo(chatWith(response), { id: 'different' }).capacity, null);
});
test('chat configured capacity overrides model metadata', () => {
 const chat = chatWith({ id: 'reply', role: 'assistant' }, { context_usage: { tokens: 1000 } });
 chat.chat.params = { num_ctx: 10000 };
 const context = contextInfo(chat, { context_length: 20000 });
 assert.equal(context.capacity.tokens, 10000);
 assert.equal(context.percent, 10);
});
test('latest run follows the current conversation branch with cycle protection', () => {
 const chat = { chat: { history: { currentId: 'reply', messages: { user: { id: 'user', role: 'user', parentId: null }, discarded: { id: 'discarded', role: 'assistant', parentId: 'user', timestamp: 999 }, reply: { id: 'reply', role: 'assistant', parentId: 'user', timestamp: 10 } } } } };
 assert.equal(latestResponse(chat).id, 'reply');
 chat.chat.history.messages.user.parentId = 'reply';
 assert.equal(activeMessages(chat).length, 2);
});
test('compact counts and durations communicate unavailable values clearly', () => {
 assert.equal(compactCount(14200), '14.2K');
 assert.equal(compactCount(0), '0');
 assert.equal(compactCount(null), 'Unavailable');
 assert.equal(durationText(0), '0 ms');
 assert.equal(durationText(72000), '1 min 12 s');
 assert.equal(durationText(null), 'Unavailable');
});

test('failed latest attempts cannot display the prior completed run counts', () => {
 const usage = runUsage({ error: { content: 'Failed' }, meta: { local_run: { version: 1, status: 'completed', input_tokens: 50, output_tokens: 20, total_tokens: 70, duration_ms: 100 } } });
 assert.equal(usage.status, 'error');
 assert.equal(usage.total, null);
 assert.equal(usage.durationMs, null);
 assert.equal(usage.complete, false);
});
