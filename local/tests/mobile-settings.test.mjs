import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../web/mobile-settings.js', import.meta.url), 'utf8');
const sourceAst = ts.createSourceFile('mobile-settings.js', source, ts.ScriptTarget.ESNext, true);

function extractFunction(name) {
	let declaration;
	function visit(node) {
		if (ts.isFunctionDeclaration(node) && node.name?.text === name) declaration = node;
		ts.forEachChild(node, visit);
	}
	visit(sourceAst);
	assert.ok(declaration, `Production function not found: ${name}`);
	return declaration.getText(sourceAst);
}

// Execute the production gate and viewport helper; DOM-decoration helpers are observed stubs.
const createUpdate = new Function(
	'document',
	'window',
	'hooks',
	`let pendingFrame = null;
	let current = hooks.initialState;
	const createState = hooks.createState;
	const disposeState = hooks.disposeState;
	const decorateCategoryRows = hooks.decorateCategoryRows;
	const decorateCategoryPanel = hooks.decorateCategoryPanel;
	const updateHeading = hooks.updateHeading;
	${extractFunction('updateViewport')}
	${extractFunction('update')}
	return { update, getState: () => current };`
);

function createFixture({ width = 390, nav = null } = {}) {
	let viewportWidth = width;
	let activeNav = nav;
	const calls = [];
	const measurements = { viewportReads: 0, styleWrites: 0 };
	const viewport = { scale: 1, offsetTop: 0, offsetLeft: 0, width: 390, height: 844 };
	const document = {
		getElementById(id) {
			assert.equal(id, 'settings-tabs-container');
			return activeNav;
		}
	};
	const window = {
		get innerWidth() {
			measurements.viewportReads++;
			return viewportWidth;
		},
		innerHeight: 844,
		visualViewport: viewport
	};
	const hooks = {
		initialState: null,
		createState(selectedNav) {
			calls.push('create');
			const properties = new Map();
			return {
				nav: selectedNav,
				properties,
				modal: {
					isConnected: true,
					style: {
						setProperty(name, value) {
							measurements.styleWrites++;
							properties.set(name, value);
						}
					}
				}
			};
		},
		disposeState(state) {
			calls.push('dispose');
			state.disposed = true;
		},
		decorateCategoryRows() {
			calls.push('rows');
		},
		decorateCategoryPanel() {
			calls.push('panel');
		},
		updateHeading() {
			calls.push('heading');
		}
	};
	const actual = createUpdate(document, window, hooks);
	return {
		...actual,
		calls,
		measurements,
		viewport,
		setNav(value) {
			activeNav = value;
		},
		setWidth(value) {
			viewportWidth = value;
		}
	};
}

test('closed Settings ignores repeated unrelated mutations without viewport reads or writes', () => {
	const fixture = createFixture();
	for (let update = 0; update < 20; update++) fixture.update();
	assert.deepEqual(fixture.measurements, { viewportReads: 0, styleWrites: 0 });
	assert.deepEqual(fixture.calls, []);
	assert.equal(fixture.getState(), null);
});

test('opening phone Settings creates its state and decorates the native panels', () => {
	const fixture = createFixture();
	const nav = {};
	fixture.setNav(nav);
	fixture.update();
	assert.equal(fixture.getState().nav, nav);
	assert.deepEqual(fixture.calls, ['create', 'rows', 'panel', 'heading']);
	assert.equal(fixture.getState().properties.get('--owui-settings-height'), '844px');
	assert.equal(fixture.measurements.styleWrites, 4);
});

test('active Settings follows keyboard resize and viewport panning without recreating state', () => {
	const fixture = createFixture({ nav: {} });
	fixture.update();
	const state = fixture.getState();
	fixture.viewport.height = 420;
	fixture.viewport.offsetTop = 160;
	fixture.update();
	assert.equal(fixture.getState(), state);
	assert.equal(state.properties.get('--owui-settings-height'), '420px');
	assert.equal(state.properties.get('--owui-settings-top'), '160px');
	assert.equal(fixture.calls.filter((call) => call === 'create').length, 1);
});

test('closing Settings disposes once and later mutations take the inactive path', () => {
	const fixture = createFixture({ nav: {} });
	fixture.update();
	const state = fixture.getState();
	fixture.setNav(null);
	fixture.update();
	assert.ok(state.disposed);
	assert.equal(fixture.getState(), null);
	const afterClose = { ...fixture.measurements };
	for (let update = 0; update < 20; update++) fixture.update();
	assert.deepEqual(fixture.measurements, afterClose);
	assert.equal(fixture.calls.filter((call) => call === 'dispose').length, 1);
});

test('desktop transitions release phone state and returning to phone opens it again', () => {
	const fixture = createFixture({ nav: {} });
	fixture.update();
	const state = fixture.getState();
	fixture.setWidth(1280);
	fixture.update();
	assert.ok(state.disposed);
	assert.equal(fixture.getState(), null);
	fixture.setWidth(390);
	fixture.update();
	assert.ok(fixture.getState());
	assert.notEqual(fixture.getState(), state);
	assert.equal(fixture.calls.filter((call) => call === 'create').length, 2);
});

test('replacing or disconnecting the Settings modal disposes the previous state', () => {
	const fixture = createFixture({ nav: {} });
	fixture.update();
	const first = fixture.getState();
	fixture.setNav({});
	fixture.update();
	assert.ok(first.disposed);
	const second = fixture.getState();
	second.modal.isConnected = false;
	fixture.update();
	assert.ok(second.disposed);
	assert.notEqual(fixture.getState(), second);
	assert.equal(fixture.calls.filter((call) => call === 'dispose').length, 2);
});
