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
const anchor = { left: 20, right: 56, top: 392, height: 36 };

test('focused menus use at most 60 percent of the keyboard-visible viewport', () => {
	assert.equal(getMobileComposerMenuMaxHeight(anchor, keyboardViewport, true), 278.4);
});

test('focused menus scale with a tall phone viewport without the old 240px ceiling', () => {
	const viewport = { left: 0, top: 0, width: 428, height: 844 };
	const lowAnchor = { left: 20, right: 56, top: 760, height: 36 };
	assert.equal(getMobileComposerMenuMaxHeight(lowAnchor, viewport, true), 506.4);
});

test('focused height is independent of the space above the pressed button', () => {
	const highAnchor = { left: 20, right: 56, top: 100, height: 36 };
	assert.equal(getMobileComposerMenuMaxHeight(highAnchor, keyboardViewport, true), 278.4);
	assert.equal(getMobileComposerMenuMaxHeight(anchor, keyboardViewport, true), 278.4);
});

test('idle menus retain the available space above their trigger', () => {
	assert.equal(getMobileComposerMenuMaxHeight(anchor, keyboardViewport, false), 376);
	const viewport = { left: 0, top: 0, width: 428, height: 844 };
	const lowAnchor = { left: 20, right: 56, top: 760, height: 36 };
	assert.equal(getMobileComposerMenuMaxHeight(lowAnchor, viewport, false), 744);
});

test('idle tight space still falls back to the visible height', () => {
	const highAnchor = { left: 20, right: 56, top: 100, height: 36 };
	assert.equal(getMobileComposerMenuMaxHeight(highAnchor, keyboardViewport, false), 448);
});

test('the idle 160px fallback boundary preserves useful space above the trigger', () => {
	const belowBoundary = { left: 20, right: 56, top: 175, height: 36 };
	const atBoundary = { left: 20, right: 56, top: 176, height: 36 };
	const aboveBoundary = { left: 20, right: 56, top: 177, height: 36 };
	assert.equal(getMobileComposerMenuMaxHeight(belowBoundary, keyboardViewport, false), 448);
	assert.equal(getMobileComposerMenuMaxHeight(atBoundary, keyboardViewport, false), 160);
	assert.equal(getMobileComposerMenuMaxHeight(aboveBoundary, keyboardViewport, false), 161);
	assert.equal(getMobileComposerMenuMaxHeight(belowBoundary, keyboardViewport, true), 278.4);
	assert.equal(getMobileComposerMenuMaxHeight(atBoundary, keyboardViewport, true), 278.4);
});

test('short focused viewports use 60 percent without an artificial minimum', () => {
	const viewport = { left: 0, top: 0, width: 390, height: 80 };
	const lowAnchor = { left: 20, right: 56, top: 70, height: 36 };
	assert.equal(getMobileComposerMenuMaxHeight(lowAnchor, viewport, true), 48);
});

test('tiny focused viewports preserve edge gutters even when the ratio cap is larger', () => {
	const viewport = { left: 0, top: 0, width: 390, height: 20 };
	const lowAnchor = { left: 20, right: 56, top: 8, height: 4 };
	const size = { width: 248, height: 4 };
	assert.equal(getMobileComposerMenuMaxHeight(lowAnchor, viewport, true), 4);
	assert.deepEqual(getMobileComposerMenuPosition(lowAnchor, viewport, size, 'start', true), {
		top: 8,
		left: 20
	});
});

test('focused menus center on the pressed button when the measured height fits', () => {
	const middleAnchor = { left: 20, right: 56, top: 230, height: 36 };
	const size = { width: 248, height: 200 };
	const position = getMobileComposerMenuPosition(
		middleAnchor,
		keyboardViewport,
		size,
		'start',
		true
	);
	assert.deepEqual(position, { top: 148, left: 20 });
	assert.equal(position.top + size.height / 2, middleAnchor.top + middleAnchor.height / 2);
});

test('naturally short menus retain their measured size while centering on the button', () => {
	const middleAnchor = { left: 20, right: 56, top: 230, height: 36 };
	const size = { width: 248, height: 80 };
	const maximumHeight = getMobileComposerMenuMaxHeight(middleAnchor, keyboardViewport, true);
	const position = getMobileComposerMenuPosition(
		middleAnchor,
		keyboardViewport,
		size,
		'start',
		true
	);
	assert.ok(size.height < maximumHeight);
	assert.deepEqual(position, { top: 208, left: 20 });
	assert.equal(position.top + size.height / 2, 248);
});

test('focused menus near the keyboard clamp above its boundary', () => {
	const size = { width: 248, height: 278.4 };
	const position = getMobileComposerMenuPosition(anchor, keyboardViewport, size, 'start', true);
	assert.ok(Math.abs(position.top - 177.6) < 0.001);
	assert.equal(position.left, 20);
	assert.equal(position.top + size.height, 456);
});

test('focused menus near the top clamp below the visible top gutter', () => {
	const highAnchor = { left: 20, right: 56, top: 30, height: 36 };
	const size = { width: 248, height: 200 };
	assert.deepEqual(
		getMobileComposerMenuPosition(highAnchor, keyboardViewport, size, 'start', true),
		{
			top: 8,
			left: 20
		}
	);
});

test('focused geometry follows a 48px visual viewport pan', () => {
	const viewport = { left: 0, top: 48, width: 390, height: 464 };
	const pannedAnchor = { left: 20, right: 56, top: 278, height: 36 };
	const size = { width: 248, height: 200 };
	assert.equal(getMobileComposerMenuMaxHeight(pannedAnchor, viewport, true), 278.4);
	assert.deepEqual(getMobileComposerMenuPosition(pannedAnchor, viewport, size, 'start', true), {
		top: 196,
		left: 20
	});
});

test('focused geometry follows a large 170px visual viewport pan', () => {
	const viewport = { left: 12, top: 170, width: 390, height: 464 };
	const pannedAnchor = { left: 312, right: 348, top: 400, height: 36 };
	const size = { width: 248, height: 200 };
	assert.equal(getMobileComposerMenuMaxHeight(pannedAnchor, viewport, true), 278.4);
	assert.deepEqual(getMobileComposerMenuPosition(pannedAnchor, viewport, size, 'end', true), {
		top: 318,
		left: 100
	});
});

test('idle menus leave an 8px gap above the trigger when there is room', () => {
	const size = { width: 248, height: 232 };
	const position = getMobileComposerMenuPosition(anchor, keyboardViewport, size, 'start');
	assert.deepEqual(position, { top: 152, left: 20 });
	assert.equal(anchor.top - position.top - size.height, 8);
});

test('idle menu positions still follow a 48px visual viewport pan', () => {
	const viewport = { left: 0, top: 48, width: 390, height: 464 };
	const pannedAnchor = { left: 20, right: 56, top: 440, height: 36 };
	const size = { width: 248, height: 232 };
	assert.equal(getMobileComposerMenuMaxHeight(pannedAnchor, viewport, false), 376);
	assert.deepEqual(getMobileComposerMenuPosition(pannedAnchor, viewport, size, 'start'), {
		top: 200,
		left: 20
	});
});

test('end alignment uses the right edge of the trigger', () => {
	const viewport = { left: 12, top: 48, width: 390, height: 464 };
	const rightAnchor = { left: 312, right: 348, top: 440, height: 36 };
	const size = { width: 248, height: 232 };
	assert.deepEqual(getMobileComposerMenuPosition(rightAnchor, viewport, size, 'end'), {
		top: 200,
		left: 100
	});
});

test('horizontal clamps keep both alignments inside the viewport gutters', () => {
	const size = { width: 248, height: 200 };
	const leftAnchor = { left: -30, right: 6, top: 230, height: 36 };
	const rightAnchor = { left: 370, right: 406, top: 230, height: 36 };
	assert.deepEqual(
		getMobileComposerMenuPosition(leftAnchor, keyboardViewport, size, 'start', true),
		{
			top: 148,
			left: 8
		}
	);
	assert.deepEqual(
		getMobileComposerMenuPosition(rightAnchor, keyboardViewport, size, 'end', true),
		{
			top: 148,
			left: 134
		}
	);
});

test('landscape focused geometry respects viewport origins and visible edge gutters', () => {
	const viewport = { left: 24, top: 48, width: 812, height: 264 };
	const rightAnchor = { left: 770, right: 806, top: 180, height: 36 };
	const size = { width: 300, height: 132 };
	assert.equal(getMobileComposerMenuMaxHeight(rightAnchor, viewport, true), 158.4);
	assert.deepEqual(getMobileComposerMenuPosition(rightAnchor, viewport, size, 'start', true), {
		top: 132,
		left: 528
	});
});

test('an idle menu that exactly fills the usable height keeps both vertical gutters', () => {
	const viewport = { left: 24, top: 48, width: 390, height: 464 };
	const lowAnchor = { left: 44, right: 80, top: 490, height: 36 };
	const size = { width: 248, height: 448 };
	assert.deepEqual(getMobileComposerMenuPosition(lowAnchor, viewport, size, 'start'), {
		top: 56,
		left: 44
	});
});

test('oversized measured content keeps its leading edges visible', () => {
	const viewport = { left: 24, top: 48, width: 200, height: 100 };
	const lowAnchor = { left: 180, right: 216, top: 140, height: 36 };
	const size = { width: 300, height: 200 };
	assert.deepEqual(getMobileComposerMenuPosition(lowAnchor, viewport, size, 'end', true), {
		top: 56,
		left: 32
	});
});

test('zero-size viewport and menu fixtures produce finite nonnegative values', () => {
	const viewport = { left: 0, top: 0, width: 0, height: 0 };
	const emptyAnchor = { left: 0, right: 0, top: 0, height: 0 };
	const size = { width: 0, height: 0 };
	assert.equal(getMobileComposerMenuMaxHeight(emptyAnchor, viewport, true), 0);
	const position = getMobileComposerMenuPosition(emptyAnchor, viewport, size, 'start', true);
	assert.deepEqual(position, { top: 8, left: 8 });
	assert.ok(Number.isFinite(position.top));
	assert.ok(Number.isFinite(position.left));
});
