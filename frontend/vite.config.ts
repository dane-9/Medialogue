import { setDefaultResultOrder } from 'node:dns'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Keep the dev server local-only while avoiding the Windows/Firefox mismatch
// where `localhost` resolves to ::1 in one process and 127.0.0.1 in another.
setDefaultResultOrder('ipv4first')

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')

  return {
    plugins: [react()],
    define: {
      __BUILD_ID__: JSON.stringify(String(Date.now())),
    },
    server: {
      // LAN exposure is an explicit opt-in: VITE_DEV_HOST=0.0.0.0.
      host: env.VITE_DEV_HOST || 'localhost',
      port: 5173,
      proxy: {
        '/api': 'http://localhost:8000',
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
    },
  }
})
