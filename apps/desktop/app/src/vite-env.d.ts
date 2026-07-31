/// <reference types="vite/client" />

import type { PpxClientApi } from "./types";

declare global {
  interface Window {
    ppxClient: PpxClientApi;
  }
}

export {};
