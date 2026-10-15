/// <reference types="vitest/config" />
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig } from "vite";

const root = resolve(import.meta.dirname, "web");
const certs = resolve(import.meta.dirname, "certs");
const hasCerts = existsSync(resolve(certs, "cert.pem")) && existsSync(resolve(certs, "key.pem"));
const backendPort = process.env.KARAOKE_PORT ?? "8000";
const backend = `${hasCerts ? "https" : "http"}://127.0.0.1:${backendPort}`;

export default defineConfig({
  root,
  build: {
    outDir: resolve(root, "dist"),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        host: resolve(root, "host/index.html"),
        mic: resolve(root, "mic/index.html"),
      },
    },
  },
  server: {
    host: true,
    https: hasCerts
      ? { cert: readFileSync(resolve(certs, "cert.pem")), key: readFileSync(resolve(certs, "key.pem")) }
      : undefined,
    proxy: {
      "/api": { target: backend, secure: false },
      "/ws": { target: backend, ws: true, secure: false },
    },
  },
  test: {
    root: import.meta.dirname,
    include: ["web/**/*.test.ts"],
  },
});
