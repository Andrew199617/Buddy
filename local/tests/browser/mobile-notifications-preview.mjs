/**
 * Synthetic production-browser notification regression checks.
 * Build first, then serve the matching frontend on BUDDY_LAZY_TEST_ORIGIN.
 * Production source maps resolve the real toast API; the app needs no test hooks.
 * APIs, providers, external navigation, and media are isolated by the shared fixture.
 */
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
	resolveProductionExports,
	readProductionSourceMaps
} from './production-module-exports.mjs';
import {
	createSession,
	workspace,
	outputDirectory as fixtureOutputDirectory
} from './lazy-feature-fixture.mjs';

const useWebKit = process.argv.includes('--webkit');
let outputFolder = 'mobile-notifications';
if (useWebKit) outputFolder = 'mobile-notifications-webkit';
const outputDirectory = resolve(fixtureOutputDirectory, '../' + outputFolder);
const sourceMaps = readProductionSourceMaps(resolve(workspace, 'build'));
const production = {
	notifications: resolveProductionExports(sourceMaps, '/lib/notifications/index.ts', ['toast']),
	mobile: resolveProductionExports(sourceMaps, '/lib/notifications/mobile.ts', [
		'mobileNotifications'
	]),
	stores: resolveProductionExports(sourceMaps, '/stores/index.ts', ['showSidebar', 'user'])
};
const highSelector = '[data-notification-priority="high-level"] .buddy-notification-card';
const urgentSelector = '[data-notification-priority="urgent"] .buddy-notification-card';
const badgeSelector = '#sidebar-toggle-button .buddy-notification-dot';
const results = [];
const filterArgument = process.argv.find((argument) => argument.startsWith('--only='));
let selectedCases;
if (filterArgument) selectedCases = filterArgument.slice('--only='.length).split(',');

async function toast(page, method, title, options = {}, callbacks = {}) {
	return page.evaluate(
		async ({ moduleReference, method, title, options, callbacks }) => {
			const module = await import(moduleReference.url);
			const api = module[moduleReference.aliases.toast];
			function record(name) {
				window.__qaNotificationEvents.push(name);
			}
			if (callbacks.dismiss) options.onDismiss = () => record(callbacks.dismiss);
			if (callbacks.autoClose) options.onAutoClose = () => record(callbacks.autoClose);
			if (callbacks.action) {
				options.action = {
					label: callbacks.actionLabel || 'Review now',
					onClick: (event) => {
						record(callbacks.action);
						if (callbacks.preventDefault) event.preventDefault();
					}
				};
			}
			if (callbacks.cancel) {
				options.cancel = {
					label: callbacks.cancelLabel || 'Later',
					onClick: () => record(callbacks.cancel)
				};
			}
			if (method === 'default') return api(title, options);
			return api[method](title, options);
		},
		{ moduleReference: production.notifications, method, title, options, callbacks }
	);
}

async function dismiss(page, id) {
	await page.evaluate(
		async ({ moduleReference, id }) => {
			const module = await import(moduleReference.url);
			module[moduleReference.aliases.toast].dismiss(id);
		},
		{ moduleReference: production.notifications, id }
	);
}

async function pendingNotifications(page) {
	return page.evaluate(async (moduleReference) => {
		const module = await import(moduleReference.url);
		let current;
		const unsubscribe = module[moduleReference.aliases.mobileNotifications].subscribe((items) => {
			current = items;
		});
		unsubscribe();
		return current.map((item) => ({
			id: item.id,
			title: item.title,
			type: item.type,
			description: item.description,
			revision: item.revision
		}));
	}, production.mobile);
}

async function notificationEvents(page) {
	return page.evaluate(() => window.__qaNotificationEvents);
}

async function setSidebar(page, open) {
	await page.evaluate(
		async ({ moduleReference, open }) => {
			const module = await import(moduleReference.url);
			module[moduleReference.aliases.showSidebar].set(open);
		},
		{ moduleReference: production.stores, open }
	);
}

async function assertComposerPreserved(page, expected) {
	assert.equal(
		await page.evaluate(
			() => document.getElementById('chat-input') === window.__qaOriginalComposer
		),
		true,
		'Notification updates preserve the mounted composer'
	);
	assert.equal(await page.locator('#chat-input').textContent(), expected, 'Draft text is retained');
}

async function assertLayout(page, options = {}) {
	const metrics = await page.evaluate(
		({ highSelector, urgentSelector }) => {
			function geometry(selector) {
				const element = document.querySelector(selector);
				if (!element) return null;
				const rect = element.getBoundingClientRect();
				return {
					x: rect.x,
					y: rect.y,
					right: rect.right,
					bottom: rect.bottom,
					width: rect.width,
					height: rect.height
				};
			}
			const input = document.getElementById('chat-input');
			const inputRect = input.getBoundingClientRect();
			const point = {
				x: inputRect.x + Math.min(inputRect.width / 2, 80),
				y: inputRect.y + Math.min(inputRect.height / 2, 12)
			};
			const hit = document.elementFromPoint(point.x, point.y);
			return {
				header: geometry('.buddy-chat-header'),
				composer: geometry('.buddy-composer'),
				high: geometry(highSelector),
				urgent: geometry(urgentSelector),
				viewportWidth: innerWidth,
				viewportBottom: (visualViewport?.offsetTop || 0) + (visualViewport?.height || innerHeight),
				overflow: document.documentElement.scrollWidth > innerWidth,
				inputReceivesPointer: Boolean(hit && (input === hit || input.contains(hit)))
			};
		},
		{ highSelector, urgentSelector }
	);
	writeFileSync(resolve(outputDirectory, 'latest-geometry.json'), JSON.stringify(metrics, null, 2));
	assert.ok(metrics.header && metrics.composer, 'Chat header and composer are present');
	assert.equal(metrics.overflow, false, 'Notifications do not create horizontal document overflow');
	assert.equal(
		metrics.inputReceivesPointer,
		true,
		'The notification leaves the draft field reachable'
	);
	for (const priority of ['high', 'urgent']) {
		const card = metrics[priority];
		if (!card) continue;
		assert.ok(
			card.x >= 0 && card.right <= metrics.viewportWidth + 1,
			priority + ' card fits the viewport'
		);
		assert.ok(
			card.y >= metrics.header.bottom + 7,
			priority + ' card does not overlap the chat header'
		);
	}
	if (options.highRequired) assert.ok(metrics.high, 'High-level notification is visible');
	if (options.urgentRequired) assert.ok(metrics.urgent, 'Urgent notification is visible');
	if (metrics.urgent) {
		assert.ok(
			metrics.urgent.bottom <= metrics.composer.y - 8,
			'Urgent card ends above the composer'
		);
		assert.ok(
			metrics.urgent.bottom <= metrics.viewportBottom,
			'Urgent card fits the visible viewport'
		);
	}
	if (metrics.high && metrics.urgent) {
		assert.ok(
			metrics.high.bottom <= metrics.urgent.y + 1,
			'Priority cards do not overlap each other'
		);
	}
	return metrics;
}

async function assertButtonTarget(locator) {
	const bounds = await locator.boundingBox();
	assert.ok(
		bounds && bounds.width >= 44 && bounds.height >= 44,
		'Notification button has a 44px tap target'
	);
}

async function runCase(browser, name, options, check) {
	if (selectedCases && !selectedCases.includes(name)) return;
	let session;
	try {
		session = await createSession(browser, options);
		await dismiss(session.page);
		await session.page.evaluate(() => {
			window.__qaNotificationEvents = [];
			window.__qaOriginalComposer = document.getElementById('chat-input');
		});
		await check(session);
		assert.deepEqual(session.errors, [], 'No browser runtime errors should occur');
		assert.equal(
			session.completions.length,
			0,
			'Notification QA never requests a model completion'
		);
		assert.equal(
			await session.page.evaluate(() => window.__qaMediaRequests),
			0,
			'Notification QA never accesses media'
		);
		results.push({ name, passed: true });
		console.log('PASS ' + name);
	} catch (error) {
		if (session) {
			await session.page.screenshot({ path: resolve(outputDirectory, name + '-failure.png') });
			writeFileSync(
				resolve(outputDirectory, name + '-failure.json'),
				JSON.stringify(
					{
						message: error.message,
						url: session.page.url(),
						errors: session.errors,
						requests: session.requests,
						body: await session.page.locator('body').innerText()
					},
					null,
					2
				)
			);
		}
		results.push({ name, passed: false, error: error.message });
		console.error('FAIL ' + name + ': ' + error.message);
	} finally {
		if (session) await session.context.close();
	}
}

async function mobileLayout(browser, theme) {
	await runCase(
		browser,
		'mobile-' + theme + '-priority-layout',
		{ mobile: true, theme },
		async ({ page }) => {
			const draft = 'A calmer weekend, with room to breathe.';
			await page.locator('#chat-input').fill(draft);
			await toast(page, 'success', 'Note saved', {
				description: 'Weekend plans is safely in Notes.',
				duration: 60000
			});
			await page.locator(highSelector).getByText('Note saved', { exact: true }).waitFor();
			await toast(page, 'error', 'Weekend plans needs attention', {
				description: 'Review your note before continuing. Your conversation stays right here.'
			});
			const urgent = page.locator(urgentSelector);
			await urgent.getByText('Weekend plans needs attention', { exact: true }).waitFor();
			await toast(page, 'info', 'Review your weekend list', {
				description: 'A quiet task is waiting in the sidebar.'
			});
			await page.locator(badgeSelector).waitFor();
			assert.equal(
				await page.locator('.buddy-notification-card').count(),
				2,
				'Info adds a dot without another floating card'
			);
			assert.equal(
				await page.locator('[data-sonner-toast]').count(),
				0,
				'Mobile toast calls use Buddy notification cards'
			);
			const metrics = await assertLayout(page, { highRequired: true, urgentRequired: true });
			await assertButtonTarget(urgent.getByRole('button', { name: 'Got it', exact: true }));
			await assertButtonTarget(
				urgent.getByRole('button', { name: 'Dismiss notification', exact: true })
			);
			await page.locator('#chat-input').click();
			await page.keyboard.type(' Keep this draft.');
			await assertComposerPreserved(page, draft + ' Keep this draft.');
			await page.screenshot({
				path: resolve(outputDirectory, 'mobile-' + theme + '-priority-layout.png')
			});
			writeFileSync(
				resolve(outputDirectory, 'mobile-' + theme + '-geometry.json'),
				JSON.stringify(metrics, null, 2)
			);
		}
	);
}

async function quietInbox(browser) {
	await runCase(browser, 'quiet-sidebar-inbox', { mobile: true }, async ({ page }) => {
		const first = await toast(
			page,
			'info',
			'Review the weekend list',
			{ description: 'Choose one next step.' },
			{ dismiss: 'first-dismissed' }
		);
		const second = await toast(
			page,
			'info',
			'Check the saved note',
			{ description: 'Your note is ready to read.' },
			{ dismiss: 'second-dismissed' }
		);
		const menu = page.locator('#sidebar-toggle-button');
		await page.locator(badgeSelector).waitFor();
		assert.equal(await menu.getAttribute('aria-label'), 'Open Sidebar');
		assert.match(
			await page.locator('#buddy-sidebar-notification-status').textContent(),
			/2.*notifications/i
		);
		const badgeColor = await page
			.locator(badgeSelector)
			.evaluate((element) => getComputedStyle(element).backgroundColor);
		const channels = badgeColor.match(/[\d.]+/g).map(Number);
		assert.ok(
			channels[2] > channels[0] && channels[2] > channels[1],
			'The quiet notification indicator is blue'
		);
		await menu.click();
		const inbox = page.locator('.buddy-notification-inbox');
		await inbox.waitFor();
		assert.equal(await inbox.locator('.buddy-inbox-card').count(), 2);
		assert.equal(
			(await pendingNotifications(page)).length,
			2,
			'Opening the drawer retains pending information'
		);
		const firstCard = inbox.locator('[data-notification-id="' + first + '"]');
		await firstCard.getByText('Choose one next step.', { exact: true }).waitFor();
		await assertButtonTarget(firstCard.locator('.buddy-inbox-dismiss'));
		await firstCard.locator('.buddy-inbox-dismiss').click();
		assert.deepEqual(
			(await pendingNotifications(page)).map((item) => item.id),
			[second]
		);
		assert.deepEqual(await notificationEvents(page), ['first-dismissed']);
		await setSidebar(page, false);
		await page.locator(badgeSelector).waitFor();
		await menu.click();
		await inbox.locator('[data-notification-id="' + second + '"] .buddy-inbox-dismiss').click();
		await inbox.waitFor({ state: 'detached' });
		await setSidebar(page, false);
		await page.locator(badgeSelector).waitFor({ state: 'detached' });
		assert.deepEqual(await notificationEvents(page), ['first-dismissed', 'second-dismissed']);
	});
}

async function sidebarUrgentNotifications(browser) {
	await runCase(
		browser,
		'mobile-sidebar-urgent-notifications',
		{ mobile: true },
		async ({ page }) => {
			const quietId = await toast(page, 'info', 'A quiet sidebar reminder');
			await page.locator('#sidebar-toggle-button').click();
			await page.locator('.buddy-notification-inbox').waitFor();
			await toast(
				page,
				'error',
				'A connection needs attention',
				{
					description: 'Review this before continuing.'
				},
				{ dismiss: 'sidebar-urgent-dismissed' }
			);
			const urgent = page.locator('.buddy-sidebar-urgent-notification .buddy-notification-card');
			await urgent.getByText('A connection needs attention', { exact: true }).waitFor();
			assert.equal(
				await page.locator('.buddy-notification-host').count(),
				0,
				'Drawer notifications remain inside the open drawer'
			);
			await assertButtonTarget(urgent.getByRole('button', { name: 'Got it', exact: true }));
			await urgent.getByRole('button', { name: 'Got it', exact: true }).click();
			await urgent.waitFor({ state: 'detached' });
			assert.deepEqual(await notificationEvents(page), ['sidebar-urgent-dismissed']);
			assert.deepEqual(
				(await pendingNotifications(page)).map((item) => item.id),
				[quietId],
				'Acknowledging urgent work preserves quiet reminders'
			);
			await page.locator('.buddy-inbox-dismiss').click();
			await page.locator('.buddy-notification-inbox').waitFor({ state: 'detached' });

			await toast(page, 'error', 'Urgent notice without quiet reminders');
			await urgent.getByText('Urgent notice without quiet reminders', { exact: true }).waitFor();
			await urgent.getByRole('button', { name: 'Got it', exact: true }).click();
			await urgent.waitFor({ state: 'detached' });
			assert.equal((await pendingNotifications(page)).length, 0);
		}
	);
}

async function accountNotificationLifecycle(browser) {
	await runCase(
		browser,
		'mobile-account-notification-lifecycle',
		{ mobile: true },
		async ({ page }) => {
			await toast(
				page,
				'info',
				'The previous account has pending work',
				{},
				{ dismiss: 'old-account-dismissed' }
			);
			const sessionId = await page.evaluate(
				async ({ storesReference, notificationReference }) => {
					const [storesModule, notificationsModule] = await Promise.all([
						import(storesReference.url),
						import(notificationReference.url)
					]);
					const userStore = storesModule[storesReference.aliases.user];
					const api = notificationsModule[notificationReference.aliases.toast];
					const unsubscribe = userStore.subscribe((current) => {
						window.__qaSignedInUser = current;
					});
					unsubscribe();
					userStore.set(null);
					return api.error('Session expired. Please sign in again.');
				},
				{ storesReference: production.stores, notificationReference: production.notifications }
			);
			await page
				.locator(urgentSelector)
				.getByText('Session expired. Please sign in again.', { exact: true })
				.waitFor();
			await page.waitForTimeout(250);
			assert.deepEqual(
				(await pendingNotifications(page)).map((item) => item.id),
				[sessionId],
				'Changing accounts clears old work without removing a synchronous session notice'
			);
			assert.deepEqual(await notificationEvents(page), ['old-account-dismissed']);

			const welcomeId = await page.evaluate(
				async ({ storesReference, notificationReference }) => {
					const [storesModule, notificationsModule] = await Promise.all([
						import(storesReference.url),
						import(notificationReference.url)
					]);
					const userStore = storesModule[storesReference.aliases.user];
					const api = notificationsModule[notificationReference.aliases.toast];
					api.info('A stale guest reminder', {
						onDismiss: () => window.__qaNotificationEvents.push('guest-dismissed')
					});
					userStore.set({ ...window.__qaSignedInUser, id: 'qa-next-account' });
					return api.success('Welcome back to your new account', { duration: 60000 });
				},
				{ storesReference: production.stores, notificationReference: production.notifications }
			);
			await page
				.locator(highSelector)
				.getByText('Welcome back to your new account', { exact: true })
				.waitFor();
			await page.waitForTimeout(250);
			assert.deepEqual(
				(await pendingNotifications(page)).map((item) => item.id),
				[welcomeId],
				'Signing in clears stale guest work while retaining the new confirmation'
			);
			assert.deepEqual(await notificationEvents(page), [
				'old-account-dismissed',
				'guest-dismissed'
			]);
		}
	);
}

async function timersAndPersistence(browser) {
	await runCase(browser, 'mobile-timers-and-persistence', { mobile: true }, async ({ page }) => {
		await toast(
			page,
			'success',
			'Hover pauses this update',
			{ duration: 500 },
			{ autoClose: 'hover-auto-closed' }
		);
		let high = page.locator(highSelector);
		await high.waitFor();
		await high.hover();
		await page.waitForTimeout(700);
		assert.equal(await high.count(), 1, 'Hover pauses the high-level timeout');
		await page.mouse.move(1, 843);
		await high.waitFor({ state: 'detached', timeout: 3000 });
		assert.deepEqual(await notificationEvents(page), ['hover-auto-closed']);

		await toast(
			page,
			'success',
			'Focus pauses this update',
			{ duration: 500 },
			{ autoClose: 'focus-auto-closed' }
		);
		high = page.locator(highSelector);
		await high.waitFor();
		await high.getByRole('button', { name: 'Dismiss notification', exact: true }).focus();
		await page.waitForTimeout(700);
		assert.equal(await high.count(), 1, 'Keyboard focus pauses the high-level timeout');
		await page.locator('#chat-input').focus();
		await high.waitFor({ state: 'detached', timeout: 3000 });
		assert.deepEqual(await notificationEvents(page), ['hover-auto-closed', 'focus-auto-closed']);

		await toast(
			page,
			'error',
			'Please review this urgent update',
			{},
			{ dismiss: 'urgent-dismissed', autoClose: 'urgent-auto-closed' }
		);
		await toast(page, 'info', 'This quiet task stays available');
		const urgent = page.locator(urgentSelector);
		await urgent.waitFor();
		await page.waitForTimeout(4500);
		assert.equal(await urgent.count(), 1, 'Default urgent notification remains until acknowledged');
		assert.equal(
			await page.locator(badgeSelector).count(),
			1,
			'Quiet information remains available beyond toast duration'
		);
		await urgent.getByRole('button', { name: 'Got it', exact: true }).click();
		await urgent.waitFor({ state: 'detached' });
		assert.deepEqual(await notificationEvents(page), [
			'hover-auto-closed',
			'focus-auto-closed',
			'urgent-dismissed'
		]);
	});
}

async function loadingAndQueues(browser) {
	await runCase(browser, 'mobile-loading-update-and-queue', { mobile: true }, async ({ page }) => {
		const id = await toast(page, 'loading', 'Saving the weekend plan…', { id: 'qa-weekend-save' });
		assert.equal(id, 'qa-weekend-save');
		const high = page.locator(highSelector);
		await high.getByText('Saving the weekend plan…', { exact: true }).waitFor();
		assert.equal(await high.locator('.notification-progress').count(), 1);
		assert.equal(
			await high.getByRole('button', { name: 'Dismiss notification', exact: true }).count(),
			0
		);
		const newest = await toast(page, 'success', 'A second note was saved', { duration: 60000 });
		await high.getByText('A second note was saved', { exact: true }).waitFor();
		assert.equal((await pendingNotifications(page)).length, 2);
		await dismiss(page, newest);
		await high.getByText('Saving the weekend plan…', { exact: true }).waitFor();
		await toast(
			page,
			'success',
			'Weekend plan saved',
			{ id, duration: 500 },
			{ autoClose: 'save-auto-closed' }
		);
		await high.getByText('Weekend plan saved', { exact: true }).waitFor();
		assert.equal(
			(await pendingNotifications(page)).length,
			1,
			'ID update replaces the existing loading notification'
		);
		assert.equal(await high.locator('.notification-progress').count(), 0);
		await high.waitFor({ state: 'detached', timeout: 3000 });
		assert.equal(
			(await pendingNotifications(page)).length,
			0,
			'Completed loading notification does not resurface'
		);
		assert.deepEqual(await notificationEvents(page), ['save-auto-closed']);

		const older = await toast(page, 'success', 'Older queued confirmation', { duration: 60000 });
		const newer = await toast(page, 'success', 'Newer queued confirmation', { duration: 60000 });
		await high.getByText('Newer queued confirmation', { exact: true }).waitFor();
		await dismiss(page, newer);
		await high.getByText('Older queued confirmation', { exact: true }).waitFor();
		await dismiss(page, older);
		await high.waitFor({ state: 'detached' });
	});
}

async function actionsAndCallbacks(browser) {
	await runCase(
		browser,
		'mobile-actions-and-dismissal-callbacks',
		{ mobile: true },
		async ({ page }) => {
			await toast(
				page,
				'warning',
				'Review a connection',
				{},
				{
					action: 'review-clicked',
					actionLabel: 'Review connection',
					dismiss: 'review-dismissed',
					autoClose: 'review-auto-closed'
				}
			);
			const urgent = page.locator(urgentSelector);
			await urgent.getByRole('button', { name: 'Review connection', exact: true }).click();
			await urgent.waitFor({ state: 'detached' });
			assert.deepEqual(
				await notificationEvents(page),
				['review-clicked', 'review-dismissed'],
				'Action and dismissal each run once'
			);

			await toast(
				page,
				'error',
				'Keep this action available',
				{},
				{
					action: 'prevented-action',
					actionLabel: 'Try again',
					preventDefault: true,
					dismiss: 'prevented-dismissed'
				}
			);
			await urgent.getByRole('button', { name: 'Try again', exact: true }).click();
			assert.equal(
				await urgent.count(),
				1,
				'An action that prevents default preserves its notification'
			);
			assert.deepEqual(await notificationEvents(page), [
				'review-clicked',
				'review-dismissed',
				'prevented-action'
			]);
			await urgent.getByRole('button', { name: 'Dismiss notification', exact: true }).click();
			await urgent.waitFor({ state: 'detached' });

			await toast(
				page,
				'error',
				'Decide when to review',
				{},
				{
					cancel: 'cancel-clicked',
					cancelLabel: 'Review later',
					dismiss: 'cancel-dismissed'
				}
			);
			await urgent.getByRole('button', { name: 'Review later', exact: true }).click();
			await urgent.waitFor({ state: 'detached' });
			assert.deepEqual(await notificationEvents(page), [
				'review-clicked',
				'review-dismissed',
				'prevented-action',
				'prevented-dismissed',
				'cancel-clicked',
				'cancel-dismissed'
			]);
		}
	);
}

async function promiseLifecycle(browser) {
	await runCase(browser, 'mobile-promise-notifications', { mobile: true }, async ({ page }) => {
		await page.evaluate(async (moduleReference) => {
			const module = await import(moduleReference.url);
			const promise = new Promise((resolve) => {
				window.__qaResolveNotification = resolve;
			});
			module[moduleReference.aliases.toast].promise(promise, {
				id: 'qa-promise-save',
				loading: 'Saving a promised note…',
				success: (value) => 'Saved ' + value,
				error: 'The note could not be saved',
				duration: 60000,
				finally: () => window.__qaNotificationEvents.push('promise-finished')
			});
		}, production.notifications);
		const high = page.locator(highSelector);
		await high.getByText('Saving a promised note…', { exact: true }).waitFor();
		await page.evaluate(() => window.__qaResolveNotification('weekend ideas'));
		await high.getByText('Saved weekend ideas', { exact: true }).waitFor();
		const current = await pendingNotifications(page);
		assert.equal(current.length, 1);
		assert.equal(current[0].id, 'qa-promise-save');
		assert.equal(current[0].type, 'success');
		assert.deepEqual(await notificationEvents(page), ['promise-finished']);
		await dismiss(page, 'qa-promise-save');

		await page.evaluate(async (moduleReference) => {
			const module = await import(moduleReference.url);
			const promise = new Promise((resolve, reject) => {
				window.__qaRejectNotification = reject;
			});
			module[moduleReference.aliases.toast].promise(promise, {
				id: 'qa-promise-failed',
				loading: 'Checking a connection…',
				error: (error) => 'Connection failed: ' + error.message
			});
		}, production.notifications);
		await high.getByText('Checking a connection…', { exact: true }).waitFor();
		await page.evaluate(() => window.__qaRejectNotification(new Error('isolated fixture')));
		await page
			.locator(urgentSelector)
			.getByText('Connection failed: isolated fixture', { exact: true })
			.waitFor();
		assert.equal(await high.count(), 0, 'Rejected promise removes the loading card');
		assert.equal((await pendingNotifications(page)).length, 1);
	});
}

async function compactAndKeyboard(browser) {
	await runCase(
		browser,
		'mobile-compact-scaled-keyboard-layout',
		{ mobile: true, theme: 'light' },
		async ({ page }) => {
			await page.setViewportSize({ width: 320, height: 700 });
			await page.evaluate(() =>
				document.documentElement.style.setProperty('--buddy-font-size-offset', '4px')
			);
			const multilineDraft = 'Weekend ideas\nLeave time for a walk\nMake a little room to think';
			await page.locator('#chat-input').fill(multilineDraft);
			const draft = await page.locator('#chat-input').textContent();
			const draftLines = await page
				.locator('#chat-input')
				.evaluate((element) => element.querySelectorAll('p, br').length);
			assert.ok(draftLines >= 3, 'The draft occupies multiple composer lines');
			await toast(page, 'success', 'Weekend plans was saved in your personal Notes', {
				description: 'The complete note is ready whenever you want to return to it.',
				duration: 60000
			});
			await toast(page, 'error', 'Review your weekend plans before continuing', {
				description:
					'One part of the plan needs your attention. Your conversation and the draft you are writing stay right here.'
			});
			await page.locator(urgentSelector).waitFor();
			await page.waitForTimeout(200);
			await assertLayout(page, { highRequired: true, urgentRequired: true });
			await assertComposerPreserved(page, draft);
			await page.screenshot({ path: resolve(outputDirectory, 'mobile-compact-scaled.png') });

			await page.evaluate(() => {
				Object.defineProperty(visualViewport, 'height', { configurable: true, get: () => 390 });
				Object.defineProperty(visualViewport, 'offsetTop', { configurable: true, get: () => 12 });
				visualViewport.dispatchEvent(new Event('resize'));
			});
			await page.waitForTimeout(250);
			const metrics = await assertLayout(page, { urgentRequired: true });
			await assertComposerPreserved(page, draft);
			await page.locator('#chat-input').click();
			await page.keyboard.type(' — still editable');
			assert.match(await page.locator('#chat-input').textContent(), /still editable/);
			await page.screenshot({ path: resolve(outputDirectory, 'mobile-keyboard-multiline.png') });
			writeFileSync(
				resolve(outputDirectory, 'mobile-keyboard-geometry.json'),
				JSON.stringify(metrics, null, 2)
			);
			await page
				.locator(urgentSelector)
				.getByRole('button', { name: 'Got it', exact: true })
				.click();
			await page.locator(urgentSelector).waitFor({ state: 'detached' });
		}
	);
}

async function desktopToaster(browser) {
	await runCase(browser, 'desktop-native-toaster', { mobile: false }, async ({ page }) => {
		await toast(page, 'success', 'Desktop confirmation', { duration: 60000 });
		await toast(page, 'error', 'Desktop urgent notice', { duration: 60000 });
		await toast(page, 'info', 'Desktop information', { duration: 60000 });
		const native = page.locator('[data-sonner-toaster]');
		await native.getByText('Desktop confirmation', { exact: true }).waitFor();
		await native.getByText('Desktop urgent notice', { exact: true }).waitFor();
		await native.getByText('Desktop information', { exact: true }).waitFor();
		assert.equal(await page.locator('.buddy-notification-host').count(), 0);
		assert.equal(await page.locator(badgeSelector).count(), 0);
		assert.equal(
			(await pendingNotifications(page)).length,
			0,
			'Desktop calls do not create mobile inbox entries'
		);
		await dismiss(page);
		await page.waitForFunction(() => document.querySelectorAll('[data-sonner-toast]').length === 0);
	});
}

const playwrightSpecifier = process.env.BUDDY_PLAYWRIGHT_MODULE || 'playwright';
const { chromium, webkit } = await import(playwrightSpecifier);
let browserEngine = chromium;
if (useWebKit) browserEngine = webkit;
mkdirSync(outputDirectory, { recursive: true });
const browser = await browserEngine.launch({ headless: true });
try {
	await mobileLayout(browser, 'dark');
	await mobileLayout(browser, 'light');
	await quietInbox(browser);
	await sidebarUrgentNotifications(browser);
	await accountNotificationLifecycle(browser);
	await timersAndPersistence(browser);
	await loadingAndQueues(browser);
	await actionsAndCallbacks(browser);
	await promiseLifecycle(browser);
	await compactAndKeyboard(browser);
	await desktopToaster(browser);
} finally {
	await browser.close();
	writeFileSync(resolve(outputDirectory, 'results.json'), JSON.stringify(results, null, 2));
}
const failures = results.filter((result) => !result.passed);
console.log(
	JSON.stringify(
		{ passed: results.length - failures.length, failed: failures.length, results },
		null,
		2
	)
);
if (failures.length) process.exitCode = 1;
