import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { TanStackRouterVite } from '@tanstack/router-plugin/vite'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    // TanStackRouterVite must come first so it generates routeTree.gen.ts
    // before the React plugin processes the files.
    TanStackRouterVite({ routesDirectory: './src/routes' }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      // Allows imports like "@/components/..." throughout the app
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    // Dev server proxy: /api and /art calls go to the FastAPI backend
    proxy: {
      '/api': 'http://localhost:8000',
      '/art': 'http://localhost:8000',
    },
  },
  build: {
    // Output to backend/dist/ so FastAPI (main.py) serves it directly.
    // main.py uses Path(__file__).parent.parent / "dist" = backend/dist/.
    outDir: '../backend/dist',
    emptyOutDir: true,
  },
})
