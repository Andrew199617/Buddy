import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const context = vm.createContext({ module: { exports: {} } });
const source = readFileSync(new URL('../web/model-activity.js', import.meta.url), 'utf8');
vm.runInContext(source, context);
const { activityLabel, elapsedText } = context.module.exports;

test('empty pending response is visibly waiting', () => {
 assert.equal(activityLabel('', '', false), 'Waiting for response…');
});
test('continuation does not treat existing answer text as new progress', () => {
 assert.equal(activityLabel('Previous answer', 'Previous answer', false), 'Waiting for response…');
 assert.equal(activityLabel('Previous answer', 'Previous answer with more text', false), 'Working…');
});
test('active native reasoning takes precedence over content growth', () => {
 assert.equal(activityLabel('', 'Reasoning text', true), 'Thinking…');
});
test('timer measures elapsed whole seconds without early rounding', () => {
 assert.equal(elapsedText(1000, 1999), '');
 assert.equal(elapsedText(1000, 2000), '1s');
 assert.equal(elapsedText(1000, 60999), '59s');
 assert.equal(elapsedText(1000, 61000), '1m 0s');
 assert.equal(elapsedText(1000, 124000), '2m 3s');
});
test('timer never shows negative elapsed time', () => {
 assert.equal(elapsedText(2000, 1000), '');
});
