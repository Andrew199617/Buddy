/** Serve an existing Buddy build while reusing a separate backend.
 * This process never imports the Python app or opens its database.
 */
import { createReadStream } from 'node:fs';
import { readFile, stat } from 'node:fs/promises';
import { dirname, extname, isAbsolute, relative, resolve, sep } from 'node:path';
import { isIP } from 'node:net';
import { fileURLToPath } from 'node:url';
import { parseArgs, promisify } from 'node:util';
import { brotliCompress, constants, gzip } from 'node:zlib';
import { preview } from 'vite';

const WORKSPACE_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const COMPRESS_MIN_BYTES = 1024;
const COMPRESS_MAX_BYTES = 32 * 1024 * 1024;
const CACHE_MAX_BYTES = 96 * 1024 * 1024;
const gzipBuffer = promisify(gzip);
const brotliBuffer = promisify(brotliCompress);
const MIME_TYPES = {
	'.html': 'text/html; charset=utf-8',
	'.js': 'text/javascript; charset=utf-8',
	'.mjs': 'text/javascript; charset=utf-8',
	'.css': 'text/css; charset=utf-8',
	'.json': 'application/json; charset=utf-8',
	'.svg': 'image/svg+xml',
	'.xml': 'application/xml; charset=utf-8',
	'.txt': 'text/plain; charset=utf-8',
	'.wasm': 'application/wasm',
	'.png': 'image/png',
	'.jpg': 'image/jpeg',
	'.jpeg': 'image/jpeg',
	'.gif': 'image/gif',
	'.webp': 'image/webp',
	'.ico': 'image/x-icon',
	'.woff': 'font/woff',
	'.woff2': 'font/woff2',
	'.ttf': 'font/ttf',
	'.otf': 'font/otf',
	'.mp3': 'audio/mpeg',
	'.mp4': 'video/mp4',
	'.pdf': 'application/pdf'
};
const TEXT_EXTENSIONS = new Set(['.html', '.js', '.mjs', '.css', '.json', '.svg', '.xml', '.txt']);
const COMPRESSIBLE_EXTENSIONS = new Set([...TEXT_EXTENSIONS, '.ttf', '.woff', '.wasm']);
const PROXY_PREFIXES = ['/api', '/oauth', '/ws', '/openai', '/ollama', '/cache', '/pyodide'];
const PROXY_FILES = new Set([
	'/static/loader.js',
	'/manifest.json',
	'/health',
	'/health/db',
	'/ready'
]);

function isBackendPath(pathname) {
	return (
		PROXY_FILES.has(pathname) ||
		PROXY_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`))
	);
}

function encodingQualities(header = '') {
	const qualities = new Map();
	for (const item of header.toLowerCase().split(',')) {
		const [name, ...parameters] = item.trim().split(';');
		if (!name) continue;
		const qualityParameter = parameters.find((parameter) => parameter.trim().startsWith('q='));
		const quality = qualityParameter ? Number(qualityParameter.trim().slice(2)) : 1;
		qualities.set(name, Number.isFinite(quality) && quality >= 0 && quality <= 1 ? quality : 0);
	}
	return qualities;
}

export function selectEncoding(header = '') {
	const qualities = encodingQualities(header);
	const wildcard = qualities.get('*') ?? 0;
	const brotliQuality = qualities.get('br') ?? wildcard;
	const gzipQuality = qualities.get('gzip') ?? wildcard;
	const identityQuality = qualities.get('identity') ?? (qualities.get('*') === 0 ? 0 : 1);
	let encoding = null;
	let selectedQuality = 0;
	if (brotliQuality > 0) {
		encoding = 'br';
		selectedQuality = brotliQuality;
	}
	if (gzipQuality > selectedQuality) {
		encoding = 'gzip';
		selectedQuality = gzipQuality;
	}
	if (qualities.has('identity') && identityQuality > selectedQuality) return 'identity';
	if (encoding) return encoding;
	return identityQuality > 0 ? 'identity' : null;
}

function identityAllowed(header) {
	const qualities = encodingQualities(header);
	return (qualities.get('identity') ?? (qualities.get('*') === 0 ? 0 : 1)) > 0;
}

function configureProxy(proxy) {
	function setTrustedForwarding(proxyRequest, request) {
		// Discard client-supplied forwarding claims at this local proxy boundary.
		proxyRequest.removeHeader('forwarded');
		proxyRequest.removeHeader('x-forwarded-for');
		proxyRequest.removeHeader('x-forwarded-host');
		proxyRequest.removeHeader('x-forwarded-port');
		proxyRequest.removeHeader('x-forwarded-proto');
		proxyRequest.setHeader('host', request.headers.host);
		proxyRequest.setHeader('x-forwarded-proto', request.socket.encrypted ? 'https' : 'http');
		proxyRequest.setHeader('x-forwarded-for', request.socket.remoteAddress || '127.0.0.1');
	}
	proxy.on('proxyReq', setTrustedForwarding);
	proxy.on('proxyReqWs', setTrustedForwarding);
	function setProxyCachePolicy(proxyResponse, request) {
		const pathname = new URL(request.url, 'http://buddy.invalid').pathname;
		if (pathname === '/manifest.json' || pathname === '/static/loader.js') {
			proxyResponse.headers['cache-control'] = 'no-cache';
		} else if (!pathname.startsWith('/pyodide')) {
			proxyResponse.headers['cache-control'] = 'private, no-store';
		}
	}
	proxy.on('proxyRes', setProxyCachePolicy);
}

function createProxyOptions(backendUrl, webSocket = false) {
	return { target: backendUrl, changeOrigin: false, ws: webSocket, configure: configureProxy };
}

function backendProxy(backendUrl) {
	return {
		'/api': createProxyOptions(backendUrl, true),
		'/oauth': createProxyOptions(backendUrl),
		'/ws': createProxyOptions(backendUrl, true),
		'/openai': createProxyOptions(backendUrl),
		'/ollama': createProxyOptions(backendUrl),
		'/cache': createProxyOptions(backendUrl),
		'/pyodide': createProxyOptions(backendUrl),
		'/static/loader.js': createProxyOptions(backendUrl),
		'/manifest.json': createProxyOptions(backendUrl),
		'/health': createProxyOptions(backendUrl),
		'/ready': createProxyOptions(backendUrl)
	};
}

function sendPlain(response, statusCode, message) {
	response.statusCode = statusCode;
	response.setHeader('Content-Type', 'text/plain; charset=utf-8');
	response.setHeader('Cache-Control', 'no-store');
	response.end(message);
}

function parseByteRange(value, size) {
	const match = /^bytes=(\d*)-(\d*)$/.exec(value);
	if (!match || size === 0 || (!match[1] && !match[2])) return null;
	let start;
	let end;
	if (!match[1]) {
		const suffixLength = Number(match[2]);
		if (suffixLength <= 0) return null;
		start = Math.max(0, size - suffixLength);
		end = size - 1;
	} else {
		start = Number(match[1]);
		end = match[2] ? Math.min(Number(match[2]), size - 1) : size - 1;
	}
	if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start > end || start >= size)
		return null;
	return { start, end };
}

function isAllowedHost(request, allowedHosts) {
	try {
		const hostname = new URL(`http://${request.headers.host}`).hostname
			.toLowerCase()
			.replace(/^\[|\]$/g, '');
		if (isIP(hostname) || hostname === 'localhost' || hostname.endsWith('.localhost')) return true;
		return allowedHosts.some((host) => host.toLowerCase() === hostname);
	} catch {
		return false;
	}
}

function createStaticMiddleware(frontendDir, allowedHosts) {
	const assetCache = new Map();
	let cachedBytes = 0;

	async function readCompressedAsset(filePath, fileStat) {
		const existing = assetCache.get(filePath);
		if (existing && existing.mtimeMs === fileStat.mtimeMs && existing.size === fileStat.size)
			return existing;
		if (existing) {
			cachedBytes -= existing.bytes;
			assetCache.delete(filePath);
		}
		const raw = await readFile(filePath);
		const gzipOptions = { level: 6 };
		const brotliOptions = {
			params: {
				[constants.BROTLI_PARAM_QUALITY]: 4,
				[constants.BROTLI_PARAM_MODE]: TEXT_EXTENSIONS.has(extname(filePath).toLowerCase())
					? constants.BROTLI_MODE_TEXT
					: constants.BROTLI_MODE_GENERIC
			}
		};
		const [compressedGzip, compressedBrotli] = await Promise.all([
			gzipBuffer(raw, gzipOptions),
			brotliBuffer(raw, brotliOptions)
		]);
		const entry = {
			mtimeMs: fileStat.mtimeMs,
			size: fileStat.size,
			raw,
			gzip: compressedGzip,
			br: compressedBrotli,
			bytes: raw.length + compressedGzip.length + compressedBrotli.length
		};
		assetCache.set(filePath, entry);
		cachedBytes += entry.bytes;
		while (cachedBytes > CACHE_MAX_BYTES && assetCache.size > 1) {
			const oldestPath = assetCache.keys().next().value;
			cachedBytes -= assetCache.get(oldestPath).bytes;
			assetCache.delete(oldestPath);
		}
		return entry;
	}

	async function serveStatic(request, response, next) {
		let pathname;
		try {
			pathname = decodeURIComponent(new URL(request.url, 'http://buddy.invalid').pathname);
		} catch {
			sendPlain(response, 400, 'Invalid path');
			return;
		}
		if (!isAllowedHost(request, allowedHosts)) {
			sendPlain(response, 403, 'Host is not allowed');
			return;
		}
		if (isBackendPath(pathname)) return next();
		if (request.method !== 'GET' && request.method !== 'HEAD') {
			response.setHeader('Allow', 'GET, HEAD');
			sendPlain(response, 405, 'Method not allowed');
			return;
		}
		if (
			pathname.includes('\\') ||
			pathname.includes('\0') ||
			pathname.split('/').some((part) => part.startsWith('.'))
		) {
			sendPlain(response, 404, 'Not found');
			return;
		}
		let filePath = resolve(frontendDir, `.${pathname}`);
		const relativePath = relative(frontendDir, filePath);
		if (relativePath.startsWith(`..${sep}`) || relativePath === '..' || isAbsolute(relativePath)) {
			sendPlain(response, 404, 'Not found');
			return;
		}
		let fileStat = await stat(filePath).catch(() => null);
		if (!fileStat?.isFile()) {
			if (pathname.startsWith('/_app/') || pathname.startsWith('/static/') || extname(pathname)) {
				sendPlain(response, 404, 'Not found');
				return;
			}
			filePath = resolve(frontendDir, 'index.html');
			fileStat = await stat(filePath);
		}
		const extension = extname(filePath).toLowerCase();
		const compressible =
			COMPRESSIBLE_EXTENSIONS.has(extension) && fileStat.size <= COMPRESS_MAX_BYTES;
		const acceptEncoding = request.headers['accept-encoding'] || '';
		let encoding = compressible ? selectEncoding(acceptEncoding) : 'identity';
		if (request.headers.range || fileStat.size < COMPRESS_MIN_BYTES)
			encoding = identityAllowed(acceptEncoding) ? 'identity' : encoding;
		if (!encoding || (encoding === 'identity' && !identityAllowed(acceptEncoding))) {
			sendPlain(response, 406, 'No acceptable content encoding');
			return;
		}
		const etag = `W/"${fileStat.size}-${fileStat.mtimeMs}-${encoding}"`;
		response.setHeader('Content-Type', MIME_TYPES[extension] || 'application/octet-stream');
		response.setHeader(
			'Cache-Control',
			pathname.startsWith('/_app/immutable/') ? 'public, max-age=31536000, immutable' : 'no-cache'
		);
		response.setHeader('ETag', etag);
		response.setHeader('Last-Modified', fileStat.mtime.toUTCString());
		response.setHeader('X-Content-Type-Options', 'nosniff');
		if (compressible) response.setHeader('Vary', 'Accept-Encoding');
		if (
			request.headers['if-none-match']
				?.split(',')
				.map((item) => item.trim())
				.includes(etag)
		) {
			response.statusCode = 304;
			response.end();
			return;
		}
		if (encoding !== 'identity') {
			const asset = await readCompressedAsset(filePath, fileStat);
			const content = asset[encoding];
			response.setHeader('Content-Encoding', encoding);
			response.setHeader('Content-Length', content.length);
			response.end(request.method === 'HEAD' ? undefined : content);
			return;
		}
		response.setHeader('Accept-Ranges', 'bytes');
		let range;
		if (
			request.headers.range &&
			(!request.headers['if-range'] || request.headers['if-range'] === etag)
		) {
			range = parseByteRange(request.headers.range, fileStat.size);
			if (!range) {
				response.setHeader('Content-Range', `bytes */${fileStat.size}`);
				sendPlain(response, 416, 'Range not satisfiable');
				return;
			}
			response.statusCode = 206;
			response.setHeader('Content-Range', `bytes ${range.start}-${range.end}/${fileStat.size}`);
		}
		response.setHeader('Content-Length', range ? range.end - range.start + 1 : fileStat.size);
		if (request.method === 'HEAD') return response.end();
		const stream = createReadStream(filePath, range);
		stream.on('error', () => response.destroy());
		response.on('close', () => stream.destroy());
		stream.pipe(response);
	}

	function staticMiddleware(request, response, next) {
		serveStatic(request, response, next).catch(() => {
			if (response.headersSent) response.destroy();
			else sendPlain(response, 500, 'Could not read frontend asset');
		});
	}
	return staticMiddleware;
}

export async function createFrontendServer(options = {}) {
	const frontendDir = resolve(options.frontendDir || resolve(WORKSPACE_DIR, 'build'));
	if (!(await stat(resolve(frontendDir, 'index.html')).catch(() => null))?.isFile()) {
		throw new Error(
			'Buddy frontend is missing. Build it first or pass --frontend-dir with an existing build.'
		);
	}
	const backendUrl = new URL(options.backendUrl || 'http://127.0.0.1:8081');
	if (
		!['http:', 'https:'].includes(backendUrl.protocol) ||
		backendUrl.username ||
		backendUrl.password ||
		backendUrl.pathname !== '/' ||
		backendUrl.search ||
		backendUrl.hash
	) {
		throw new Error('Backend must be an HTTP or HTTPS origin without credentials, path, or query.');
	}
	const allowedHosts = options.allowedHosts || [];
	const staticMiddleware = createStaticMiddleware(frontendDir, allowedHosts);
	const staticPlugin = {
		name: 'buddy-frozen-frontend',
		configurePreviewServer(server) {
			server.middlewares.use(staticMiddleware);
		}
	};
	const server = await preview({
		configFile: false,
		root: frontendDir,
		publicDir: false,
		logLevel: options.logLevel || 'warn',
		plugins: [staticPlugin],
		build: { outDir: frontendDir },
		appType: 'spa',
		preview: {
			host: options.host || '127.0.0.1',
			port: options.port ?? 8082,
			strictPort: true,
			allowedHosts,
			proxy: backendProxy(backendUrl.origin)
		}
	});
	return server;
}

async function main() {
	const { values } = parseArgs({
		options: {
			host: { type: 'string', default: '0.0.0.0' },
			port: { type: 'string', default: '8082' },
			backend: { type: 'string', default: 'http://127.0.0.1:8081' },
			'frontend-dir': { type: 'string', default: resolve(WORKSPACE_DIR, 'build') },
			'allowed-host': { type: 'string', multiple: true },
			help: { type: 'boolean' }
		}
	});
	if (values.help) {
		console.log(
			'node local/serve-frontend.mjs --port 8082 --backend http://127.0.0.1:8081 --frontend-dir build [--allowed-host hostname]'
		);
		return;
	}
	const port = Number(values.port);
	if (!Number.isInteger(port) || port < 1 || port > 65535)
		throw new Error('Port must be between 1 and 65535.');
	const environmentHosts = (process.env.MCP_OAUTH_ALLOWED_REDIRECT_HOSTS || '')
		.split(',')
		.map((host) => host.trim())
		.filter(Boolean);
	const server = await createFrontendServer({
		host: values.host,
		port,
		backendUrl: values.backend,
		frontendDir: values['frontend-dir'],
		allowedHosts: [...environmentHosts, ...(values['allowed-host'] || [])]
	});
	console.log(`Buddy frontend ready on port ${port}; backend ${new URL(values.backend).origin}`);
	server.printUrls();
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
	main().catch((error) => {
		console.error(error.message);
		process.exitCode = 1;
	});
}
