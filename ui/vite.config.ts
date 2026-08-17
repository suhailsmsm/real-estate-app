import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * MapLibre decodes tiles off the main thread via a Worker it constructs
 * internally from a relative runtime path, which is neither a static import
 * nor a bundler-analyzable `new Worker(new URL(...))` — Vite has no way to
 * know the file needs including, so it never lands in the build and the
 * request silently falls through to the SPA's own index.html (wrong MIME
 * type, worker construction fails, map hangs forever with no tiles and no
 * loud error). This bit for real (found by reproducing the production build
 * and inspecting the network response's actual bytes, not by inspection).
 *
 * The worker script itself then does a SECOND relative import of a sibling
 * file, `maplibre-gl-shared.mjs`, hardcoded as `./maplibre-gl-shared.mjs` —
 * so both files must live together, under their ORIGINAL names, for that
 * internal reference to resolve. That is exactly what Vite's `public/`
 * directory is for: files copied byte-for-byte, unhashed, served at a fixed
 * path. `MapView.tsx` points MapLibre at `/maplibre-gl-worker.mjs` via
 * `setWorkerUrl()` (its own supported escape hatch for this).
 *
 * Copying happens here, at config-load time, rather than as a one-off
 * manual copy into public/: it runs on every `dev`/`build`/`preview`, reads
 * straight from whatever maplibre-gl version is actually installed, and so
 * can never silently drift out of sync the way a checked-in static copy
 * would after the next `npm update`.
 */
function syncMapLibreWorkerAssets(): void {
  const src = dirname(fileURLToPath(import.meta.resolve("maplibre-gl/package.json")));
  const publicDir = fileURLToPath(new URL("./public", import.meta.url));
  mkdirSync(publicDir, { recursive: true });
  for (const name of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) {
    const from = join(src, "dist", name);
    if (!existsSync(from)) {
      throw new Error(
        `vite.config.ts: expected ${from} to exist — maplibre-gl's dist layout ` +
          "changed; update syncMapLibreWorkerAssets() to match.",
      );
    }
    copyFileSync(from, join(publicDir, name));
  }
}
syncMapLibreWorkerAssets();

/**
 * The dev server proxies `/api` to the API's loopback-only binding
 * (docker-compose.yml), so the browser is always same-origin and CORS never
 * enters the picture. The prefix is stripped on the way through because the
 * API serves its routes at the root (`/facts/...`) — it is nginx that mounts
 * it under `/api` in production, and this mirrors that shape so the same
 * client code works in both.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.DXB_API_URL ?? "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      // The copilot streams SSE, so buffering must stay off through the proxy
      // or the answer arrives all at once at the end instead of as it is written.
      "/copilot": {
        target: process.env.DXB_COPILOT_URL ?? "http://127.0.0.1:8200",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/copilot/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/core/api-types.ts"],
    },
  },
});
