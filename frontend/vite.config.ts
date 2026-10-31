import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({mode})=>({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy: { '/api': loadEnv(mode,'.','COMPANY_').COMPANY_FRONTEND_API_TARGET || 'http://127.0.0.1:8000' } },
}))
