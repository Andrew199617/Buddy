/** Resolve real production exports from source maps without app test hooks. */
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { parse } from 'acorn';
import { originalPositionFor, TraceMap } from '@jridgewell/trace-mapping';

function visitAst(node, callback) {
	if (!node || typeof node !== 'object') return;
	callback(node);
	for (const value of Object.values(node)) {
		if (Array.isArray(value)) {
			for (const child of value) visitAst(child, callback);
		} else if (value && typeof value === 'object') {
			visitAst(value, callback);
		}
	}
}

export function readProductionSourceMaps(buildDirectory) {
	const chunksDirectory = resolve(buildDirectory, '_app/immutable/chunks');
	const modules = [];
	for (const filename of readdirSync(chunksDirectory)) {
		if (!filename.endsWith('.js.map')) continue;
		const mapPath = resolve(chunksDirectory, filename);
		const map = JSON.parse(readFileSync(mapPath, 'utf8'));
		modules.push({
			map: map,
			codePath: mapPath.slice(0, -4),
			url: '/_app/immutable/chunks/' + filename.slice(0, -4)
		});
	}
	return modules;
}

export function findProductionModule(modules, sourceSuffix) {
	const matches = modules.filter((module) =>
		module.map.sources.some((source) => source.endsWith(sourceSuffix))
	);
	assert.equal(matches.length, 1, 'Expected one production module for ' + sourceSuffix);
	return matches[0];
}

export function resolveProductionExports(modules, sourceSuffix, requestedNames = []) {
	const module = findProductionModule(modules, sourceSuffix);
	const code = readFileSync(module.codePath, 'utf8');
	const ast = parse(code, { ecmaVersion: 'latest', sourceType: 'module', locations: true });
	const traceMap = new TraceMap(module.map);
	const originals = new Map();
	visitAst(ast, (node) => {
		let identifier;
		if (node.type === 'VariableDeclarator' && node.id.type === 'Identifier') identifier = node.id;
		if (node.type === 'FunctionDeclaration' && node.id) identifier = node.id;
		if (!identifier) return;
		const original = originalPositionFor(traceMap, identifier.loc.start);
		if (original.source?.endsWith(sourceSuffix) && original.name) {
			originals.set(identifier.name, original.name);
		}
	});
	const aliases = {};
	for (const statement of ast.body) {
		if (statement.type !== 'ExportNamedDeclaration') continue;
		for (const exported of statement.specifiers) {
			const name = originals.get(exported.local.name);
			if (name) aliases[name] = exported.exported.name;
		}
	}
	for (const name of requestedNames) assert.ok(aliases[name], 'Missing production export: ' + name);
	return { url: module.url, aliases: aliases };
}
