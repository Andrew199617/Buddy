/** Secondary Available Tools layout checks; all tools and APIs are synthetic. */
import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createMockedPage, outputDirectory } from './mobile-ui-preview.mjs';
import {
	readProductionSourceMaps,
	resolveProductionExports
} from './production-module-exports.mjs';

const workspace = fileURLToPath(new URL('../../../', import.meta.url));
const sourceMaps = readProductionSourceMaps(
	process.env.BUDDY_UI_TEST_BUILD || resolve(workspace, 'build')
);
const stores = resolveProductionExports(sourceMaps, '/stores/index.ts', ['tools', 'toolServers']);
const longFunctionName = 'preview_search_' + 'long_function_identifier_'.repeat(6);
const fixtures = [
	{
		id: 'preview-research-tools',
		name: 'Preview Research and Reference Library Tools',
		authenticated: true,
		meta: {
			description:
				'Synthetic tools for checking readable names, descriptions and function details on a narrow phone. No real account or provider is connected.'
		},
		specs: Array.from({ length: 14 }, (_, index) => ({
			name: index === 0 ? longFunctionName : `preview_reference_lookup_${index + 1}`,
			description: `Synthetic function ${index + 1} description wraps across lines so the complete explanation remains readable without horizontal scrolling.`
		}))
	},
	{
		id: 'preview-planning-tools',
		name: 'Preview Planning Tools',
		authenticated: true,
		meta: { description: 'An isolated fixture with a nested function specification.' },
		specs: [
			{
				function: {
					name: 'preview_plan_next_step',
					description: 'Nested synthetic function details remain readable after expanding the row.'
				}
			}
		]
	}
];
const serverFixtures = [
	{
		url: 'https://preview.example.invalid/' + 'long_server_path/'.repeat(8),
		openapi: {
			info: {
				title: 'Preview OpenAPI Reference Server',
				version: '1.0',
				description:
					'Synthetic server details for checking long URLs and wrapped function descriptions.'
			}
		},
		specs: [
			{
				name: 'preview_server_lookup',
				description:
					'Server function details are display fixtures only; this test never executes tools.'
			}
		]
	}
];
const results = [];
const keyboardTrigger = process.env.BUDDY_UI_TOOL_TRIGGER_KEYBOARD === '1';
const unsentDraft = 'Preview tools layout';

async function openAvailableTools(page) {
	await page.locator('#chat-input').focus();
	const trigger = page.locator('#available-tools-button');
	await trigger.waitFor({ state: 'visible', timeout: 5000 });
	if (keyboardTrigger) {
		await trigger.focus();
		await page.keyboard.press('Enter');
		return;
	}
	await trigger.click();
}

async function installFixtures(page) {
	await page.evaluate(
		async ({ stores, fixtures, serverFixtures }) => {
			const module = await import(stores.url);
			module[stores.aliases.tools].set(fixtures);
			module[stores.aliases.toolServers].set(serverFixtures);
		},
		{ stores, fixtures, serverFixtures }
	);
}

async function enableFixtureTools(page) {
	await page.locator('#chat-input').click();
	await page.locator('#integration-menu-button').focus();
	await page.keyboard.press('Enter');
	const integrations = page.locator('.app-dropdown-menu:visible');
	await integrations.getByRole('button', { name: /^Tools/ }).click();
	for (const fixture of fixtures) {
		const action = integrations.getByRole('button', { name: fixture.name, exact: true });
		await action.click();
		assert.equal(await action.getAttribute('aria-pressed'), 'true');
	}
	await page.keyboard.press('Escape');
	await integrations.waitFor({ state: 'detached' });
	await page.locator('#available-tools-button').waitFor({ state: 'visible' });
	assert.equal((await page.locator('#available-tools-button').innerText()).trim(), '2');
}

async function inspectGeometry(page, mobile) {
	const geometry = await page.locator('.available-tools-content').evaluate((element) => {
		const rectangle = (node) => {
			const rect = node.getBoundingClientRect();
			return {
				left: rect.left,
				right: rect.right,
				top: rect.top,
				bottom: rect.bottom,
				width: rect.width,
				height: rect.height
			};
		};
		const body = element.querySelector('.available-tools-body');
		const row = element.querySelector('.available-tools-row');
		const description = element.querySelector('.available-tools-description');
		return {
			panel: rectangle(element),
			close: rectangle(element.querySelector('.available-tools-close')),
			row: rectangle(row),
			body: rectangle(body),
			bodyScrollWidth: body.scrollWidth,
			bodyClientWidth: body.clientWidth,
			descriptionFont: parseFloat(getComputedStyle(description).fontSize),
			descriptionWhitespace: getComputedStyle(description).whiteSpace,
			viewport: { width: innerWidth, height: innerHeight }
		};
	});
	assert.ok(
		geometry.panel.left >= 7 && geometry.panel.right <= geometry.viewport.width - 7,
		'Comfortable viewport margins'
	);
	assert.ok(
		geometry.panel.top >= 6 && geometry.panel.bottom <= geometry.viewport.height - 6,
		'Panel height stays inside the viewport: ' + JSON.stringify(geometry.panel)
	);
	assert.ok(
		geometry.close.width >= 44 && geometry.close.height >= 44,
		'Touch friendly close control'
	);
	assert.ok(geometry.row.height >= 48, 'Comfortable expandable rows');
	assert.ok(
		geometry.bodyScrollWidth <= geometry.bodyClientWidth + 1,
		'No horizontal body overflow'
	);
	assert.ok(geometry.descriptionFont >= (mobile ? 14 : 13), 'Readable description size');
	assert.equal(geometry.descriptionWhitespace, 'normal');
	return geometry;
}

async function scenario(name, viewport, mobile, dark = false, urlPath = '/') {
	const session = await createMockedPage({
		viewport,
		hasTouch: mobile,
		isMobile: mobile,
		colorScheme: dark ? 'dark' : 'light',
		modelSelectorVisible: false,
		polish: false,
		usage: false,
		urlPath
	});
	const { page } = session;
	try {
		await installFixtures(page);
		await enableFixtureTools(page);
		await page.locator('#chat-input').focus();
		await page.keyboard.type(unsentDraft);
		await openAvailableTools(page);
		const content = page.locator('.available-tools-content');
		await content.waitFor({ state: 'visible' });
		await page.waitForTimeout(350);
		assert.equal(await content.locator('h2').innerText(), 'Available Tools');
		const initialGeometry = await inspectGeometry(page, mobile);
		await page.screenshot({ path: resolve(outputDirectory, `${name}-tools.png`) });
		const research = content.getByRole('button').filter({ hasText: fixtures[0].name });
		await research.click();
		assert.equal(await research.getAttribute('aria-expanded'), 'true');
		await content.getByText(longFunctionName, { exact: true }).waitFor({ state: 'visible' });
		await page.waitForTimeout(350);
		const expandedGeometry = await inspectGeometry(page, mobile);
		await page.screenshot({ path: resolve(outputDirectory, `${name}-tools-expanded.png`) });
		await research.click();
		assert.equal(await research.getAttribute('aria-expanded'), 'false');
		await research.click();
		assert.equal(await research.getAttribute('aria-expanded'), 'true');
		await page.waitForTimeout(350);
		const headerTop = await content
			.locator('.available-tools-header')
			.evaluate((element) => element.getBoundingClientRect().top);
		const body = content.locator('.available-tools-body');
		const scroll = await body.evaluate((element) => {
			element.scrollTop = element.scrollHeight;
			return { top: element.scrollTop, height: element.scrollHeight, client: element.clientHeight };
		});
		assert.ok(
			scroll.top > 0 && scroll.height > scroll.client,
			'Expanded details scroll inside the panel'
		);
		assert.equal(
			await content
				.locator('.available-tools-header')
				.evaluate((element) => element.getBoundingClientRect().top),
			headerTop,
			'Close header remains fixed while details scroll'
		);
		const planning = content.getByRole('button').filter({ hasText: fixtures[1].name });
		await planning.click();
		await content
			.getByText('preview_plan_next_step', { exact: true })
			.waitFor({ state: 'visible' });
		const server = content
			.getByRole('button')
			.filter({ hasText: 'Preview OpenAPI Reference Server' });
		await server.click();
		await content.getByText('preview_server_lookup', { exact: true }).waitFor({ state: 'visible' });
		await inspectGeometry(page, mobile);
		await content.locator('.available-tools-close').click();
		await content.waitFor({ state: 'detached' });
		assert.equal(
			(await page.locator('#available-tools-button').innerText()).trim(),
			'2',
			'Dismiss preserves selected tools'
		);
		assert.equal((await page.locator('#chat-input').innerText()).trim(), unsentDraft);
		await openAvailableTools(page);
		await content.waitFor({ state: 'visible' });
		await page.waitForTimeout(350);
		await inspectGeometry(page, mobile);
		await page.keyboard.press('Escape');
		await content.waitFor({ state: 'detached' });
		assert.ok(
			!session.requests.some((request) =>
				/\/tools\/.*\/(run|execute)|\/chat\/completions/.test(request)
			),
			'No tool execution or model requests'
		);
		assert.ok(
			!session.blockedMutations.some((request) => request.startsWith('DELETE ')),
			'No deletion'
		);
		const scopeNotes = [];
		if (name === 'phone-landscape') {
			scopeNotes.push(
				'Landscape uses a synthetic existing conversation. The preserved local home composer clips its secondary controls at 667 × 375; that existing layout is outside this tool panel styling change.'
			);
		}
		results.push({
			name,
			passed: true,
			origin: new URL(page.url()).origin,
			route: new URL(page.url()).pathname,
			scopeNotes,
			triggerActivation: keyboardTrigger ? 'keyboard' : 'pointer',
			initialGeometry,
			expandedGeometry,
			cases: [
				'secondary wrench after enabling tools',
				'wrapped function details',
				'fixed close and bounded scrolling',
				'server details and long URL',
				'dismiss and reopen',
				'unsent draft preservation',
				'Escape dismissal',
				'no tool execution'
			]
		});
	} catch (error) {
		await page.screenshot({ path: resolve(outputDirectory, `${name}-tools-failure.png`) });
		writeFileSync(
			resolve(outputDirectory, `${name}-tools-failure.json`),
			JSON.stringify(
				{
					message: error.message,
					body: (await page.locator('body').innerText()).slice(0, 5000),
					requests: session.requests,
					errors: session.errors,
					blockedMutations: session.blockedMutations,
					composer: await page.evaluate(() => {
						const editor = document.getElementById('chat-input');
						const trigger = document.getElementById('available-tools-button');
						return {
							activeElement: document.activeElement?.id,
							expanded: editor?.closest('.buddy-composer')?.getAttribute('data-expanded'),
							editorRect: editor?.getBoundingClientRect().toJSON(),
							triggerRect: trigger?.getBoundingClientRect().toJSON(),
							triggerVisibility: trigger ? getComputedStyle(trigger).visibility : null,
							triggerDisplay: trigger ? getComputedStyle(trigger).display : null
						};
					})
				},
				null,
				2
			)
		);
		throw error;
	} finally {
		await session.browser.close();
	}
}

const selectedCases = process.argv
	.find((argument) => argument.startsWith('--only='))
	?.slice(7)
	.split(',');
if (!selectedCases || selectedCases.includes('phone-narrow')) {
	await scenario('phone-narrow', { width: 320, height: 740 }, true);
}
if (!selectedCases || selectedCases.includes('phone-dark')) {
	await scenario('phone-dark', { width: 390, height: 844 }, true, true);
}
if (!selectedCases || selectedCases.includes('phone-landscape')) {
	await scenario(
		'phone-landscape',
		{ width: 667, height: 375 },
		true,
		false,
		'/c/mobile-preview-chat-1'
	);
}
if (!selectedCases || selectedCases.includes('desktop')) {
	await scenario('desktop', { width: 1280, height: 900 }, false);
}
writeFileSync(
	resolve(outputDirectory, 'available-tools-results.json'),
	JSON.stringify(results, null, 2)
);
console.log(JSON.stringify(results, null, 2));
