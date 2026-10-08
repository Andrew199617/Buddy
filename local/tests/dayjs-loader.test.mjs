import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import frenchLocale from 'dayjs/locale/fr.js';
import germanLocale from 'dayjs/locale/de.js';
import traditionalChineseLocale from 'dayjs/locale/zh-tw.js';

const require = createRequire(import.meta.url);
const dayjsModulePath = require.resolve('dayjs');
const source = await readFile(new URL('../../src/lib/dayjs.js', import.meta.url), 'utf8');
const executableSource = source
	.replace(/^import dayjs from 'dayjs';\s*/m, '')
	.replace('import.meta.glob', 'loadLocaleModules')
	.replace('export const loadDateLocale', 'const loadDateLocale')
	.replace('export default dayjs;', 'return { dayjs, loadDateLocale };');
const createSourceLoader = new Function('dayjs', 'loadLocaleModules', 'console', executableSource);

function deferred() {
	let resolve;
	let reject;
	const promise = new Promise((resolvePromise, rejectPromise) => {
		resolve = resolvePromise;
		reject = rejectPromise;
	});
	return { promise, resolve, reject };
}

function registerFrench(dayjs) {
	dayjs.locale(frenchLocale, undefined, true);
}

function registerGerman(dayjs) {
	dayjs.locale(germanLocale, undefined, true);
}

function registerTraditionalChinese(dayjs) {
	dayjs.locale(traditionalChineseLocale, undefined, true);
}

function createFixture({ frenchLoader = registerFrench } = {}) {
	// Each source evaluation owns an independent real Day.js instance and request counter.
	delete require.cache[dayjsModulePath];
	const dayjs = require(dayjsModulePath);
	const requestedChunks = [];
	const warnings = [];

	function trackedLoader(locale, loader) {
		return async function loadTrackedLocale() {
			requestedChunks.push(locale);
			await loader(dayjs);
		};
	}

	const localeModules = {
		'../../node_modules/dayjs/locale/en.js': trackedLoader('en', () => {}),
		'../../node_modules/dayjs/locale/fr.js': trackedLoader('fr', frenchLoader),
		'../../node_modules/dayjs/locale/de.js': trackedLoader('de', registerGerman),
		'../../node_modules/dayjs/locale/zh-tw.js': trackedLoader('zh-tw', registerTraditionalChinese)
	};
	function loadLocaleModules(pattern) {
		assert.equal(pattern, '../../node_modules/dayjs/locale/*.js');
		return localeModules;
	}
	const testConsole = {
		warn(message, error) {
			warnings.push({ message, error });
		}
	};
	const loader = createSourceLoader(dayjs, loadLocaleModules, testConsole);
	return { ...loader, requestedChunks, warnings };
}

test('English and default requests load no locale chunks', async () => {
	const fixture = createFixture();
	await fixture.loadDateLocale(undefined);
	await fixture.loadDateLocale('');
	await fixture.loadDateLocale('en');
	await fixture.loadDateLocale('EN-US');
	await fixture.loadDateLocale('en_US');
	assert.equal(fixture.dayjs.locale(), 'en');
	assert.deepEqual(fixture.requestedChunks, []);
	assert.deepEqual(fixture.warnings, []);
});

test('regional fallback and specific regional locales use actual Day.js grammars', async () => {
	const fixture = createFixture();
	await fixture.loadDateLocale('fr-FR');
	assert.equal(fixture.dayjs.locale(), 'fr');
	assert.equal(fixture.dayjs('2026-01-07').format('MMMM'), 'janvier');

	await fixture.loadDateLocale('ZH_TW');
	assert.equal(fixture.dayjs.locale(), 'zh-tw');
	assert.equal(fixture.dayjs('2026-01-07').format('MMMM'), '一月');
	assert.deepEqual(fixture.requestedChunks, ['fr', 'zh-tw']);
});

test('a failed locale chunk falls back to English dates', async () => {
	const failure = new Error('Synthetic locale import failure');
	const fixture = createFixture({
		frenchLoader() {
			throw failure;
		}
	});
	fixture.dayjs.locale(germanLocale);
	assert.equal(fixture.dayjs.locale(), 'de');

	await fixture.loadDateLocale('fr-FR');
	assert.equal(fixture.dayjs.locale(), 'en');
	assert.deepEqual(fixture.requestedChunks, ['fr']);
	assert.equal(fixture.warnings.length, 1);
	assert.match(fixture.warnings[0].message, /using English dates/);
	assert.equal(fixture.warnings[0].error, failure);
});

test('an older pending locale cannot override the latest successful request', async () => {
	const pending = deferred();
	const fixture = createFixture({
		async frenchLoader(dayjs) {
			await pending.promise;
			registerFrench(dayjs);
		}
	});
	const olderRequest = fixture.loadDateLocale('fr-FR');
	await fixture.loadDateLocale('zh-TW');
	assert.equal(fixture.dayjs.locale(), 'zh-tw');

	pending.resolve();
	await olderRequest;
	assert.equal(fixture.dayjs.locale(), 'zh-tw');
	assert.deepEqual(fixture.requestedChunks, ['fr', 'zh-tw']);
});

test('an older failed request cannot replace the latest locale with English', async () => {
	const pending = deferred();
	const fixture = createFixture({
		frenchLoader() {
			return pending.promise;
		}
	});
	const olderRequest = fixture.loadDateLocale('fr');
	await fixture.loadDateLocale('de');
	pending.reject(new Error('Synthetic delayed import failure'));
	await olderRequest;
	assert.equal(fixture.dayjs.locale(), 'de');
	assert.equal(fixture.warnings.length, 1);
});

test('a latest English request supersedes a pending locale without loading a chunk', async () => {
	const pending = deferred();
	const fixture = createFixture({
		async frenchLoader(dayjs) {
			await pending.promise;
			registerFrench(dayjs);
		}
	});
	const olderRequest = fixture.loadDateLocale('fr');
	await fixture.loadDateLocale('en-US');
	pending.resolve();
	await olderRequest;
	assert.equal(fixture.dayjs.locale(), 'en');
	assert.deepEqual(fixture.requestedChunks, ['fr']);
});

test('an unsupported locale resets previously selected dates to English', async () => {
	const fixture = createFixture();
	await fixture.loadDateLocale('fr');
	assert.equal(fixture.dayjs.locale(), 'fr');
	await fixture.loadDateLocale('unsupported-REGION');
	assert.equal(fixture.dayjs.locale(), 'en');
	assert.deepEqual(fixture.requestedChunks, ['fr']);
	assert.deepEqual(fixture.warnings, []);
});
