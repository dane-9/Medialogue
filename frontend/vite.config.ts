import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')

  return {
    plugins: [react()],
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
