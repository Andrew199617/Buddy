import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve, sep } from 'node:path';
import { createHash } from 'node:crypto';
import { TraceMap, originalPositionFor } from '@jridgewell/trace-mapping';

function parseArguments(arguments_) {
	const options = {
		input: null,
		build: null,
		output: null,
		loader: null,
		loaderSources: resolve('local/web')
	};
	for (let index = 0; index < arguments_.length; index += 2) {
		const flag = arguments_[index];
		const value = arguments_[index + 1];
		if (!value) throw new Error('Every option requires a value');
		if (flag === '--input') options.input = resolve(value);
		else if (flag === '--build') options.build = resolve(value);
		else if (flag === '--output') options.output = resolve(value);
		else if (flag === '--loader') options.loader = resolve(value);
		else if (flag === '--loader-sources') options.loaderSources = resolve(value);
		else throw new Error('Unknown option: ' + flag);
	}
	if (!options.input || !options.build || !options.output) {
		throw new Error('Provide --input diagnostic.json --build build-dir --output summary.json');
	}
	return options;
}

function readLoaderRanges(loaderPath, loaderSources) {
	if (!loaderPath) return { sha256: null, ranges: [] };
	const rawLoader = readFileSync(loaderPath, 'utf8');
	const loader = rawLoader.replace(/\r\n/g, '\n');
	const config = readFileSync(resolve('local/owui_local_patches.py'), 'utf8');
	const paths = [...config.matchAll(/WEB_DIR \/ '([A-Za-z0-9_-]+\.js)'/g)];
	const ranges = [];
	for (const match of paths) {
		const source = 'local/web/' + match[1];
		const content = readFileSync(resolve(loaderSources, match[1]), 'utf8').replace(/\r\n/g, '\n');
		const index = loader.indexOf(content);
		if (index < 0) continue;
		const startLine = loader.slice(0, index).split('\n').length;
		const endLine = startLine + content.split('\n').length - 1;
		ranges.push({ source, startLine, endLine });
	}
	return { sha256: createHash('sha256').update(rawLoader).digest('hex'), ranges };
}

function normalizeSourcePath(value) {
	const path = value.replace(/\\/g, '/');
	const modules = path.indexOf('node_modules/');
	if (modules >= 0) return path.slice(modules);
	const source = path.indexOf('src/');
	if (source >= 0) return path.slice(source);
	return path.replace(/^(?:\.\.\/)+/, '');
}

function createFrameResolver(buildDirectory, loaderRanges) {
	const maps = new Map();
	return function resolveFrame(frame) {
		let source = frame.url || '[runtime]';
		let line = frame.lineNumber + 1;
		let name = frame.functionName || '(anonymous)';
		if (frame.url?.startsWith('http')) {
			const path = new URL(frame.url).pathname;
			source = path;
			if (path === '/static/loader.js') {
				const range = loaderRanges.find(
					(range) => line >= range.startLine && line <= range.endLine
				);
				if (range) return { source: range.source, line: line - range.startLine + 1, name };
			}
			const mapPath = resolve(buildDirectory, '.' + path + '.map');
			const withinBuild = mapPath.startsWith(buildDirectory + sep);
			if (withinBuild && !maps.has(mapPath)) {
				let map = null;
				if (existsSync(mapPath)) map = new TraceMap(JSON.parse(readFileSync(mapPath, 'utf8')));
				maps.set(mapPath, map);
			}
			const map = maps.get(mapPath);
			if (map && frame.lineNumber >= 0 && frame.columnNumber >= 0) {
				const original = originalPositionFor(map, { line, column: frame.columnNumber });
				if (original.source) {
					source = normalizeSourcePath(original.source);
					line = original.line;
					if (original.name) name = original.name;
				}
			}
		}
		return { source, line, name };
	};
}

function addDuration(records, key, identity, milliseconds, field) {
	let record = records.get(key);
	if (!record) {
		record = { ...identity, selfMs: 0, inclusiveMs: 0 };
		records.set(key, record);
	}
	record[field] += milliseconds;
}

function ranked(records, field, limit = 40) {
	return [...records.values()]
		.map((record) => ({
			...record,
			selfMs: Math.round(record.selfMs * 100) / 100,
			inclusiveMs: Math.round(record.inclusiveMs * 100) / 100
		}))
		.sort((left, right) => right[field] - left[field])
		.slice(0, limit);
}

function summarizeProfile(run, resolveFrame) {
	const profile = run.cpuProfile;
	if (!profile?.samples || !profile.timeDeltas) throw new Error('Run lacks a sampled CPU profile');
	const nodes = new Map(profile.nodes.map((node) => [node.id, node]));
	const parents = new Map();
	for (const node of profile.nodes) {
		for (const child of node.children || []) parents.set(child, node.id);
	}
	const frames = new Map();
	for (const node of profile.nodes) frames.set(node.id, resolveFrame(node.callFrame));
	const sources = new Map();
	const functions = new Map();
	let sampledMs = 0;
	for (let index = 0; index < profile.samples.length; index += 1) {
		const milliseconds = (profile.timeDeltas[index] || 0) / 1000;
		sampledMs += milliseconds;
		const leafId = profile.samples[index];
		const leaf = frames.get(leafId);
		if (!leaf) continue;
		addDuration(sources, leaf.source, { source: leaf.source }, milliseconds, 'selfMs');
		const leafKey = JSON.stringify(leaf);
		addDuration(functions, leafKey, leaf, milliseconds, 'selfMs');
		const seenSources = new Set();
		const seenFunctions = new Set();
		let nodeId = leafId;
		while (nodes.has(nodeId)) {
			const frame = frames.get(nodeId);
			const functionKey = JSON.stringify(frame);
			if (!seenSources.has(frame.source)) {
				addDuration(sources, frame.source, { source: frame.source }, milliseconds, 'inclusiveMs');
				seenSources.add(frame.source);
			}
			if (!seenFunctions.has(functionKey)) {
				addDuration(functions, functionKey, frame, milliseconds, 'inclusiveMs');
				seenFunctions.add(functionKey);
			}
			nodeId = parents.get(nodeId);
		}
	}
	return {
		profile: run.profile,
		cache: run.cache,
		pairIndex: run.pairIndex,
		status: run.status,
		verifiedDraftMs: run.probe?.milestones.firstDraftVerifiedMs,
		sampledMs: Math.round(sampledMs * 100) / 100,
		profileDurationMs: (profile.endTime - profile.startTime) / 1000,
		sourceTotals: ranked(sources, 'selfMs', Infinity),
		functionTotals: ranked(functions, 'selfMs', Infinity),
		topSourcesBySelf: ranked(sources, 'selfMs'),
		topSourcesByInclusive: ranked(sources, 'inclusiveMs'),
		topFunctionsBySelf: ranked(functions, 'selfMs'),
		topFunctionsByInclusive: ranked(functions, 'inclusiveMs')
	};
}

const options = parseArguments(process.argv.slice(2));
const report = JSON.parse(readFileSync(options.input, 'utf8'));
if (!report.diagnostic) throw new Error('Only separate diagnostic reports may be attributed');
const loader = readLoaderRanges(options.loader, options.loaderSources);
const resolveFrame = createFrameResolver(options.build, loader.ranges);
const summary = {
	label: report.label,
	revision: report.revision,
	targetUrl: report.targetUrl,
	diagnostic: true,
	loaderAttribution: loader,
	note: 'Sampled attribution includes profiler overhead and idle/runtime samples. Inclusive rows overlap; compare source self time for additive attribution. Source maps are read locally and source content is never emitted.',
	runs: report.runs.map((run) => summarizeProfile(run, resolveFrame))
};
writeFileSync(options.output, JSON.stringify(summary, null, 2));
console.log('Wrote sanitized CPU attribution: ' + options.output);
