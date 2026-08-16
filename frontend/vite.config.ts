import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/auth': 'http://localhost:8000',
      '/chat': 'http://localhost:8000',
      '/docs': 'http://localhost:8000',
      '/lessons': 'http://localhost:8000',
      '/quiz': 'http://localhost:8000',
      '/rewards': 'http://localhost:8000',
      '/settings': 'http://localhost:8000',
      '/sync': 'http://localhost:8000',
      '/usage': 'http://localhost:8000',
      '/pipeline': 'http://localhost:8000',
      '/bin': 'http://localhost:8000',
      '/friends': 'http://localhost:8000',
      '/presence': 'http://localhost:8000',
      '/leaderboard': 'http://localhost:8000',
      '/shop': 'http://localhost:8000',
      '/workspace': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
