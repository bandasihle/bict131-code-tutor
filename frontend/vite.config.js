import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Tailwind v4 is configured through this plugin — there is no tailwind.config.js.
export default defineConfig({
  plugins: [react(), tailwindcss()],
})
