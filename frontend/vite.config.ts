import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The board talks straight to the FastAPI backend at http://localhost:8000 (see src/api.ts);
// the backend's CORS settings allow this page's origin, http://localhost:5173.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
})
