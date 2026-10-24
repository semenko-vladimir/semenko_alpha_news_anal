import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const proxy = { '/api': process.env.ALPHA_UI_API_TARGET || 'http://127.0.0.1:8000' };

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy,
  },
  preview: { host: '127.0.0.1', proxy },
});
