/**
 * Controlled startup measurement, with real read APIs and no browser routing.
 *
 * Set BUDDY_PLAYWRIGHT_MODULE to the local Playwright module URL when it is not
 * available through ordinary Node package resolution. The private auth JSON is
 * {token, expires_at}; neither value is written to metrics or console output.
 *
 * Example: node local/tests/browser/startup-benchmark.mjs --url http://100.122.80.32:8092/
 *   --auth-file .cache/buddy-performance/benchmark-auth.json --label production-baseline
 *   --revision <source-commit> --runs 5
 *
 * Use --resume true to retain completed pairs. --cpu-profile true is a separate
 * attribution diagnostic; give it its own label and exclude it from timings.
 * Script counts represent fetched resources, rather than modules inside bundles.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { performance as nodePerformance } from 'node:perf_hooks';

const profiles = {
	desktop: {
		viewport: { width: 1440, height: 1000 },
		isMobile: false,
		hasTouch: false,
		deviceScaleFactor: 1,
		cpuSlowdown: 1,
		network: { latencyMs: 40, downloadMbps: 20, uploadMbps: 5 }
	},
	mobile: {
		viewport: { width: 428, height: 926 },
		isMobile: true,
		hasTouch: true,
		deviceScaleFactor: 3,
		cpuSlowdown: 4,
		network: { latencyMs: 150, downloadMbps: 4, uploadMbps: 1 }
	}
};

function parseArguments(arguments_) {
	const options = {
		url: null,
		authFile: null,
		output: '.cache/buddy-performance/results',
		label: 'baseline',
		revision: 'unknown',
		runs: 5,
		profiles: ['desktop', 'mobile'],
		timeoutMs: 180000,
		settleMs: 1500,
		modelLabel: null,
		resume: false,
		cpuProfile: false
	};
	for (let index = 0; index < arguments_.length; index += 2) {
		const flag = arguments_[index];
		const value = arguments_[index + 1];
		if (!value || !flag.startsWith('--')) throw new Error('Every option requires a value');
		if (flag === '--url') options.url = value;
		else if (flag === '--auth-file') options.authFile = value;
		else if (flag === '--output') options.output = value;
		else if (flag === '--label') options.label = value;
		else if (flag === '--revision') options.revision = value;
		else if (flag === '--runs') options.runs = Number(value);
		else if (flag === '--profiles') options.profiles = value.split(',');
		else if (flag === '--timeout-ms') options.timeoutMs = Number(value);
		else if (flag === '--settle-ms') options.settleMs = Number(value);
		else if (flag === '--model-label') options.modelLabel = value;
		else if (flag === '--resume') options.resume = value === 'true';
		else if (flag === '--cpu-profile') options.cpuProfile = value === 'true';
		else throw new Error('Unknown benchmark option: ' + flag);
	}
	if (!options.url || !options.authFile) throw new Error('--url and --auth-file are required');
	if (!Number.isInteger(options.runs) || options.runs < 1)
		throw new Error('--runs must be positive');
	if (!options.profiles.every((name) => Object.hasOwn(profiles, name))) {
		throw new Error('Profiles must be desktop or mobile');
	}
	const url = new URL(options.url);
	if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
		throw new Error('The target must be an HTTP app URL without credentials');
	}
	options.url = url.href;
	return options;
}

function sanitizeUrl(value) {
	try {
		const url = new URL(value);
		const path = url.pathname.replace(
			/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi,
			'[id]'
		);
		return url.origin + path;
	} catch {
		return '[non-http resource]';
	}
}

function loadPrivateAuthentication(path) {
	const authentication = JSON.parse(readFileSync(resolve(path), 'utf8'));
	if (typeof authentication.token !== 'string' || authentication.token.length < 1) {
		throw new Error('The private auth file must contain a token');
	}
	return authentication;
}

function installStartupProbe(options) {
	const probe = {
		milestones: { navigationStartMs: 0 },
		longTasks: [],
		paints: {},
		layoutShifts: [],
		inputEvents: [],
		splashSeen: false,
		observerError: false
	};
	window.__buddyStartupProbe = probe;
	performance.setResourceTimingBufferSize(10000);

	function visible(element) {
		if (!element || !element.isConnected) return false;
		const bounds = element.getBoundingClientRect();
		const style = getComputedStyle(element);
		return (
			bounds.width > 0 &&
			bounds.height > 0 &&
			style.visibility !== 'hidden' &&
			style.display !== 'none'
		);
	}

	function mark(name) {
		if (probe.milestones[name] === undefined) probe.milestones[name] = performance.now();
	}

	function checkMilestones() {
		const splash = document.getElementById('splash-screen');
		if (splash) probe.splashSeen = true;
		if (probe.splashSeen && !splash) mark('splashRemovedMs');
		const editor = document.getElementById('chat-input');
		const blocked = editor?.closest('[inert], [aria-hidden="true"]');
		if (editor?.isContentEditable && visible(editor) && !blocked) mark('composerEditableMs');
		const model = document.getElementById('model-selector-model-button');
		const selectedLabel = model?.getAttribute('aria-label') || '';
		const selected = selectedLabel.startsWith('Selected model: ');
		const expectedLabel = !options.modelLabel || selectedLabel.includes(options.modelLabel);
		// The selected model stays mounted while its row is intentionally collapsed.
		if (selected && expectedLabel && model?.isConnected && !model.disabled)
			mark('selectedModelReadyMs');
		if (
			probe.milestones.splashRemovedMs !== undefined &&
			probe.milestones.composerEditableMs !== undefined &&
			probe.milestones.selectedModelReadyMs !== undefined
		)
			mark('allReadyMs');
	}

	let checkScheduled = false;
	function scheduleCheck() {
		if (checkScheduled) return;
		checkScheduled = true;
		requestAnimationFrame(() => {
			checkScheduled = false;
			checkMilestones();
		});
	}
	const mutations = new MutationObserver(scheduleCheck);
	mutations.observe(document, { subtree: true, childList: true, attributes: true });
	document.addEventListener('DOMContentLoaded', () => {
		mark('domContentLoadedMs');
		checkMilestones();
	});
	window.addEventListener('load', () => mark('loadEventMs'));
	document.addEventListener(
		'input',
		(event) => {
			if (!event.isTrusted || !event.target?.closest?.('#chat-input')) return;
			mark('firstTrustedInputMs');
			probe.inputEvents.push({ timeMs: performance.now(), inputType: event.inputType || null });
			requestAnimationFrame(() =>
				requestAnimationFrame(() => {
					const editor = document.getElementById('chat-input');
					if (editor?.textContent?.includes(options.draftCharacter)) {
						mark('firstDraftVerifiedMs');
						mutations.disconnect();
					}
				})
			);
		},
		true
	);

	function observeEntries(type, callback) {
		try {
			const observer = new PerformanceObserver((entries) => callback(entries.getEntries()));
			observer.observe({ type, buffered: true });
		} catch {
			probe.observerError = true;
		}
	}
	observeEntries('longtask', (entries) => {
		for (const entry of entries) {
			probe.longTasks.push({ startMs: entry.startTime, durationMs: entry.duration });
		}
	});
	observeEntries('paint', (entries) => {
		for (const entry of entries) probe.paints[entry.name] = entry.startTime;
	});
	observeEntries('largest-contentful-paint', (entries) => {
		for (const entry of entries) probe.paints.largestContentfulPaintMs = entry.startTime;
	});
	observeEntries('layout-shift', (entries) => {
		for (const entry of entries) {
			probe.layoutShifts.push({
				startMs: entry.startTime,
				value: entry.value,
				recentInput: entry.hadRecentInput
			});
		}
	});
}

function createNetworkRecorder(session) {
	const requests = new Map();
	let firstDocumentTimestamp = null;
	session.on('Network.requestWillBeSent', (event) => {
		if (!event.request.url.startsWith('http')) return;
		if (firstDocumentTimestamp === null && event.type === 'Document') {
			firstDocumentTimestamp = event.timestamp;
		}
		const resource = {
			id: event.requestId,
			url: sanitizeUrl(event.request.url),
			method: event.request.method,
			type: event.type,
			startTimestamp: event.timestamp,
			initiatorType: event.initiator?.type || 'other',
			status: null,
			mimeType: null,
			fromDiskCache: false,
			fromMemoryCache: false,
			fromServiceWorker: false,
			encodedBytes: 0,
			decodedBytes: 0
		};
		requests.set(event.requestId, resource);
	});
	session.on('Network.requestServedFromCache', ({ requestId }) => {
		const resource = requests.get(requestId);
		if (resource) resource.fromMemoryCache = true;
	});
	session.on('Network.responseReceived', (event) => {
		const resource = requests.get(event.requestId);
		if (!resource) return;
		resource.responseTimestamp = event.timestamp;
		resource.status = event.response.status;
		resource.mimeType = event.response.mimeType;
		resource.protocol = event.response.protocol;
		const responseHeaders = event.response.headers;
		const cacheControl = String(
			responseHeaders['cache-control'] || responseHeaders['Cache-Control'] || ''
		);
		const maxAge = cacheControl.match(/max-age=(\d+)/i);
		resource.cachePolicy = {
			immutable: /immutable/i.test(cacheControl),
			noStore: /no-store/i.test(cacheControl),
			noCache: /no-cache/i.test(cacheControl),
			maxAgeSeconds: maxAge ? Number(maxAge[1]) : null
		};
		const contentEncoding = String(
			responseHeaders['content-encoding'] || responseHeaders['Content-Encoding'] || ''
		);
		resource.compression = ['gzip', 'br', 'deflate'].includes(contentEncoding)
			? contentEncoding
			: null;
		resource.fromDiskCache = Boolean(event.response.fromDiskCache);
		resource.fromServiceWorker = Boolean(event.response.fromServiceWorker);
		resource.responseTiming = event.response.timing
			? {
					dnsStartMs: event.response.timing.dnsStart,
					dnsEndMs: event.response.timing.dnsEnd,
					connectStartMs: event.response.timing.connectStart,
					connectEndMs: event.response.timing.connectEnd,
					sslStartMs: event.response.timing.sslStart,
					sslEndMs: event.response.timing.sslEnd,
					sendStartMs: event.response.timing.sendStart,
					sendEndMs: event.response.timing.sendEnd,
					receiveHeadersEndMs: event.response.timing.receiveHeadersEnd
				}
			: null;
	});
	session.on('Network.responseReceivedExtraInfo', (event) => {
		const resource = requests.get(event.requestId);
		if (resource) resource.wireStatus = event.statusCode;
	});
	session.on('Network.dataReceived', (event) => {
		const resource = requests.get(event.requestId);
		if (resource) resource.decodedBytes += event.dataLength;
	});
	session.on('Network.loadingFinished', (event) => {
		const resource = requests.get(event.requestId);
		if (!resource) return;
		resource.endTimestamp = event.timestamp;
		resource.encodedBytes = event.encodedDataLength;
	});
	session.on('Network.loadingFailed', (event) => {
		const resource = requests.get(event.requestId);
		if (!resource) return;
		resource.endTimestamp = event.timestamp;
		resource.failed = true;
		resource.failureType = event.blockedReason || 'network-error';
	});
	return {
		resources() {
			const origin = firstDocumentTimestamp;
			return Array.from(requests.values(), (resource) => {
				const result = { ...resource };
				result.startMs = origin === null ? null : (resource.startTimestamp - origin) * 1000;
				result.responseMs =
					resource.responseTimestamp === undefined
						? null
						: (resource.responseTimestamp - origin) * 1000;
				result.endMs =
					resource.endTimestamp === undefined ? null : (resource.endTimestamp - origin) * 1000;
				result.durationMs = result.endMs === null ? null : result.endMs - result.startMs;
				delete result.startTimestamp;
				delete result.responseTimestamp;
				delete result.endTimestamp;
				return result;
			});
		}
	};
}

function round(value) {
	return Math.round(value * 100) / 100;
}

function summarizeRun(result) {
	const ready = result.probe?.milestones.firstDraftVerifiedMs ?? Infinity;
	const beforeReady = result.resources.filter((resource) => resource.startMs <= ready);
	const scripts = beforeReady.filter((resource) => resource.type === 'Script');
	const api = beforeReady.filter((resource) => new URL(resource.url).pathname.startsWith('/api/'));
	const longTasks = (result.probe?.longTasks || []).filter((task) => task.startMs < ready);
	return {
		requestCount: beforeReady.length,
		scriptRequestCount: scripts.length,
		uniqueScriptCount: new Set(scripts.map((resource) => resource.url)).size,
		encodedTransferBytes: beforeReady.reduce((total, resource) => total + resource.encodedBytes, 0),
		decodedResourceBytes: beforeReady.reduce((total, resource) => total + resource.decodedBytes, 0),
		cachedRequestCount: beforeReady.filter(
			(resource) => resource.fromDiskCache || resource.fromMemoryCache
		).length,
		apiRequestCount: api.length,
		apiDurations: api.map((resource) => ({
			path: new URL(resource.url).pathname,
			method: resource.method,
			status: resource.status,
			startMs: round(resource.startMs),
			durationMs: resource.durationMs === null ? null : round(resource.durationMs)
		})),
		longTaskCount: longTasks.length,
		longTaskTotalMs: round(longTasks.reduce((total, task) => total + task.durationMs, 0)),
		longTaskBlockingMs: round(
			longTasks.reduce((total, task) => total + Math.max(0, task.durationMs - 50), 0)
		),
		mutatingRequestCount: beforeReady.filter(
			(resource) => !['GET', 'HEAD', 'OPTIONS'].includes(resource.method)
		).length,
		externalRequestCount: beforeReady.filter(
			(resource) => new URL(resource.url).origin !== new URL(result.targetUrl).origin
		).length
	};
}

async function measureNavigation(context, profile, options, cache, pairIndex) {
	const page = await context.newPage();
	const session = await context.newCDPSession(page);
	const recorder = createNetworkRecorder(session);
	const pageErrors = [];
	page.on('pageerror', (error) => pageErrors.push({ name: error.name }));
	await session.send('Network.enable');
	await session.send('Performance.enable');
	await session.send('Network.setCacheDisabled', { cacheDisabled: false });
	if (cache === 'cold') await session.send('Network.clearBrowserCache');
	await session.send('Emulation.setCPUThrottlingRate', { rate: profile.cpuSlowdown });
	await session.send('Network.emulateNetworkConditions', {
		offline: false,
		latency: profile.network.latencyMs,
		downloadThroughput: (profile.network.downloadMbps * 1000000) / 8,
		uploadThroughput: (profile.network.uploadMbps * 1000000) / 8,
		connectionType: profile.isMobile ? 'cellular4g' : 'ethernet'
	});
	const result = {
		pairIndex,
		cache,
		targetUrl: sanitizeUrl(options.url),
		status: 'failed',
		startedAt: new Date().toISOString(),
		wallDurationMs: null,
		probe: null,
		resources: [],
		pageErrors,
		failurePhase: null
	};
	let cpuProfileStopped = false;
	async function stopCpuProfile() {
		if (!options.cpuProfile || cpuProfileStopped) return;
		cpuProfileStopped = true;
		const captured = await session.send('Profiler.stop');
		for (const node of captured.profile.nodes) {
			if (node.callFrame.url) node.callFrame.url = sanitizeUrl(node.callFrame.url);
		}
		result.cpuProfile = captured.profile;
	}
	if (options.cpuProfile) {
		await session.send('Profiler.enable');
		await session.send('Profiler.setSamplingInterval', { interval: 1000 });
		await session.send('Profiler.start');
	}
	const start = nodePerformance.now();
	let phase = 'navigation';
	try {
		await page.goto(options.url, { waitUntil: 'commit', timeout: options.timeoutMs });
		phase = 'readiness';
		await page.waitForFunction(
			() => window.__buddyStartupProbe?.milestones.allReadyMs !== undefined,
			null,
			{ polling: 'raf', timeout: options.timeoutMs }
		);
		phase = 'typing';
		await page.locator('#chat-input').click({ timeout: options.timeoutMs });
		await page.keyboard.type('b');
		await page.waitForFunction(
			() => window.__buddyStartupProbe?.milestones.firstDraftVerifiedMs !== undefined,
			null,
			{ polling: 'raf', timeout: options.timeoutMs }
		);
		await stopCpuProfile();
		phase = 'capture';
		await page.waitForTimeout(options.settleMs);
		result.status = 'passed';
	} catch {
		result.failurePhase = phase;
	} finally {
		await stopCpuProfile().catch(() => {});
		result.wallDurationMs = round(nodePerformance.now() - start);
		result.probe = await page
			.evaluate(() => {
				const probe = window.__buddyStartupProbe;
				if (!probe) return null;
				const navigation = performance.getEntriesByType('navigation')[0];
				const navigationTiming = navigation
					? {
							responseStartMs: navigation.responseStart,
							responseEndMs: navigation.responseEnd,
							domInteractiveMs: navigation.domInteractive,
							domContentLoadedEndMs: navigation.domContentLoadedEventEnd,
							loadEndMs: navigation.loadEventEnd,
							transferBytes: navigation.transferSize,
							encodedBodyBytes: navigation.encodedBodySize,
							decodedBodyBytes: navigation.decodedBodySize
						}
					: null;
				return { ...probe, navigationTiming };
			})
			.catch(() => null);
		const browserMetrics = await session
			.send('Performance.getMetrics')
			.catch(() => ({ metrics: [] }));
		const allowedMetrics = new Set([
			'TaskDuration',
			'ScriptDuration',
			'LayoutDuration',
			'RecalcStyleDuration',
			'JSHeapUsedSize',
			'Nodes',
			'Documents',
			'LayoutCount',
			'RecalcStyleCount'
		]);
		result.browserMetrics = Object.fromEntries(
			browserMetrics.metrics
				.filter((metric) => allowedMetrics.has(metric.name))
				.map((metric) => [metric.name, metric.value])
		);
		result.resources = recorder.resources();
		result.summary = summarizeRun(result);
		await page.close();
	}
	return result;
}

function percentile(values, fraction) {
	if (!values.length) return null;
	const ordered = [...values].sort((left, right) => left - right);
	return round(ordered[Math.max(0, Math.ceil(ordered.length * fraction) - 1)]);
}

function summarizeMeasurements(runs) {
	const milestones = [
		'splashRemovedMs',
		'composerEditableMs',
		'selectedModelReadyMs',
		'firstTrustedInputMs',
		'firstDraftVerifiedMs'
	];
	const summary = {};
	for (const profile of ['desktop', 'mobile']) {
		for (const cache of ['cold', 'warm']) {
			const group = runs.filter((run) => run.profile === profile && run.cache === cache);
			if (!group.length) continue;
			const passed = group.filter((run) => run.status === 'passed');
			const metrics = {};
			for (const milestone of milestones) {
				const values = passed.map((run) => run.probe.milestones[milestone]).filter(Number.isFinite);
				metrics[milestone] = { medianMs: percentile(values, 0.5), p90Ms: percentile(values, 0.9) };
			}
			summary[profile + '-' + cache] = {
				sampleCount: group.length,
				passedCount: passed.length,
				metrics,
				medianTransferBytes: percentile(
					passed.map((run) => run.summary.encodedTransferBytes),
					0.5
				),
				medianScriptCount: percentile(
					passed.map((run) => run.summary.uniqueScriptCount),
					0.5
				),
				medianLongTaskBlockingMs: percentile(
					passed.map((run) => run.summary.longTaskBlockingMs),
					0.5
				)
			};
		}
	}
	return summary;
}

async function main() {
	const options = parseArguments(process.argv.slice(2));
	loadPrivateAuthentication(options.authFile);
	const playwrightSpecifier = process.env.BUDDY_PLAYWRIGHT_MODULE || 'playwright';
	const { chromium } = await import(playwrightSpecifier);
	const browser = await chromium.launch({ headless: true });
	const outputDirectory = resolve(options.output);
	mkdirSync(outputDirectory, { recursive: true });
	let report = {
		schemaVersion: 1,
		label: options.label,
		revision: options.revision,
		targetUrl: sanitizeUrl(options.url),
		createdAt: new Date().toISOString(),
		browserVersion: browser.version(),
		nodeVersion: process.version,
		authentication: 'private-short-lived-token',
		diagnostic: options.cpuProfile,
		routeInterception: false,
		serviceWorkers: 'blocked',
		percentileMethod: 'nearest-rank; five samples makes p90 the observed maximum',
		profiles,
		runs: [],
		summary: {}
	};
	const reportPath = resolve(outputDirectory, options.label + '.json');
	if (options.resume && existsSync(reportPath)) {
		const saved = JSON.parse(readFileSync(reportPath, 'utf8'));
		if (
			saved.revision !== options.revision ||
			saved.targetUrl !== sanitizeUrl(options.url) ||
			Boolean(saved.diagnostic) !== options.cpuProfile ||
			JSON.stringify(saved.profiles) !== JSON.stringify(profiles)
		) {
			await browser.close();
			throw new Error('Cannot resume a different revision or target');
		}
		report = saved;
	}
	try {
		for (const profileName of options.profiles) {
			const profile = profiles[profileName];
			for (let pairIndex = 1; pairIndex <= options.runs; pairIndex += 1) {
				const previous = report.runs.filter(
					(run) => run.profile === profileName && run.pairIndex === pairIndex
				);
				if (previous.length === 2 && previous.every((run) => run.status === 'passed')) continue;
				report.runs = report.runs.filter(
					(run) => run.profile !== profileName || run.pairIndex !== pairIndex
				);
				const authentication = loadPrivateAuthentication(options.authFile);
				const context = await browser.newContext({
					viewport: profile.viewport,
					isMobile: profile.isMobile,
					hasTouch: profile.hasTouch,
					deviceScaleFactor: profile.deviceScaleFactor,
					locale: 'en-US',
					colorScheme: 'light',
					reducedMotion: 'no-preference',
					serviceWorkers: 'block'
				});
				await context.addInitScript(
					({ token }) => {
						localStorage.setItem('token', token);
						localStorage.setItem('locale', 'en-US');
						localStorage.setItem('theme', 'light');
						localStorage.setItem('sidebar', 'false');
						for (const key of Object.keys(localStorage)) {
							if (key.startsWith('draft-chat') || key.startsWith('chat-input'))
								localStorage.removeItem(key);
						}
						sessionStorage.removeItem('chat-input');
						sessionStorage.removeItem('buddyOAuthConnectSession');
					},
					{ token: authentication.token }
				);
				await context.addInitScript(installStartupProbe, {
					modelLabel: options.modelLabel,
					draftCharacter: 'b'
				});
				try {
					const cold = await measureNavigation(context, profile, options, 'cold', pairIndex);
					cold.profile = profileName;
					report.runs.push(cold);
					const warm = await measureNavigation(context, profile, options, 'warm', pairIndex);
					warm.profile = profileName;
					report.runs.push(warm);
				} finally {
					await context.close();
				}
				report.summary = summarizeMeasurements(report.runs);
				writeFileSync(reportPath, JSON.stringify(report, null, 2));
				console.log(
					options.label +
						': ' +
						profileName +
						' pair ' +
						pairIndex +
						'/' +
						options.runs +
						' captured'
				);
			}
		}
	} finally {
		await browser.close();
	}
	console.log(JSON.stringify({ reportPath, summary: report.summary }, null, 2));
	if (report.runs.some((run) => run.status !== 'passed')) process.exitCode = 1;
}

main().catch((error) => {
	console.error('Startup benchmark failed: ' + error.name);
	process.exitCode = 1;
});
