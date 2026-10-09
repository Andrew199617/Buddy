import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

import { viteStaticCopy } from 'vite-plugin-static-copy';
import { createStartupChunks, separateSharedKeyboardHelper } from './local/startup-chunks.mjs';

const backendTarget = process.env.WEBUI_BACKEND_URL || 'http://localhost:8080';

export default defineConfig(({ isSsrBuild }) => ({
	resolve: {
		conditions: ['onnxruntime-web-use-extern-wasm']
	},
	plugins: [
		sveltekit(),
		viteStaticCopy({
			targets: [
				{
					src: 'node_modules/onnxruntime-web/dist/*.jsep.*',

					dest: 'wasm'
				}
			]
		})
	],
	define: {
		APP_VERSION: JSON.stringify(process.env.npm_package_version),
		APP_BUILD_HASH: JSON.stringify(process.env.APP_BUILD_HASH || 'dev-build')
	},
	build: {
		sourcemap: true,
		rollupOptions: {
			output: {
				manualChunks: isSsrBuild ? separateSharedKeyboardHelper : createStartupChunks(),
				onlyExplicitManualChunks: true
			}
		}
	},
	server: {
		proxy: {
			'/api': {
				target: backendTarget,
				// OAuth callbacks must retain the browser's host and port.
				changeOrigin: false,
				ws: true
			},
			'/ollama': {
				target: backendTarget,
				changeOrigin: true
			},
			'/openai': {
				target: backendTarget,
				changeOrigin: true
			},
			'/oauth': {
				target: backendTarget,
				// OAuth callbacks must retain the browser's host and port.
				changeOrigin: false
			},
			'/ws': {
				target: backendTarget,
				changeOrigin: true,
				ws: true
			}
		}
	},
	worker: {
		format: 'es'
	},
	esbuild: {
		pure: process.env.ENV === 'dev' ? [] : ['console.log', 'console.debug', 'console.error']
	}
}));
