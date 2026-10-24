// Original Vite config and production pipeline. All output/cache/env paths
// belong to EVAL. Native config loading avoids .vite-temp outside our scope.
import path from 'node:path';
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const [root, out] = process.argv.slice(2);
const { build } = await import(pathToFileURL(path.join(root, 'frontend/node_modules/vite/dist/node/index.js')));
fs.mkdirSync(path.join(out, 'empty-env'), { recursive: true });
await build({ root: path.join(root, 'frontend'), configFile: path.join(root, 'frontend/vite.config.ts'),
  configLoader: 'native', envDir: path.join(out, 'empty-env'), cacheDir: path.join(out, 'vite-cache'),
  build: { outDir: path.join(out, 'dist'), emptyOutDir: true } });
