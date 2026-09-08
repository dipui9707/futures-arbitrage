import { sites } from '@openai/sites-vite-plugin';
import tailwindcss from '@tailwindcss/postcss';
import vinext from 'vinext';
import { defineConfig } from 'vite';
export default defineConfig({
  css: { postcss: { plugins: [tailwindcss()] } },
  server: {
    host: process.env.HOST || '127.0.0.1',
    port: Number(process.env.FRONTEND_PORT || 5173),
    strictPort: true,
    watch: { useFsEvents: false, usePolling: true },
    proxy: {
      '/api': {
        target:
          process.env.BACKEND_URL ||
          `http://127.0.0.1:${process.env.BACKEND_PORT || 8767}`,
        changeOrigin: true,
      },
    },
  },
  plugins: [vinext(), sites()],
});
