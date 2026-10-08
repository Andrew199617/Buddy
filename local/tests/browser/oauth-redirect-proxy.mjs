import assert from 'node:assert/strict';
import { createServer as createHttpServer, request as httpRequest } from 'node:http';
import { resolve } from 'node:path';
import { createServer as createViteServer, loadConfigFromFile } from 'vite';

function requestThroughProxy(port, host, path, method) {
	return new Promise((resolveResponse, rejectResponse) => {
		const request = httpRequest(
			{
				hostname: '127.0.0.1',
				port,
				path,
				method,
				headers: { Host: host, 'Content-Type': 'application/json' }
			},
			(response) => {
				let body = '';
				response.setEncoding('utf8');
				response.on('data', (chunk) => {
					body += chunk;
				});
				response.on('end', () => {
					try {
						resolveResponse(JSON.parse(body));
					} catch (error) {
						rejectResponse(error);
					}
				});
				response.on('error', rejectResponse);
			}
		);
		request.on('error', rejectResponse);
		if (method === 'POST') request.write('{}');
		request.end();
	});
}

const backend = createHttpServer((request, response) => {
	response.writeHead(200, { 'Content-Type': 'application/json' });
	response.end(JSON.stringify({ host: request.headers.host, path: request.url }));
});
await new Promise((resolveListening) => backend.listen(0, '127.0.0.1', resolveListening));
const backendPort = backend.address().port;
const previousBackendUrl = process.env.WEBUI_BACKEND_URL;
process.env.WEBUI_BACKEND_URL = `http://127.0.0.1:${backendPort}`;
let preview;
try {
	const loaded = await loadConfigFromFile(
		{ command: 'serve', mode: 'development' },
		resolve('vite.config.ts')
	);
	assert.ok(loaded, 'Tracked Vite configuration must load');
	preview = await createViteServer({
		configFile: false,
		appType: 'custom',
		server: { host: '127.0.0.1', port: 0, proxy: loaded.config.server.proxy },
		logLevel: 'error'
	});
	await new Promise((resolveListening) =>
		preview.httpServer.listen(0, '127.0.0.1', resolveListening)
	);
	const previewPort = preview.httpServer.address().port;
	const origins = ['localhost:8080', '127.0.0.1:8081', '100.122.80.32:8082', '100.122.80.32:9123'];
	const registrationPath = '/api/v1/configs/oauth/clients/register';
	const callbackPath = '/oauth/clients/mcp:slack/callback?state=mock-state';
	for (const host of origins) {
		const registration = await requestThroughProxy(previewPort, host, registrationPath, 'POST');
		assert.deepEqual(registration, { host, path: registrationPath });
		const callback = await requestThroughProxy(previewPort, host, callbackPath, 'GET');
		assert.deepEqual(callback, { host, path: callbackPath });
	}
	console.log('PASS: OAuth registration and callbacks preserve all four browser hosts/ports.');
} finally {
	if (preview) await preview.close();
	await new Promise((resolveClosed) => backend.close(resolveClosed));
	if (previousBackendUrl === undefined) delete process.env.WEBUI_BACKEND_URL;
	else process.env.WEBUI_BACKEND_URL = previousBackendUrl;
}
