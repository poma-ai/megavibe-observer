import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
// The server only accepts POSTs whose Host is its own and whose Origin is absent or its own: the dev proxy
// rewrites Host and drops the page's Origin (localhost:5173) so "open in terminal" works under `npm run dev`.
const api = { target: 'http://127.0.0.1:4747', changeOrigin: true, configure: (p) => p.on('proxyReq', (r) => r.removeHeader('origin')) };
export default defineConfig({ plugins: [svelte()], server: { host: '127.0.0.1', proxy: { '/api': api } } });
