import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { get, writable } from 'svelte/store';

const sourceUrl = new URL('../../src/lib/utils/lazy-component.ts', import.meta.url);
const errors = [];
const compiled = ts.transpileModule(readFileSync(sourceUrl, 'utf8'), {
	compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
	fileName: sourceUrl.pathname
});
const module = { exports: {} };
vm.runInNewContext(
	compiled.outputText,
	{
		module,
		exports: module.exports,
		console: { error: (...arguments_) => errors.push(arguments_) },
		require(specifier) {
			assert.equal(specifier, 'svelte/store');
			return { writable };
		}
	},
	{ filename: sourceUrl.pathname }
);
const { createLazyComponent } = module.exports;

function deferredImport() {
	let resolve;
	let reject;
	const promise = new Promise((resolvePromise, rejectPromise) => {
		resolve = resolvePromise;
		reject = rejectPromise;
	});
	return { promise, resolve, reject };
}

function state(loader) {
	return { ...get(loader) };
}

test('a closed feature does not import a component merely by creating or reading its loader', () => {
	let calls = 0;
	const loader = createLazyComponent(async () => {
		calls += 1;
		return { default: class Feature {} };
	});
	assert.deepEqual(state(loader), { component: null, loading: false, error: false });
	assert.equal(calls, 0);
});

test('concurrent opens share one pending import and cache the successful constructor', async () => {
	const imported = deferredImport();
	const Feature = class Feature {};
	let calls = 0;
	const loader = createLazyComponent(() => {
		calls += 1;
		return imported.promise;
	});
	const first = loader.load();
	const second = loader.load();
	assert.equal(first, second);
	assert.deepEqual(state(loader), { component: null, loading: true, error: false });
	await Promise.resolve();
	assert.equal(calls, 1);
	imported.resolve({ default: Feature });
	await first;
	assert.deepEqual(state(loader), { component: Feature, loading: false, error: false });
	await loader.load();
	assert.equal(calls, 1);
});

test('failed imports remain retryable without an automatic retry loop', async () => {
	const Feature = class Feature {};
	let calls = 0;
	const loader = createLazyComponent(async () => {
		calls += 1;
		if (calls === 1) throw new Error('Offline chunk failure');
		return { default: Feature };
	});
	await loader.load();
	assert.deepEqual(state(loader), { component: null, loading: false, error: true });
	await Promise.resolve();
	assert.equal(calls, 1);
	const retry = loader.load();
	assert.deepEqual(state(loader), { component: null, loading: true, error: false });
	await retry;
	assert.deepEqual(state(loader), { component: Feature, loading: false, error: false });
	assert.equal(calls, 2);
});

test('a synchronous importer throw clears the pending promise so an explicit retry succeeds', async () => {
	const Feature = class Feature {};
	let calls = 0;
	const loader = createLazyComponent(() => {
		calls += 1;
		if (calls === 1) throw new Error('Offline synchronous failure');
		return Promise.resolve({ default: Feature });
	});
	await loader.load();
	assert.deepEqual(state(loader), { component: null, loading: false, error: true });
	await loader.load();
	assert.deepEqual(state(loader), { component: Feature, loading: false, error: false });
	assert.equal(calls, 2);
});

test('a reentrant loading subscriber deduplicates against the same pending promise', async () => {
	const imported = deferredImport();
	let calls = 0;
	let reentrant;
	const loader = createLazyComponent(() => {
		calls += 1;
		return imported.promise;
	});
	const unsubscribe = loader.subscribe((current) => {
		if (current.loading && !reentrant) reentrant = loader.load();
	});
	const initial = loader.load();
	assert.equal(reentrant, initial);
	await Promise.resolve();
	assert.equal(calls, 1);
	imported.resolve({ default: class Feature {} });
	await initial;
	unsubscribe();
});

test('closing a consumer before import finishes lets a later reopening reuse the loaded constructor', async () => {
	const imported = deferredImport();
	const Feature = class Feature {};
	let calls = 0;
	let closedConsumerUpdates = 0;
	const loader = createLazyComponent(() => {
		calls += 1;
		return imported.promise;
	});
	const unsubscribe = loader.subscribe(() => {
		closedConsumerUpdates += 1;
	});
	const pending = loader.load();
	unsubscribe();
	const updatesAtClose = closedConsumerUpdates;
	imported.resolve({ default: Feature });
	await pending;
	assert.equal(closedConsumerUpdates, updatesAtClose);
	assert.equal(state(loader).component, Feature);
	await loader.load();
	assert.equal(calls, 1);
});
