import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import electron from "vite-plugin-electron";
import renderer from "vite-plugin-electron-renderer";

export default defineConfig({
  plugins: [
    react(),
    electron([
      {
        entry: "electron/main/index.ts",
        vite: {
          build: {
            outDir: "dist-electron/main",
            emptyOutDir: false,
            rollupOptions: {
              output: {
                entryFileNames: "index.js",
              },
            },
          },
        },
      },
      {
        entry: "electron/preload/index.ts",
        vite: {
          build: {
            outDir: "dist-electron/preload",
            emptyOutDir: false,
            lib: {
              entry: "electron/preload/index.ts",
              formats: ["cjs"],
              fileName: () => "index.cjs",
            },
          },
        },
      },
    ]),
    renderer(),
  ],
  resolve: {
    alias: {
      "@": new URL("./app/src", import.meta.url).pathname,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./tests/setup.ts",
  },
});
