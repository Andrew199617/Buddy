import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import ts from 'typescript';
import { CodeBlockLowlight } from '@tiptap/extension-code-block-lowlight';
import highlighterCore from 'highlight.js/lib/core';
import javascriptGrammar from 'highlight.js/lib/languages/javascript';
import xmlGrammar from 'highlight.js/lib/languages/xml';
import { createLowlight as createActualLowlight } from 'lowlight';
import { Schema } from 'prosemirror-model';
import { EditorState, Plugin } from 'prosemirror-state';
import { EditorView } from 'prosemirror-view';
import { history, undo, undoDepth } from 'prosemirror-history';
import * as Y from 'yjs';
import { ySyncPluginKey, yUndoPlugin, yUndoPluginKey } from 'y-prosemirror';

const root = new URL('../../', import.meta.url);
const bridgeSource = await readFile(new URL('src/lib/utils/lazy-highlighting.ts', root), 'utf8');
const editorSource = await readFile(
	new URL('src/lib/components/common/RichTextInput.svelte', root),
	'utf8'
);
const editorScript = editorSource.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
const editorAst = ts.createSourceFile(
	'RichTextInput.ts',
	editorScript,
	ts.ScriptTarget.ESNext,
	true,
	ts.ScriptKind.TS
);

function transpile(source) {
	return ts.transpileModule(source, {
		compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext }
	}).outputText;
}

function extractDeclaration(name) {
	for (const statement of editorAst.statements) {
		if (!ts.isVariableStatement(statement)) continue;
		if (
			statement.declarationList.declarations.some(
				(declaration) => declaration.name.getText(editorAst) === name
			)
		)
			return statement.getText(editorAst);
	}
	throw new Error(`Production declaration not found: ${name}`);
}

const wrapperCode = transpile(
	extractDeclaration('HIGHLIGHTING_READY') +
		'\n' +
		extractDeclaration('createRefreshableHighlightingPlugin')
);
const wrapperFactory = new Function(
	'Plugin',
	wrapperCode + '\nreturn { HIGHLIGHTING_READY, createRefreshableHighlightingPlugin };'
);
const { HIGHLIGHTING_READY, createRefreshableHighlightingPlugin } = wrapperFactory(Plugin);
const refreshCode = transpile(extractDeclaration('refreshHighlighting'));
function actualRefresh(editor) {
	const factory = new Function(
		'editor',
		'HIGHLIGHTING_READY',
		'let highlightingLoading = true; let highlightingError = true;\n' +
			refreshCode +
			'\nreturn refreshHighlighting;'
	);
	factory(editor, HIGHLIGHTING_READY)();
}

function deferred() {
	let resolve;
	let reject;
	const promise = new Promise((resolvePromise, rejectPromise) => {
		resolve = resolvePromise;
		reject = rejectPromise;
	});
	return { promise, resolve, reject };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));
function plain(code) {
	return { type: 'root', children: [{ type: 'text', value: code }], data: { relevance: 0 } };
}

function fakeLowlight() {
	const grammars = new Map();
	const aliases = new Map();
	return {
		register(language, grammarFactory) {
			const grammar = grammarFactory();
			grammars.set(language, grammar);
			for (const alias of grammar.aliases || []) aliases.set(alias, language);
		},
		listLanguages: () => [...grammars.keys()],
		registered: (language) => grammars.has(language) || aliases.has(language),
		highlight(language, code) {
			const resolved = aliases.get(language) || language;
			if (!grammars.has(resolved)) throw new Error('Fake language unavailable');
			return { ...plain(code), data: { language: resolved, relevance: 1 } };
		},
		highlightAuto(code) {
			return { ...plain(code), data: { language: 'javascript', relevance: 1 } };
		}
	};
}

function bridgeHarness(importOverride) {
	const calls = { highlight: 0, lowlight: 0 };
	const highlighter = {
		listLanguages: () => ['javascript', 'xml'],
		getLanguage: (name) => ({ aliases: name === 'javascript' ? ['js'] : ['html'] })
	};
	async function importModule(name) {
		if (name === 'highlight.js') calls.highlight += 1;
		if (name === 'lowlight') calls.lowlight += 1;
		if (importOverride) return importOverride(name, calls, highlighter);
		return name === 'highlight.js' ? { default: highlighter } : { createLowlight: fakeLowlight };
	}
	const compiled = transpile(bridgeSource)
		.replaceAll("import('highlight.js')", "importModule('highlight.js')")
		.replaceAll("import('lowlight')", "importModule('lowlight')")
		.replaceAll('export const ', 'const ');
	const factory = new Function(
		'importModule',
		compiled + '\nreturn { createLazyLowlight, loadSyntaxHighlighter };'
	);
	return { ...factory(importModule), calls, highlighter };
}

function callbackRecorder() {
	const counts = { loading: 0, ready: 0, errors: [] };
	return {
		counts,
		callbacks: {
			onLoading: () => {
				counts.loading += 1;
			},
			onReady: () => {
				counts.ready += 1;
			},
			onError: (error) => {
				counts.errors.push(error);
			}
		}
	};
}

test('blank code keeps the grammar packages deferred', async () => {
	const harness = bridgeHarness();
	const recorder = callbackRecorder();
	const facade = harness.createLazyLowlight(recorder.callbacks);
	assert.deepEqual(facade.listLanguages(), []);
	assert.equal(facade.registered('js'), false);
	assert.deepEqual(facade.highlightAuto('  \n'), plain('  \n'));
	facade.retry();
	await settle();
	assert.deepEqual(harness.calls, { highlight: 0, lowlight: 0 });
	assert.equal(recorder.counts.loading, 0);
});

test('delayed modules return unchanged plain text and share one pending import', async () => {
	const gate = deferred();
	const harness = bridgeHarness(async (name, calls, highlighter) =>
		name === 'highlight.js' ? gate.promise : { createLowlight: fakeLowlight }
	);
	const recorder = callbackRecorder();
	const facade = harness.createLazyLowlight(recorder.callbacks);
	assert.deepEqual(facade.highlight('js', 'const first = 1'), plain('const first = 1'));
	assert.deepEqual(facade.highlightAuto('const latest = 2'), plain('const latest = 2'));
	facade.retry();
	assert.deepEqual(harness.calls, { highlight: 1, lowlight: 1 });
	assert.equal(recorder.counts.loading, 1);
	assert.equal(recorder.counts.ready, 0);
	gate.resolve({ default: harness.highlighter });
	await settle();
	assert.equal(recorder.counts.ready, 1);
	assert.equal(facade.highlight('js', 'const latest = 2').data.language, 'javascript');
	assert.equal(facade.registered('html'), true);
	assert.equal(facade.highlight('html', '<p>Buddy</p>').data.language, 'xml');
});

test('multiple editor bridges deduplicate grammar packages but each refreshes once', async () => {
	const harness = bridgeHarness();
	const first = callbackRecorder();
	const second = callbackRecorder();
	const facadeOne = harness.createLazyLowlight(first.callbacks);
	const facadeTwo = harness.createLazyLowlight(second.callbacks);
	facadeOne.highlightAuto('first');
	facadeTwo.highlightAuto('second');
	await settle();
	assert.deepEqual(harness.calls, { highlight: 1, lowlight: 1 });
	assert.equal(first.counts.ready, 1);
	assert.equal(second.counts.ready, 1);
	assert.deepEqual(facadeOne.listLanguages(), ['javascript', 'xml']);
});

test('module rejection preserves plain text and explicit retry recovers', async () => {
	const harness = bridgeHarness(async (name, calls, highlighter) => {
		if (name === 'highlight.js' && calls.highlight === 1) throw new Error('Offline fixture');
		return name === 'highlight.js' ? { default: highlighter } : { createLowlight: fakeLowlight };
	});
	const recorder = callbackRecorder();
	const facade = harness.createLazyLowlight(recorder.callbacks);
	assert.deepEqual(facade.highlightAuto('original'), plain('original'));
	await settle();
	assert.equal(recorder.counts.errors.length, 1);
	assert.equal(recorder.counts.ready, 0);
	facade.retry();
	await settle();
	assert.deepEqual(harness.calls, { highlight: 2, lowlight: 2 });
	assert.equal(recorder.counts.ready, 1);
	assert.equal(facade.highlightAuto('original').data.relevance, 1);
});

test('actual compiled grammars retain JavaScript and HTML aliases through the lazy bridge', async () => {
	const highlighter = highlighterCore.newInstance();
	highlighter.registerLanguage('javascript', javascriptGrammar);
	highlighter.registerLanguage('xml', xmlGrammar);
	// Exercise the compiled grammar objects returned by getLanguage after use.
	highlighter.highlight('const buddy = 1;', { language: 'javascript' });
	highlighter.highlight('<p>Buddy</p>', { language: 'xml' });
	const harness = bridgeHarness(async (name) =>
		name === 'highlight.js' ? { default: highlighter } : { createLowlight: createActualLowlight }
	);
	const recorder = callbackRecorder();
	const facade = harness.createLazyLowlight(recorder.callbacks);
	facade.highlightAuto('const buddy = 1;');
	await settle();
	assert.equal(recorder.counts.errors.length, 0);
	assert.equal(facade.registered('js'), true);
	assert.equal(facade.registered('html'), true);
	const javascript = facade.highlight('js', 'const buddy = 1;');
	const html = facade.highlight('html', '<p>Buddy</p>');
	assert.ok(javascript.children.some((node) => node.type === 'element'));
	assert.ok(html.children.some((node) => node.type === 'element'));
});

test('highlight-only consumers deduplicate and retry failed syntax imports', async () => {
	const harness = bridgeHarness(async (name, calls, highlighter) => {
		if (calls.highlight === 1) throw new Error('Offline fixture');
		return { default: highlighter };
	});
	const first = harness.loadSyntaxHighlighter();
	assert.equal(harness.loadSyntaxHighlighter(), first);
	await assert.rejects(first, /Offline fixture/);
	const retried = harness.loadSyntaxHighlighter();
	assert.notEqual(retried, first);
	assert.equal(await retried, harness.highlighter);
	assert.deepEqual(harness.calls, { highlight: 2, lowlight: 0 });
});

const schema = new Schema({
	nodes: {
		doc: { content: 'block+' },
		paragraph: { content: 'text*', group: 'block' },
		codeBlock: { content: 'text*', group: 'block', attrs: { language: { default: 'javascript' } } },
		text: { group: 'inline' }
	}
});

function highlightingFixture() {
	let ready = false;
	const lowlight = {
		listLanguages: () => ['javascript'],
		registered: () => true,
		highlight(language, code) {
			if (!ready) return plain(code);
			return {
				type: 'root',
				children: [
					{
						type: 'element',
						properties: { className: ['hljs-keyword'] },
						children: [{ type: 'text', value: code }]
					}
				]
			};
		},
		highlightAuto(code) {
			return this.highlight('javascript', code);
		}
	};
	const nativePlugins = CodeBlockLowlight.config.addProseMirrorPlugins.call({
		name: 'codeBlock',
		options: { lowlight },
		parent: () => []
	});
	const native = nativePlugins.find((plugin) => plugin.key.startsWith('lowlight$'));
	const wrapped = createRefreshableHighlightingPlugin(native);
	const doc = schema.node(
		'doc',
		null,
		schema.node('codeBlock', null, schema.text('const buddy = 1'))
	);
	return {
		native,
		wrapped,
		doc,
		makeReady: () => {
			ready = true;
		}
	};
}

test('real metadata refresh preserves document, selection, plugin order and ordinary undo', () => {
	const fixture = highlightingFixture();
	const historyPlugin = history();
	assert.equal(createRefreshableHighlightingPlugin(historyPlugin), historyPlugin);
	let state = EditorState.create({
		schema,
		doc: fixture.doc,
		plugins: [historyPlugin, fixture.wrapped]
	});
	state = state.apply(state.tr.insertText('X', 2));
	assert.equal(undoDepth(state), 1);
	assert.equal(fixture.native.props.decorations(state).find().length, 0);
	const before = state;
	const editor = {
		isDestroyed: false,
		state,
		view: {
			dispatch(transaction) {
				assert.equal(transaction.docChanged, false);
				assert.equal(transaction.getMeta('addToHistory'), false);
				assert.equal(transaction.getMeta('preventUpdate'), true);
				state = state.apply(transaction);
			}
		}
	};
	fixture.makeReady();
	actualRefresh(editor);
	assert.equal(state.doc, before.doc);
	assert.ok(state.selection.eq(before.selection));
	assert.equal(state.plugins, before.plugins);
	assert.equal(undoDepth(state), 1);
	assert.ok(fixture.native.props.decorations(state).find().length > 0);
	assert.equal(
		undo(state, (transaction) => {
			state = state.apply(transaction);
		}),
		true
	);
	assert.equal(state.doc.textContent, fixture.doc.textContent);
});

test('real EditorView lifecycle retains Yjs undo subscriptions after highlighting', () => {
	const fixture = highlightingFixture();
	const ydoc = new Y.Doc();
	const fragment = ydoc.getXmlFragment('fixture');
	// Only the transport binding is mocked; Yjs undo and native view lifecycle are real.
	const syncPlugin = new Plugin({
		key: ySyncPluginKey,
		state: { init: () => ({ type: fragment, binding: null }), apply: (transaction, value) => value }
	});
	const undoPlugin = yUndoPlugin();
	let state = EditorState.create({
		schema,
		doc: fixture.doc,
		plugins: [syncPlugin, undoPlugin, fixture.wrapped]
	});
	const undoManager = yUndoPluginKey.getState(state).undoManager;
	let destroyCount = 0;
	const originalDestroy = undoManager.destroy.bind(undoManager);
	undoManager.destroy = () => {
		destroyCount += 1;
		originalDestroy();
	};
	const view = Object.create(EditorView.prototype);
	view.state = state;
	view.directPlugins = [];
	view.prevDirectPlugins = view.directPlugins;
	view.pluginViews = [undoPlugin.spec.view(view)];
	const first = new Y.XmlElement('paragraph');
	ydoc.transact(() => fragment.insert(0, [first]), ySyncPluginKey);
	assert.equal(undoManager.undoStack.length, 1);
	undoManager.stopCapturing();
	const before = state;
	fixture.makeReady();
	actualRefresh({
		isDestroyed: false,
		state,
		view: {
			dispatch(transaction) {
				state = state.apply(transaction);
				view.state = state;
				view.updatePluginViews(before);
			}
		}
	});
	assert.equal(destroyCount, 0);
	assert.equal(yUndoPluginKey.getState(state).undoManager, undoManager);
	ydoc.transact(() => fragment.insert(1, [new Y.XmlElement('paragraph')]), ySyncPluginKey);
	assert.equal(undoManager.undoStack.length, 2);
	undoManager.undo();
	assert.equal(fragment.length, 1);
	view.destroyPluginViews();
	ydoc.destroy();
});

test('highlighting callback is harmless after editor destruction', () => {
	let dispatched = false;
	actualRefresh({
		isDestroyed: true,
		view: {
			dispatch() {
				dispatched = true;
			}
		}
	});
	assert.equal(dispatched, false);
});
