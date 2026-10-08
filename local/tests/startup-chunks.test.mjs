import assert from 'node:assert/strict';
import { test } from 'node:test';
import { rollup } from 'rollup';
import { createStartupChunks, separateSharedKeyboardHelper } from '../startup-chunks.mjs';

function createFixture(optimized = true) {
	let generatedDirectory = '/project/.svelte-kit/generated/client';
	if (optimized) {
		generatedDirectory += '-optimized';
	}
	const paths = {
		start: '/project/node_modules/@sveltejs/kit/src/runtime/client/entry.js',
		app: generatedDirectory + '/app.js',
		rootNode: generatedDirectory + '/nodes/0.js',
		shellNode: generatedDirectory + '/nodes/2.js',
		chatNode: generatedDirectory + '/nodes/9.js',
		otherNode: generatedDirectory + '/nodes/10.js',
		root: '/project/src/routes/+layout.svelte',
		rootOptions: '/project/src/routes/+layout.js',
		shell: '/project/src/routes/(app)/+layout.svelte',
		chat: '/project/src/routes/(app)/+page.svelte',
		store: '/project/src/lib/stores/index.js',
		runtime: '/project/runtime.js',
		icon: '/project/src/lib/components/icons/Icon.svelte',
		composer: '/project/src/lib/components/chat/MessageInput.svelte',
		keyboard: '/project/node_modules/w3c-keyname/index.js',
		settings: '/project/src/lib/components/chat/SettingsModal.svelte',
		files: '/project/src/lib/components/chat/Messages/TerminalOutputFile.svelte',
		editor: '/project/node_modules/@codemirror/state/dist/index.js',
		grammar: '/project/node_modules/highlight.js/lib/languages/python.js',
		highlightCore: '/project/node_modules/highlight.js/lib/core.js',
		locale: '/project/src/lib/i18n/locales/es-ES/translation.json',
		dateLocale: '/project/node_modules/dayjs/locale/es.js'
	};
	const graph = {
		[paths.start]: `export { start } from "${paths.runtime}";`,
		[paths.app]: `export const nodes = [() => import("${paths.rootNode}"), () => import("${paths.shellNode}"), () => import("${paths.chatNode}"), () => import("${paths.otherNode}")];`,
		[paths.rootNode]: `export { root as component } from "${paths.root}"; export { ssr } from "${paths.rootOptions}";`,
		[paths.shellNode]: `export { shell as component } from "${paths.shell}";`,
		[paths.chatNode]: `export * from "${paths.chat}";`,
		[paths.otherNode]: `export { settings as component } from "${paths.settings}";`,
		[paths.root]: `import { state } from "${paths.store}"; export const root = state; export const loadLocale = () => import("${paths.locale}"); export const loadDateLocale = () => import("${paths.dateLocale}");`,
		[paths.rootOptions]: 'export const ssr = false;',
		[paths.shell]: `import { state } from "${paths.store}"; import { icon } from "${paths.icon}"; export const shell = state + icon; export const openSettings = () => import("${paths.settings}");`,
		[paths.chat]: `import { shell } from "${paths.shell}"; import { composer } from "${paths.composer}"; import { core } from "${paths.highlightCore}"; export const chat = shell + composer + core; export const openFiles = () => import("${paths.files}"); export const loadGrammar = () => import("${paths.grammar}");`,
		[paths.store]: 'export const state = 1;',
		[paths.runtime]: `import { state } from "${paths.store}"; export function start() { return state; }`,
		[paths.icon]: 'export const icon = 2;',
		[paths.composer]: `import { state } from "${paths.store}"; import { keyboard } from "${paths.keyboard}"; export const composer = state + keyboard;`,
		[paths.keyboard]: 'export const keyboard = 3;',
		[paths.settings]: `import { state } from "${paths.store}"; import { editor } from "${paths.editor}"; export const settings = state + editor;`,
		[paths.files]: `import { icon } from "${paths.icon}"; import { editor } from "${paths.editor}"; export const files = icon + editor;`,
		[paths.editor]: `import { keyboard } from "${paths.keyboard}"; export const editor = keyboard + 4;`,
		[paths.grammar]: 'export const grammar = 5;',
		[paths.highlightCore]: 'export const core = 6;',
		[paths.locale]: 'export default { welcome: "Hola" };',
		[paths.dateLocale]: 'export default { name: "es" };'
	};
	const input = {
		start: paths.start,
		app: paths.app,
		root: paths.rootNode,
		shell: paths.shellNode,
		chat: paths.chatNode,
		other: paths.otherNode
	};
	const initialEntries = [paths.start, paths.app, paths.rootNode, paths.shellNode, paths.chatNode];
	return { paths, graph, input, initialEntries };
}

async function generate(fixture, manualChunks = createStartupChunks()) {
	const warnings = [];
	const virtualModules = {
		name: 'startup-chunk-test',
		resolveId(id) {
			if (Object.hasOwn(fixture.graph, id)) {
				return id;
			}
			return null;
		},
		load(id) {
			return fixture.graph[id] ?? null;
		}
	};
	const bundle = await rollup({
		input: fixture.input,
		plugins: [virtualModules],
		treeshake: { moduleSideEffects: false },
		onwarn(warning) {
			warnings.push(warning.code);
		}
	});
	try {
		const generated = await bundle.generate({
			format: 'es',
			chunkFileNames: 'chunks/[name]-[hash].js',
			manualChunks,
			onlyExplicitManualChunks: true
		});
		const chunks = generated.output.filter((item) => item.type === 'chunk');
		return { chunks, warnings };
	} finally {
		await bundle.close();
	}
}

function initialClosure(chunks, initialEntries) {
	const chunksByName = new Map(chunks.map((chunk) => [chunk.fileName, chunk]));
	const files = new Set();
	const modules = new Set();
	function visit(chunk) {
		if (files.has(chunk.fileName)) {
			return;
		}
		files.add(chunk.fileName);
		for (const id of Object.keys(chunk.modules)) {
			modules.add(id);
		}
		for (const dependency of chunk.imports) {
			visit(chunksByName.get(dependency));
		}
	}
	for (const chunk of chunks) {
		if (initialEntries.includes(chunk.facadeModuleId)) {
			visit(chunk);
		}
	}
	return { files, modules };
}

function chunkForModule(chunks, id) {
	return chunks.find((chunk) => Object.hasOwn(chunk.modules, id));
}

function assertDeferredModulesStayLazy(fixture, chunks) {
	const closure = initialClosure(chunks, fixture.initialEntries);
	assert.equal(closure.modules.has(fixture.paths.settings), false);
	assert.equal(closure.modules.has(fixture.paths.files), false);
	assert.equal(closure.modules.has(fixture.paths.editor), false);
	assert.equal(closure.modules.has(fixture.paths.grammar), false);
	assert.equal(closure.modules.has(fixture.paths.locale), false);
	assert.equal(closure.modules.has(fixture.paths.dateLocale), false);
	assert.equal(closure.modules.has(fixture.paths.highlightCore), true);
	return closure;
}

function assertInitialGroups(fixture, chunks) {
	assert.equal(chunkForModule(chunks, fixture.paths.root).name, 'buddy-boot');
	assert.equal(chunkForModule(chunks, fixture.paths.runtime).name, 'buddy-boot');
	assert.equal(chunkForModule(chunks, fixture.paths.store).name, 'buddy-boot');
	assert.equal(chunkForModule(chunks, fixture.paths.shell).name, 'buddy-shell');
	assert.equal(chunkForModule(chunks, fixture.paths.icon).name, 'buddy-shell');
	assert.equal(chunkForModule(chunks, fixture.paths.chat).name, 'buddy-chat');
	assert.equal(chunkForModule(chunks, fixture.paths.composer).name, 'buddy-chat');
	assert.equal(chunkForModule(chunks, fixture.paths.keyboard).name, 'keyboard-names');
	for (const id of Object.values(fixture.input)) {
		const facade = chunks.find((chunk) => chunk.facadeModuleId === id);
		assert.ok(facade?.isEntry, 'SvelteKit entry URLs must remain separate facades: ' + id);
		assert.equal(facade.name.startsWith('buddy-'), false);
	}
}

test('optimized client groups shared startup code and preserves dynamic features and route facades', async () => {
	const fixture = createFixture();
	const { chunks, warnings } = await generate(fixture);
	assert.deepEqual(warnings, []);
	assertInitialGroups(fixture, chunks);
	const closure = assertDeferredModulesStayLazy(fixture, chunks);
	assert.equal(
		closure.files.size,
		9,
		'Five route/entry facades plus three groups and keyboard helper'
	);
});

test('unoptimized generated client IDs use the same guarded policy', async () => {
	const fixture = createFixture(false);
	const { chunks, warnings } = await generate(fixture);
	assert.deepEqual(warnings, []);
	assertInitialGroups(fixture, chunks);
	assertDeferredModulesStayLazy(fixture, chunks);
});

test('Windows module IDs are normalized for root discovery and deferred dependency guards', async () => {
	const fixture = createFixture();
	function windowsId(id) {
		return id.replace('/project', 'C:/project').replaceAll('/', '\\');
	}
	const windowsGraph = {};
	for (const [id, source] of Object.entries(fixture.graph)) {
		windowsGraph[windowsId(id)] = source.replace(/"\/project[^\"]*"/g, (specifier) =>
			JSON.stringify(windowsId(JSON.parse(specifier)))
		);
	}
	const windowsFixture = {
		graph: windowsGraph,
		paths: Object.fromEntries(
			Object.entries(fixture.paths).map(([name, id]) => [name, windowsId(id)])
		),
		input: Object.fromEntries(
			Object.entries(fixture.input).map(([name, id]) => [name, windowsId(id)])
		),
		initialEntries: fixture.initialEntries.map(windowsId)
	};
	const { chunks, warnings } = await generate(windowsFixture);
	assert.deepEqual(warnings, []);
	assertInitialGroups(windowsFixture, chunks);
	assertDeferredModulesStayLazy(windowsFixture, chunks);
});

test('an existing runtime cycle stays inside boot while lazy consumers reuse shared code', async () => {
	const fixture = createFixture();
	const runtimeHelper = '/project/runtime-helper.js';
	fixture.graph[fixture.paths.runtime] =
		`import { helper } from "${runtimeHelper}"; export function start() { return helper(); }`;
	fixture.graph[runtimeHelper] =
		`import { start } from "${fixture.paths.runtime}"; export function helper() { return typeof start; }`;
	const { chunks, warnings } = await generate(fixture);
	assert.ok(warnings.every((warning) => warning === 'CIRCULAR_DEPENDENCY'));
	const runtimeChunk = chunkForModule(chunks, fixture.paths.runtime);
	assert.equal(runtimeChunk.name, 'buddy-boot');
	assert.ok(Object.hasOwn(runtimeChunk.modules, runtimeHelper));
	assertDeferredModulesStayLazy(fixture, chunks);
});

test('a static re-export barrel is rejected instead of moving a deferred editor into startup', async () => {
	const fixture = createFixture();
	const barrel = '/project/barrel.js';
	fixture.graph[fixture.paths.root] =
		`import { formatter } from "${barrel}"; export const root = formatter;`;
	fixture.graph[barrel] =
		`export const formatter = 7; export { editor } from "${fixture.paths.editor}";`;
	fixture.graph[fixture.paths.settings] =
		`import { editor } from "${barrel}"; export const settings = editor;`;
	await assert.rejects(
		generate(fixture),
		/Startup chunk "buddy-boot" reached deferred dependency:.*@codemirror/
	);
});

test('an accidentally static optional component is rejected at its source boundary', async () => {
	const fixture = createFixture();
	fixture.graph[fixture.paths.shell] += ` export { settings } from "${fixture.paths.settings}";`;
	await assert.rejects(
		generate(fixture),
		/Startup chunk "buddy-shell" reached deferred dependency:.*SettingsModal/
	);
});

test('full Highlight.js grammars are rejected while the core-only import remains allowed', async () => {
	const fixture = createFixture();
	fixture.graph[fixture.paths.chat] += ` export { grammar } from "${fixture.paths.grammar}";`;
	await assert.rejects(
		generate(fixture),
		/Startup chunk "buddy-chat" reached deferred dependency:.*highlight\.js.*languages/
	);
});

test('absent client roots fall back to normal chunking with the shared keyboard helper preserved', async () => {
	const fixture = createFixture();
	delete fixture.input.app;
	delete fixture.graph[fixture.paths.app];
	const { chunks, warnings } = await generate(fixture);
	assert.deepEqual(warnings, []);
	assert.equal(
		chunks.some((chunk) => chunk.name.startsWith('buddy-')),
		false
	);
	assert.equal(chunkForModule(chunks, fixture.paths.keyboard).name, 'keyboard-names');
	assertDeferredModulesStayLazy(fixture, chunks);
});

test('keyboard-only policy used by SSR does not batch route closures', async () => {
	const fixture = createFixture();
	const { chunks, warnings } = await generate(fixture, separateSharedKeyboardHelper);
	assert.deepEqual(warnings, []);
	assert.equal(
		chunks.some((chunk) => chunk.name.startsWith('buddy-')),
		false
	);
	assert.equal(chunkForModule(chunks, fixture.paths.keyboard).name, 'keyboard-names');
	assertDeferredModulesStayLazy(fixture, chunks);
});
