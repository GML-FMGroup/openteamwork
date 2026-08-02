import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";
import vm from "node:vm";

const apiExposurePattern = /\.exposeInMainWorld\(\s*["']ppxClient["']/g;

/** Validate that a preload is parseable CommonJS and injects the host API exactly once. */
export function verifyPreloadSource(source, sourceName) {
  try {
    new vm.Script(source, { filename: sourceName });
  } catch (error) {
    throw new Error(`Preload syntax validation failed for ${sourceName}: ${error.message}`, {
      cause: error,
    });
  }

  const exposureCount = source.match(apiExposurePattern)?.length ?? 0;
  if (exposureCount !== 1) {
    throw new Error(
      `Expected exactly one ppxClient contextBridge exposure in ${sourceName}; found ${exposureCount}.`,
    );
  }
}

/** Resolve Electron Builder's locked ASAR implementation without adding a duplicate dependency. */
function loadAsarApi() {
  const scriptRequire = createRequire(import.meta.url);
  const electronBuilderPackage = scriptRequire.resolve("electron-builder/package.json");
  const electronBuilderRequire = createRequire(electronBuilderPackage);
  const appBuilderPackage = electronBuilderRequire.resolve("app-builder-lib/package.json");
  return createRequire(appBuilderPackage)("@electron/asar");
}

async function readPreload(args) {
  if (args[0] === "--asar") {
    const [, asarPath, memberPath, ...unexpected] = args;
    if (!asarPath || !memberPath || unexpected.length > 0) {
      throw new Error("Usage: verify-preload.mjs --asar <app.asar> <member-path>");
    }
    const absoluteAsarPath = path.resolve(asarPath);
    const source = loadAsarApi().extractFile(absoluteAsarPath, memberPath).toString("utf8");
    return {
      source,
      sourceName: `${absoluteAsarPath}:${memberPath}`,
    };
  }

  if (args.length !== 1) {
    throw new Error("Usage: verify-preload.mjs <preload-path>");
  }
  const absolutePath = path.resolve(args[0]);
  return {
    source: await readFile(absolutePath, "utf8"),
    sourceName: absolutePath,
  };
}

async function main() {
  const { source, sourceName } = await readPreload(process.argv.slice(2));
  verifyPreloadSource(source, sourceName);
  console.log(`Verified preload: ${sourceName}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  await main();
}
