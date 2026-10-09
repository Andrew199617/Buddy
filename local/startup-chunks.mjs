/** @typedef {Parameters<import('rollup').GetManualChunk>[1]} ChunkGraph */

/** @param {string} id */
function normalizeModuleId(id) {
	return id.replaceAll('\\', '/');
}

/** @param {string} id */
export function separateSharedKeyboardHelper(id) {
	if (normalizeModuleId(id).includes('/node_modules/w3c-keyname/')) {
		// ProseMirror and the optional editor share only this tiny, independent helper.
		return 'keyboard-names';
	}
}

/** @param {string[]} moduleIds @param {string} suffix */
function findModuleId(moduleIds, suffix) {
	return moduleIds.find((id) => normalizeModuleId(id).endsWith(suffix));
}

/** @param {string} id */
function isGeneratedClientFacade(id) {
	const normalizedPath = normalizeModuleId(id);
	return /\/\.svelte-kit\/generated\/client(?:-optimized)?\/(?:app\.js|nodes\/)/.test(
		normalizedPath
	);
}

/** @param {string} id */
function isDeferredStartupModule(id) {
	const normalizedPath = normalizeModuleId(id);

	if (
		normalizedPath.includes('/node_modules/@codemirror/') ||
		normalizedPath.includes('/node_modules/codemirror/') ||
		normalizedPath.includes('/node_modules/codemirror-lang-') ||
		normalizedPath.includes('/node_modules/@lezer/') ||
		normalizedPath.includes('/node_modules/@xterm/') ||
		normalizedPath.includes('/node_modules/lowlight/')
	) {
		return true;
	}

	// Markdown may use Highlight.js core. Full grammars must remain on demand.
	if (
		normalizedPath.includes('/node_modules/highlight.js/lib/languages/') ||
		normalizedPath.includes('/node_modules/highlight.js/es/languages/') ||
		/\/node_modules\/highlight\.js\/(?:lib|es)\/(?:index|common)\.js/.test(normalizedPath)
	) {
		return true;
	}

	if (
		normalizedPath.includes('/node_modules/dayjs/locale/') ||
		/\/src\/lib\/i18n\/locales\/[^/]+\/translation\.json/.test(normalizedPath)
	) {
		return true;
	}

	return (
		normalizedPath.includes('/src/lib/components/chat/SettingsModal.svelte') ||
		normalizedPath.includes('/src/lib/components/chat/MessageInput/CallOverlay.svelte') ||
		normalizedPath.includes('/src/lib/components/chat/Artifacts.svelte') ||
		normalizedPath.includes('/src/lib/components/chat/ChatControls/Embeds.svelte') ||
		normalizedPath.includes('/src/lib/components/chat/FileNav.svelte') ||
		normalizedPath.includes('/src/lib/components/chat/PyodideFileNav.svelte') ||
		normalizedPath.includes('/src/lib/components/chat/Overview.svelte') ||
		normalizedPath.includes('/src/lib/components/chat/Messages/CodeBlock.svelte') ||
		normalizedPath.includes('/src/lib/components/chat/Messages/OutputEditView.svelte') ||
		normalizedPath.includes('/src/lib/components/chat/Messages/TerminalOutputFile.svelte')
	);
}

/** @param {string} root @param {ChunkGraph} graph */
function collectStaticClosure(root, graph) {
	const visited = new Set();
	const remaining = [root];

	while (remaining.length > 0) {
		const id = remaining.pop();
		if (id === undefined || visited.has(id)) {
			continue;
		}
		const moduleInfo = graph.getModuleInfo(id);
		if (!moduleInfo || moduleInfo.isExternal) {
			continue;
		}
		visited.add(id);
		// Dynamic imports are intentionally excluded, even when the target is a route entry.
		for (const dependency of moduleInfo.importedIds) {
			remaining.push(dependency);
		}
	}
	return visited;
}

/**
 * @param {Set<string>} closure
 * @param {string} name
 * @param {Map<string, string>} assignments
 */
function assignStaticClosure(closure, name, assignments) {
	for (const id of closure) {
		if (isDeferredStartupModule(id)) {
			// importedIds also includes tree-shaken static re-exports. Reject a barrel
			// that would otherwise drag a deferred editor or grammar into this chunk.
			throw new Error(
				`Startup chunk "${name}" reached deferred dependency: ${id}. Keep this dependency behind a dynamic import.`
			);
		}
		if (!assignments.has(id)) {
			assignments.set(id, name);
		}
	}
}

/** @param {ChunkGraph} graph */
function createStartupAssignments(graph) {
	const moduleIds = Array.from(graph.getModuleIds());
	let clientApp = findModuleId(moduleIds, '/.svelte-kit/generated/client-optimized/app.js');
	if (!clientApp) {
		clientApp = findModuleId(moduleIds, '/.svelte-kit/generated/client/app.js');
	}
	const runtimeEntry = findModuleId(
		moduleIds,
		'/node_modules/@sveltejs/kit/src/runtime/client/entry.js'
	);
	const rootLayout = findModuleId(moduleIds, '/src/routes/+layout.svelte');
	const appLayout = findModuleId(moduleIds, '/src/routes/(app)/+layout.svelte');
	const chatPage = findModuleId(moduleIds, '/src/routes/(app)/+page.svelte');
	const assignments = new Map();

	// SSR, workers, and a different SvelteKit output strategy keep normal chunking.
	if (!clientApp || !runtimeEntry || !rootLayout || !appLayout || !chatPage) {
		return assignments;
	}

	assignStaticClosure(collectStaticClosure(rootLayout, graph), 'buddy-boot', assignments);
	assignStaticClosure(collectStaticClosure(runtimeEntry, graph), 'buddy-boot', assignments);
	assignStaticClosure(collectStaticClosure(clientApp, graph), 'buddy-boot', assignments);
	let rootOptions = findModuleId(moduleIds, '/src/routes/+layout.js');
	if (!rootOptions) {
		rootOptions = findModuleId(moduleIds, '/src/routes/+layout.ts');
	}
	if (rootOptions) {
		assignStaticClosure(collectStaticClosure(rootOptions, graph), 'buddy-boot', assignments);
	}
	assignStaticClosure(collectStaticClosure(appLayout, graph), 'buddy-shell', assignments);
	assignStaticClosure(collectStaticClosure(chatPage, graph), 'buddy-chat', assignments);
	return assignments;
}

/**
 * Batch only the three initial static closures. Earlier groups take shared modules,
 * so chat depends on shell/boot without splitting existing source cycles. Deferred
 * features can use these shared modules without becoming part of the initial graph.
 * @returns {import('rollup').GetManualChunk}
 */
export function createStartupChunks() {
	/** @type {Map<string, string> | null} */
	let assignments = null;

	/** @type {import('rollup').GetManualChunk} */
	function assignStartupChunk(id, graph) {
		if (!assignments) {
			assignments = createStartupAssignments(graph);
		}
		const keyboardChunk = separateSharedKeyboardHelper(id);
		if (keyboardChunk) {
			return keyboardChunk;
		}
		const moduleInfo = graph.getModuleInfo(id);
		if (moduleInfo?.isEntry || isGeneratedClientFacade(id)) {
			return;
		}
		return assignments.get(id);
	}

	return assignStartupChunk;
}
