import type { createLowlight } from 'lowlight';

type Lowlight = ReturnType<typeof createLowlight>;
type SyntaxHighlighter = typeof import('highlight.js').default;

let syntaxHighlighterPromise: Promise<SyntaxHighlighter> | null = null;
let lowlightPromise: Promise<Lowlight> | null = null;

export const loadSyntaxHighlighter = (): Promise<SyntaxHighlighter> => {
	if (!syntaxHighlighterPromise) {
		syntaxHighlighterPromise = import('highlight.js')
			.then((module) => module.default)
			.catch((error) => {
				syntaxHighlighterPromise = null;
				throw error;
			});
	}
	return syntaxHighlighterPromise;
};

const loadLowlight = (): Promise<Lowlight> => {
	if (!lowlightPromise) {
		lowlightPromise = Promise.all([import('lowlight'), loadSyntaxHighlighter()])
			.then(([module, highlighter]) => {
				const lowlight = module.createLowlight();
				for (const language of highlighter.listLanguages()) {
					const grammar = highlighter.getLanguage(language);
					if (grammar) {
						lowlight.register(language, () => grammar);
					}
				}
				return lowlight;
			})
			.catch((error) => {
				lowlightPromise = null;
				throw error;
			});
	}
	return lowlightPromise;
};

type HighlightingCallbacks = {
	onLoading: () => void;
	onReady: () => void;
	onError: (error: unknown) => void;
};

/** Plain text remains editable while code highlighting loads on its first use. */
export const createLazyLowlight = (callbacks: HighlightingCallbacks) => {
	let lowlight: Lowlight | null = null;
	let pending: Promise<void> | null = null;
	let latestCode = '';

	const loadWhenNeeded = (code: string) => {
		latestCode = code;
		if (lowlight || pending || !code.trim()) {
			return;
		}
		callbacks.onLoading();
		pending = loadLowlight()
			.then((loaded) => {
				lowlight = loaded;
				callbacks.onReady();
			})
			.catch(callbacks.onError)
			.finally(() => {
				pending = null;
			});
	};

	const plainText = (code: string) => ({
		type: 'root' as const,
		children: [{ type: 'text' as const, value: code }],
		data: { relevance: 0 }
	});

	return {
		retry: () => loadWhenNeeded(latestCode),
		listLanguages: () => lowlight?.listLanguages() ?? [],
		registered: (language: string) => lowlight?.registered(language) ?? false,
		highlight: (language: string, code: string) => {
			if (lowlight) {
				return lowlight.highlight(language, code);
			}
			loadWhenNeeded(code);
			return plainText(code);
		},
		highlightAuto: (code: string) => {
			if (lowlight) {
				return lowlight.highlightAuto(code);
			}
			loadWhenNeeded(code);
			return plainText(code);
		}
	};
};
