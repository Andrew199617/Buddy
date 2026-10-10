/** Aggregate unread and destructive menu UI checks with synthetic APIs only. */
import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chats, createMockedPage, outputDirectory } from './mobile-ui-preview.mjs';
import {
	readProductionSourceMaps,
	resolveProductionExports
} from './production-module-exports.mjs';

const workspace = fileURLToPath(new URL('../../../', import.meta.url));
const sourceMaps = readProductionSourceMaps(
	process.env.BUDDY_UI_TEST_BUILD || resolve(workspace, 'build')
);
const production = resolveProductionExports(sourceMaps, '/stores/chatList.ts', [
	'setChatReadAt',
	'setChatActive',
	'setAllChatsRead',
	'refreshUnreadChats'
]);
const results = [];

async function updateUnread(page, name, ...args) {
	// Reflect the synthetic server event before the store fetches the bounded summary.
	const chat = chats.find((item) => item.id === args[0]);
	if (name === 'setChatReadAt' && chat) chat.last_read_at = args[1];
	if (name === 'setChatActive' && chat) chat.active = args[1];
	if (name === 'setAllChatsRead') {
		for (const item of chats) item.last_read_at = item.updated_at;
	}
	await page.evaluate(
		async ({ production, name, args }) => {
			const module = await import(production.url);
			module[production.aliases[name]](...args);
			await module[production.aliases.refreshUnreadChats](localStorage.token);
		},
		{ production, name, args }
	);
}

async function expectUnread(page, expected) {
	const dot = page.locator('#sidebar-toggle-button [data-unread="true"]');
	await dot.waitFor({ state: expected ? 'visible' : 'detached' });
	if (expected) {
		assert.equal(
			await dot.evaluate((element) => getComputedStyle(element).backgroundColor),
			'rgb(59, 130, 246)'
		);
		assert.match(
			await page.locator('#sidebar-toggle-button').getAttribute('aria-describedby'),
			/buddy-sidebar-unread-status/
		);
		assert.match(await page.locator('#buddy-sidebar-unread-status').innerText(), /Unread.*Chats/i);
	}
}

async function unreadScenario(name, viewport, mobile) {
	chats[0].last_read_at = 0;
	chats[1].last_read_at = null;
	const session = await createMockedPage({
		viewport,
		hasTouch: mobile,
		isMobile: mobile,
		modelSelectorVisible: false,
		polish: false,
		usage: false
	});
	const { page } = session;
	try {
		await expectUnread(page, true);
		await page.locator('#sidebar-toggle-button').click();
		await page.locator('#buddy-sidebar-close').waitFor({ state: 'visible' });
		assert.equal(await page.locator('#sidebar-toggle-button').count(), 0);
		await page.locator('#buddy-sidebar-close').click();
		await expectUnread(page, true);
		await page.waitForFunction(() => {
			const sidebar = document.getElementById('sidebar');
			return sidebar && sidebar.getBoundingClientRect().right <= 1;
		});
		await updateUnread(page, 'setChatReadAt', chats[0].id, chats[0].updated_at);
		await expectUnread(page, true);
		await updateUnread(page, 'setChatReadAt', chats[1].id, chats[1].updated_at);
		await expectUnread(page, false);
		await updateUnread(page, 'setChatReadAt', chats[0].id, 0);
		await expectUnread(page, true);
		await updateUnread(page, 'setChatActive', chats[0].id, true);
		await expectUnread(page, false);
		await updateUnread(page, 'setChatActive', chats[0].id, false);
		await expectUnread(page, true);
		await page.screenshot({ path: resolve(outputDirectory, `${name}-unread.png`) });
		await page.locator('#sidebar-toggle-button').click();
		await page.locator(`#sidebar a[href="/c/${chats[0].id}"]`).first().click();
		await page.waitForURL(`**/c/${chats[0].id}`);
		// Mobile chat selection closes the drawer before navigation finishes.
		if (!mobile) {
			await page.locator('#buddy-sidebar-close').click();
		}
		await page.waitForFunction(() => {
			const sidebar = document.getElementById('sidebar');
			return (
				sidebar?.getAttribute('aria-hidden') === 'true' &&
				sidebar.getBoundingClientRect().right <= 1
			);
		});
		await expectUnread(page, false);
		await updateUnread(page, 'setChatActive', chats[0].id, true);
		await updateUnread(page, 'setChatActive', chats[0].id, false);
		await expectUnread(page, false);
		await updateUnread(page, 'setChatReadAt', chats[1].id, 0);
		await expectUnread(page, true);
		await updateUnread(page, 'setChatReadAt', chats[1].id, chats[1].updated_at);
		await expectUnread(page, false);
		await page.locator('button[aria-label="New Chat"]:visible').click();
		await expectUnread(page, true);
		assert.equal(chats[0].last_read_at, 0, 'Selecting the chat does not clear server unread state');
		await updateUnread(page, 'setAllChatsRead');
		await expectUnread(page, false);
		assert.ok(!session.blockedMutations.some((request) => request.startsWith('DELETE ')));
		assert.ok(session.requests.includes('GET /api/v1/chats/unread'));
		assert.ok(
			!session.requests.some((request) => /include_pinned=true|include_folders=true/.test(request)),
			'Unread aggregation does not fetch whole history'
		);
		results.push({
			name,
			passed: true,
			cases: [
				'blue accessible indicator',
				'sidebar open preserves unread',
				'read one of two',
				'read last',
				'live unread update',
				'active completion',
				'selected completed chat suppressed',
				'another unread chat still visible',
				'selection does not mark read',
				'bounded unread summary request',
				'bulk read'
			]
		});
	} catch (error) {
		await page.screenshot({ path: resolve(outputDirectory, `${name}-failure.png`) });
		throw error;
	} finally {
		chats[0].last_read_at = chats[0].updated_at;
		chats[1].last_read_at = chats[1].updated_at;
		chats[0].active = false;
		await session.browser.close();
	}
}

async function inspectDelete(page, dark) {
	const action = page.locator('.app-dropdown-menu .chat-menu-delete:visible');
	await action.waitFor({ state: 'visible' });
	await page.mouse.move(900, 700);
	const resting = await action.evaluate((element) => ({
		color: getComputedStyle(element).color,
		icon: getComputedStyle(element.querySelector('svg')).color
	}));
	assert.equal(resting.color, dark ? 'rgb(252, 165, 165)' : 'rgb(185, 28, 28)');
	assert.equal(resting.icon, resting.color);
	await action.hover();
	const hovered = await action.evaluate((element) => ({
		color: getComputedStyle(element).color,
		background: getComputedStyle(element).backgroundColor
	}));
	assert.equal(hovered.color, dark ? 'rgb(254, 202, 202)' : 'rgb(153, 27, 27)');
	assert.equal(hovered.background, dark ? 'rgb(69, 10, 10)' : 'rgb(254, 242, 242)');
	await page.keyboard.press('Tab');
	await action.focus();
	const focused = await action.evaluate((element) => ({
		visible: element.matches(':focus-visible'),
		outlineWidth: getComputedStyle(element).outlineWidth,
		background: getComputedStyle(element).backgroundColor
	}));
	assert.equal(focused.visible, true);
	assert.equal(focused.outlineWidth, '2px');
	assert.equal(focused.background, hovered.background);
}

async function deleteScenario(dark) {
	const name = dark ? 'delete-dark' : 'delete-light';
	const session = await createMockedPage({
		viewport: { width: 1280, height: 900 },
		hasTouch: false,
		isMobile: false,
		modelSelectorVisible: false,
		polish: false,
		usage: false,
		urlPath: '/c/mobile-preview-chat-1'
	});
	const { page } = session;
	try {
		await page.evaluate((dark) => document.documentElement.classList.toggle('dark', dark), dark);
		await page.locator('#chat-context-menu-button').click();
		await inspectDelete(page, dark);
		await page.screenshot({ path: resolve(outputDirectory, `${name}-header.png`) });
		await page.keyboard.press('Escape');
		await page.locator('.app-dropdown-menu .chat-menu-delete').waitFor({ state: 'detached' });
		await page.locator('#sidebar-toggle-button').click();
		await page.locator('#buddy-sidebar-close').waitFor({ state: 'visible' });
		const row = page
			.locator('#sidebar-chat-group')
			.filter({ has: page.locator('a[href="/c/mobile-preview-chat-1"]') })
			.first();
		await row.hover();
		await row.locator('button[aria-label="Chat Menu"]').click();
		await inspectDelete(page, dark);
		await page.screenshot({ path: resolve(outputDirectory, `${name}-sidebar.png`) });
		assert.ok(
			!session.blockedMutations.some((request) => request.startsWith('DELETE ')),
			'No deletion is attempted'
		);
		results.push({
			name,
			passed: true,
			cases: [
				'header action color and icon',
				'sidebar action color and icon',
				'hover and keyboard focus',
				'no deletion'
			]
		});
	} catch (error) {
		await page.screenshot({ path: resolve(outputDirectory, `${name}-failure.png`) });
		throw error;
	} finally {
		await session.browser.close();
	}
}

await unreadScenario('desktop', { width: 1280, height: 900 }, false);
await unreadScenario('mobile', { width: 390, height: 844 }, true);
await deleteScenario(false);
await deleteScenario(true);
writeFileSync(
	resolve(outputDirectory, 'styling-feedback-results.json'),
	JSON.stringify(results, null, 2)
);
console.log(JSON.stringify(results, null, 2));
