import assert from "node:assert/strict";
import test from "node:test";

import { verifyPreloadSource } from "./verify-preload.mjs";

const validPreload = `
  "use strict";
  const electron = require("electron");
  electron.contextBridge.exposeInMainWorld("ppxClient", {});
`;

test("accepts one parseable CommonJS host API exposure", () => {
  assert.doesNotThrow(() => verifyPreloadSource(validPreload, "valid-preload.cjs"));
});

test("rejects a syntactically corrupted preload", () => {
  const corruptedPreload = `${validPreload}\nreturn () => {};\n}`;

  assert.throws(
    () => verifyPreloadSource(corruptedPreload, "corrupted-preload.cjs"),
    /Preload syntax validation failed/,
  );
});

test("rejects missing or duplicate host API exposure", () => {
  assert.throws(
    () => verifyPreloadSource('"use strict";', "missing-exposure.cjs"),
    /found 0/,
  );
  assert.throws(
    () =>
      verifyPreloadSource(
        `${validPreload}\nelectron.contextBridge.exposeInMainWorld("ppxClient", {});`,
        "duplicate-exposure.cjs",
      ),
    /found 2/,
  );
});
