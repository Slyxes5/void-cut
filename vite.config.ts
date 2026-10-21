import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
port: 5173,
strictPort: true,

allowedHosts: [
    'laptop-h144cnvd.tail52931a.ts.net',
  ],

    watch: {
      ignored: ['**/dist_app/**', '**/dist_installer/**', '**/build/**', '**/venv/**']
    },
    proxy: {
      '/social/oauth': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
        configure: (proxy) => {
          proxy.on('error', (_err, _req, res: any) => {
            if (res && !res.headersSent && typeof res.writeHead === 'function') {
              res.writeHead(503, { 'Content-Type': 'application/json' });
              res.end(JSON.stringify({
                error: 'Backend API server on port 8000 is not running. Start the backend with `npm run dev` or `python -m uvicorn backend.main:app --port 8000`.',
                status: 503
              }));
            }
          });
        }
      },
      '/docs': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
      '/redoc': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
      '/openapi.json': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    }
  }
})

