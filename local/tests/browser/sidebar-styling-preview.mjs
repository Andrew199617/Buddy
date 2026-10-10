import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { chats, config, createMockedPage, outputDirectory, user } from './mobile-ui-preview.mjs';

const directory = resolve(outputDirectory, 'sidebar-styling');
mkdirSync(directory, { recursive: true });
const initialUser = structuredClone(user);
const initialConfig = structuredClone(config);
const results = [];
const pinnedNavigation = ['notes', 'workspace', 'automations'];
// Chat search pages hold this many chats (backend routers/chats.py).
const searchPageSize = 60;

function projectSearchPage(searchPage) {
	if (searchPage === 1) {
		const firstMatch = { ...chats[0], snippet: 'Project plan search match.' };
		const otherMatches = Array.from({ length: searchPageSize - 1 }, (_, index) => ({
			...chats[(index + 1) % chats.length],
			id: `project-result-${index + 1}`,
			title: `Project result ${index + 1}`
		}));
		return [firstMatch, ...otherMatches];
	}
	if (searchPage === 2) {
		return [{ ...chats[1], id: 'project-result-page-2', title: 'Project result on page 2' }];
	}
	return [];
}

function configureNavigation({ knowledgeOnly = false, hiddenWorkspace = false } = {}) {
	Object.assign(user, structuredClone(initialUser));
	Object.assign(config, structuredClone(initialConfig));
	config.features.enable_notes = true;
	config.features.enable_automations = true;
	config.features.enable_calendar = true;
	user.permissions.features.notes = true;
	user.permissions.features.automations = true;
	user.permissions.features.calendar = true;
	user.permissions.workspace = { knowledge: true, models: true, prompts: true };
	if (knowledgeOnly) user.permissions.workspace = { knowledge: true };
	if (hiddenWorkspace) {
		// Workspace remains a sidebar destination, but tools are gated from the
		// bottom dock until the plugins feature is enabled.
		user.permissions.workspace = { tools: true };
		config.features.enable_plugins = false;
	}
	config.permissions = user.permissions;
}

async function assertHeaderFits(page) {
	await page.waitForFunction(() => {
		const sidebar = document.getElementById('sidebar');
		return sidebar?.dataset.state === 'true' && Math.abs(sidebar.getBoundingClientRect().left) < 1;
	});
	const geometry = await page.locator('.buddy-sidebar-header').evaluate((header) => {
		const row = header.getBoundingClientRect();
		const controls = [...header.querySelectorAll('button, input')].map((element) => {
			const box = element.getBoundingClientRect();
			return {
				id: element.id,
				left: box.left,
				right: box.right,
				width: box.width,
				height: box.height
			};
		});
		return {
			left: row.left,
			right: row.right,
			height: row.height,
			viewportWidth: innerWidth,
			controls
		};
	});
	assert.ok(geometry.height <= 60, 'Header stays on one row');
	assert.ok(
		geometry.left >= 0 && geometry.right <= geometry.viewportWidth,
		'Header stays within the viewport'
	);
	for (const control of geometry.controls) {
		assert.ok(control.left >= geometry.left - 1, `${control.id} stays inside the sidebar`);
		assert.ok(control.right <= geometry.right + 1, `${control.id} stays inside the sidebar`);
		assert.ok(control.height >= 44, `${control.id} has a comfortable touch target`);
	}
	return geometry;
}

async function assertNoRuntimeErrors(session) {
	const runtimeErrors = session.errors.filter((error) =>
		/TypeError|ReferenceError|SyntaxError|RangeError/.test(error)
	);
	assert.deepEqual(runtimeErrors, []);
}

async function assertSidebarArrow(page, controlId, expectedDirection) {
	const direction = await page
		.locator(`#${controlId} svg path`)
		.last()
		.evaluate((arrow) => {
			const length = arrow.getTotalLength();
			const matrix = arrow.getScreenCTM();
			const toScreen = (point) => new DOMPoint(point.x, point.y).matrixTransform(matrix);
			const start = toScreen(arrow.getPointAtLength(0));
			const tip = toScreen(arrow.getPointAtLength(length / 2));
			const end = toScreen(arrow.getPointAtLength(length));
			if (tip.x < start.x && tip.x < end.x) return 'left';
			if (tip.x > start.x && tip.x > end.x) return 'right';
			return 'indeterminate';
		});
	assert.equal(
		direction,
		expectedDirection,
		`${controlId} arrow follows the physical sidebar edge`
	);
}

async function isFocusInSidebar(page) {
	return page.locator('#sidebar').evaluate((sidebar) => sidebar.contains(document.activeElement));
}

// Escape pressed outside the sidebar is not the sidebar's to handle: the open search keeps
// its query and the sidebar does not take focus. (The composer's hidden voice recorder
// moves focus to the composer on any Escape, so focus is not expected to stay put.)
async function assertEscapeOutsideSidebarKeepsSearch(page, place, focusOutsideSidebar) {
	await page.locator('#sidebar-search-button').click();
	await page.locator('#sidebar-search-input').fill('nothing');
	await focusOutsideSidebar();
	assert.equal(
		await isFocusInSidebar(page),
		false,
		`Focus is on the ${place}, outside the sidebar`
	);

	await page.keyboard.press('Escape');
	await page.waitForTimeout(100);
	assert.equal(
		await page.locator('#sidebar-search-input').count(),
		1,
		`Escape on the ${place} keeps the search open`
	);
	assert.equal(await page.locator('#sidebar-search-input').inputValue(), 'nothing');
	assert.equal(
		await isFocusInSidebar(page),
		false,
		`Escape on the ${place} leaves focus outside the sidebar`
	);

	await page.locator('#sidebar-search-dismiss').click();
	await page.locator('#sidebar-webui-name').waitFor({ state: 'visible' });
}

async function assertProfileNavigation(page, expected, screenshotName) {
	await page.locator('#sidebar button[aria-label="User menu"]').click();
	const popup = page.locator('.app-dropdown-menu:visible');
	await popup.waitFor({ state: 'visible' });
	await page.waitForFunction(
		() => {
			const container = document.querySelector('[role="menu"]');
			return container && getComputedStyle(container).opacity === '1';
		},
		undefined,
		{ timeout: 5000 }
	);
	assert.equal(await popup.locator('a[href="/notes"]').count(), expected.notes ? 1 : 0);
	assert.equal(await popup.locator('a[href="/workspace"]').count(), expected.workspace ? 1 : 0);
	assert.equal(await popup.locator('a[href="/automations"]').count(), expected.automations ? 1 : 0);
	assert.equal(await popup.locator('a[href="/calendar"]').count(), 1, 'Unrelated Calendar remains');
	assert.equal(await popup.getByText('Settings', { exact: true }).count(), 1, 'Settings remains');
	if (screenshotName) {
		await page.screenshot({ path: resolve(directory, `${screenshotName}-profile-popup.png`) });
	}
	await page.keyboard.press('Escape');
	await popup.waitFor({ state: 'detached' });
	assert.equal(await page.locator('#sidebar').getAttribute('data-state'), 'true');
}

async function runHeaderScenario(options) {
	configureNavigation();
	const session = await createMockedPage({
		...options,
		polish: false,
		usage: false,
		modelSelectorVisible: false,
		pinnedMenuItems: pinnedNavigation
	});
	const { page, context } = session;
	try {
		if (options.colorScheme === 'dark') {
			assert.equal(
				await page.locator('html').evaluate((html) => html.classList.contains('dark')),
				true
			);
		}
		const searches = [];
		await context.route('**/api/v1/chats/search?**', async (route) => {
			const query = new URL(route.request().url()).searchParams.get('text');
			const searchPage = Number(new URL(route.request().url()).searchParams.get('page'));
			searches.push({ query, searchPage });
			if (query === 'slow') await new Promise((resolve) => setTimeout(resolve, 600));
			let found = [];
			if (query === 'project') found = projectSearchPage(searchPage);
			if (query === 'single') found = [{ ...chats[2], snippet: 'Only one match.' }];
			if (query === 'slow') found = [{ ...chats[1], title: 'Stale search result' }];
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(found)
			});
		});
		const newChat = page.locator('nav.buddy-chat-header button[aria-label="New Chat"]');
		await newChat.waitFor({ state: 'visible' });
		await assertSidebarArrow(page, 'sidebar-toggle-button', 'right');
		await page.locator('#sidebar-toggle-button').click();
		await page.locator('#buddy-sidebar-close').waitFor({ state: 'visible' });
		await assertSidebarArrow(page, 'buddy-sidebar-close', 'left');
		assert.equal(await page.locator('#sidebar-new-chat-link').count(), 0);
		assert.equal(await page.locator('#sidebar-toggle-button:visible').count(), 0);
		assert.equal(await page.locator('.buddy-sidebar-header #sidebar-search-button').count(), 1);
		assert.equal(await page.locator('#sidebar #sidebar-search-button').count(), 1);
		const mobileDrawer = options.viewport.width < 768;
		assert.equal(await page.locator('#sidebar-notes-button').count(), mobileDrawer ? 1 : 0);
		assert.equal(await page.locator('#sidebar-workspace-button').count(), mobileDrawer ? 1 : 0);
		assert.equal(await page.locator('#sidebar-automations-button').count(), mobileDrawer ? 1 : 0);
		const regularGeometry = await assertHeaderFits(page);
		await page.screenshot({ path: resolve(directory, `${options.name}-sidebar.png`) });
		await assertProfileNavigation(
			page,
			{ notes: mobileDrawer, workspace: mobileDrawer, automations: mobileDrawer },
			options.name
		);

		for (let repeat = 0; repeat < 3; repeat += 1) {
			await page.locator('#sidebar-search-button').click();
			await page.locator('#sidebar-search-input').waitFor({ state: 'visible' });
			assert.equal(await page.locator('#sidebar-webui-name').count(), 0);
			assert.equal(await page.locator('#buddy-sidebar-close:visible').count(), 1);
			assert.equal(await page.locator('.modal:visible').count(), 0);
			assert.equal(await page.locator('#sidebar-search-input').inputValue(), '');
			await assertHeaderFits(page);
			if (repeat === 1) await page.keyboard.press('Escape');
			else await page.locator('#sidebar-search-dismiss').click();
			await page.locator('#sidebar-webui-name').waitFor({ state: 'visible' });
			assert.equal(await page.locator('#sidebar').getAttribute('data-state'), 'true');
		}

		if (!mobileDrawer) {
			// Escape on a header button or the page body reaches the sidebar unhandled. The
			// composer already marks Escape as handled, so it is only an extra case.
			await assertEscapeOutsideSidebarKeepsSearch(page, 'header New Chat button', () =>
				newChat.focus()
			);
			await assertEscapeOutsideSidebarKeepsSearch(page, 'page body', () =>
				page.evaluate(() => document.activeElement.blur())
			);
			await assertEscapeOutsideSidebarKeepsSearch(page, 'composer', () =>
				page.locator('#chat-input').click()
			);
		}

		await page.locator('#sidebar-search-button').click();
		await page.locator('#sidebar-search-input').fill('project');
		await page.locator('#sidebar-search-results a').first().waitFor({ state: 'visible' });
		assert.match(
			await page.locator('#sidebar-search-results').innerText(),
			/Project plan search match/
		);
		const searchGeometry = await assertHeaderFits(page);
		await page.screenshot({ path: resolve(directory, `${options.name}-search.png`) });
		// Touch users reach the full search (filters, preview, chat actions) from the results.
		const advancedSearch = page.locator('#sidebar-search-advanced');
		assert.ok((await advancedSearch.boundingBox()).height >= (mobileDrawer ? 44 : 36));
		await advancedSearch.click();
		const fullSearch = page.locator('.modal').filter({ has: page.locator('#search-input') });
		await fullSearch.waitFor({ state: 'visible' });
		assert.equal(await fullSearch.locator('#search-input').inputValue(), 'project');
		await page.screenshot({ path: resolve(directory, `${options.name}-full-search.png`) });
		// The desktop sidebar stays open behind the full search with the inline results. On
		// phones the full search replaces the drawer, as Settings does.
		const sidebarState = mobileDrawer ? 'false' : 'true';
		assert.equal(await page.locator('#sidebar').getAttribute('data-state'), sidebarState);
		// Pressing on the backdrop closes the full search.
		await page.mouse.click(4, 4);
		await fullSearch.waitFor({ state: 'detached' });
		assert.equal(await page.locator('#sidebar').getAttribute('data-state'), sidebarState);
		if (mobileDrawer) {
			// The phone returns to the chat, so search again from a reopened drawer.
			await page.locator('#sidebar-toggle-button').click();
			await page.locator('#sidebar-search-button').click();
			await page.locator('#sidebar-search-input').fill('project');
			await page.locator('#sidebar-search-results a').first().waitFor({ state: 'visible' });
		}
		assert.equal(await page.locator('#sidebar-search-input').inputValue(), 'project');
		// A full page offers the next one; the short second page is the last.
		const loadMore = page.locator('#sidebar-search-load-more');
		await loadMore.click();
		await loadMore.waitFor({ state: 'detached' });
		assert.equal(await page.locator('#sidebar-search-results a').count(), searchPageSize + 1);
		assert.ok(
			searches.some((search) => search.query === 'project' && search.searchPage === 2),
			'Search supports pagination'
		);
		// Removing the focused Load more button hands focus to the first new result.
		await page.waitForFunction(
			() => document.activeElement?.getAttribute('href') === '/c/project-result-page-2'
		);
		await page.locator('#sidebar-search-input').fill('single');
		await page.locator('#sidebar-search-results').getByText('Only one match.').waitFor();
		assert.equal(await loadMore.count(), 0, 'A short first page offers no Load more');
		assert.ok(
			!searches.some((search) => search.query === 'single' && search.searchPage > 1),
			'A short first page requests no further pages'
		);
		await page.locator('#sidebar-search-input').fill('nothing');
		await page
			.locator('#sidebar-search-results')
			.getByText('No results found', { exact: true })
			.waitFor();
		await page.locator('#sidebar-search-input').fill('slow');
		await page.waitForTimeout(300);
		await page.locator('#sidebar-search-dismiss').click();
		await page.locator('#sidebar-search-button').click();
		await page.waitForTimeout(700);
		assert.equal(
			await page.locator('#sidebar-search-results').count(),
			0,
			'Dismissed requests cannot restore stale results'
		);
		assert.equal(await page.locator('#sidebar-search-input').inputValue(), '');
		await page.locator('#sidebar-search-input').fill('project');
		await page.locator('#sidebar-search-results a').first().waitFor({ state: 'visible' });
		await page.locator('#sidebar-search-input').press('Enter');
		await page.waitForURL('**/c/mobile-preview-chat-1');
		await page.locator('#chat-input').waitFor({ state: 'visible' });
		if (mobileDrawer) {
			await page.locator('#sidebar-toggle-button').click();
			await page.locator('#buddy-sidebar-close').waitFor({ state: 'visible' });
		}

		await page.locator('#buddy-sidebar-close').click();
		await page.locator('#sidebar-toggle-button').waitFor({ state: 'visible' });
		await assertSidebarArrow(page, 'sidebar-toggle-button', 'right');
		// Desktop and the mobile drawer both hand focus between the toggle and the close control.
		await page.waitForFunction(() => document.activeElement?.id === 'sidebar-toggle-button');
		await page.keyboard.press('Enter');
		await page.locator('#sidebar-search-button').waitFor({ state: 'visible' });
		await page.waitForFunction(() => document.activeElement?.id === 'buddy-sidebar-close');
		assert.equal(
			await page.locator('#sidebar-search-input').count(),
			0,
			'Closing the drawer resets search'
		);
		if (mobileDrawer) {
			await page.keyboard.press('Escape');
			await page.locator('#sidebar-toggle-button').waitFor({ state: 'visible' });
			await page.waitForFunction(() => document.activeElement?.id === 'sidebar-toggle-button');
			await page.locator('#sidebar-toggle-button').click();
			await page.locator('#buddy-sidebar-close').waitFor({ state: 'visible' });
		}
		await page.locator('#buddy-sidebar-close').click();
		await newChat.click();
		await page.waitForURL('**/');
		assert.equal(new URL(page.url()).pathname, '/');
		await page.locator('#chat-input').waitFor({ state: 'visible' });
		await assertNoRuntimeErrors(session);
		results.push({
			name: options.name,
			regularGeometry,
			searchGeometry,
			searches,
			newChatPreserved: true
		});
	} finally {
		await session.browser.close();
	}
}

async function runPermissionScenario({ name, knowledgeOnly, hiddenWorkspace }) {
	configureNavigation({ knowledgeOnly, hiddenWorkspace });
	const session = await createMockedPage({
		viewport: { width: 1280, height: 900 },
		isMobile: false,
		hasTouch: false,
		polish: false,
		usage: false,
		modelSelectorVisible: false,
		pinnedMenuItems: pinnedNavigation
	});
	const { page } = session;
	try {
		await page.locator('#sidebar-toggle-button').click();
		await page.locator('#buddy-sidebar-close').waitFor({ state: 'visible' });
		assert.equal(await page.locator('.buddy-dock a[aria-label="Notes"]').count(), 1);
		assert.equal(await page.locator('#sidebar-notes-button').count(), 0);
		assert.equal(await page.locator('.buddy-dock a[aria-label="Workspace"]').count(), 0);
		assert.equal(await page.locator('#sidebar-workspace-button').count(), hiddenWorkspace ? 1 : 0);
		assert.equal(await page.locator('#sidebar-automations-button').count(), 0);
		await assertProfileNavigation(page, {
			notes: false,
			workspace: Boolean(hiddenWorkspace),
			automations: false
		});
		if (knowledgeOnly) {
			assert.equal(
				await page.locator('.buddy-dock a[aria-label="Knowledge"]').getAttribute('href'),
				'/workspace/knowledge'
			);
		}
		await page.setViewportSize({ width: 390, height: 844 });
		await page.locator('#sidebar-toggle-button').waitFor({ state: 'visible' });
		await page.locator('#sidebar-toggle-button').click();
		await page.locator('#buddy-sidebar-close').waitFor({ state: 'visible' });
		assert.equal(await page.locator('#sidebar-notes-button').count(), 1);
		assert.equal(await page.locator('#sidebar-workspace-button').count(), 1);
		assert.equal(await page.locator('#sidebar-automations-button').count(), 1);
		await assertProfileNavigation(page, { notes: true, workspace: true, automations: true });
		await page.setViewportSize({ width: 1280, height: 900 });
		await page.locator('#buddy-sidebar-close').waitFor({ state: 'visible' });
		// The close control is already visible before resize handlers update the fallback links.
		await page.locator('#sidebar-notes-button').waitFor({ state: 'detached' });
		assert.equal(await page.locator('#sidebar-notes-button').count(), 0);
		assert.equal(await page.locator('#sidebar-workspace-button').count(), hiddenWorkspace ? 1 : 0);
		assert.equal(await page.locator('#sidebar-automations-button').count(), 0);
		await assertNoRuntimeErrors(session);
		results.push({
			name,
			desktopFallback: Boolean(hiddenWorkspace),
			mobileFallback: true,
			responsiveRestored: true
		});
	} finally {
		await session.browser.close();
	}
}

async function runKeyboardScenario() {
	configureNavigation();
	const session = await createMockedPage({
		viewport: { width: 1280, height: 900 },
		isMobile: false,
		hasTouch: true,
		polish: false,
		usage: false,
		modelSelectorVisible: false,
		pinnedMenuItems: pinnedNavigation
	});
	const { page } = session;
	try {
		await page.locator('#sidebar-toggle-button').click();
		await page.locator('#buddy-sidebar-close').waitFor({ state: 'visible' });
		assert.equal(await page.locator('#sidebar-notes-button').count(), 0);
		assert.equal(await page.locator('#sidebar-workspace-button').count(), 0);
		assert.equal(await page.locator('#sidebar-automations-button').count(), 0);
		await page.locator('#sidebar-search-button').click();
		await page.locator('#sidebar-search-input').waitFor({ state: 'visible' });
		await page.evaluate(() => {
			Object.defineProperty(window.visualViewport, 'height', {
				configurable: true,
				get: () => window.innerHeight - 300
			});
			window.visualViewport.dispatchEvent(new Event('resize'));
		});
		await page.locator('.buddy-dock-hidden').waitFor({ state: 'attached' });
		await page.locator('#sidebar-notes-button').waitFor({ state: 'visible' });
		await page.locator('#sidebar-workspace-button').waitFor({ state: 'visible' });
		await page.locator('#sidebar-automations-button').waitFor({ state: 'visible' });
		assert.equal(
			await page.locator('.buddy-dock').evaluate((dock) => getComputedStyle(dock).visibility),
			'hidden'
		);
		await page.screenshot({ path: resolve(directory, 'keyboard-hidden-dock-fallback.png') });
		await assertProfileNavigation(
			page,
			{ notes: true, workspace: true, automations: true },
			'keyboard-hidden-dock'
		);
		await page.evaluate(() => {
			Object.defineProperty(window.visualViewport, 'height', {
				configurable: true,
				get: () => window.innerHeight
			});
			window.visualViewport.dispatchEvent(new Event('resize'));
		});
		await page.locator('.buddy-dock-hidden').waitFor({ state: 'detached' });
		assert.equal(await page.locator('#sidebar-notes-button').count(), 0);
		assert.equal(await page.locator('#sidebar-workspace-button').count(), 0);
		assert.equal(await page.locator('#sidebar-automations-button').count(), 0);
		await assertNoRuntimeErrors(session);
		results.push({ name: 'keyboard-hidden-dock', hiddenFallback: true, visibleDockRestored: true });
	} finally {
		await session.browser.close();
	}
}

async function runShiftPinScenario() {
	configureNavigation();
	const session = await createMockedPage({
		viewport: { width: 1280, height: 900 },
		isMobile: false,
		hasTouch: false,
		polish: false,
		usage: false,
		modelSelectorVisible: false,
		pinnedMenuItems: pinnedNavigation
	});
	const { page } = session;
	try {
		await page.locator('#sidebar-toggle-button').click();
		await page.locator('#buddy-sidebar-close').waitFor({ state: 'visible' });
		await page.locator('#sidebar button[aria-label="User menu"]').click();
		const popup = page.locator('.app-dropdown-menu:visible');
		await popup.waitFor({ state: 'visible' });
		const automationsRow = popup
			.locator('.user-menu-row')
			.filter({ has: page.locator('a[href="/automations"]') });
		assert.equal(await automationsRow.count(), 0, 'The dock covers Automations');
		// Shift reveals pin buttons, including for destinations the dock already shows,
		// because those pins still choose what the sidebar lists when the dock is hidden.
		await page.keyboard.down('Shift');
		await automationsRow.waitFor({ state: 'visible' });
		assert.equal(await popup.locator('a[href="/notes"]').count(), 1);
		assert.equal(await popup.locator('a[href="/workspace"]').count(), 1);
		const settingsWrite = page.waitForRequest(
			(request) =>
				new URL(request.url()).pathname === '/api/v1/users/user/settings/update' &&
				request.method() === 'POST'
		);
		await automationsRow.locator('button').click();
		const pinnedMenuItems = (await settingsWrite).postDataJSON().ui.pinnedMenuItems;
		assert.deepEqual(pinnedMenuItems, ['notes', 'workspace']);
		await page.keyboard.up('Shift');
		await automationsRow.waitFor({ state: 'detached' });
		assert.equal(await popup.locator('a[href="/notes"]').count(), 0);
		await page.keyboard.press('Escape');
		await popup.waitFor({ state: 'detached' });
		await assertNoRuntimeErrors(session);
		results.push({ name: 'shift-pin-dock-destinations', unpinnedWhileDocked: true });
	} finally {
		await session.browser.close();
	}
}

async function runSortableAfterSearchScenario() {
	configureNavigation();
	user.role = 'admin';
	const session = await createMockedPage({
		viewport: { width: 1280, height: 900 },
		isMobile: false,
		hasTouch: false,
		polish: false,
		usage: false,
		modelSelectorVisible: false,
		pinnedMenuItems: ['notes', 'workspace', 'calendar', 'playground']
	});
	const { page, context } = session;
	try {
		const releaseNotes = page.locator('.modal').filter({ hasText: 'Release Notes' });
		if (await releaseNotes.count()) {
			await releaseNotes.locator('button[aria-label="Close"]').click();
			await releaseNotes.waitFor({ state: 'detached' });
		}
		let capturedSettings;
		await context.route('**/api/v1/users/user/settings/update', async (route) => {
			if (route.request().method() !== 'POST') {
				await route.fallback();
				return;
			}
			capturedSettings = route.request().postDataJSON();
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(capturedSettings)
			});
		});
		await page.locator('#sidebar-toggle-button').click();
		await page.locator('#buddy-sidebar-close').waitFor({ state: 'visible' });
		const pinnedOrder = () =>
			page
				.locator('#pinned-menu-items-list [data-id]')
				.evaluateAll((rows) => rows.map((row) => row.dataset.id));
		assert.deepEqual(await pinnedOrder(), ['calendar', 'playground']);
		await page.locator('#sidebar-search-button').click();
		await page.locator('#sidebar-search-input').fill('project');
		await page.locator('#sidebar-search-results').waitFor({ state: 'visible' });
		await page.locator('#sidebar-search-dismiss').click();
		await page.locator('#sidebar-calendar-button').waitFor({ state: 'visible' });
		const calendar = await page.locator('#sidebar-calendar-button').boundingBox();
		const playground = await page.locator('#sidebar-playground-button').boundingBox();
		const settingsWrite = page.waitForResponse(
			(response) =>
				new URL(response.url()).pathname === '/api/v1/users/user/settings/update' &&
				response.request().method() === 'POST'
		);
		await page.mouse.move(calendar.x + calendar.width / 2, calendar.y + calendar.height / 2);
		await page.mouse.down();
		await page.mouse.move(calendar.x + calendar.width / 2, calendar.y + calendar.height / 2 + 10, {
			steps: 4
		});
		await page.mouse.move(
			playground.x + playground.width / 2,
			playground.y + playground.height - 3,
			{ steps: 15 }
		);
		await page.waitForTimeout(200);
		await page.mouse.up();
		await page.waitForFunction(
			() =>
				document.querySelector('#pinned-menu-items-list [data-id]')?.getAttribute('data-id') ===
				'playground'
		);
		assert.deepEqual(await pinnedOrder(), ['playground', 'calendar']);
		await settingsWrite;
		assert.deepEqual(capturedSettings.ui.pinnedMenuItems, [
			'notes',
			'workspace',
			'playground',
			'calendar'
		]);
		await assertNoRuntimeErrors(session);
		results.push({
			name: 'sortable-after-search',
			reorderedAfterSearchDismiss: true,
			hiddenPinsPreserved: true
		});
	} finally {
		await session.browser.close();
	}
}

async function runRtlDirectionScenario(name, viewport, mobile) {
	configureNavigation();
	const session = await createMockedPage({
		viewport,
		isMobile: mobile,
		hasTouch: mobile,
		polish: false,
		usage: false,
		modelSelectorVisible: false,
		pinnedMenuItems: pinnedNavigation
	});
	const { page } = session;
	try {
		await page.evaluate(() => {
			document.documentElement.dir = 'rtl';
		});
		await assertSidebarArrow(page, 'sidebar-toggle-button', 'right');
		await page.locator('#sidebar-toggle-button').click();
		await page.locator('#buddy-sidebar-close').waitFor({ state: 'visible' });
		await assertHeaderFits(page);
		assert.ok(Math.abs((await page.locator('#sidebar').boundingBox()).x) < 1);
		await assertSidebarArrow(page, 'buddy-sidebar-close', 'left');
		await page.screenshot({ path: resolve(directory, `${name}-sidebar.png`) });
		await page.locator('#buddy-sidebar-close').click();
		await page.locator('#sidebar-toggle-button').waitFor({ state: 'visible' });
		await assertSidebarArrow(page, 'sidebar-toggle-button', 'right');
		await assertNoRuntimeErrors(session);
		results.push({ name, collapseTowardEdge: true, reopenAwayFromEdge: true });
	} finally {
		await session.browser.close();
	}
}

await runHeaderScenario({
	name: 'desktop',
	viewport: { width: 1280, height: 900 },
	isMobile: false,
	hasTouch: false
});
await runHeaderScenario({ name: 'phone', viewport: { width: 390, height: 844 } });
await runHeaderScenario({
	name: 'narrow-phone-dark',
	viewport: { width: 320, height: 740 },
	colorScheme: 'dark'
});
await runPermissionScenario({ name: 'knowledge-only', knowledgeOnly: true });
await runPermissionScenario({ name: 'hidden-workspace', hiddenWorkspace: true });
await runKeyboardScenario();
await runShiftPinScenario();
await runSortableAfterSearchScenario();
await runRtlDirectionScenario('rtl-desktop', { width: 1280, height: 900 }, false);
await runRtlDirectionScenario('rtl-phone', { width: 390, height: 844 }, true);
Object.assign(user, structuredClone(initialUser));
Object.assign(config, structuredClone(initialConfig));
writeFileSync(resolve(directory, 'results.json'), JSON.stringify(results, null, 2));
console.log(JSON.stringify({ passed: results.length, results }, null, 2));
