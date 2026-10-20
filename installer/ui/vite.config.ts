import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins: [react()], server: {port: 1420, strictPort: true}, clearScreen: false,
  build: {rollupOptions: {input: {installer:'index.html', diff:'diff.html', ota:'ota.html'}}}});
