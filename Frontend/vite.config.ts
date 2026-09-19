import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// Dev proxy mirrors nginx.conf so the browser only ever talks to one origin.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:3000', rewrite: (p) => p.replace(/^\/api/, '') },
      '/ai': { target: 'http://localhost:8000' },
    },
  },
});
