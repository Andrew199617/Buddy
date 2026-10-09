import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';

const sourceUrl = new URL('../../src/lib/utils/mobile-composer-menu.ts', import.meta.url);
const source = readFileSync(sourceUrl, 'utf8');
const compiled = ts.transpileModule(source, {
	compilerOptions: {
		module: ts.ModuleKind.ESNext,
		target: ts.ScriptTarget.ES2022
	},
	fileName: sourceUrl.pathname
});
const moduleUrl =
	'data:text/javascript;base64,' + Buffer.from(compiled.outputText).toString('base64');
const { getMobileComposerMenuMaxHeight, getMobileComposerMenuPosition } = await import(moduleUrl);

const keyboardViewport = { left: 0, top: 0, width: 390, height: 464 };
const anchor = { left: 20, right: 56, top: 392 };

test('focused menus use half of the visible keyboard viewport', () => {
	assert.equal(getMobileComposerMenuMaxHeight(anchor, keyboardViewport, true), 232);
});

test('focused menus stay compact on tall phones', () => {
	const viewport = { left: 0, top: 0, width: 428, height: 844 };
	const lowAnchor = { left: 20, right: 56, top: 760 };
	assert.equal(getMobileComposerMenuMaxHeight(lowAnchor, viewport, true), 240);
});

test('idle menus retain the available space above their trigger', () => {
	assert.equal(getMobileComposerMenuMaxHeight(anchor, keyboardViewport, false), 376);
	const viewport = { left: 0, top: 0, width: 428, height: 844 };
	const lowAnchor = { left: 20, right: 56, top: 760 };
	assert.equal(getMobileComposerMenuMaxHeight(lowAnchor, viewport, false), 744);
});

test('tight space falls back to visible height before applying the focused cap', () => {
	const highAnchor = { left: 20, right: 56, top: 100 };
	assert.equal(getMobileComposerMenuMaxHeight(highAnchor, keyboardViewport, false), 448);
	assert.equal(getMobileComposerMenuMaxHeight(highAnchor, keyboardViewport, true), 232);
});

test('the 160px fallback boundary preserves useful space above the trigger', () => {
	const belowBoundary = { left: 20, right: 56, top: 175 };
	const atBoundary = { left: 20, right: 56, top: 176 };
	const aboveBoundary = { left: 20, right: 56, top: 177 };
	assert.equal(getMobileComposerMenuMaxHeight(belowBoundary, keyboardViewport, false), 448);
	assert.equal(getMobileComposerMenuMaxHeight(atBoundary, keyboardViewport, false), 160);
	assert.equal(getMobileComposerMenuMaxHeight(aboveBoundary, keyboardViewport, false), 161);
	assert.equal(getMobileComposerMenuMaxHeight(belowBoundary, keyboardViewport, true), 232);
	assert.equal(getMobileComposerMenuMaxHeight(atBoundary, keyboardViewport, true), 160);
});

test('focused menus keep a usable minimum when the viewport can fit it', () => {
	const viewport = { left: 0, top: 0, width: 390, height: 160 };
	const lowAnchor = { left: 20, right: 56, top: 144 };
	assert.equal(getMobileComposerMenuMaxHeight(lowAnchor, viewport, true), 96);
});

test('very short viewports override the focused minimum instead of overflowing', () => {
	const viewport = { left: 0, top: 0, width: 390, height: 80 };
	const lowAnchor = { left: 20, right: 56, top: 70 };
	assert.equal(getMobileComposerMenuMaxHeight(lowAnchor, viewport, true), 64);
});

test('panning the viewport and trigger together preserves the available menu height', () => {
	const viewport = { left: 0, top: 48, width: 390, height: 464 };
	const pannedAnchor = { left: 20, right: 56, top: 440 };
	assert.equal(getMobileComposerMenuMaxHeight(pannedAnchor, viewport, true), 232);
	assert.equal(getMobileComposerMenuMaxHeight(pannedAnchor, viewport, false), 376);
});

test('menus leave an 8px gap above the trigger when there is room', () => {
	const size = { width: 248, height: 232 };
	const position = getMobileComposerMenuPosition(anchor, keyboardViewport, size, 'start');
	assert.deepEqual(position, { top: 152, left: 20 });
	assert.equal(anchor.top - position.top - size.height, 8);
});

test('menu positions follow a 48px visual viewport pan', () => {
	const viewport = { left: 0, top: 48, width: 390, height: 464 };
	const pannedAnchor = { left: 20, right: 56, top: 440 };
	const size = { width: 248, height: 232 };
	assert.deepEqual(getMobileComposerMenuPosition(pannedAnchor, viewport, size, 'start'), {
		top: 200,
		left: 20
	});
});

test('end alignment uses the right edge of the trigger', () => {
	const viewport = { left: 12, top: 48, width: 390, height: 464 };
	const rightAnchor = { left: 312, right: 348, top: 440 };
	const size = { width: 248, height: 232 };
	assert.deepEqual(getMobileComposerMenuPosition(rightAnchor, viewport, size, 'end'), {
		top: 200,
		left: 100
	});
});

test('horizontal clamps keep both alignments inside the viewport gutters', () => {
	const size = { width: 248, height: 200 };
	const leftAnchor = { left: -30, right: 6, top: 392 };
	const rightAnchor = { left: 370, right: 406, top: 392 };
	assert.deepEqual(getMobileComposerMenuPosition(leftAnchor, keyboardViewport, size, 'start'), {
		top: 184,
		left: 8
	});
	assert.deepEqual(getMobileComposerMenuPosition(rightAnchor, keyboardViewport, size, 'end'), {
		top: 184,
		left: 134
	});
});

test('menus near the top edge stay below the visible top gutter', () => {
	const highAnchor = { left: 20, right: 56, top: 60 };
	const size = { width: 248, height: 160 };
	assert.deepEqual(getMobileComposerMenuPosition(highAnchor, keyboardViewport, size, 'start'), {
		top: 8,
		left: 20
	});
});

test('menus near the lower viewport edge stay above its bottom gutter', () => {
	const viewport = { left: 0, top: 48, width: 390, height: 464 };
	const outsideAnchor = { left: 20, right: 56, top: 900 };
	const size = { width: 248, height: 232 };
	assert.deepEqual(getMobileComposerMenuPosition(outsideAnchor, viewport, size, 'start'), {
		top: 272,
		left: 20
	});
});

test('landscape positioning respects viewport origins and right-edge gutters', () => {
	const viewport = { left: 24, top: 48, width: 812, height: 264 };
	const rightAnchor = { left: 770, right: 806, top: 312 };
	const size = { width: 300, height: 132 };
	assert.equal(getMobileComposerMenuMaxHeight(rightAnchor, viewport, true), 132);
	assert.deepEqual(getMobileComposerMenuPosition(rightAnchor, viewport, size, 'start'), {
		top: 172,
		left: 528
	});
});

test('a menu that exactly fills the usable height keeps both vertical gutters', () => {
	const viewport = { left: 24, top: 48, width: 390, height: 464 };
	const lowAnchor = { left: 44, right: 80, top: 490 };
	const size = { width: 248, height: 448 };
	assert.deepEqual(getMobileComposerMenuPosition(lowAnchor, viewport, size, 'start'), {
		top: 56,
		left: 44
	});
});

test('oversized measured content keeps its leading edges visible', () => {
	const viewport = { left: 24, top: 48, width: 200, height: 100 };
	const lowAnchor = { left: 180, right: 216, top: 140 };
	const size = { width: 300, height: 200 };
	assert.deepEqual(getMobileComposerMenuPosition(lowAnchor, viewport, size, 'end'), {
		top: 56,
		left: 32
	});
});

test('zero-size viewport and menu fixtures produce finite nonnegative values', () => {
	const viewport = { left: 0, top: 0, width: 0, height: 0 };
	const emptyAnchor = { left: 0, right: 0, top: 0 };
	const size = { width: 0, height: 0 };
	assert.equal(getMobileComposerMenuMaxHeight(emptyAnchor, viewport, true), 0);
	const position = getMobileComposerMenuPosition(emptyAnchor, viewport, size, 'start');
	assert.deepEqual(position, { top: 8, left: 8 });
	assert.ok(Number.isFinite(position.top));
	assert.ok(Number.isFinite(position.left));
});
