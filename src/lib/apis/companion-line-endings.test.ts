import { describe, expect, it } from 'vitest';

import { detectLineEnding, toEditorText, toFileText } from './companion';

/** What a textarea's value reports for text it was given. */
function throughTextarea(text: string): string {
	return text.replace(/\r\n?/g, '\n');
}

describe('Runner project file line endings', () => {
	it('detects the line ending most lines use', () => {
		expect(detectLineEnding('@echo off\r\ngoto end\r\n:end\r\n')).toBe('\r\n');
		expect(detectLineEnding('one\ntwo\n')).toBe('\n');
		expect(detectLineEnding('one\rtwo\r')).toBe('\r');
		expect(detectLineEnding('one\r\ntwo\r\nthree\n')).toBe('\r\n');
		expect(detectLineEnding('one\ntwo\nthree\r\n')).toBe('\n');
		expect(detectLineEnding('no line breaks')).toBe('\n');
	});

	it('saves an edited CRLF file with CRLF line endings', () => {
		const original = '@echo off\r\ngoto end\r\n:end\r\n';
		const lineEnding = detectLineEnding(original);
		const edited = throughTextarea(toEditorText(original)).replace('goto end', 'goto done');
		expect(toFileText(edited, lineEnding)).toBe('@echo off\r\ngoto done\r\n:end\r\n');
	});

	it('round-trips CRLF and LF files unchanged when nothing was edited', () => {
		for (const original of ['a\r\nb\r\n', 'a\nb\n', 'a\rb\r', 'single line']) {
			const editorText = throughTextarea(toEditorText(original));
			expect(editorText).toBe(toEditorText(original));
			expect(toFileText(editorText, detectLineEnding(original))).toBe(original);
		}
	});
});
